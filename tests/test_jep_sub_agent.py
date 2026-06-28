import pytest

from agent.tools.specialists import _document_review_findings
from sub_agents.jep.server import handle
from sub_agents.models import A2ARequest


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_explicit_jep_uses_grounded_path_without_inference(monkeypatch):
    monkeypatch.setattr(
        "sub_agents.jep.server.run_inference",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not infer")),
    )
    task = (
        "Using the persisted POV and confirmed POC, create the final 14-day JEP and POC plan for Apex Retail to validate migration of an "
        "on-premises three-tier retail web application to OCI us-ashburn-1. Scope: WAF, "
        "public Flexible Load Balancer, two private VM.Standard.E5.Flex web servers, private "
        "PostgreSQL database, Object Storage, Block Volume, logging, and monitoring. Use exactly "
        "three phases: Phase 1 Assessment on days 1-3, Phase 2 Build on days 4-9, and Phase 3 "
        "Validate on days 10-14. Success criteria: 99.9% availability during a 48-hour soak test, "
        "p95 response time under 500 milliseconds at 100 requests per second, and database restore "
        "within 60 minutes. Oracle SA and Apex Retail technical lead each commit 8 hours per week. "
        "Include at least three risks, a go/no-go sign-off with fallback, explicit out-of-scope "
        "items, a BOM section, timeline, owners, approvals, and handoff deliverables. Generate only "
        "the JEP artifact; do not generate a separate BOM workbook."
    )
    response = await handle(A2ARequest(task=task, trace_id="jep-fast"))

    assert response.status == "ok"
    assert response.trace["generation_mode"] == "deterministic_grounded_brief"
    assert "## Handoff Deliverables" in response.result
    assert "Phase 1 Assessment" in response.result
    assert "Phase 2 Build" in response.result
    assert "Phase 3 Validate" in response.result
    assert "$" not in response.result
    assert _document_review_findings("jep", response.result, task) == []
