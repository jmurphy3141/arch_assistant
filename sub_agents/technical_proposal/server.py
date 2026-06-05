from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import yaml

from agent.llm_inference_client import run_inference
from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"
_SYSTEM_PROMPT = _HERE / "system_prompt.md"


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
_system_message = _SYSTEM_PROMPT.read_text(encoding="utf-8")


card = AgentCard(
    name="technical_proposal",
    description="Writes an OCI Technical Proposal document (C3E Design→Win phase).",
    inputs={
        "required": ["task"],
        "optional": [
            "customer_name",
            "engagement_context",
            "architecture_summary",
            "bom_summary",
            "poc_results",
            "transition_plan",
            "prior_version",
            "trace_id",
        ],
    },
    output="Technical Proposal document in Markdown",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    parts = [req.task]
    if context.get("architecture_summary"):
        parts.append(f"Future state architecture:\n{context['architecture_summary']}")
    if context.get("bom_summary"):
        parts.append(f"BOM summary:\n{context['bom_summary']}")
    if context.get("poc_results"):
        parts.append(f"POC / JEP results:\n{context['poc_results']}")
    if context.get("transition_plan"):
        parts.append(f"Transition plan:\n{context['transition_plan']}")
    if context.get("diagram_key"):
        parts.append(f"Architecture diagram artifact key: {context['diagram_key']}")
    prior = context.get("prior_version")
    if prior:
        parts.append(
            f"Prior draft to update:\n{prior}\n\n"
            "Revise only the sections explicitly requested. Preserve all other sections."
        )
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


async def handle(req: A2ARequest) -> A2AResponse:
    prompt = _build_prompt(req)
    text = await anyio.to_thread.run_sync(
        lambda: run_inference(
            prompt,
            endpoint=str(_main_inference.get("service_endpoint") or ""),
            model_id=_model_id,
            compartment_id=str(_main_config.get("compartment_id") or ""),
            max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=6000)),
            temperature=float(_first_present(_agent_llm.get("temperature"), _main_inference.get("temperature"), default=0.6)),
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
    )
    return A2AResponse(result=text, status="ok", trace={"agent": card.name, "trace_id": req.trace_id})


app = make_agent_app(card, handle)
