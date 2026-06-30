from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import yaml

from agent.jep_composer import compose_jep, render_jep_with_inference
from agent.llm_inference_client import run_inference
from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))


card = AgentCard(
    name="jep",
    description="Writes an OCI Joint Engagement Plan document.",
    inputs={
        "required": ["task"],
        "optional": ["customer_name", "engagement_context", "prior_version", "feedback", "trace_id"],
    },
    output="JEP document in Markdown",
    llm_model_id=_model_id,
)


async def handle(req: A2ARequest) -> A2AResponse:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    composition = compose_jep(req.task, context)
    trace = {
        "agent": card.name,
        "trace_id": req.trace_id,
        "generation_mode": "deterministic_grounded_brief",
        "missing_fields": list(composition.missing_fields),
    }
    if composition.status != "ok":
        questions = "\n".join(f"- {question}" for question in composition.questions)
        return A2AResponse(
            result="I need the following JEP kickoff details before I can create an artifact:\n\n" + questions,
            status="needs_input",
            trace=trace,
        )
    if composition.brief is None:
        trace["generation_mode"] = "deterministic_grounded_revision"
        return A2AResponse(result=composition.markdown, status="ok", trace=trace)

    render_result = await anyio.to_thread.run_sync(
        lambda: render_jep_with_inference(composition.brief, _run_renderer)
    )
    trace.update(
        {
            "generation_mode": render_result.generation_mode,
            "render_attempts": render_result.attempts,
            "grounding_findings": list(render_result.findings),
        }
    )
    return A2AResponse(result=render_result.markdown, status="ok", trace=trace)


def _run_renderer(prompt: str, system_message: str) -> str:
    return run_inference(
        prompt,
        endpoint=str(_main_inference.get("service_endpoint") or ""),
        model_id=_model_id,
        compartment_id=str(_main_config.get("compartment_id") or ""),
        max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=5000)),
        temperature=0.3,
        top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
        top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
        system_message=system_message,
    )


app = make_agent_app(card, handle)
