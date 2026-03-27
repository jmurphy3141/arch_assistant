"""
tests/test_jep_agent.py
------------------------
Unit tests for agent/jep_agent.py and agent/bom_stub.py.
All tests use InMemoryObjectStore — no OCI SDK or real LLM required.
"""
import json
import pytest

from agent.persistence_objectstore import InMemoryObjectStore
from agent.document_store import save_note, get_latest_doc
from agent.bom_stub import generate_stub_bom, bom_to_markdown
from agent.jep_agent import generate_jep, _infer_duration


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    return InMemoryObjectStore()


_SAMPLE_NOTES = (
    "Jane Street will test BM.GPU.B300 nodes.\n"
    "GPU Memory: 80GB/GPU. CPUs: 128 OCPUs. RAM: 2TB DDR4. Storage: 27.2TB NVMe.\n"
    "Software: CUDA 13.1, OKE kubectl v1.33.3, PyTorch with torchrun.\n"
    "File storage: 200TB OCI Managed Lustre. POC duration: 14 days.\n"
    "Goal: validate NVLink performance and provisioning speed.\n"
)

_MINIMAL_JEP = (
    "# AI Infrastructure on OCI — TestCo\n"
    "*Confidential — Oracle Restricted*\n\n"
    "## Overview\nTestCo tests OCI GPU infrastructure.\n\n"
    "## Bill of Materials\nSee table below.\n\n"
    "## Success Criteria\n- NVLink bandwidth > 900 GB/s\n\n"
    "## Timing\n**POC Duration**: 14 days\n"
)


def fake_text_runner(prompt: str, system_message: str = "") -> str:
    """
    Fake text runner that returns either a stub BOM JSON or a minimal JEP,
    based on prompt content.
    """
    if '"hardware"' in prompt or '"software"' in prompt or "Bill of Materials" in prompt[:200]:
        # BOM stub prompt
        return json.dumps({
            "source": "stub",
            "agent": "agent3-bom-stub",
            "note": "Pending Agent 2 integration",
            "duration_days": 14,
            "funding": "Oracle",
            "hardware": [
                {"item": "BM.GPU.B300", "shape": "BM.GPU.B300", "quantity": 8, "unit_cost": "TBD", "notes": ""}
            ],
            "software": [
                {"item": "CUDA", "version": "13.1", "notes": ""},
                {"item": "PyTorch", "version": "latest", "notes": ""},
            ],
            "storage": [
                {"item": "OCI Managed Lustre", "capacity": "200TB", "notes": ""}
            ],
        })
    return _MINIMAL_JEP


# ── bom_stub: generate_stub_bom ───────────────────────────────────────────────

class TestGenerateStubBom:
    def test_returns_dict(self):
        bom = generate_stub_bom(_SAMPLE_NOTES, fake_text_runner)
        assert isinstance(bom, dict)

    def test_required_keys_present(self):
        bom = generate_stub_bom(_SAMPLE_NOTES, fake_text_runner)
        assert "source" in bom
        assert "hardware" in bom
        assert "software" in bom
        assert "storage" in bom
        assert "duration_days" in bom

    def test_source_is_stub(self):
        bom = generate_stub_bom(_SAMPLE_NOTES, fake_text_runner)
        assert bom["source"] == "stub"

    def test_hardware_items_parsed(self):
        bom = generate_stub_bom(_SAMPLE_NOTES, fake_text_runner)
        assert len(bom["hardware"]) > 0
        assert bom["hardware"][0]["item"] == "BM.GPU.B300"

    def test_software_items_parsed(self):
        bom = generate_stub_bom(_SAMPLE_NOTES, fake_text_runner)
        items = [s["item"] for s in bom["software"]]
        assert "CUDA" in items

    def test_duration_extracted(self):
        bom = generate_stub_bom(_SAMPLE_NOTES, fake_text_runner)
        assert bom["duration_days"] == 14

    def test_fallback_on_parse_error(self):
        def bad_runner(prompt, system_message=""):
            return "not valid json at all"
        bom = generate_stub_bom(_SAMPLE_NOTES, bad_runner)
        # Should return minimal fallback
        assert bom["source"] == "stub"
        assert bom["hardware"] == []

    def test_fallback_on_runner_exception(self):
        def error_runner(prompt, system_message=""):
            raise RuntimeError("LLM unavailable")
        bom = generate_stub_bom(_SAMPLE_NOTES, error_runner)
        assert bom["source"] == "stub"
        assert isinstance(bom["hardware"], list)

    def test_strips_markdown_fences(self):
        def fenced_runner(prompt, system_message=""):
            return '```json\n{"source": "stub", "hardware": [], "software": [], "storage": [], "duration_days": 14, "funding": "Oracle", "agent": "x", "note": "y"}\n```'
        bom = generate_stub_bom("notes", fenced_runner)
        assert bom["source"] == "stub"


class TestBomToMarkdown:
    def test_renders_hardware_table(self):
        bom = {
            "source": "stub", "note": "test", "duration_days": 14, "funding": "Oracle",
            "hardware": [{"item": "GPU Node", "shape": "BM.GPU.B300", "quantity": 8, "unit_cost": "TBD", "notes": ""}],
            "software": [], "storage": [],
        }
        md = bom_to_markdown(bom)
        assert "GPU Node" in md
        assert "BM.GPU.B300" in md

    def test_renders_software_table(self):
        bom = {
            "source": "stub", "note": "test", "duration_days": 14, "funding": "Oracle",
            "hardware": [],
            "software": [{"item": "CUDA", "version": "13.1", "notes": ""}],
            "storage": [],
        }
        md = bom_to_markdown(bom)
        assert "CUDA" in md
        assert "13.1" in md

    def test_renders_storage_table(self):
        bom = {
            "source": "stub", "note": "test", "duration_days": 14, "funding": "Oracle",
            "hardware": [], "software": [],
            "storage": [{"item": "OCI Managed Lustre", "capacity": "200TB", "notes": ""}],
        }
        md = bom_to_markdown(bom)
        assert "Lustre" in md
        assert "200TB" in md

    def test_includes_duration_and_funding(self):
        bom = {
            "source": "stub", "note": "test", "duration_days": 30, "funding": "Oracle",
            "hardware": [], "software": [], "storage": [],
        }
        md = bom_to_markdown(bom)
        assert "30" in md
        assert "Oracle" in md

    def test_empty_bom_placeholder(self):
        bom = {
            "source": "stub", "note": "test", "duration_days": 14, "funding": "Oracle",
            "hardware": [], "software": [], "storage": [],
        }
        md = bom_to_markdown(bom)
        assert "TBD" in md or "Pending" in md or "provide meeting notes" in md.lower()


# ── jep_agent: _infer_duration ────────────────────────────────────────────────

class TestInferDuration:
    def test_14_day(self):
        assert "14" in _infer_duration("POC duration: 14 days")

    def test_14_day_hyphen(self):
        assert "14" in _infer_duration("This is a 14-day POC")

    def test_2_week(self):
        assert "2 week" in _infer_duration("2 week engagement")

    def test_default_2_weeks(self):
        assert "2 week" in _infer_duration("no duration mentioned here")

    def test_30_day(self):
        assert "30" in _infer_duration("30 day POC")


# ── jep_agent: generate_jep ──────────────────────────────────────────────────

class TestGenerateJep:
    def test_generate_jep_returns_result_dict(self, store):
        result = generate_jep("cust1", "TestCo", store, fake_text_runner)
        assert result["version"] == 1
        assert "key" in result
        assert "latest_key" in result
        assert "content" in result
        assert "bom" in result

    def test_generate_jep_persists_to_store(self, store):
        generate_jep("cust1", "TestCo", store, fake_text_runner)
        assert store.head("jep/cust1/LATEST.md")
        assert store.head("jep/cust1/v1.md")
        assert store.head("jep/cust1/MANIFEST.json")

    def test_generate_jep_increments_version(self, store):
        generate_jep("cust1", "TestCo", store, fake_text_runner)
        result2 = generate_jep("cust1", "TestCo", store, fake_text_runner)
        assert result2["version"] == 2

    def test_generate_jep_uses_notes(self, store):
        save_note(store, "cust1", "notes.txt", _SAMPLE_NOTES.encode())
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_jep("cust1", "TestCo", store, capturing_runner)
        # At least one prompt should contain notes text
        assert any("B300" in p for p in received_prompts)

    def test_generate_jep_references_diagram_key(self, store):
        result = generate_jep(
            "cust1", "TestCo", store, fake_text_runner,
            diagram_key="agent3/cust1/diag/LATEST.json",
        )
        assert result.get("diagram_key") == "agent3/cust1/diag/LATEST.json"

    def test_generate_jep_uses_diagram_from_bucket(self, store):
        # Plant a LATEST.json in the bucket
        store.put(
            "agent3/cust1/LATEST.json",
            b'{"artifacts": {"diagram.drawio": "agent3/cust1/v1/diagram.drawio"}}',
            "application/json",
        )
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_jep("cust1", "TestCo", store, capturing_runner)
        assert any("agent3/cust1/LATEST.json" in p for p in received_prompts)

    def test_generate_jep_tbd_diagram_when_none(self, store):
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_jep("cust1", "TestCo", store, capturing_runner)
        # Should mention TBD diagram in the prompt
        assert any("TBD" in p or "Agent 3" in p for p in received_prompts)

    def test_generate_jep_includes_previous_version(self, store):
        generate_jep("cust1", "TestCo", store, fake_text_runner)
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_jep("cust1", "TestCo", store, capturing_runner)
        assert any("Previous JEP version" in p for p in received_prompts)

    def test_generate_jep_bom_in_result(self, store):
        result = generate_jep("cust1", "TestCo", store, fake_text_runner)
        bom = result["bom"]
        assert isinstance(bom, dict)
        assert "source" in bom

    def test_generate_jep_no_notes_still_works(self, store):
        result = generate_jep("cust1", "EmptyCustomer", store, fake_text_runner)
        assert result["version"] == 1
        assert result["content"]

    def test_generate_jep_customer_name_in_prompt(self, store):
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_jep("cust1", "Jane Street Capital", store, capturing_runner)
        assert any("Jane Street Capital" in p for p in received_prompts)

    def test_generate_jep_passes_system_message(self, store):
        received_system_msgs = []

        def capturing_runner(prompt, system_message=""):
            received_system_msgs.append(system_message)
            return fake_text_runner(prompt, system_message)

        generate_jep("cust1", "TestCo", store, capturing_runner)
        # The JEP system message should mention JEP
        assert any("JEP" in sm or "Joint Execution" in sm for sm in received_system_msgs)
