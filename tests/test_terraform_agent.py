"""
tests/test_terraform_agent.py
------------------------------
Unit tests for agent/terraform_agent.py.
All tests use InMemoryObjectStore — no OCI SDK or real LLM required.
GitHub API calls are patched out.
"""
import json
import pytest
from unittest.mock import patch

from agent.persistence_objectstore import InMemoryObjectStore
from agent.document_store import save_note
from agent.context_store import read_context, record_agent_run
from agent.terraform_agent import (
    generate_terraform,
    get_latest_terraform_files,
    list_terraform_versions,
    _parse_terraform_files,
    _get_next_version,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    return InMemoryObjectStore()


_FAKE_MAIN_TF = """\
terraform {
  required_providers {
    oci = { source = "oracle/oci", version = "~> 5.0" }
  }
}

provider "oci" {
  tenancy_ocid = var.tenancy_ocid
  region       = var.region
}

resource "oci_core_vcn" "main" {
  compartment_id = var.compartment_id
  cidr_block     = var.vcn_cidr
  display_name   = "TestCo-VCN"
  freeform_tags  = { "managed-by" = "terraform", "customer" = "TestCo" }
}
"""

_FAKE_VARIABLES_TF = """\
variable "tenancy_ocid"    { description = "Tenancy OCID" }
variable "compartment_id"  { description = "Compartment OCID" }
variable "region"          { default = "us-chicago-1" }
variable "vcn_cidr"        { default = "10.0.0.0/16" }
"""

_FAKE_OUTPUTS_TF = """\
output "vcn_id" { value = oci_core_vcn.main.id }
"""

_FAKE_TFVARS = """\
# tenancy_ocid = "[TBD]"
# compartment_id = "[TBD]"
region   = "us-chicago-1"
vcn_cidr = "10.0.0.0/16"
"""


def _fake_runner_structured(prompt: str, system_message: str = "") -> str:
    """Return a properly structured LLM response with all four files."""
    return (
        "// FILE: main.tf\n```hcl\n" + _FAKE_MAIN_TF + "```\n\n"
        "// FILE: variables.tf\n```hcl\n" + _FAKE_VARIABLES_TF + "```\n\n"
        "// FILE: outputs.tf\n```hcl\n" + _FAKE_OUTPUTS_TF + "```\n\n"
        "// FILE: terraform.tfvars.example\n```hcl\n" + _FAKE_TFVARS + "```\n"
    )


def _fake_runner_fallback(prompt: str, system_message: str = "") -> str:
    """Return plain fenced HCL blocks (fallback parsing path)."""
    return (
        "```hcl\n" + _FAKE_MAIN_TF + "```\n\n"
        "```hcl\n" + _FAKE_VARIABLES_TF + "```\n\n"
        "```hcl\n" + _FAKE_OUTPUTS_TF + "```\n\n"
        "```hcl\n" + _FAKE_TFVARS + "```\n"
    )


# ── _parse_terraform_files ────────────────────────────────────────────────────

class TestParseTerraformFiles:
    def test_structured_output_parsed(self):
        raw = _fake_runner_structured("", "")
        files = _parse_terraform_files(raw)
        assert "main.tf" in files
        assert "variables.tf" in files
        assert "outputs.tf" in files
        assert "terraform.tfvars.example" in files

    def test_structured_main_tf_content(self):
        raw = _fake_runner_structured("", "")
        files = _parse_terraform_files(raw)
        assert "oci_core_vcn" in files["main.tf"]

    def test_structured_variables_tf_content(self):
        raw = _fake_runner_structured("", "")
        files = _parse_terraform_files(raw)
        assert "tenancy_ocid" in files["variables.tf"]

    def test_fallback_plain_blocks(self):
        raw = _fake_runner_fallback("", "")
        files = _parse_terraform_files(raw)
        assert "main.tf" in files
        assert "oci_core_vcn" in files["main.tf"]

    def test_total_fallback_puts_everything_in_main(self):
        raw = "resource \"oci_core_vcn\" \"main\" { display_name = \"VCN\" }"
        files = _parse_terraform_files(raw)
        assert "main.tf" in files
        assert "oci_core_vcn" in files["main.tf"]

    def test_all_four_files_present_after_parse(self):
        raw = _fake_runner_structured("", "")
        files = _parse_terraform_files(raw)
        assert len(files) == 4

    def test_empty_main_tf_block_falls_through_to_fallback(self):
        """LLM emits empty main.tf block but content elsewhere — must not return empty main.tf."""
        raw = (
            "// FILE: main.tf\n```hcl\n```\n\n"  # empty block
            "// FILE: variables.tf\n```hcl\n" + _FAKE_VARIABLES_TF + "```\n\n"
            "// FILE: outputs.tf\n```hcl\n" + _FAKE_OUTPUTS_TF + "```\n\n"
            "// FILE: terraform.tfvars.example\n```hcl\n" + _FAKE_TFVARS + "```\n"
            # Fallback block: an hcl block that contains the actual main.tf content
            "\n```hcl\n" + _FAKE_MAIN_TF + "```\n"
        )
        files = _parse_terraform_files(raw)
        assert files.get("main.tf"), "main.tf must not be empty when fallback content exists"
        assert "oci_core_vcn" in files["main.tf"]
        # Other files should still have their structured content
        assert "tenancy_ocid" in files["variables.tf"]

    def test_partial_structured_no_main_tf_uses_fallback_block(self):
        """Only variables/outputs/tfvars extracted; main.tf falls back to first fenced block."""
        raw = (
            # main.tf intentionally missing the // FILE: comment — parser can't find it
            "```hcl\n" + _FAKE_MAIN_TF + "```\n\n"
            "// FILE: variables.tf\n```hcl\n" + _FAKE_VARIABLES_TF + "```\n\n"
            "// FILE: outputs.tf\n```hcl\n" + _FAKE_OUTPUTS_TF + "```\n\n"
            "// FILE: terraform.tfvars.example\n```hcl\n" + _FAKE_TFVARS + "```\n"
        )
        files = _parse_terraform_files(raw)
        assert "main.tf" in files
        # variables/outputs/tfvars should preserve structured content
        assert "tenancy_ocid" in files["variables.tf"]


# ── generate_terraform ────────────────────────────────────────────────────────

class TestGenerateTerraform:
    def test_generate_returns_result_dict(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            result = generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        assert result["version"] == 1
        assert result["file_count"] == 4
        assert "prefix_key" in result
        assert "files" in result
        assert "latest_key" in result

    def test_generate_persists_files(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        assert store.head("terraform/cust1/v1/main.tf")
        assert store.head("terraform/cust1/v1/variables.tf")
        assert store.head("terraform/cust1/v1/outputs.tf")
        assert store.head("terraform/cust1/v1/terraform.tfvars.example")

    def test_generate_writes_manifest(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        assert store.head("terraform/cust1/MANIFEST.json")

    def test_generate_writes_latest_json(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        assert store.head("terraform/cust1/LATEST.json")

    def test_generate_increments_version(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
            result2 = generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        assert result2["version"] == 2
        assert store.head("terraform/cust1/v2/main.tf")

    def test_generate_updates_context(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            result = generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        assert "terraform" in result["context"]["agents"]
        assert result["context"]["agents"]["terraform"]["version"] == 1

    def test_generate_notes_ingested_in_context(self, store):
        save_note(store, "cust1", "arch_notes.txt", b"VCN with 3 subnets")
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            result = generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        incorporated = result["context"]["agents"]["terraform"]["notes_incorporated"]
        assert "notes/cust1/arch_notes.txt" in incorporated

    def test_generate_no_notes_still_works(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            result = generate_terraform("cust1", "EmptyCo", store, _fake_runner_structured)
        assert result["version"] == 1

    def test_generate_includes_notes_in_prompt(self, store):
        save_note(store, "cust1", "notes.txt", b"Need OKE cluster with 4 GPU nodes")
        prompts = []

        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_runner_structured(prompt, system_msg)

        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, capturing)
        assert any("GPU" in p for p in prompts)

    def test_generate_includes_previous_version_in_prompt(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)

        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_runner_structured(prompt, system_msg)

        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, capturing)
        assert any("Previous Terraform" in p for p in prompts)

    def test_generate_github_examples_injected_in_prompt(self, store):
        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_runner_structured(prompt, system_msg)

        with patch(
            "agent.terraform_agent._search_github_examples",
            return_value="# Example: vcn.tf\nresource oci_core_vcn ...",
        ):
            generate_terraform("cust1", "TestCo", store, capturing)
        assert any("oracle-quickstart" in p.lower() or "Example:" in p for p in prompts)

    def test_generate_context_summary_injected(self, store):
        """If a prior agent ran, its summary should appear in the Terraform prompt."""
        ctx = read_context(store, "cust1", "TestCo")
        ctx = record_agent_run(ctx, "pov", [], {"version": 1, "key": "pov/cust1/v1.md"})
        from agent.context_store import write_context
        write_context(store, "cust1", ctx)

        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_runner_structured(prompt, system_msg)

        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, capturing)
        assert any("POV" in p or "Prior agent" in p for p in prompts)

    def test_generate_does_not_reingest_seen_notes(self, store):
        save_note(store, "cust1", "old.txt", b"old architecture note")
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)

        prompts = []
        def capturing(prompt, system_msg=""):
            prompts.append(prompt)
            return _fake_runner_structured(prompt, system_msg)

        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, capturing)
        assert any("No new notes" in p for p in prompts)


# ── get_latest_terraform_files ────────────────────────────────────────────────

class TestGetLatestTerraformFiles:
    def test_raises_keyerror_if_missing(self, store):
        with pytest.raises(KeyError):
            get_latest_terraform_files(store, "nonexistent")

    def test_returns_files_after_generate(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        result = get_latest_terraform_files(store, "cust1")
        assert "main.tf" in result["files"]
        assert "oci_core_vcn" in result["files"]["main.tf"]

    def test_version_is_correct(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        result = get_latest_terraform_files(store, "cust1")
        assert result["version"] == 2


# ── list_terraform_versions ───────────────────────────────────────────────────

class TestListTerraformVersions:
    def test_empty_if_no_terraform(self, store):
        versions = list_terraform_versions(store, "cust1")
        assert versions == []

    def test_lists_versions_after_generate(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        versions = list_terraform_versions(store, "cust1")
        assert len(versions) == 1
        assert versions[0]["version"] == 1

    def test_lists_multiple_versions(self, store):
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
            generate_terraform("cust1", "TestCo", store, _fake_runner_structured)
        versions = list_terraform_versions(store, "cust1")
        assert len(versions) == 2
