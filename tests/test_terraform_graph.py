from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent.graphs import terraform_graph


def test_terraform_graph_success_path():
    def fake_text_runner(prompt: str, _system_message: str) -> str:
        if "STAGE: plan-eng-review" in prompt:
            return json.dumps({"ok": True, "output": "plan output", "questions": []})
        if "STAGE: review" in prompt:
            return json.dumps({"ok": True, "output": "review output", "questions": []})
        if "STAGE: cso" in prompt:
            return json.dumps({"ok": True, "output": "cso output", "questions": []})
        return json.dumps({"ok": True, "output": "final terraform output", "questions": []})

    skill_root = Path(__file__).resolve().parents[1] / "gstack_skills"
    summary, artifact_key, result_data = asyncio.run(
        terraform_graph.run(
            args={"prompt": "Build secure Terraform for OKE and networking"},
            skill_root=skill_root,
            text_runner=fake_text_runner,
        )
    )

    assert "Terraform generation completed via stages:" in summary
    assert "final terraform output" in summary
    assert artifact_key == ""
    assert result_data["ok"] is True
    assert len(result_data["stages"]) == 4


def test_terraform_graph_blocked_on_failed_stage():
    def fake_text_runner(prompt: str, _system_message: str) -> str:
        if "STAGE: plan-eng-review" in prompt:
            return json.dumps({"ok": True, "output": "plan output", "questions": []})
        return json.dumps(
            {
                "ok": False,
                "output": "",
                "questions": ["Confirm Terraform provider version.", "Confirm tenancy guardrails."],
            }
        )

    skill_root = Path(__file__).resolve().parents[1] / "gstack_skills"
    summary, artifact_key, result_data = asyncio.run(
        terraform_graph.run(
            args={"prompt": "Build secure Terraform for OKE and networking"},
            skill_root=skill_root,
            text_runner=fake_text_runner,
        )
    )

    assert "Terraform generation blocked at stage `review`" in summary
    assert "Confirm Terraform provider version." in summary
    assert artifact_key == ""
    assert result_data["ok"] is False
    assert result_data["stages"][-1]["stage"] == "review"


def test_terraform_graph_blocks_on_non_json_stage_output():
    def fake_text_runner(_prompt: str, _system_message: str) -> str:
        return "not-json"

    skill_root = Path(__file__).resolve().parents[1] / "gstack_skills"
    summary, artifact_key, result_data = asyncio.run(
        terraform_graph.run(
            args={"prompt": "Build Terraform"},
            skill_root=skill_root,
            text_runner=fake_text_runner,
        )
    )

    assert "Terraform generation blocked at stage `plan-eng-review`" in summary
    assert "returned non-JSON output" in summary
    assert artifact_key == ""
    assert result_data["ok"] is False


def test_terraform_graph_repairs_invalid_oci_fields_in_final_output():
    def fake_text_runner(prompt: str, _system_message: str) -> str:
        if "STAGE: plan-eng-review" in prompt:
            return json.dumps({"ok": True, "output": "plan output", "questions": []})
        if "STAGE: review" in prompt:
            return json.dumps({"ok": True, "output": "review output", "questions": []})
        if "STAGE: cso" in prompt:
            return json.dumps({"ok": True, "output": "cso output", "questions": []})
        if "STAGE: qa" in prompt:
            return json.dumps(
                {
                    "ok": True,
                    "output": """
resource "oci_core_subnet" "bad" {
  dns_label                         = "acmedemopublicsubnet"
  prohibit_internet_ingress         = false
  prohibit_public_ip_on_vnic_option = "NONE"
}
""",
                    "questions": [],
                }
            )
        # Repair pass
        return json.dumps(
            {
                "files": {
                    "main.tf": """
resource "oci_core_subnet" "good" {
  dns_label                  = "acmedemo01"
  prohibit_public_ip_on_vnic = false
}
"""
                }
            }
        )

    skill_root = Path(__file__).resolve().parents[1] / "gstack_skills"
    summary, artifact_key, result_data = asyncio.run(
        terraform_graph.run(
            args={"prompt": "Build Terraform"},
            skill_root=skill_root,
            text_runner=fake_text_runner,
        )
    )

    assert "Terraform generation completed via stages:" in summary
    assert artifact_key == ""
    assert result_data["ok"] is True
    assert "files" in result_data
    assert "main.tf" in result_data["files"]
    assert "prohibit_internet_ingress" not in result_data["files"]["main.tf"]
    assert "prohibit_public_ip_on_vnic_option" not in result_data["files"]["main.tf"]
