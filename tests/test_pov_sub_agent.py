from pathlib import Path

import pytest

from agent.tools.specialists import _document_review_findings
from sub_agents.models import A2ARequest
from sub_agents.pov.server import _build_prompt, card, handle


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


async def test_pov_prompt_uses_canonical_prfaq_sections_and_reasoning_model():
    prompt = (Path(__file__).parents[1] / "sub_agents" / "pov" / "system_prompt.md").read_text(
        encoding="utf-8"
    )
    sections = [
        "## Summary",
        "## Problem",
        "## Solution",
        "## Oracle Quote",
        "## Customer Quote",
        "## External (Customer) Questions & Answers",
        "## Internal (Oracle) Questions & Answers",
    ]

    assert all(section in prompt for section in sections)
    assert [prompt.index(section) for section in sections] == sorted(prompt.index(section) for section in sections)
    assert "never invent a customer, number, or fact" in prompt
    assert "never invent a name" in prompt
    assert card.llm_model_id == (
        "ocid1.generativeaimodel.oc1.us-chicago-1."
        "amaaaaaask7dceya4fxp5zjj27q24rjxk46l43die7u6nclgwfbemklsdvoa"
    )
    built = _build_prompt(A2ARequest(task="Write the POV", engagement_context={"customer_name": "ACME"}))
    assert "MANDATORY OUTPUT CONTRACT" in built
    assert built.index("## Summary") < built.index("Write the POV")
