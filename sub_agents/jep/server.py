from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import yaml

from agent.jep_composer import compose_jep
from agent.llm_inference_client import run_inference  # compatibility patch point; never called
from sub_agents.base import make_agent_app
from sub_agents.grounding import (
    grounding_prompt,
    grounding_trace,
    input_grounding_missing,
    needs_input_message,
    normalize_engagement_context,
    output_grounding_missing,
)
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
    name="jep",
    description="Writes an OCI Joint Engagement Plan document.",
    inputs={
        "required": ["task", "engagement_context"],
        "optional": ["customer_name", "prior_version", "feedback", "trace_id"],
    },
    output="JEP document in Markdown",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = normalize_engagement_context(req.engagement_context)
    parts: list[str] = [grounding_prompt(context)]
    customer_name = context.get("customer_name")
    if customer_name:
        parts.append(f"Customer: {customer_name}")
    parts.append(req.task)
    feedback = context.get("feedback")
    prior = context.get("prior_version")
    prior_key = context.get("prior_version_key")
    prior_number = context.get("prior_version_number")
    engagement_summary = str(context.get("engagement_context_summary") or "").strip()
    memory_summary = str(context.get("archie_memory_summary") or "").strip()
    artifact_context = context.get("artifact_context")
    resolved_decisions = context.get("resolved_decisions")
    expected_criteria = int(context.get("expected_success_criteria_count") or 3)
    if engagement_summary:
        parts.append(f"Persisted engagement context:\n{engagement_summary}")
    if memory_summary:
        parts.append(f"Archie memory summary:\n{memory_summary}")
    if isinstance(artifact_context, dict) and artifact_context:
        parts.append(
            "Related artifact context:\n"
            "```json\n"
            f"{json.dumps(artifact_context, ensure_ascii=True, sort_keys=True, indent=2)}\n"
            "```"
        )
    if resolved_decisions:
        resolved_text = (
            resolved_decisions
            if isinstance(resolved_decisions, str)
            else json.dumps(resolved_decisions, ensure_ascii=True, sort_keys=True, indent=2)
        )
        parts.append(f"Resolved decisions:\n{resolved_text}")
    if feedback:
        parts.append(f"Revision feedback:\n{feedback}")
    if prior:
        label_parts = ["Prior JEP version to revise"]
        if prior_number:
            label_parts.append(f"v{prior_number}")
        if prior_key:
            label_parts.append(f"({prior_key})")
        label = " ".join(str(part) for part in label_parts)
        parts.append(
            f"{label}:\n"
            "```markdown\n"
            f"{prior}\n"
            "```"
        )
    if feedback or prior:
        parts.append(
            "Treat this call as a revision request. Use the prior JEP as the base, "
            "preserve customer-specific facts, and replace any non-C3E or self-referential "
            "revision text with an executable Joint Execution Plan. Do not invent a new POC, "
            "customer scenario, workload, or architecture that is not present in the prior JEP "
            "or persisted engagement context."
        )
    parts.append(
        "JEP Writer review gate: return exactly the C3E JEP document in Markdown with "
        "the 10 canonical SE sections, exactly Phase 1 Assessment / Phase 2 Build / "
        f"Phase 3 Validate, exactly {expected_criteria} grounded numeric SMART success "
        "criteria (three by default; a lower count is allowed only when supplied from the "
        "authoritative selected POC), at least 3 "
        "customer-specific risks, criterion-linked test cases, participants, deliverables, "
        "logistics, and a Phase 3 go/no-go sign-off and fallback."
    )
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


async def handle(req: A2ARequest) -> A2AResponse:
    raw_context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    context = normalize_engagement_context(raw_context)
    grounding_missing = input_grounding_missing(
        raw_context, require_customer_name=True
    )
    if raw_context and not _has_workload_grounding(req.task, context):
        grounding_missing.append("workload")
    if grounding_missing:
        return A2AResponse(
            result=needs_input_message(grounding_missing),
            status="needs_input",
            trace={"agent": card.name, "trace_id": req.trace_id, **grounding_trace(context, grounding_missing)},
        )
    composition = compose_jep(req.task, context)
    trace = {
        "agent": card.name,
        "trace_id": req.trace_id,
        "generation_mode": "deterministic_grounded_brief",
        "missing_fields": list(composition.missing_fields),
        **grounding_trace(context, []),
    }
    if composition.status != "ok":
        questions = "\n".join(f"- {question}" for question in composition.questions)
        return A2AResponse(
            result="I need the following JEP kickoff details before I can create an artifact:\n\n" + questions,
            status="needs_input",
            trace=trace,
        )
    grounding_missing = output_grounding_missing(
        raw_context, composition.markdown, require_customer_name=True
    )
    if grounding_missing:
        return A2AResponse(
            result=needs_input_message(grounding_missing),
            status="needs_input",
            trace={**trace, **grounding_trace(context, grounding_missing)},
        )
    return A2AResponse(result=composition.markdown, status="ok", trace=trace)


def _has_workload_grounding(task: str, context: dict[str, Any]) -> bool:
    facts = context.get("facts") if isinstance(context.get("facts"), dict) else {}
    fact_keys = {str(key).casefold() for key in facts}
    if fact_keys & {"workload", "workloads", "platform", "current_platform", "application"}:
        return True
    text = f"{task} {json.dumps(facts, ensure_ascii=True, sort_keys=True)}"
    return bool(
        re.search(
            r"\b(?:workload|application|app|platform|portal|database|service|system|"
            r"three[- ]tier|web tier|claims)\b",
            text,
            re.IGNORECASE,
        )
    )


app = make_agent_app(card, handle)
