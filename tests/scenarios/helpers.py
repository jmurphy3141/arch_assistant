"""
tests/scenarios/helpers.py
--------------------------
Mirrors the production input-hash and canonical-JSON logic so tests can
compute expected hashes without importing the server module.

Also provides shared assertion helpers.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Deterministic JSON — mirrors drawing_agent_server.canonical_json."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_input_hash_for_generate(
    resources: list,
    context: str = "",
    questionnaire: str = "",
    notes: str = "",
    deployment_hints: dict | None = None,
) -> str:
    """Mirror of the input_hash computation in /generate."""
    if deployment_hints is None:
        deployment_hints = {}
    context_total = context or ""
    if questionnaire and questionnaire.strip():
        context_total += f"\n\nQUESTIONNAIRE:\n{questionnaire}"
    if notes and notes.strip():
        context_total += f"\n\nNOTES:\n{notes}"
    parts = (
        canonical_json(resources)
        + "\n"
        + context_total
        + "\n"
        + canonical_json(deployment_hints)
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


# ── Assertion helpers ──────────────────────────────────────────────────────────

def assert_ok_envelope(data: dict) -> None:
    """Assert that a response has all required v1.5.0 OK fields."""
    assert data["status"] == "ok", f"Expected status=ok, got: {data.get('status')}"
    assert data["agent_version"] == "1.9.1"
    assert isinstance(data["schema_version"], dict)
    assert "request_id" in data and data["request_id"]
    assert "input_hash" in data and data["input_hash"]
    assert "drawio_xml" in data and data["drawio_xml"]
    assert "spec" in data
    assert "draw_dict" in data
    assert "render_manifest" in data
    assert "node_to_resource_map" in data
    assert "download" in data
    assert "url" in data["download"]
    assert "object_storage_latest" in data["download"]
    assert isinstance(data.get("errors"), list)


def assert_clarify_envelope(data: dict) -> None:
    """Assert that a response has all required v1.5.0 need_clarification fields."""
    assert data["status"] == "need_clarification"
    assert data["agent_version"] == "1.9.1"
    assert "request_id" in data
    assert "input_hash" in data
    assert isinstance(data["questions"], list)
    assert isinstance(data.get("errors"), list)
