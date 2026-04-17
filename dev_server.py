"""
dev_server.py
--------------
Start the drawing agent locally WITHOUT OCI credentials.

Injects a stub LLM runner and in-memory object store before startup,
so the server runs fully on localhost for development and manual testing.

The stub LLM runner returns a realistic HPC OKE layout spec for ANY
diagram request, and realistic writing-agent output for POV/JEP/Terraform/WAF.
Swap out the stubs below with your own responses to test other scenarios.

Usage:
    python3 dev_server.py

Then use the curl commands in tests/fixtures/hpc_oke_context.txt instructions
or the Makefile targets to drive the API.
"""
import json
import uvicorn

from drawing_agent_server import app
from agent.persistence_objectstore import InMemoryObjectStore

# ── Import the HPC OKE reference responses ──────────────────────────────────
from tests.fixtures.hpc_oke_scenario import (
    FAKE_LAYOUT_SPEC_JSON,
    FAKE_POV,
    FAKE_JEP,
    FAKE_TERRAFORM,
    FAKE_WAF,
)


def _llm_runner(prompt: str, client_id: str) -> dict:
    """JSON runner for /generate and /upload-bom: returns HPC OKE layout spec."""
    return json.loads(FAKE_LAYOUT_SPEC_JSON)


def _text_runner(prompt: str, system_message: str = "") -> str:
    """Text runner for writing agents (POV, JEP, Terraform, WAF)."""
    sm = system_message.lower()
    if "terraform" in sm:
        return FAKE_TERRAFORM
    if "well-architected" in sm or "waf" in sm:
        return FAKE_WAF
    if "pov" in sm or "point of view" in sm:
        return FAKE_POV
    if "jep" in sm or "execution plan" in sm:
        return FAKE_JEP
    return FAKE_POV  # fallback


# Pre-inject before startup so OCI init is skipped entirely
app.state.llm_runner   = _llm_runner
app.state.text_runner  = _text_runner
app.state.object_store = InMemoryObjectStore()
app.state.persistence_config = {"prefix": "agent3"}

if __name__ == "__main__":
    print("=" * 60)
    print("  Drawing Agent — LOCAL DEV MODE (no OCI auth)")
    print("  Stub LLM: HPC OKE reference responses")
    print("  Store:    InMemoryObjectStore (data lost on restart)")
    print("  URL:      http://localhost:8080")
    print("  Docs:     http://localhost:8080/docs")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
