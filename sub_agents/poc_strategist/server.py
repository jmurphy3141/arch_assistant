from __future__ import annotations

import json as _json_mod
import re as _re
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
    data = _json_mod.loads(path.read_text(encoding="utf-8"))
    studies = data.get("case_studies", [])
    lines = [
        "\n\n## Oracle Customer Case Study Reference",
        "Use these for relevance scoring and wow-moment evidence. Metrics are as published by Oracle.\n",
        "| Company | Industry | Use Case | Key Outcome |",
        "|---------|----------|----------|-------------|",
    ]
    for s in studies:
        outcomes = s.get("quantified_outcomes", {})
        key = (
            outcomes.get("cost_savings_percent")
            or outcomes.get("cost_savings_annual_usd")
            or outcomes.get("performance")
            or "—"
        )
        if isinstance(key, str) and len(key) > 60:
            key = key[:57] + "..."
        lines.append(f"| {s.get('company','?')} | {s.get('industry','?')} | {s.get('use_case_type','?')} | {key} |")
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    text = text.strip()
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence = _re.match(r'^```(?:json)?\s*([\s\S]*?)\s*```$', text, _re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        _json_mod.loads(text)
        return text
    except _json_mod.JSONDecodeError:
        pass
    match = _re.search(r'\{.*\}', text, _re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            _json_mod.loads(candidate)
            return candidate
        except _json_mod.JSONDecodeError:
            pass
    return text


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = _SYSTEM_PROMPT.read_text(encoding="utf-8") + _format_case_studies(_CASE_STUDIES)


card = AgentCard(
    name="poc_strategist",
    description="Explores one scored OCI POC option for a customer engagement angle.",
    inputs={
        "required": ["task"],
        "optional": ["angle", "customer_context", "engagement_context", "trace_id"],
    },
    output="One scored POC option as raw JSON",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    angle = str(context.get("angle") or "").strip()
    customer_context = context.get("customer_context")
    parts = [req.task]
    if angle:
        parts.append(f"Exploration angle: {angle}")
    if customer_context:
        parts.append(f"Customer context:\n{customer_context}")
    # Hard format enforcement at end of prompt — Cohere follows end-of-prompt
    # instructions more reliably than system-message instructions.
    parts.append(
        "YOU ARE AN OCI SOLUTIONS ENGINEER. Generate ONE OCI POC option for Oracle Cloud "
        "Infrastructure. Do NOT mention AWS, Azure, GCP, or Bedrock as the solution — "
        "this is an OCI-only POC.\n\n"
        "OUTPUT RULE: Your ENTIRE response must be a single JSON object and nothing else. "
        "No prose. No markdown. No code fences. No explanation before or after. "
        "Start your response with { and end with }.\n\n"
        'Required fields: {"option_name": "string", "relevance_score": integer 1-10, '
        '"executability_hours": integer, "cost_effectiveness": "string", '
        '"security_highlights": ["string"], "wow_moment": "string", '
        '"demo_script_summary": "string", "oci_services": ["string"]}'
    )
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


async def _call_inference(prompt: str) -> str:
    return await anyio.to_thread.run_sync(
        lambda: run_inference(
            prompt,
            endpoint=str(_main_inference.get("service_endpoint") or ""),
            model_id=_model_id,
            compartment_id=str(_main_config.get("compartment_id") or ""),
            max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=2048)),
            temperature=0.1,
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
    )


async def handle(req: A2ARequest) -> A2AResponse:
    prompt = _build_prompt(req)
    text = await _call_inference(prompt)
    text = _extract_json(text)

    # If response is still not JSON, retry once with a stripped-down prompt
    try:
        _json_mod.loads(text)
    except _json_mod.JSONDecodeError:
        retry_prompt = (
            f"{prompt}\n\n"
            "RETRY: Your previous response was not valid JSON. "
            "Output ONLY the JSON object. Start with {{ and end with }}. No other text."
        )
        text = await _call_inference(retry_prompt)
        text = _extract_json(text)

    return A2AResponse(result=text, status="ok", trace={"agent": card.name, "trace_id": req.trace_id})


app = make_agent_app(card, handle)
