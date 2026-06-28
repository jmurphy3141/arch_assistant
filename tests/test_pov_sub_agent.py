import pytest

from agent.tools.specialists import _document_review_findings
from sub_agents.models import A2ARequest
from sub_agents.pov.server import handle


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_explicit_pov_uses_grounded_path_without_inference(monkeypatch):
    monkeypatch.setattr(
        "sub_agents.pov.server.run_inference",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not infer")),
    )
    task = (
        "Draft an internal OCI POV for Apex Retail migrating an internet-facing "
        "three-tier retail web application from on-premises to OCI. Cover WAF and "
        "Flexible Load Balancer, private networking and IAM, Object Storage, 500 GB "
        "Block Volume, and PostgreSQL. The alternative is remaining on-premises. "
        "Proposed validation targets, not achieved outcomes: 30% within 12 months, under "
        "300 ms, and 99.9%. No customer case studies."
    )
    response = await handle(
        A2ARequest(
            task=task,
            trace_id="pov-fast",
        )
    )

    assert response.status == "ok"
    assert response.trace["generation_mode"] == "deterministic_grounded_brief"
    assert "## 1. Internal Press Release" in response.result
    assert "## 2. External Customer FAQ" in response.result
    assert "## 3. Internal Oracle Questions" in response.result
    assert "not achieved results" in response.result
    assert "requires approval" in response.result
    assert response.result.lower().count("proposed quote") == 3
    assert _document_review_findings("pov", response.result, task) == []
