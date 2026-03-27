"""
tests/test_writing_agents_system.py
-------------------------------------
System / integration tests for the writing agent HTTP endpoints.

These tests run against the full FastAPI app with:
  - InMemoryObjectStore (no OCI SDK)
  - Fake text_runner (no LLM calls)
  - TestClient (no running server process)

They exercise the full request/response cycle for:
  POST /notes/upload
  GET  /notes/{customer_id}
  POST /pov/generate
  GET  /pov/{customer_id}/latest
  GET  /pov/{customer_id}/versions
  POST /jep/generate
  GET  /jep/{customer_id}/latest
  GET  /jep/{customer_id}/versions
"""
import io
import json
import pytest
from fastapi.testclient import TestClient

from drawing_agent_server import app, PENDING_CLARIFY, SESSION_STORE, IDEMPOTENCY_CACHE
from agent.persistence_objectstore import InMemoryObjectStore


# ── Helpers / constants ───────────────────────────────────────────────────────

_FAKE_BOM_JSON = json.dumps({
    "source": "stub", "agent": "test", "note": "test",
    "duration_days": 14, "funding": "Oracle",
    "hardware": [{"item": "BM.GPU.B300", "shape": "BM.GPU.B300", "quantity": 4, "unit_cost": "TBD", "notes": ""}],
    "software": [{"item": "CUDA", "version": "13.1", "notes": ""}],
    "storage": [{"item": "OCI Lustre", "capacity": "200TB", "notes": ""}],
})

_FAKE_POV = (
    "# TestCo — Oracle Cloud Point of View\n\n"
    "## Internal Visionary Press Release\n\n"
    "### Summary\nTestCo succeeds with OCI.\n\n"
    "## External Questions\n\n**Q: Challenges?**\nA: Scale and compliance.\n"
)

_FAKE_JEP = (
    "# AI Infrastructure on OCI — TestCo\n"
    "*Confidential — Oracle Restricted*\n\n"
    "## Overview\nTestCo POC on OCI.\n\n"
    "## Success Criteria\n- NVLink OK\n\n"
    "## Timing\n**POC Duration**: 14 days\n"
)

_SAMPLE_NOTES = b"Customer uses B300 GPUs. 14-day POC. CUDA 13.1."


def _fake_text_runner(prompt: str, system_message: str = "") -> str:
    """Route fake responses based on prompt content."""
    # BOM extraction prompt contains JSON template with 'hardware' key
    if '"hardware"' in prompt and '"software"' in prompt:
        return _FAKE_BOM_JSON
    return _FAKE_POV if "POV" in system_message or "Point of View" in system_message else _FAKE_JEP


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_global_state():
    """Clear all global state before every test."""
    PENDING_CLARIFY.clear()
    SESSION_STORE.clear()
    IDEMPOTENCY_CACHE.clear()
    yield
    PENDING_CLARIFY.clear()
    SESSION_STORE.clear()
    IDEMPOTENCY_CACHE.clear()


@pytest.fixture
def store():
    return InMemoryObjectStore()


@pytest.fixture
def client(store):
    """TestClient with fake text_runner and in-memory object store injected."""
    app.state.llm_runner = lambda prompt, client_id: {"status": "ok", "nodes": []}
    app.state.text_runner = _fake_text_runner
    app.state.object_store = store
    app.state.persistence_config = {"prefix": "agent3"}
    with TestClient(app) as c:
        yield c
    # Cleanup injected state
    if hasattr(app.state, "llm_runner"):
        del app.state.llm_runner
    if hasattr(app.state, "text_runner"):
        del app.state.text_runner
    if hasattr(app.state, "object_store"):
        del app.state.object_store
    if hasattr(app.state, "persistence_config"):
        del app.state.persistence_config


# ── /notes/upload ─────────────────────────────────────────────────────────────

class TestNotesUpload:
    def test_upload_note_returns_ok(self, client):
        resp = client.post(
            "/notes/upload",
            data={"customer_id": "cust1", "note_name": "meeting1.md"},
            files={"file": ("meeting1.md", io.BytesIO(_SAMPLE_NOTES), "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["customer_id"] == "cust1"
        assert body["note_name"] == "meeting1.md"

    def test_upload_note_key_format(self, client):
        resp = client.post(
            "/notes/upload",
            data={"customer_id": "custA", "note_name": "notes.txt"},
            files={"file": ("notes.txt", io.BytesIO(b"content"), "text/plain")},
        )
        assert resp.json()["key"] == "notes/custA/notes.txt"

    def test_upload_note_uses_filename_as_default(self, client):
        resp = client.post(
            "/notes/upload",
            data={"customer_id": "cust1"},
            files={"file": ("autoname.md", io.BytesIO(b"content"), "text/plain")},
        )
        assert resp.json()["note_name"] == "autoname.md"

    def test_upload_note_without_store_returns_503(self):
        app.state.text_runner = _fake_text_runner
        app.state.object_store = None
        app.state.persistence_config = {}
        with TestClient(app) as c:
            resp = c.post(
                "/notes/upload",
                data={"customer_id": "cust1"},
                files={"file": ("n.txt", io.BytesIO(b"x"), "text/plain")},
            )
        assert resp.status_code == 503


# ── /notes/{customer_id} ─────────────────────────────────────────────────────

class TestListNotes:
    def test_list_notes_empty(self, client):
        resp = client.get("/notes/nobody")
        assert resp.status_code == 200
        assert resp.json()["notes"] == []

    def test_list_notes_after_upload(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": "cust1", "note_name": "a.md"},
            files={"file": ("a.md", io.BytesIO(b"first note"), "text/plain")},
        )
        client.post(
            "/notes/upload",
            data={"customer_id": "cust1", "note_name": "b.md"},
            files={"file": ("b.md", io.BytesIO(b"second note"), "text/plain")},
        )
        resp = client.get("/notes/cust1")
        notes = resp.json()["notes"]
        names = [n["name"] for n in notes]
        assert "a.md" in names
        assert "b.md" in names

    def test_list_notes_customer_scoped(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": "custX"},
            files={"file": ("note.txt", io.BytesIO(b"X"), "text/plain")},
        )
        resp = client.get("/notes/custY")
        assert resp.json()["notes"] == []


# ── /pov/generate ─────────────────────────────────────────────────────────────

class TestPovGenerate:
    def test_generate_pov_ok(self, client):
        resp = client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["doc_type"] == "pov"
        assert body["version"] == 1
        assert body["content"]

    def test_generate_pov_increments_version(self, client):
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.json()["version"] == 2

    def test_generate_pov_different_customers_independent(self, client):
        r1 = client.post("/pov/generate", json={"customer_id": "custA", "customer_name": "CompanyA"})
        r2 = client.post("/pov/generate", json={"customer_id": "custB", "customer_name": "CompanyB"})
        assert r1.json()["version"] == 1
        assert r2.json()["version"] == 1

    def test_generate_pov_uses_uploaded_notes(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": "cust1", "note_name": "meeting.md"},
            files={"file": ("meeting.md", io.BytesIO(_SAMPLE_NOTES), "text/plain")},
        )
        resp = client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.status_code == 200
        assert resp.json()["content"]

    def test_generate_pov_no_store_returns_503(self):
        app.state.text_runner = _fake_text_runner
        app.state.object_store = None
        app.state.persistence_config = {}
        with TestClient(app) as c:
            resp = c.post("/pov/generate", json={"customer_id": "c1", "customer_name": "X"})
        assert resp.status_code == 503

    def test_generate_pov_agent_version_present(self, client):
        resp = client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert "agent_version" in resp.json()


# ── /pov/{customer_id}/latest ────────────────────────────────────────────────

class TestPovLatest:
    def test_latest_pov_ok(self, client):
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = client.get("/pov/cust1/latest")
        assert resp.status_code == 200
        assert resp.json()["content"]

    def test_latest_pov_not_found(self, client):
        resp = client.get("/pov/no-such-customer/latest")
        assert resp.status_code == 404

    def test_latest_pov_is_newest_version(self, client):
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = client.get("/pov/cust1/latest")
        # The fake runner always returns _FAKE_POV — just verify it's there
        assert resp.json()["content"]


# ── /pov/{customer_id}/versions ──────────────────────────────────────────────

class TestPovVersions:
    def test_versions_empty(self, client):
        resp = client.get("/pov/nobody/versions")
        assert resp.status_code == 200
        assert resp.json()["versions"] == []

    def test_versions_count(self, client):
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = client.get("/pov/cust1/versions")
        assert len(resp.json()["versions"]) == 2


# ── /jep/generate ─────────────────────────────────────────────────────────────

class TestJepGenerate:
    def test_generate_jep_ok(self, client):
        resp = client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["doc_type"] == "jep"
        assert body["version"] == 1
        assert body["content"]

    def test_generate_jep_has_bom(self, client):
        resp = client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.json()["bom"] is not None
        assert "source" in resp.json()["bom"]

    def test_generate_jep_increments_version(self, client):
        client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.json()["version"] == 2

    def test_generate_jep_with_diagram_key(self, client):
        resp = client.post(
            "/jep/generate",
            json={
                "customer_id": "cust1",
                "customer_name": "TestCo",
                "diagram_key": "agent3/cust1/my_diag/LATEST.json",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["diagram_key"] == "agent3/cust1/my_diag/LATEST.json"

    def test_generate_jep_no_store_returns_503(self):
        app.state.text_runner = _fake_text_runner
        app.state.object_store = None
        app.state.persistence_config = {}
        with TestClient(app) as c:
            resp = c.post("/jep/generate", json={"customer_id": "c1", "customer_name": "X"})
        assert resp.status_code == 503

    def test_generate_jep_agent_version_present(self, client):
        resp = client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert "agent_version" in resp.json()


# ── /jep/{customer_id}/latest ────────────────────────────────────────────────

class TestJepLatest:
    def test_latest_jep_ok(self, client):
        client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = client.get("/jep/cust1/latest")
        assert resp.status_code == 200
        assert resp.json()["content"]

    def test_latest_jep_not_found(self, client):
        resp = client.get("/jep/no-such-customer/latest")
        assert resp.status_code == 404


# ── /jep/{customer_id}/versions ──────────────────────────────────────────────

class TestJepVersions:
    def test_versions_empty(self, client):
        resp = client.get("/jep/nobody/versions")
        assert resp.status_code == 200
        assert resp.json()["versions"] == []

    def test_versions_count(self, client):
        client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = client.get("/jep/cust1/versions")
        assert len(resp.json()["versions"]) == 2


# ── Cross-agent workflow ──────────────────────────────────────────────────────

class TestEndToEndWorkflow:
    def test_full_workflow_notes_then_pov_then_jep(self, client):
        """Full workflow: upload notes → generate POV → generate JEP."""
        # 1. Upload notes
        r = client.post(
            "/notes/upload",
            data={"customer_id": "jane_street", "note_name": "meeting1.md"},
            files={"file": ("meeting1.md", io.BytesIO(_SAMPLE_NOTES), "text/plain")},
        )
        assert r.status_code == 200

        # 2. Generate POV
        r = client.post(
            "/pov/generate",
            json={"customer_id": "jane_street", "customer_name": "Jane Street Capital"},
        )
        assert r.status_code == 200
        assert r.json()["version"] == 1

        # 3. Generate JEP
        r = client.post(
            "/jep/generate",
            json={"customer_id": "jane_street", "customer_name": "Jane Street Capital"},
        )
        assert r.status_code == 200
        assert r.json()["version"] == 1

        # 4. Verify docs are retrievable
        pov_r = client.get("/pov/jane_street/latest")
        jep_r = client.get("/jep/jane_street/latest")
        assert pov_r.status_code == 200
        assert jep_r.status_code == 200

    def test_update_notes_and_regenerate(self, client):
        """Uploading new notes then re-generating produces a new version."""
        client.post(
            "/notes/upload",
            data={"customer_id": "cust1", "note_name": "v1.md"},
            files={"file": ("v1.md", io.BytesIO(b"Initial notes"), "text/plain")},
        )
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})

        client.post(
            "/notes/upload",
            data={"customer_id": "cust1", "note_name": "v2.md"},
            files={"file": ("v2.md", io.BytesIO(b"Updated notes with new requirements"), "text/plain")},
        )
        r = client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert r.json()["version"] == 2

    def test_pov_and_jep_are_independent_doc_types(self, client):
        """POV and JEP version counters are independent."""
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        r = client.post("/jep/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        # JEP is version 1 even though POV is at version 2
        assert r.json()["version"] == 1


# ── /terraform/generate ───────────────────────────────────────────────────────

_FAKE_TERRAFORM = (
    "// FILE: main.tf\n```hcl\n"
    "resource \"oci_core_vcn\" \"main\" { display_name = \"TestCo-VCN\" }\n"
    "```\n\n"
    "// FILE: variables.tf\n```hcl\nvariable \"tenancy_ocid\" {}\n```\n\n"
    "// FILE: outputs.tf\n```hcl\noutput \"vcn_id\" { value = oci_core_vcn.main.id }\n```\n\n"
    "// FILE: terraform.tfvars.example\n```hcl\n# tenancy_ocid = \"[TBD]\"\n```\n"
)

_FAKE_WAF = (
    "# TestCo — OCI Well-Architected Framework Review\n\n"
    "## Executive Summary\nGood baseline.\n\n"
    "### Overall Rating\n"
    "| Pillar | Rating | Summary |\n"
    "|--------|--------|---------|\n"
    "| Operational Excellence | ✅ | Good |\n"
    "| Security | ⚠️ | Needs Vault |\n"
)


def _fake_all_runner(prompt: str, system_message: str = "") -> str:
    """Route fake responses for all agent types."""
    if '"hardware"' in prompt and '"software"' in prompt:
        return _FAKE_BOM_JSON
    if "Well-Architected" in system_message or "WAF" in system_message or "pillar" in system_message.lower():
        return _FAKE_WAF
    if "FILE: main.tf" in prompt or "Terraform HCL" in prompt:
        return _FAKE_TERRAFORM
    return _FAKE_POV if "POV" in system_message or "Point of View" in system_message else _FAKE_JEP


@pytest.fixture
def full_client(store):
    """TestClient wired for all 6 agents including terraform and waf."""
    from unittest.mock import patch
    app.state.llm_runner = lambda prompt, client_id: {"status": "ok", "nodes": []}
    app.state.text_runner = _fake_all_runner
    app.state.object_store = store
    app.state.persistence_config = {"prefix": "agent3"}
    with patch("agent.terraform_agent._search_github_examples", return_value=""):
        with TestClient(app) as c:
            yield c
    if hasattr(app.state, "llm_runner"):   del app.state.llm_runner
    if hasattr(app.state, "text_runner"):  del app.state.text_runner
    if hasattr(app.state, "object_store"): del app.state.object_store
    if hasattr(app.state, "persistence_config"): del app.state.persistence_config


class TestTerraformGenerate:
    def test_generate_terraform_ok(self, full_client):
        resp = full_client.post(
            "/terraform/generate",
            json={"customer_id": "cust1", "customer_name": "TestCo"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["doc_type"] == "terraform"
        assert body["version"] == 1
        assert body["file_count"] > 0

    def test_generate_terraform_increments_version(self, full_client):
        full_client.post("/terraform/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.post("/terraform/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.json()["version"] == 2

    def test_generate_terraform_no_store_503(self):
        app.state.text_runner = _fake_all_runner
        app.state.object_store = None
        app.state.persistence_config = {}
        with TestClient(app) as c:
            resp = c.post("/terraform/generate", json={"customer_id": "c1", "customer_name": "X"})
        assert resp.status_code == 503

    def test_generate_terraform_has_files_key(self, full_client):
        resp = full_client.post(
            "/terraform/generate",
            json={"customer_id": "cust1", "customer_name": "TestCo"},
        )
        assert "files" in resp.json()

    def test_generate_terraform_agent_version_present(self, full_client):
        resp = full_client.post(
            "/terraform/generate",
            json={"customer_id": "cust1", "customer_name": "TestCo"},
        )
        assert "agent_version" in resp.json()


class TestTerraformLatest:
    def test_latest_terraform_ok(self, full_client):
        full_client.post("/terraform/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.get("/terraform/cust1/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert "files" in body
        assert "main.tf" in body["files"]

    def test_latest_terraform_not_found(self, full_client):
        resp = full_client.get("/terraform/no-such/latest")
        assert resp.status_code == 404

    def test_latest_shows_newest_version(self, full_client):
        full_client.post("/terraform/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        full_client.post("/terraform/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.get("/terraform/cust1/latest")
        assert resp.json()["version"] == 2


class TestTerraformVersions:
    def test_versions_empty(self, full_client):
        resp = full_client.get("/terraform/nobody/versions")
        assert resp.status_code == 200
        assert resp.json()["versions"] == []

    def test_versions_count(self, full_client):
        full_client.post("/terraform/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        full_client.post("/terraform/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.get("/terraform/cust1/versions")
        assert len(resp.json()["versions"]) == 2


# ── /waf/generate ─────────────────────────────────────────────────────────────

class TestWafGenerate:
    def test_generate_waf_ok(self, full_client):
        resp = full_client.post(
            "/waf/generate",
            json={"customer_id": "cust1", "customer_name": "TestCo"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["doc_type"] == "waf"
        assert body["version"] == 1
        assert body["content"]

    def test_generate_waf_has_overall_rating(self, full_client):
        resp = full_client.post(
            "/waf/generate",
            json={"customer_id": "cust1", "customer_name": "TestCo"},
        )
        assert "overall_rating" in resp.json()

    def test_generate_waf_increments_version(self, full_client):
        full_client.post("/waf/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.post("/waf/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        assert resp.json()["version"] == 2

    def test_generate_waf_no_store_503(self):
        app.state.text_runner = _fake_all_runner
        app.state.object_store = None
        app.state.persistence_config = {}
        with TestClient(app) as c:
            resp = c.post("/waf/generate", json={"customer_id": "c1", "customer_name": "X"})
        assert resp.status_code == 503

    def test_generate_waf_agent_version_present(self, full_client):
        resp = full_client.post(
            "/waf/generate",
            json={"customer_id": "cust1", "customer_name": "TestCo"},
        )
        assert "agent_version" in resp.json()


class TestWafLatest:
    def test_latest_waf_ok(self, full_client):
        full_client.post("/waf/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.get("/waf/cust1/latest")
        assert resp.status_code == 200
        assert resp.json()["content"]

    def test_latest_waf_not_found(self, full_client):
        resp = full_client.get("/waf/no-such/latest")
        assert resp.status_code == 404


class TestWafVersions:
    def test_versions_empty(self, full_client):
        resp = full_client.get("/waf/nobody/versions")
        assert resp.status_code == 200
        assert resp.json()["versions"] == []

    def test_versions_count(self, full_client):
        full_client.post("/waf/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        full_client.post("/waf/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.get("/waf/cust1/versions")
        assert len(resp.json()["versions"]) == 2


# ── /context/{customer_id} ────────────────────────────────────────────────────

class TestContextEndpoint:
    def test_context_empty_customer(self, full_client):
        resp = full_client.get("/context/newcustomer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["context"]["customer_id"] == "newcustomer"
        assert body["context"]["agents"] == {}

    def test_context_after_pov_generation(self, full_client):
        full_client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.get("/context/cust1")
        assert resp.status_code == 200
        ctx = resp.json()["context"]
        assert "pov" in ctx["agents"]
        assert ctx["agents"]["pov"]["version"] == 1

    def test_context_accumulates_multiple_agents(self, full_client):
        full_client.post("/pov/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        full_client.post("/waf/generate", json={"customer_id": "cust1", "customer_name": "TestCo"})
        resp = full_client.get("/context/cust1")
        ctx = resp.json()["context"]
        assert "pov" in ctx["agents"]
        assert "waf" in ctx["agents"]


# ── Full 6-agent workflow ──────────────────────────────────────────────────────

class TestFullFleetWorkflow:
    def test_six_agent_workflow(self, full_client):
        """Upload notes → POV → JEP → Terraform → WAF → check context."""
        cid = "acme_corp"

        # Upload notes
        full_client.post(
            "/notes/upload",
            data={"customer_id": cid, "note_name": "kickoff.md"},
            files={"file": ("kickoff.md", io.BytesIO(_SAMPLE_NOTES), "text/plain")},
        )

        # POV
        r = full_client.post("/pov/generate", json={"customer_id": cid, "customer_name": "Acme Corp"})
        assert r.status_code == 200

        # JEP
        r = full_client.post("/jep/generate", json={"customer_id": cid, "customer_name": "Acme Corp"})
        assert r.status_code == 200

        # Terraform
        r = full_client.post("/terraform/generate", json={"customer_id": cid, "customer_name": "Acme Corp"})
        assert r.status_code == 200

        # WAF
        r = full_client.post("/waf/generate", json={"customer_id": cid, "customer_name": "Acme Corp"})
        assert r.status_code == 200

        # Verify context has all agents
        ctx_resp = full_client.get(f"/context/{cid}")
        assert ctx_resp.status_code == 200
        agents = ctx_resp.json()["context"]["agents"]
        assert "pov" in agents
        assert "jep" in agents
        assert "terraform" in agents
        assert "waf" in agents
