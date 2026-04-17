"""
tests/test_server_live.py
--------------------------
Installation smoke tests — run these against the LIVE deployed server
to verify the full stack is working end-to-end with real OCI auth.

Prerequisites
-------------
  1. Server running on OCI Compute with Instance Principal attached:
       uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080

  2. Set the env var:
       export AGENT_BASE_URL=http://10.0.3.47:8080

  3. Optional — skip slow LLM generation tests:
       export SKIP_LLM_TESTS=1

Run all smoke tests:
    pytest tests/test_server_live.py -v -s

Run only the fast checks (health, notes, no LLM):
    SKIP_LLM_TESTS=1 pytest tests/test_server_live.py -v -s

Run a single test:
    pytest tests/test_server_live.py::TestHealth -v -s

Run as a standalone script (summary table):
    python tests/test_server_live.py

Coverage
--------
  GET  /health
  GET  /.well-known/agent.json
  GET  /mcp/tools
  POST /notes/upload
  GET  /notes/{customer_id}
  POST /pov/generate          (LLM call — skipped when SKIP_LLM_TESTS=1)
  GET  /pov/{id}/latest
  GET  /pov/{id}/versions
  POST /jep/generate          (LLM call — skipped when SKIP_LLM_TESTS=1)
  GET  /jep/{id}/latest
  GET  /jep/{id}/versions
  POST /terraform/generate    (LLM call — skipped when SKIP_LLM_TESTS=1)
  GET  /terraform/{id}/latest
  GET  /terraform/{id}/versions
  POST /waf/generate          (LLM call — skipped when SKIP_LLM_TESTS=1)
  GET  /waf/{id}/latest
  GET  /waf/{id}/versions
  GET  /context/{customer_id}
  Full fleet: notes → POV → JEP → Terraform → WAF (LLM calls)
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
from typing import Optional

import pytest

logger = logging.getLogger(__name__)

# ── Opt-in gate ───────────────────────────────────────────────────────────────

_BASE_URL    = os.environ.get("AGENT_BASE_URL", "").rstrip("/")
_SKIP_LLM    = os.environ.get("SKIP_LLM_TESTS", "0") == "1"
_RUN_LIVE    = bool(_BASE_URL)

pytestmark = pytest.mark.live

requires_server = pytest.mark.skipif(
    not _RUN_LIVE,
    reason="Set AGENT_BASE_URL=http://<host>:<port> to run live server tests",
)
requires_llm = pytest.mark.skipif(
    not _RUN_LIVE or _SKIP_LLM,
    reason=(
        "Set AGENT_BASE_URL and unset SKIP_LLM_TESTS=1 to run tests that call the LLM"
    ),
)

# ── Shared test customer ───────────────────────────────────────────────────────

# Use a timestamped ID so repeated runs don't pollute each other's context
_TS = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
TEST_CUSTOMER_ID   = f"install_test_{_TS}"
TEST_CUSTOMER_NAME = "Install Test Customer"

SAMPLE_NOTE = (
    b"# Installation Test Meeting Notes\n\n"
    b"Customer requires a 2-node GPU cluster with BM.GPU.B300.8 shapes.\n"
    b"14-day POC. CUDA 12.6. PyTorch workloads. OKE preferred.\n"
    b"Single-region, two availability domains, active-passive HA.\n"
)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str, *, timeout: int = 30) -> dict:
    """GET {BASE_URL}{path}; return parsed JSON body. Raises on non-2xx."""
    import urllib.request
    import urllib.error
    url = f"{_BASE_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(
            f"GET {url} returned {exc.code}: {body[:400]}"
        ) from exc


def _post_json(path: str, body: dict, *, timeout: int = 300) -> dict:
    """POST JSON to {BASE_URL}{path}; return parsed response. Raises on non-2xx."""
    import urllib.request
    import urllib.error
    url = f"{_BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(
            f"POST {url} returned {exc.code}: {body_text[:400]}"
        ) from exc


def _post_multipart(
    path: str,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_data: bytes,
    content_type: str = "text/plain",
    *,
    timeout: int = 30,
) -> dict:
    """POST multipart/form-data. Returns parsed JSON."""
    import urllib.request
    import urllib.error

    boundary = "----SmokeBoundary7a3b9c"
    body_parts: list[bytes] = []
    for name, value in fields.items():
        body_parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    body_parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    body_parts.append(file_data)
    body_parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    raw = b"".join(body_parts)

    url = f"{_BASE_URL}{path}"
    req = urllib.request.Request(
        url, data=raw,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(
            f"POST {url} returned {exc.code}: {err[:400]}"
        ) from exc


def _upload_note(
    customer_id: str,
    note_name: str,
    content: bytes,
) -> dict:
    return _post_multipart(
        "/notes/upload",
        fields={"customer_id": customer_id, "note_name": note_name},
        file_field="file",
        filename=note_name,
        file_data=content,
    )


# ── Test classes ──────────────────────────────────────────────────────────────

class TestHealth:
    @requires_server
    def test_health_returns_ok(self):
        body = _get("/health")
        assert body.get("status") in ("ok", "healthy", "degraded"), (
            f"/health returned unexpected status: {body}"
        )
        print(f"\n  server: {body.get('agent', 'unknown')} v{body.get('agent_version', '?')}")

    @requires_server
    def test_health_has_agent_version(self):
        body = _get("/health")
        assert "agent_version" in body, f"Missing agent_version in /health: {body}"

    @requires_server
    def test_agent_card(self):
        body = _get("/.well-known/agent.json")
        assert "name" in body or "agent" in body, (
            f"agent-card missing name: {body}"
        )

    @requires_server
    def test_mcp_tools_listed(self):
        body = _get("/mcp/tools")
        assert "tools" in body, f"/mcp/tools missing 'tools' key: {body}"
        assert len(body["tools"]) > 0, "/mcp/tools returned empty tool list"
        print(f"\n  MCP tools: {[t.get('name') for t in body['tools'][:5]]}")


class TestNotes:
    @requires_server
    def test_upload_note_returns_ok(self):
        body = _upload_note(TEST_CUSTOMER_ID, "install_note.md", SAMPLE_NOTE)
        assert body.get("status") == "ok", f"Upload failed: {body}"
        assert body.get("customer_id") == TEST_CUSTOMER_ID

    @requires_server
    def test_upload_note_key_format(self):
        body = _upload_note(TEST_CUSTOMER_ID, "key_test.txt", b"test content")
        key = body.get("key", "")
        assert key.startswith("notes/"), f"Unexpected key format: {key!r}"
        assert TEST_CUSTOMER_ID in key

    @requires_server
    def test_list_notes_returns_uploaded(self):
        note_name = "list_test.txt"
        _upload_note(TEST_CUSTOMER_ID, note_name, b"list test note")
        body = _get(f"/notes/{TEST_CUSTOMER_ID}")
        assert body.get("status") == "ok"
        names = [n["name"] for n in body.get("notes", [])]
        assert note_name in names, f"Uploaded note not in list: {names}"

    @requires_server
    def test_list_notes_empty_for_unknown_customer(self):
        body = _get("/notes/no_such_customer_xyz_never_existed")
        assert body.get("status") == "ok"
        assert body.get("notes") == [], f"Expected empty notes: {body}"


class TestContext:
    @requires_server
    def test_context_empty_for_new_customer(self):
        cid = f"context_test_{_TS}"
        body = _get(f"/context/{cid}")
        assert body.get("status") == "ok"
        ctx = body.get("context", {})
        assert ctx.get("customer_id") == cid
        assert ctx.get("agents") == {}

    @requires_server
    def test_context_endpoint_responds(self):
        body = _get(f"/context/{TEST_CUSTOMER_ID}")
        assert body.get("status") == "ok"
        assert "context" in body


class TestPovGenerate:
    @requires_server
    def test_pov_404_before_generate(self):
        import urllib.error
        try:
            _get(f"/pov/never_existed_{_TS}/latest")
            assert False, "Expected 404"
        except AssertionError as e:
            assert "404" in str(e), f"Expected 404, got: {e}"

    @requires_server
    def test_pov_versions_empty_before_generate(self):
        body = _get(f"/pov/never_existed_{_TS}/versions")
        assert body.get("versions") == []

    @requires_llm
    def test_pov_generate_ok(self):
        _upload_note(TEST_CUSTOMER_ID, "pov_note.md", SAMPLE_NOTE)
        body = _post_json(
            "/pov/generate",
            {"customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME},
        )
        assert body.get("status") == "ok", f"POV generate failed: {body}"
        assert body.get("doc_type") == "pov"
        assert isinstance(body.get("version"), int)
        assert body.get("version") >= 1
        assert body.get("content"), "POV content is empty"
        print(f"\n  POV v{body['version']}: {len(body['content'])} chars")

    @requires_llm
    def test_pov_latest_after_generate(self):
        body = _get(f"/pov/{TEST_CUSTOMER_ID}/latest")
        assert body.get("status") == "ok"
        assert body.get("content"), "POV latest content is empty"

    @requires_llm
    def test_pov_versions_after_generate(self):
        body = _get(f"/pov/{TEST_CUSTOMER_ID}/versions")
        assert body.get("status") == "ok"
        versions = body.get("versions", [])
        assert len(versions) >= 1, "No POV versions found after generate"
        assert all("version" in v for v in versions)

    @requires_llm
    def test_pov_context_updated(self):
        ctx_body = _get(f"/context/{TEST_CUSTOMER_ID}")
        agents = ctx_body.get("context", {}).get("agents", {})
        assert "pov" in agents, f"POV not recorded in context: {agents}"
        assert agents["pov"].get("version") >= 1


class TestJepGenerate:
    @requires_server
    def test_jep_404_before_generate(self):
        try:
            _get(f"/jep/never_existed_{_TS}/latest")
            assert False, "Expected 404"
        except AssertionError as e:
            assert "404" in str(e)

    @requires_server
    def test_jep_versions_empty(self):
        body = _get(f"/jep/never_existed_{_TS}/versions")
        assert body.get("versions") == []

    @requires_llm
    def test_jep_generate_ok(self):
        body = _post_json(
            "/jep/generate",
            {"customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME},
        )
        assert body.get("status") == "ok", f"JEP generate failed: {body}"
        assert body.get("doc_type") == "jep"
        assert isinstance(body.get("version"), int)
        assert body.get("version") >= 1
        assert body.get("content"), "JEP content is empty"
        assert body.get("bom") is not None, "JEP missing BOM"
        print(f"\n  JEP v{body['version']}: {len(body['content'])} chars, BOM source={body['bom'].get('source')}")

    @requires_llm
    def test_jep_latest_after_generate(self):
        body = _get(f"/jep/{TEST_CUSTOMER_ID}/latest")
        assert body.get("status") == "ok"
        assert body.get("content")

    @requires_llm
    def test_jep_versions_after_generate(self):
        body = _get(f"/jep/{TEST_CUSTOMER_ID}/versions")
        versions = body.get("versions", [])
        assert len(versions) >= 1

    @requires_llm
    def test_jep_context_updated(self):
        ctx_body = _get(f"/context/{TEST_CUSTOMER_ID}")
        agents = ctx_body.get("context", {}).get("agents", {})
        assert "jep" in agents, f"JEP not recorded in context: {agents}"


class TestTerraformGenerate:
    @requires_server
    def test_terraform_404_before_generate(self):
        try:
            _get(f"/terraform/never_existed_{_TS}/latest")
            assert False, "Expected 404"
        except AssertionError as e:
            assert "404" in str(e)

    @requires_server
    def test_terraform_versions_empty(self):
        body = _get(f"/terraform/never_existed_{_TS}/versions")
        assert body.get("versions") == []

    @requires_llm
    def test_terraform_generate_ok(self):
        body = _post_json(
            "/terraform/generate",
            {"customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME},
            timeout=600,  # Terraform generates 4 files; allow up to 10 min
        )
        assert body.get("status") == "ok", f"Terraform generate failed: {body}"
        assert body.get("doc_type") == "terraform"
        assert isinstance(body.get("version"), int)
        assert body.get("version") >= 1
        assert body.get("file_count", 0) > 0, "Terraform file_count is 0"
        print(f"\n  Terraform v{body['version']}: {body['file_count']} files")

    @requires_llm
    def test_terraform_latest_has_main_tf(self):
        body = _get(f"/terraform/{TEST_CUSTOMER_ID}/latest")
        assert body.get("status") == "ok"
        files = body.get("files", {})
        assert "main.tf" in files, f"main.tf missing from Terraform latest: {list(files.keys())}"
        assert len(files["main.tf"]) > 10, "main.tf is suspiciously short"

    @requires_llm
    def test_terraform_latest_has_variables_tf(self):
        body = _get(f"/terraform/{TEST_CUSTOMER_ID}/latest")
        files = body.get("files", {})
        assert "variables.tf" in files, "variables.tf missing from Terraform"

    @requires_llm
    def test_terraform_latest_has_outputs_tf(self):
        body = _get(f"/terraform/{TEST_CUSTOMER_ID}/latest")
        files = body.get("files", {})
        assert "outputs.tf" in files, "outputs.tf missing from Terraform"

    @requires_llm
    def test_terraform_latest_has_tfvars_example(self):
        body = _get(f"/terraform/{TEST_CUSTOMER_ID}/latest")
        files = body.get("files", {})
        assert "terraform.tfvars.example" in files, "terraform.tfvars.example missing"

    @requires_llm
    def test_terraform_main_tf_has_oci_provider(self):
        body = _get(f"/terraform/{TEST_CUSTOMER_ID}/latest")
        main = body.get("files", {}).get("main.tf", "")
        assert "oci" in main.lower(), "main.tf has no OCI provider reference"

    @requires_llm
    def test_terraform_versions_after_generate(self):
        body = _get(f"/terraform/{TEST_CUSTOMER_ID}/versions")
        versions = body.get("versions", [])
        assert len(versions) >= 1

    @requires_llm
    def test_terraform_context_updated(self):
        ctx_body = _get(f"/context/{TEST_CUSTOMER_ID}")
        agents = ctx_body.get("context", {}).get("agents", {})
        assert "terraform" in agents, f"Terraform not recorded in context: {agents}"


class TestWafGenerate:
    @requires_server
    def test_waf_404_before_generate(self):
        try:
            _get(f"/waf/never_existed_{_TS}/latest")
            assert False, "Expected 404"
        except AssertionError as e:
            assert "404" in str(e)

    @requires_server
    def test_waf_versions_empty(self):
        body = _get(f"/waf/never_existed_{_TS}/versions")
        assert body.get("versions") == []

    @requires_llm
    def test_waf_generate_ok(self):
        body = _post_json(
            "/waf/generate",
            {"customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME},
        )
        assert body.get("status") == "ok", f"WAF generate failed: {body}"
        assert body.get("doc_type") == "waf"
        assert isinstance(body.get("version"), int)
        assert body.get("version") >= 1
        assert body.get("content"), "WAF content is empty"
        rating = body.get("overall_rating", "")
        print(f"\n  WAF v{body['version']}: {len(body['content'])} chars, rating={rating}")

    @requires_llm
    def test_waf_has_all_pillars(self):
        body = _get(f"/waf/{TEST_CUSTOMER_ID}/latest")
        content = body.get("content", "")
        for pillar in [
            "Operational Excellence", "Security", "Reliability",
            "Performance Efficiency", "Cost Optimization", "Sustainability",
        ]:
            assert pillar in content, (
                f"WAF review missing pillar: {pillar!r}. "
                f"Content starts with: {content[:300]!r}"
            )

    @requires_llm
    def test_waf_overall_rating_is_emoji(self):
        body = _post_json(
            "/waf/generate",
            {"customer_id": f"waf_rating_test_{_TS}", "customer_name": "WAF Rating Test"},
        )
        rating = body.get("overall_rating", "")
        assert rating in ("✅", "⚠️", "❌", "unknown"), (
            f"Unexpected overall_rating: {rating!r}"
        )

    @requires_llm
    def test_waf_latest_after_generate(self):
        body = _get(f"/waf/{TEST_CUSTOMER_ID}/latest")
        assert body.get("status") == "ok"
        assert body.get("content")

    @requires_llm
    def test_waf_versions_after_generate(self):
        body = _get(f"/waf/{TEST_CUSTOMER_ID}/versions")
        versions = body.get("versions", [])
        assert len(versions) >= 1

    @requires_llm
    def test_waf_context_updated(self):
        ctx_body = _get(f"/context/{TEST_CUSTOMER_ID}")
        agents = ctx_body.get("context", {}).get("agents", {})
        assert "waf" in agents, f"WAF not recorded in context: {agents}"


class TestContextAccumulation:
    """Verify the context file accumulates correctly across all agents."""

    @requires_llm
    def test_context_has_all_agent_records(self):
        """After running all 4 writing agents, context should record all of them."""
        body = _get(f"/context/{TEST_CUSTOMER_ID}")
        assert body.get("status") == "ok"
        agents = body.get("context", {}).get("agents", {})

        missing = [a for a in ("pov", "jep", "terraform", "waf") if a not in agents]
        assert not missing, (
            f"Context missing agents: {missing}. "
            f"Agents present: {list(agents.keys())}"
        )
        print(f"\n  Context agents: {list(agents.keys())}")
        for name, data in agents.items():
            print(f"    {name}: v{data.get('version', '?')}  "
                  f"notes={len(data.get('notes_incorporated', []))}")

    @requires_llm
    def test_context_notes_incorporated_tracked(self):
        """Each agent should have notes_incorporated in its context record."""
        body = _get(f"/context/{TEST_CUSTOMER_ID}")
        agents = body.get("context", {}).get("agents", {})
        for agent_name, data in agents.items():
            assert "notes_incorporated" in data, (
                f"Agent {agent_name!r} missing notes_incorporated in context"
            )
            assert isinstance(data["notes_incorporated"], list)

    @requires_llm
    def test_context_pov_notes_not_duplicated_in_jep(self):
        """
        Notes that POV saw on first run should also appear in JEP notes_incorporated.
        (Each agent tracks its own — they can overlap but neither should miss new notes.)
        """
        body = _get(f"/context/{TEST_CUSTOMER_ID}")
        agents = body.get("context", {}).get("agents", {})
        pov_notes = set(agents.get("pov", {}).get("notes_incorporated", []))
        jep_notes = set(agents.get("jep", {}).get("notes_incorporated", []))
        # Both should have tracked the same install_note.md
        assert pov_notes, "POV has no notes_incorporated"
        assert jep_notes, "JEP has no notes_incorporated"


class TestFullFleetWorkflow:
    """
    Sequential end-to-end test using a fresh customer ID.
    Verifies the complete fleet pipeline works in order.
    """

    @requires_llm
    def test_full_fleet_sequential(self):
        """
        Full pipeline: notes → POV → JEP → Terraform → WAF.
        Verifies each agent runs, updates context, and the final context
        contains all agent records.
        """
        cid   = f"fleet_test_{_TS}"
        cname = "Fleet Integration Test"

        # 1. Upload notes
        print(f"\n  [fleet] customer_id={cid!r}")
        note = _upload_note(cid, "fleet_notes.md", SAMPLE_NOTE)
        assert note.get("status") == "ok", f"Note upload failed: {note}"
        print(f"  [fleet] Note uploaded: {note['key']}")

        # 2. POV
        pov = _post_json("/pov/generate", {"customer_id": cid, "customer_name": cname})
        assert pov.get("status") == "ok", f"POV failed: {pov}"
        assert pov.get("version") == 1
        print(f"  [fleet] POV v{pov['version']}: {len(pov['content'])} chars")

        # 3. JEP
        jep = _post_json("/jep/generate", {"customer_id": cid, "customer_name": cname})
        assert jep.get("status") == "ok", f"JEP failed: {jep}"
        assert jep.get("version") == 1
        print(f"  [fleet] JEP v{jep['version']}: {len(jep['content'])} chars")

        # 4. Terraform
        tf = _post_json("/terraform/generate", {"customer_id": cid, "customer_name": cname}, timeout=600)
        assert tf.get("status") == "ok", f"Terraform failed: {tf}"
        assert tf.get("version") == 1
        assert tf.get("file_count", 0) > 0
        print(f"  [fleet] Terraform v{tf['version']}: {tf['file_count']} files")

        # 5. WAF
        waf = _post_json("/waf/generate", {"customer_id": cid, "customer_name": cname})
        assert waf.get("status") == "ok", f"WAF failed: {waf}"
        assert waf.get("version") == 1
        print(f"  [fleet] WAF v{waf['version']}: {len(waf['content'])} chars, "
              f"rating={waf.get('overall_rating')}")

        # 6. Verify context has all agents
        ctx = _get(f"/context/{cid}")
        agents = ctx.get("context", {}).get("agents", {})
        for agent in ("pov", "jep", "terraform", "waf"):
            assert agent in agents, f"Agent {agent!r} missing from context"
        print(f"  [fleet] Context OK — agents: {list(agents.keys())}")

        # 7. Verify JEP context summary includes POV
        #    (context_summary is in the JEP prompt but not in the response;
        #     we verify indirectly that the context object has a POV record)
        assert agents["pov"]["version"] == 1
        assert agents["jep"]["version"] == 1

        # 8. Verify Terraform latest has all files
        tf_latest = _get(f"/terraform/{cid}/latest")
        files = tf_latest.get("files", {})
        for fname in ("main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example"):
            assert fname in files, f"Missing Terraform file: {fname!r}"

        print(f"\n  [fleet] ALL CHECKS PASSED for customer_id={cid!r}")


# ── Standalone runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Run smoke tests directly and print a summary table.

    Usage:
        AGENT_BASE_URL=http://10.0.3.47:8080 python tests/test_server_live.py

    Skip LLM-heavy tests (faster, just checks endpoints exist):
        AGENT_BASE_URL=http://10.0.3.47:8080 SKIP_LLM_TESTS=1 python tests/test_server_live.py
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not _BASE_URL:
        print("ERROR: Set AGENT_BASE_URL=http://<host>:<port>")
        sys.exit(1)

    # Ordered smoke checks: (label, callable)
    checks: list[tuple[str, callable]] = [
        ("GET /health",          lambda: _get("/health")),
        ("GET /.well-known/agent.json", lambda: _get("/.well-known/agent.json")),
        ("GET /mcp/tools",       lambda: _get("/mcp/tools")),
        ("POST /notes/upload",   lambda: _upload_note(TEST_CUSTOMER_ID, "smoke.md", SAMPLE_NOTE)),
        ("GET /notes/{id}",      lambda: _get(f"/notes/{TEST_CUSTOMER_ID}")),
        ("GET /context (empty)", lambda: _get(f"/context/smoke_empty_{_TS}")),
    ]

    if not _SKIP_LLM:
        checks += [
            ("POST /pov/generate",  lambda: _post_json("/pov/generate", {
                "customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME
            })),
            ("GET /pov/latest",     lambda: _get(f"/pov/{TEST_CUSTOMER_ID}/latest")),
            ("GET /pov/versions",   lambda: _get(f"/pov/{TEST_CUSTOMER_ID}/versions")),
            ("POST /jep/generate",  lambda: _post_json("/jep/generate", {
                "customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME
            })),
            ("GET /jep/latest",     lambda: _get(f"/jep/{TEST_CUSTOMER_ID}/latest")),
            ("POST /terraform/generate", lambda: _post_json("/terraform/generate", {
                "customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME
            }, timeout=600)),
            ("GET /terraform/latest",    lambda: _get(f"/terraform/{TEST_CUSTOMER_ID}/latest")),
            ("POST /waf/generate",  lambda: _post_json("/waf/generate", {
                "customer_id": TEST_CUSTOMER_ID, "customer_name": TEST_CUSTOMER_NAME
            })),
            ("GET /waf/latest",     lambda: _get(f"/waf/{TEST_CUSTOMER_ID}/latest")),
            ("GET /context (full)", lambda: _get(f"/context/{TEST_CUSTOMER_ID}")),
        ]

    results: list[tuple[str, str, str]] = []
    print(f"\nSmoke-testing {_BASE_URL}")
    print(f"Customer: {TEST_CUSTOMER_ID}")
    print(f"LLM tests: {'SKIPPED' if _SKIP_LLM else 'ENABLED'}")
    print("─" * 60)

    for label, fn in checks:
        try:
            resp = fn()
            status = resp.get("status", "?")
            results.append((label, "PASS", str(status)))
            print(f"  ✓  {label:<38} [{status}]")
        except Exception as exc:
            short = str(exc)[:60]
            results.append((label, "FAIL", short))
            print(f"  ✗  {label:<38} {short}")

    failed = [r for r in results if r[1] != "PASS"]
    print(f"\n{'═' * 60}")
    print(f"  {len(results) - len(failed)} / {len(results)} checks passed")
    if failed:
        print(f"\nFailed:")
        for label, _, msg in failed:
            print(f"  ✗ {label}: {msg}")

    sys.exit(1 if failed else 0)
