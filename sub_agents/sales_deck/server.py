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
_SLA_REFERENCE = _ROOT / "agent" / "standards" / "oci_service_slas.json"


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
            or "-"
        )
        if isinstance(key, str) and len(key) > 70:
            key = key[:67] + "..."
        lines.append(f"| {s.get('company','?')} | {s.get('industry','?')} | {s.get('use_case_type','?')} | {key} |")
    lines.append("\nDo not invent metrics. If no matching case study exists, frame outcomes as 'expected based on workload profile'.")
    return "\n".join(lines)


def _format_sla_reference(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    services = data.get("services", [])
    lines = [
        "\n\n## OCI SLA Reference",
        "Cite these figures in Why OCI or cost slides. Always note 'verify against current Oracle Pillar Document'.\n",
        "| Service | SLA | Multi-AD Note |",
        "|---------|-----|---------------|",
    ]
    for service in services:
        service_name = str(service.get("service") or "?")
        if len(service_name) > 40:
            service_name = service_name[:37] + "..."

        sla_pct = service.get("sla_pct")
        sla = f"{sla_pct}%" if sla_pct is not None else "SLO only"

        multi_ad_note = str(service.get("multi_ad_note") or "-")
        if len(multi_ad_note) > 60:
            multi_ad_note = multi_ad_note[:57] + "..."

        lines.append(f"| {service_name} | {sla} | {multi_ad_note} |")
    return "\n".join(lines)


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = (
    _SYSTEM_PROMPT.read_text(encoding="utf-8")
    + _format_case_studies(_CASE_STUDIES)
    + _format_sla_reference(_SLA_REFERENCE)
)


card = AgentCard(
    name="sales_deck",
    description="OCI customer-facing sales deck and PowerPoint slide specialist",
    inputs={
        "required": ["task"],
        "optional": ["customer_context", "existing_artifacts", "deck_type", "engagement_context", "trace_id"],
    },
    output="structured JSON slide specification for PowerPoint rendering",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    parts = [req.task]
    architect_brief = context.get("architect_brief") or {}
    if architect_brief:
        parts.append(f"Architect brief:\n{architect_brief}")
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


async def handle(req: A2ARequest) -> A2AResponse:
    prompt = _build_prompt(req)
    text = await anyio.to_thread.run_sync(
        lambda: run_inference(
            prompt,
            endpoint=str(_main_inference.get("service_endpoint") or ""),
            model_id=_model_id,
            compartment_id=str(_main_config.get("compartment_id") or ""),
            max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=8000)),
            temperature=float(_first_present(_agent_llm.get("temperature"), _main_inference.get("temperature"), default=0.6)),
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
    )
    return A2AResponse(result=text, status="ok", trace={"agent": card.name, "trace_id": req.trace_id})


app = make_agent_app(card, handle)
