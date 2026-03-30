"""
tests/test_bom_pipeline.py
---------------------------
BOM-to-pipeline integration test.

Starts from a resource list (equivalent to a parsed BOM), calls POST /generate,
then runs every writing agent (POV, JEP, Terraform, WAF) with the same
customer_id and verifies that:

  1. The diagram is produced and its key is written to the fleet context.
  2. Each writing agent runs successfully and references the HPC OKE architecture.
  3. The context file records all four agent runs.

All agents run against InMemoryObjectStore — no OCI SDK, no real LLM.

The resource list below is derived from the HPC OKE reference architecture
(same components as tests/fixtures/hpc_oke_scenario.py).
"""
import json
import pytest
from fastapi.testclient import TestClient

from agent.persistence_objectstore import InMemoryObjectStore
from agent.context_store import read_context
from drawing_agent_server import app, PENDING_CLARIFY, SESSION_STORE, IDEMPOTENCY_CACHE

from tests.fixtures.hpc_oke_scenario import (
    CUSTOMER_ID,
    CUSTOMER_NAME,
    FAKE_LAYOUT_SPEC_JSON,
    FAKE_POV,
    FAKE_JEP,
    FAKE_TERRAFORM,
    FAKE_WAF,
    EXPECTED_POV_STRINGS,
    EXPECTED_JEP_STRINGS,
    EXPECTED_TERRAFORM_STRINGS,
    EXPECTED_WAF_PILLARS,
)

# ---------------------------------------------------------------------------
# Resource list — the "BOM" expressed as a list of OCI resource dicts.
# This is what you'd get after parsing BOM.xlsx via bom_to_llm_input().
# ---------------------------------------------------------------------------
HPC_OKE_RESOURCES = [
    {"id": "bastion_1",  "oci_type": "bastion",        "label": "Bastion Host",         "layer": "ingress"},
    {"id": "igw_1",      "oci_type": "internet gateway","label": "Internet Gateway",     "layer": "ingress"},
    {"id": "operator_1", "oci_type": "compute",        "label": "Operator VM",           "layer": "compute"},
    {"id": "oke_1",      "oci_type": "oke",             "label": "OKE Cluster (Enhanced)","layer": "compute"},
    {"id": "hpc_1",      "oci_type": "compute",        "label": "HPC Node 1 BM.Optimized3.36","layer": "compute"},
    {"id": "hpc_2",      "oci_type": "compute",        "label": "HPC Node 2 BM.Optimized3.36","layer": "compute"},
    {"id": "hpc_3",      "oci_type": "compute",        "label": "HPC Node 3 BM.Optimized3.36","layer": "compute"},
    {"id": "fss_1",      "oci_type": "file storage",   "label": "OCI File Storage (FSS)", "layer": "data"},
    {"id": "block_1",    "oci_type": "block storage",  "label": "Block Volume",          "layer": "data"},
    {"id": "objstr_1",   "oci_type": "object storage", "label": "Object Storage",        "layer": "data"},
]

DIAGRAM_NAME = "hpc_oke_bom_test"


# ---------------------------------------------------------------------------
# Fake LLM runner — routes by system message, identical to hpc_oke_scenario
# ---------------------------------------------------------------------------
def _llm_runner(prompt: str, client_id: str) -> dict:
    """JSON runner for /generate: return the HPC OKE layout spec."""
    return json.loads(FAKE_LAYOUT_SPEC_JSON)


def _text_runner(prompt: str, system_message: str = "") -> str:
    """Text runner for writing agents."""
    sm = system_message.lower()
    if "terraform" in sm:
        return FAKE_TERRAFORM
    if "well-architected" in sm or "waf" in sm:
        return FAKE_WAF
    if "pov" in sm or "point of view" in sm:
        return FAKE_POV
    if "jep" in sm or "execution plan" in sm:
        return FAKE_JEP
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
    app.state.llm_runner   = _llm_runner
    app.state.text_runner  = _text_runner
    app.state.object_store = store
    app.state.persistence_config = {"prefix": "agent3"}
    with TestClient(app) as c:
        yield c
    for attr in ("llm_runner", "text_runner", "object_store", "persistence_config"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


# ---------------------------------------------------------------------------
# P1: /generate with customer_id produces diagram + writes context
# ---------------------------------------------------------------------------
class TestGenerateWritesContext:
    def test_generate_returns_ok(self, client):
        r = client.post("/generate", json={
            "resources":     HPC_OKE_RESOURCES,
            "diagram_name":  DIAGRAM_NAME,
            "client_id":     CUSTOMER_ID,
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"

    def test_generate_produces_drawio_xml(self, client):
        r = client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
        })
        assert r.status_code == 200
        xml = r.json()["drawio_xml"]
        assert xml.strip().startswith("<")
        assert "mxCell" in xml

    def test_generate_writes_diagram_key_to_context(self, client, store):
        client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
        })
        ctx = read_context(store, CUSTOMER_ID)
        assert "diagram" in ctx["agents"], "diagram key not written to context"
        assert ctx["agents"]["diagram"]["diagram_key"] != ""

    def test_generate_context_diagram_key_matches_latest_path(self, client, store):
        client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
        })
        ctx = read_context(store, CUSTOMER_ID)
        diagram_key = ctx["agents"]["diagram"]["diagram_key"]
        assert diagram_key.endswith("LATEST.json")
        assert DIAGRAM_NAME in diagram_key

    def test_generate_without_customer_id_does_not_write_context(self, client, store):
        client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    "anon_client",
            # no customer_id
        })
        ctx = read_context(store, CUSTOMER_ID)
        assert "diagram" not in ctx["agents"]

    def test_generate_node_count_recorded_in_context(self, client, store):
        client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
        })
        ctx = read_context(store, CUSTOMER_ID)
        assert ctx["agents"]["diagram"]["node_count"] > 0


# ---------------------------------------------------------------------------
# P2: Writing agents run successfully after /generate
# ---------------------------------------------------------------------------
class TestWritingAgentsAfterGenerate:
    @pytest.fixture(autouse=True)
    def _generate_diagram(self, client):
        """Run /generate once; all tests in this class share the generated context."""
        r = client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
        })
        assert r.status_code == 200

    def test_pov_runs_after_generate(self, client):
        r = client.post("/pov/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        assert r.status_code == 200, r.text

    def test_pov_output_mentions_hpc(self, client):
        r = client.post("/pov/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        content = r.json()["content"]
        for s in EXPECTED_POV_STRINGS:
            assert s.upper() in content.upper(), f"POV missing '{s}'"

    def test_jep_runs_after_generate(self, client):
        r = client.post("/jep/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        assert r.status_code == 200, r.text

    def test_jep_output_mentions_hpc_shapes(self, client):
        r = client.post("/jep/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        content = r.json()["content"]
        for s in EXPECTED_JEP_STRINGS:
            assert s.upper() in content.upper(), f"JEP missing '{s}'"

    def test_terraform_runs_after_generate(self, client):
        r = client.post("/terraform/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        assert r.status_code == 200, r.text

    def test_terraform_main_tf_not_empty(self, client):
        r = client.post("/terraform/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        files = r.json()["files"]
        # files may be storage keys or inline content depending on store
        assert files  # at least one file key returned

    def test_terraform_output_has_hpc_strings(self, client):
        r = client.post("/terraform/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        # Verify via /terraform/{customer_id}/latest which returns full content
        lr = client.get(f"/terraform/{CUSTOMER_ID}/latest")
        assert lr.status_code == 200, lr.text
        files = lr.json()["files"]
        all_content = "\n".join(files.values())
        for s in EXPECTED_TERRAFORM_STRINGS:
            assert s.lower() in all_content.lower(), f"Terraform missing '{s}'"

    def test_waf_runs_after_generate(self, client):
        r = client.post("/waf/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        assert r.status_code == 200, r.text

    def test_waf_has_all_six_pillars(self, client):
        r = client.post("/waf/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        content = r.json()["content"]
        for pillar in EXPECTED_WAF_PILLARS:
            assert pillar in content, f"WAF missing pillar '{pillar}'"

    def test_waf_has_overall_rating(self, client):
        r = client.post("/waf/generate", json={
            "customer_id":   CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        assert r.json()["overall_rating"] != ""


# ---------------------------------------------------------------------------
# P3: Full BOM-to-pipeline — context accumulates all agent runs
# ---------------------------------------------------------------------------
class TestFullBomPipeline:
    def test_all_agents_recorded_in_context(self, client, store):
        # Step 1: Generate diagram
        r = client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
            "customer_name": CUSTOMER_NAME,
        })
        assert r.status_code == 200

        # Step 2: Run all writing agents
        for endpoint, payload in [
            ("/pov/generate",       {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
            ("/jep/generate",       {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
            ("/terraform/generate", {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
            ("/waf/generate",       {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
        ]:
            rr = client.post(endpoint, json=payload)
            assert rr.status_code == 200, f"{endpoint} failed: {rr.text}"

        # Step 3: Verify context has all five agent records
        ctx = read_context(store, CUSTOMER_ID)
        agents = ctx["agents"]
        for name in ("diagram", "pov", "jep", "terraform", "waf"):
            assert name in agents, f"'{name}' not recorded in context after full pipeline"

    def test_context_endpoint_exposes_all_agents(self, client):
        # Run full pipeline
        client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
        })
        for endpoint, payload in [
            ("/pov/generate",       {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
            ("/jep/generate",       {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
            ("/terraform/generate", {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
            ("/waf/generate",       {"customer_id": CUSTOMER_ID, "customer_name": CUSTOMER_NAME}),
        ]:
            client.post(endpoint, json=payload)

        r = client.get(f"/context/{CUSTOMER_ID}")
        assert r.status_code == 200
        agents = r.json()["context"]["agents"]
        for name in ("diagram", "pov", "jep", "terraform", "waf"):
            assert name in agents, f"'{name}' missing from /context endpoint"

    def test_diagram_key_visible_to_writing_agents_via_context(self, client, store):
        client.post("/generate", json={
            "resources":    HPC_OKE_RESOURCES,
            "diagram_name": DIAGRAM_NAME,
            "client_id":    CUSTOMER_ID,
            "customer_id":  CUSTOMER_ID,
        })
        ctx = read_context(store, CUSTOMER_ID)
        diagram_key = ctx["agents"]["diagram"]["diagram_key"]

        # Writing agents use the diagram_key; verify it points at a stored artifact
        latest_raw = store.get(diagram_key)
        latest = json.loads(latest_raw)
        assert "files" in latest or "artifacts" in latest or latest  # LATEST.json is non-empty
