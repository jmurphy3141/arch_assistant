from __future__ import annotations

import json
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
_CASE_STUDIES = _ROOT / "agent" / "standards" / "oracle_customer_case_studies.json"


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


def _format_case_studies(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    studies = data.get("case_studies", [])
    lines = [
        "\n\n## Oracle Customer Outcomes Reference",
        "Use these published outcomes when writing POV business impact sections. Cite the company and outcome; do not invent metrics.\n",
        "| Company | Industry | Use Case | Quantified Outcome |",
        "|---------|----------|----------|--------------------|",
    ]
    for s in studies:
        outcomes = s.get("quantified_outcomes", {})
        key = (
            outcomes.get("cost_savings_percent")
            or outcomes.get("cost_savings_annual_usd")
            or outcomes.get("performance")
            or "—"
        )
        if isinstance(key, str) and len(key) > 70:
            key = key[:67] + "..."
        lines.append(f"| {s.get('company','?')} | {s.get('industry','?')} | {s.get('use_case_type','?')} | {key} |")
    lines.append("\nDo not invent metrics. If no matching case study exists, frame outcomes as 'expected based on workload profile'.")
    return "\n".join(lines)


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = _SYSTEM_PROMPT.read_text(encoding="utf-8") + _format_case_studies(_CASE_STUDIES)


card = AgentCard(
    name="pov",
    description="Writes an OCI Point-of-View document for a customer engagement.",
    inputs={
        "required": ["task"],
        "optional": ["customer_name", "engagement_context", "prior_version", "trace_id"],
    },
    output="POV document in Markdown",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    parts = [req.task]
    prior = context.get("prior_version")
    if prior:
        parts.append(
            "Prior draft to update:\n"
            f"{prior}\n\nRevise the prior draft using the current brief."
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
            max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=4000)),
            temperature=float(_first_present(_agent_llm.get("temperature"), _main_inference.get("temperature"), default=0.7)),
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
    )
    return A2AResponse(result=text, status="ok", trace={"agent": card.name, "trace_id": req.trace_id})


app = make_agent_app(card, handle)
