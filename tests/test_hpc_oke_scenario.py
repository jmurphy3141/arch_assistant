"""
tests/test_hpc_oke_scenario.py
--------------------------------
End-to-end scenario test: HPC cluster on OKE with RDMA networking and FSS mount.

Reference:
  Blog:      https://blogs.oracle.com/cloud-infrastructure/deploying-hpc-cluster-rdma-network-oke-fss-mount
  Diagram:   https://github.com/dezma/oci-hpc-oke/blob/main/Architecture/oci-hpc-arc.png
  Terraform: https://github.com/dezma/oci-hpc-oke/tree/main

Test strategy:
  - All agents run against InMemoryObjectStore (no OCI SDK)
  - Fake LLM runner returns responses faithful to the reference architecture
  - Assertions check that each agent output contains the key content expected
    for this specific architecture (shapes, services, RDMA config, WAF pillars)
  - No external HTTP calls; all routes through FastAPI TestClient

Tests are grouped by agent / pipeline stage:
  S0: Fixture validity — layout spec and expected-strings sanity checks
  S1: Diagram agent — spec_to_draw_dict produces the right nodes/boxes
  S2: Notes upload — architecture notes accepted and listed
  S3: POV agent — output mentions HPC/RDMA/OKE
  S4: JEP agent — output contains shapes and steps
  S5: Terraform agent — output has correct shapes, CNI, and FSS resources
  S6: WAF agent — all six pillars present, overall rating extracted
  S7: Full pipeline — all agents run sequentially, context accumulates
"""
import io
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from agent.layout_engine import spec_to_draw_dict
from agent.persistence_objectstore import InMemoryObjectStore
from drawing_agent_server import app, PENDING_CLARIFY, SESSION_STORE, IDEMPOTENCY_CACHE

from tests.fixtures.hpc_oke_scenario import (
    CUSTOMER_ID,
    CUSTOMER_NAME,
    NOTES_TEXT,
    LAYOUT_SPEC,
    FAKE_LAYOUT_SPEC_JSON,
    FAKE_POV,
    FAKE_JEP,
    FAKE_TERRAFORM,
    FAKE_WAF,
    EXPECTED_DIAGRAM_NODE_IDS,
    EXPECTED_TERRAFORM_STRINGS,
    EXPECTED_POV_STRINGS,
    EXPECTED_JEP_STRINGS,
    EXPECTED_WAF_PILLARS,
)


# ---------------------------------------------------------------------------
# Fake LLM runner — routes by system message / prompt content
# ---------------------------------------------------------------------------
def _hpc_oke_runner(prompt: str, system_message: str = "") -> str:
    """Return architecture-faithful fake content for each agent."""
    sm = system_message.lower()
    if "terraform" in sm:
        return FAKE_TERRAFORM
    if "well-architected" in sm or "waf" in sm:
        return FAKE_WAF
    if "pov" in sm or "point of view" in sm:
        return FAKE_POV
    if "jep" in sm or "execution plan" in sm:
        return FAKE_JEP
    # BOM extraction (JSON response)
    if '"hardware"' in prompt and '"software"' in prompt:
        return json.dumps({
            "source": "hpc_oke_reference",
            "agent": "test",
            "note": "HPC OKE reference architecture",
            "duration_days": 14,
            "funding": "Oracle",
            "hardware": [
                {"item": "BM.Optimized3.36", "shape": "BM.Optimized3.36", "quantity": 3, "unit_cost": "TBD", "notes": "RDMA node pool"},
                {"item": "VM.Standard.E4.Flex", "shape": "VM.Standard.E4.Flex", "quantity": 2, "unit_cost": "TBD", "notes": "bastion + operator"},
            ],
            "software": [{"item": "OKE", "version": "v1.29.1", "notes": "Enhanced cluster, Flannel"}],
            "storage": [{"item": "FSS", "capacity": "10TB", "notes": "NFS PVC for MPI jobs"}],
        })
    return FAKE_POV  # safe fallback


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_global_state():
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
    app.state.llm_runner = lambda prompt, client_id: {"status": "ok", "nodes": []}
    app.state.text_runner = _hpc_oke_runner
    app.state.object_store = store
    app.state.persistence_config = {"prefix": "agent3"}
    with TestClient(app) as c:
        yield c
    for attr in ("llm_runner", "text_runner", "object_store", "persistence_config"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


# ---------------------------------------------------------------------------
# S0: Fixture validity
# ---------------------------------------------------------------------------
class TestFixtureValidity:
    def test_layout_spec_has_all_node_ids(self):
        all_node_ids = {
            n["id"]
            for layer in LAYOUT_SPEC["layers"].values()
            for n in layer
        }
        assert EXPECTED_DIAGRAM_NODE_IDS.issubset(all_node_ids)

    def test_layout_spec_groups_reference_valid_nodes(self):
        all_node_ids = {
            n["id"]
            for layer in LAYOUT_SPEC["layers"].values()
            for n in layer
        }
        for group in LAYOUT_SPEC["groups"]:
            for nid in group["nodes"]:
                assert nid in all_node_ids, f"Group '{group['id']}' references unknown node '{nid}'"

    def test_fake_terraform_has_expected_strings(self):
        for s in EXPECTED_TERRAFORM_STRINGS:
            assert s.lower() in FAKE_TERRAFORM.lower(), f"FAKE_TERRAFORM missing '{s}'"

    def test_fake_pov_has_expected_strings(self):
        for s in EXPECTED_POV_STRINGS:
            assert s.upper() in FAKE_POV.upper(), f"FAKE_POV missing '{s}'"

    def test_fake_waf_has_all_pillars(self):
        for pillar in EXPECTED_WAF_PILLARS:
            assert pillar in FAKE_WAF, f"FAKE_WAF missing pillar '{pillar}'"

    def test_fake_waf_has_overall_rating(self):
        assert "Overall Rating" in FAKE_WAF

    def test_notes_text_mentions_rdma(self):
        assert b"RDMA" in NOTES_TEXT

    def test_notes_text_mentions_hpc_shape(self):
        assert b"BM.Optimized3.36" in NOTES_TEXT


# ---------------------------------------------------------------------------
# S1: Diagram agent — spec_to_draw_dict
# ---------------------------------------------------------------------------
class TestDiagramFromSpec:
    def test_spec_produces_draw_dict(self):
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        assert "nodes" in draw_dict
        assert "boxes" in draw_dict

    def test_expected_nodes_are_present(self):
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        node_ids = {n["id"] for n in draw_dict["nodes"]}
        for nid in EXPECTED_DIAGRAM_NODE_IDS:
            assert nid in node_ids, f"Node '{nid}' missing from draw_dict"

    def test_hpc_nodes_all_present(self):
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        node_ids = {n["id"] for n in draw_dict["nodes"]}
        assert "hpc_1" in node_ids
        assert "hpc_2" in node_ids
        assert "hpc_3" in node_ids

    def test_bastion_in_public_subnet_box(self):
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        box_labels = {b["label"] for b in draw_dict["boxes"]}
        assert any("Public" in lbl or "bastion" in lbl.lower() for lbl in box_labels)

    def test_vcn_box_present(self):
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        box_ids = {b["id"] for b in draw_dict["boxes"]}
        assert "vcn_box" in box_ids

    def test_all_nodes_within_page_bounds(self):
        from agent.layout_engine import PAGE_W, PAGE_H
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        for n in draw_dict["nodes"]:
            assert 0 <= n["x"] <= PAGE_W, f"Node {n['id']} x={n['x']} out of bounds"
            assert 0 <= n["y"] <= PAGE_H, f"Node {n['id']} y={n['y']} out of bounds"

    def test_edges_reference_existing_nodes(self):
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        node_ids = {n["id"] for n in draw_dict["nodes"]}
        for edge in draw_dict.get("edges", []):
            assert edge["source"] in node_ids, f"Edge source '{edge['source']}' not in nodes"
            assert edge["target"] in node_ids, f"Edge target '{edge['target']}' not in nodes"

    def test_drawio_xml_generated_from_spec(self):
        from agent.drawio_generator import generate_drawio
        import tempfile, os
        draw_dict = spec_to_draw_dict(LAYOUT_SPEC, {})
        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            path = f.name
        try:
            generate_drawio(draw_dict, path)
            xml = open(path).read()
            assert "mxCell" in xml
            assert "mxGraph" in xml
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# S2: Notes upload
# ---------------------------------------------------------------------------
class TestNotesUpload:
    def test_upload_hpc_notes_ok(self, client):
        r = client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["customer_id"] == CUSTOMER_ID

    def test_uploaded_note_appears_in_list(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )
        r = client.get(f"/notes/{CUSTOMER_ID}")
        assert r.status_code == 200
        names = [n["name"] for n in r.json()["notes"]]
        assert "hpc_oke_architecture.md" in names


# ---------------------------------------------------------------------------
# S3: POV agent
# ---------------------------------------------------------------------------
class TestPovAgent:
    def _upload_notes(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )

    def test_pov_generate_ok(self, client):
        self._upload_notes(client)
        r = client.post("/pov/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_pov_content_mentions_hpc(self, client):
        self._upload_notes(client)
        r = client.post("/pov/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        content = r.json()["content"]
        for s in EXPECTED_POV_STRINGS:
            assert s.upper() in content.upper(), f"POV missing '{s}'"

    def test_pov_version_increments_on_new_notes(self, client):
        self._upload_notes(client)
        r1 = client.post("/pov/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r1.json()["version"] == 1
        # Upload another note
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "follow_up.md"},
            files={"file": ("follow_up.md", io.BytesIO(b"RDMA benchmark results"), "text/plain")},
        )
        r2 = client.post("/pov/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r2.json()["version"] == 2


# ---------------------------------------------------------------------------
# S4: JEP agent
# ---------------------------------------------------------------------------
class TestJepAgent:
    def _upload_notes(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )

    def test_jep_generate_ok(self, client):
        self._upload_notes(client)
        r = client.post("/jep/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_jep_content_mentions_shape_and_oke(self, client):
        self._upload_notes(client)
        r = client.post("/jep/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        content = r.json()["content"]
        for s in EXPECTED_JEP_STRINGS:
            assert s.upper() in content.upper(), f"JEP missing '{s}'"


# ---------------------------------------------------------------------------
# S5: Terraform agent
# ---------------------------------------------------------------------------
class TestTerraformAgent:
    def _upload_notes(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )

    def test_terraform_generate_ok(self, client):
        self._upload_notes(client)
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            r = client.post("/terraform/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_terraform_has_four_files(self, client):
        self._upload_notes(client)
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            r = client.post("/terraform/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.json()["file_count"] == 4
        files = r.json()["files"]
        for fname in ("main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example"):
            assert fname in files, f"Missing {fname}"

    def _generate_and_get_files(self, client):
        """Generate Terraform and return the file contents from /latest."""
        self._upload_notes(client)
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            client.post("/terraform/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        r = client.get(f"/terraform/{CUSTOMER_ID}/latest")
        return r.json()["files"]

    def test_terraform_main_tf_has_hpc_shape(self, client):
        files = self._generate_and_get_files(client)
        main_tf = files.get("main.tf", "")
        assert "BM.Optimized3.36" in main_tf, "main.tf should contain HPC node shape"

    def test_terraform_main_tf_has_flannel_cni(self, client):
        files = self._generate_and_get_files(client)
        main_tf = files.get("main.tf", "")
        assert "flannel" in main_tf.lower(), "main.tf should specify flannel CNI (required for RDMA)"

    def test_terraform_main_tf_has_enhanced_cluster(self, client):
        files = self._generate_and_get_files(client)
        main_tf = files.get("main.tf", "")
        assert "enhanced" in main_tf.lower(), "main.tf should use enhanced cluster type (required for RDMA)"

    def test_terraform_main_tf_has_fss_resource(self, client):
        files = self._generate_and_get_files(client)
        main_tf = files.get("main.tf", "")
        assert "fss" in main_tf.lower() or "file_storage" in main_tf.lower(), \
            "main.tf should contain FSS resource"

    def test_terraform_main_tf_non_empty(self, client):
        files = self._generate_and_get_files(client)
        main_tf = files.get("main.tf", "")
        assert len(main_tf) > 50, f"main.tf is suspiciously short: {len(main_tf)} chars"

    def test_terraform_latest_retrievable(self, client):
        self._upload_notes(client)
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            client.post("/terraform/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        r = client.get(f"/terraform/{CUSTOMER_ID}/latest")
        assert r.status_code == 200
        assert "main.tf" in r.json()["files"]


# ---------------------------------------------------------------------------
# S6: WAF agent
# ---------------------------------------------------------------------------
class TestWafAgent:
    def _upload_notes(self, client):
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )

    def test_waf_generate_ok(self, client):
        self._upload_notes(client)
        r = client.post("/waf/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_waf_has_all_six_pillars(self, client):
        self._upload_notes(client)
        r = client.post("/waf/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        content = r.json()["content"]
        for pillar in EXPECTED_WAF_PILLARS:
            assert pillar in content, f"WAF review missing pillar '{pillar}'"

    def test_waf_overall_rating_is_emoji(self, client):
        self._upload_notes(client)
        r = client.post("/waf/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        rating = r.json().get("overall_rating", "")
        assert rating in ("✅", "⚠️", "❌"), f"Unexpected overall_rating: '{rating}'"

    def test_waf_content_mentions_rdma(self, client):
        self._upload_notes(client)
        r = client.post("/waf/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        content = r.json()["content"]
        assert "RDMA" in content, "WAF review should reference RDMA performance characteristics"

    def test_waf_latest_retrievable(self, client):
        self._upload_notes(client)
        r = client.post("/waf/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200
        r2 = client.get(f"/waf/{CUSTOMER_ID}/latest")
        assert r2.status_code == 200
        assert r2.json()["content"]


# ---------------------------------------------------------------------------
# S7: Full pipeline — notes → POV → JEP → Terraform → WAF → context
# ---------------------------------------------------------------------------
class TestFullHpcPipeline:
    def test_all_agents_run_sequentially(self, client):
        """Full fleet run for HPC OKE architecture — mirrors live test_server_live.py scenario."""
        # 1. Upload architecture notes
        r = client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )
        assert r.status_code == 200, "Notes upload failed"

        # 2. POV
        r = client.post("/pov/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200 and r.json()["version"] == 1, "POV generate failed"

        # 3. JEP
        r = client.post("/jep/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200 and r.json()["version"] == 1, "JEP generate failed"

        # 4. Terraform
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            r = client.post("/terraform/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200 and r.json()["file_count"] == 4, "Terraform generate failed"

        # 5. WAF
        r = client.post("/waf/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        assert r.status_code == 200, "WAF generate failed"

        # 6. Context should record all four agent runs
        r = client.get(f"/context/{CUSTOMER_ID}")
        assert r.status_code == 200
        agents_recorded = r.json().get("context", {}).get("agents", {})
        for agent_name in ("pov", "jep", "terraform", "waf"):
            assert agent_name in agents_recorded, f"Context missing '{agent_name}' entry"

    def test_pov_note_not_re_ingested_by_jep(self, client):
        """Notes incorporated by POV should not be treated as new by JEP."""
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )
        client.post("/pov/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        client.post("/jep/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})

        r = client.get(f"/context/{CUSTOMER_ID}")
        ctx = r.json().get("context", {})
        pov_notes = set(ctx.get("agents", {}).get("pov", {}).get("notes_incorporated", []))
        jep_notes = set(ctx.get("agents", {}).get("jep", {}).get("notes_incorporated", []))

        # Both agents saw the same note, but each tracks it independently
        assert pov_notes, "POV should have incorporated the note"
        assert jep_notes, "JEP should have incorporated the note"

    def test_context_accumulates_across_all_agents(self, client):
        """After full pipeline, context.json contains entries for all agents."""
        client.post(
            "/notes/upload",
            data={"customer_id": CUSTOMER_ID, "note_name": "hpc_oke_architecture.md"},
            files={"file": ("hpc_oke_architecture.md", io.BytesIO(NOTES_TEXT), "text/plain")},
        )
        client.post("/pov/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        client.post("/jep/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        with patch("agent.terraform_agent._search_github_examples", return_value=""):
            client.post("/terraform/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})
        client.post("/waf/generate", json={"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME})

        r = client.get(f"/context/{CUSTOMER_ID}")
        assert r.status_code == 200
        agents = r.json().get("context", {}).get("agents", {})
        for name in ("pov", "jep", "terraform", "waf"):
            assert name in agents
            assert agents[name].get("last_run"), f"'{name}' missing last_run timestamp"
