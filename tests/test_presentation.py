from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

import drawing_agent_server
from agent.persistence_objectstore import InMemoryObjectStore
from agent.tools import presentation as presentation_module
from agent.tools.presentation import PresentationHandler, PPTX_CONTENT_TYPE
from skillforge.types import MemorySnapshot
from sub_agents.presentation.scripts import render_oci_powerpoint


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_memory(**overrides):
    decision_context = {
        "poc_recommendation": {
            "option_name": "ADB Migration POC",
            "oci_services": ["Oracle Autonomous Database", "Virtual Cloud Network"],
        },
        "pain_statement": "Oracle RAC cost pressure.",
        "bom_summary": "$500/mo estimate",
        "jep_phases": ["Prepare", "Build", "Validate"],
    }
    decision_context.update(overrides)
    return MemorySnapshot(
        session_id="s1",
        decision_context=decision_context,
        raw={"customer_id": "test-customer"},
    )


def _fake_pptx_bytes() -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        path = Path(f.name)
    try:
        render_oci_powerpoint.render(
            {
                "poc_name": "ADB Migration POC",
                "customer_name": "Acme",
                "pain_statement": "Oracle RAC cost pressure.",
                "oci_services": ["Oracle Autonomous Database"],
                "bom_summary": "$500/mo estimate",
                "jep_phases": ["Prepare", "Build", "Validate"],
            },
            str(path),
        )
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


async def test_pptx_artifact_key_format(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "presentation"
        assert engagement_context["customer_name"] == "Acme"
        return {
            "status": "ok",
            "result": base64.b64encode(_fake_pptx_bytes()).decode("ascii"),
            "trace": {"trace_id": trace_id},
        }

    monkeypatch.setattr(
        presentation_module.sub_agent_client,
        "call_sub_agent",
        fake_call_sub_agent,
    )

    store = InMemoryObjectStore()
    result = await PresentationHandler(store, "test-customer", "Acme")(
        {},
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert result.artifact_key == "presentation/test-customer/v1.pptx"
    assert store.get("presentation/test-customer/v1.pptx").startswith(b"PK")


def test_download_endpoint_pptx_content_type():
    store = InMemoryObjectStore()
    store.put("presentation/test/v1.pptx", b"pptx-bytes", PPTX_CONTENT_TYPE)
    drawing_agent_server.app.state.object_store = store

    try:
        with TestClient(drawing_agent_server.app, raise_server_exceptions=True) as client:
            resp = client.get("/api/download?key=presentation/test/v1.pptx")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(PPTX_CONTENT_TYPE)
        assert resp.content == b"pptx-bytes"
    finally:
        drawing_agent_server.app.state.object_store = None


async def test_needs_clarification_when_no_poc_recommendation():
    result = await PresentationHandler(InMemoryObjectStore(), "test-customer", "Acme")(
        {},
        memory=make_memory(poc_recommendation={}),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "needs_input"
    assert "Run generate_poc_plan first" in result.clarification


def test_render_creates_seven_slides():
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        path = Path(f.name)
    try:
        render_oci_powerpoint.render(
            {
                "poc_name": "Test",
                "customer_name": "Acme",
                "pain_statement": "",
                "oci_services": [],
                "bom_summary": "",
                "jep_phases": [],
                "date": "Jan 1 2026",
            },
            str(path),
        )
        prs = Presentation(str(path))
        assert len(prs.slides) == 7
    finally:
        path.unlink(missing_ok=True)
