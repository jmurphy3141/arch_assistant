import pytest

from agent.tools.specialists import _document_review_findings
from agent.pov_composer import extract_pov_brief, render_pov_with_inference
from sub_agents.models import A2ARequest
from sub_agents.pov.server import _grounded_explicit_pov, handle


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_explicit_pov_falls_back_safely_when_inference_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "sub_agents.pov.server.run_inference",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("inference unavailable")),
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
    assert response.trace["generation_mode"] == "deterministic_grounded_fallback"
    assert response.trace["render_attempts"] == 2
    assert "## 1. Internal Press Release" in response.result
    assert "## 2. External Customer FAQ" in response.result
    assert "## 3. Internal Oracle Questions" in response.result
    assert "not achieved results" in response.result
    assert "requires approval" in response.result
    assert response.result.lower().count("proposed quote") == 3
    assert _document_review_findings("pov", response.result, task) == []


def test_pov_generative_prose_passes_grounding_guard() -> None:
    task = (
        "Draft an internal OCI POV for Apex Retail migrating from on-premises to OCI. "
        "Cover WAF, Flexible Load Balancer, IAM, Object Storage, 500 GB Block Volume, "
        "and PostgreSQL. The alternative is remaining on-premises. Proposed validation "
        "targets, not achieved outcomes: 30% within 12 months, under 300 ms, and 99.9%. "
        "No customer case studies."
    )
    brief = extract_pov_brief(task)
    candidate = _grounded_explicit_pov(task.replace("migrating from", "migrating an internet-facing three-tier retail web application from"))
    assert candidate is not None
    candidate = candidate.replace(
        "The business case remains a hypothesis to validate against the alternative of remaining on-premises.",
        "Against the alternative of remaining on-premises, the business case remains a hypothesis that the joint team must validate.",
    ).replace(
        "Apex Retail is assessing whether OCI is a better future platform for its internet-facing three-tier retail application than continuing to operate it on-premises.",
        "Apex Retail is weighing OCI against continued on-premises operation for its internet-facing three-tier retail application, with evidence—not assertion—driving the decision.",
    )

    result = render_pov_with_inference(brief, lambda _prompt, _system: candidate)

    assert result.generation_mode == "generative_grounded_prose"
    assert result.attempts == 1


def test_pov_fabrication_retries_once_then_uses_safe_fallback() -> None:
    task = (
        "Draft an internal OCI POV for Apex Retail migrating an internet-facing three-tier "
        "retail web application from on-premises to OCI. Cover WAF and Flexible Load Balancer, "
        "private networking and IAM, Object Storage, 500 GB Block Volume, and PostgreSQL. "
        "The alternative is remaining on-premises. Proposed validation targets, not achieved "
        "outcomes: 30% within 12 months, under 300 ms, and 99.9%. No customer case studies."
    )
    brief = extract_pov_brief(task)
    fallback = _grounded_explicit_pov(task)
    assert fallback is not None
    calls: list[str] = []

    def fabricated(prompt: str, _system: str) -> str:
        calls.append(prompt)
        return fallback + "\nContoso achieved a 47% SLA using customers/apex/bom/result.xlsx."

    result = render_pov_with_inference(brief, fabricated, deterministic_fallback=fallback)

    assert result.generation_mode == "deterministic_grounded_fallback"
    assert result.markdown == fallback
    assert len(calls) == 2
    assert "unsupported numeric tokens" in calls[1]
    assert "internal artifact references" in calls[1]


def test_explicit_locked_pov_brief_excludes_unrelated_accumulated_metrics() -> None:
    task = (
        "Generate a POV. Proposed validation targets exactly: under 500 milliseconds. "
        "In-scope services: PostgreSQL. Treat outcomes as proposed."
    )
    brief = extract_pov_brief(
        task,
        {
            "architect_brief": {
                "architect_context": "Unrelated prior draft claimed 99.9% and FastConnect."
            }
        },
    )

    serialized = " ".join(brief.engagement_facts)
    assert "99.9%" not in serialized
    assert "FastConnect" not in serialized
    assert brief.services == ("PostgreSQL",)
