"""
archie_memory.py
----------------
Context assembly, memory enforcement, BOM intent detection,
infrastructure profiling, and specialist-question management for Archie.

Called by agent.archie_loop. Specialist-question retries dispatch back
through archie_loop._execute_tool via a late import to avoid circular
module initialization.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import agent.context_store as context_store
import agent.decision_context as decision_context_builder
import agent.document_store as document_store
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

_MEMORY_CONTRACT_TOOLS = {"generate_diagram", "generate_bom", "generate_pov", "generate_jep", "generate_waf", "generate_terraform"}

_BOM_DEICTIC_MARKERS: tuple[str, ...] = (
    "for this", "from this", "use this", "use that", "use that information", "use that info", "that information", "that info", "for that", "from that", "from the notes", "from saved notes", "from the conversation", "what it has", "this diagram", "that diagram", "previous diagram", "latest diagram",
)

_INJECTED_GUIDANCE_BLOCKS: tuple[tuple[str, str], ...] = (
    ("[Decision Context]", "[End Decision Context]"), ("[Archie Canonical Memory]", "[End Archie Canonical Memory]"),
    ("[Skill Injection Contract]", "[End Skill Injection Contract]"), ("[Injected Skill Guidance]", "[End Skill Guidance]"),
)

_DIAGRAM_COMPONENT_MARKERS = (
    "oke", "kubernetes", "container engine", "database", "db", "load balancer", "lb", "waf",
    "object storage", "bucket", "bastion", "web", "app tier", "data tier", "private subnet",
    "public subnet", "vcn", "subnet", "dr", "disaster recovery", "multi-region", "multi region",
)


async def _execute_tool(*args: Any, **kwargs: Any) -> tuple[str, str, dict[str, Any]]:
    import agent.archie_loop as archie_loop

    return await archie_loop._execute_tool(*args, **kwargs)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_context_summary_for_skills(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
) -> str:
    try:
        ctx = context_store.read_context(store, customer_id, customer_name)
        return context_store.build_context_summary(ctx)
    except Exception as exc:
        logger.warning("Failed to build context summary for skill checks: %s", exc)
        return ""

def _diagram_request_has_topology_intent(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _DIAGRAM_COMPONENT_MARKERS)

def _prepare_bom_tool_args(
    *,
    args: dict[str, Any] | None,
    user_message: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(args or {})
    prompt = str(payload.get("prompt", "") or "").strip() or str(user_message or "").strip()
    payload["prompt"] = prompt or "Generate a BOM from current request context."
    payload["_bom_context_source"] = str(payload.get("_bom_context_source", "") or "direct_request")
    if isinstance(context, dict):
        archie = context_store.get_archie_state(context)
        reusable: list[str] = []
        facts_summary = str(archie.get("facts_summary", "") or "").strip()
        if facts_summary:
            reusable.append(f"- accumulated_client_facts: {facts_summary}")
        reusable.extend(_infrastructure_profile_context_lines(context))
        constraints = dict(archie.get("latest_approved_constraints", {}) or {})
        if str(constraints.get("region", "") or "").strip():
            reusable.append(f"- constraints.region: {constraints.get('region')}")
        seen: set[str] = set()
        for item in reversed(archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []):
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("question_id", "") or item.get("id", "") or "").strip()
            if question_id not in {"components.scope", "workload.components", "regions.mode", "region.mode", "topology.scope", "regions.count"}:
                continue
            canonical = "components.scope" if question_id in {"components.scope", "workload.components"} else "regions.mode"
            answer = _coerce_specialist_answer(canonical, str(item.get("final_answer", "") or item.get("suggested_answer", "") or ""))
            if answer and canonical not in seen:
                reusable.append(f"- {canonical}: {answer}")
                seen.add(canonical)
        if reusable:
            payload["prompt"] = f"{payload['prompt']}\n\n[Archie Reusable Approved Inputs]\n" + "\n".join(reusable) + "\n[End Archie Reusable Approved Inputs]"
            prompt = payload["prompt"]

    if bool(payload.get("_bom_grounded_from_context")):
        return payload

    if _is_bom_revision_request(prompt, user_message, context) or (
        _mentions_bom_work_product(user_message) and _latest_bom_fact_mismatches(context)
    ):
        payload["prompt"] = _build_bom_revision_prompt(
            prompt=prompt,
            user_message=user_message,
            context=context,
            decision_context=decision_context,
        )
        payload["_bom_context_source"] = "bom_revision"
        payload["_bom_grounded_from_context"] = True
        payload["_bom_grounding"] = "revision-grounded"
        return payload

    combined = " ".join(part.strip().lower() for part in (user_message, prompt) if str(part).strip())
    is_deictic = (
        bool(combined)
        and any(marker in combined for marker in ("bom", "bill of materials", "cost", "pricing"))
        and any(marker in combined for marker in _BOM_DEICTIC_MARKERS)
    )
    if not is_deictic:
        return payload

    diagram_ctx = dict(((context or {}).get("agents", {}) or {}).get("diagram", {}) or {})
    diagram_has_context = bool(str(diagram_ctx.get("diagram_key", "") or "").strip()) and (
        int(diagram_ctx.get("node_count", 0) or 0) > 0
        or any(str(diagram_ctx.get(key, "") or "").strip() for key in ("deployment_summary", "spec_summary", "reference_family", "decision_context_summary", "summary"))
        or bool(list(diagram_ctx.get("assumptions_used", []) or []))
    )
    if diagram_has_context:
        lines = [
            "Generate BOM for the latest OCI architecture diagram.",
            "Treat this as a best-effort OCI BOM draft/finalization request, not a generic clarification-only question.",
            "Use existing BOM draft defaults for missing numeric sizing and surface assumptions or checkpoint items instead of refusing the draft.",
        ]
        cleaned_prompt = _strip_injected_guidance_blocks(prompt).strip()
        if cleaned_prompt:
            lines.append(f"User follow-up: {cleaned_prompt}")
        lines.extend(("[Latest Diagram Context]", f"- diagram_key: {diagram_ctx.get('diagram_key', '')}"))
        scope = ", ".join(
            part
            for part in (
                str(diagram_ctx.get("deployment_summary", "") or "").strip(),
                f"reference family={diagram_ctx.get('reference_family')}" if str(diagram_ctx.get("reference_family", "") or "").strip() else "",
                f"node_count={int(diagram_ctx.get('node_count', 0) or 0)}" if int(diagram_ctx.get("node_count", 0) or 0) > 0 else "",
                str(diagram_ctx.get("spec_summary", "") or "").strip(),
            )
            if part
        )
        if scope:
            lines.append(f"- scope_summary: {scope}")
        if str(diagram_ctx.get("decision_context_summary", "") or "").strip():
            lines.append(f"- prior_decision_context: {diagram_ctx.get('decision_context_summary')}")
        lines.append("[End Latest Diagram Context]")
        payload["prompt"] = "\n".join(lines).strip()
        payload["_bom_context_source"] = "latest_diagram"
        payload["_bom_grounded_from_context"] = True
        return payload

    archie_context = _build_archie_specialist_context(context, decision_context=decision_context)
    archie_lower = archie_context.lower()
    has_compute = "ocpu" in archie_lower or re.search(r"\b\d+(?:\.\d+)?\s*(?:cpu|cores?)\b", archie_lower) is not None
    has_memory = "ram" in archie_lower or "memory" in archie_lower
    has_storage = (
        "storage" in archie_lower
        or "block volume" in archie_lower
        or re.search(r"\b\d+(?:\.\d+)?\s*tb\b", archie_lower) is not None
    )
    if has_compute and has_memory and has_storage:
        lines = [
            "Generate BOM from the persisted customer notes and conversation context.",
            "Use explicit sizing values from the context; do not fall back to default sizing when OCPU, RAM, or storage are present.",
        ]
        cleaned_prompt = _strip_injected_guidance_blocks(prompt).strip()
        if cleaned_prompt:
            lines.append(f"User follow-up: {cleaned_prompt}")
        lines.extend(("[Persisted Customer Context]", archie_context))
        current_decision_context = dict(decision_context or {})
        constraints = dict(current_decision_context.get("constraints", {}) or {})
        if (
            str(current_decision_context.get("goal", "") or "").strip()
            or list(current_decision_context.get("success_criteria", []) or [])
            or any(value not in (None, "", [], {}) for value in constraints.values())
        ):
            lines.append(f"Current decision context: {decision_context_builder.summarize_decision_context(current_decision_context)}")
        lines.append("[End Persisted Customer Context]")
        payload["prompt"] = "\n".join(lines).strip()
        payload["_bom_context_source"] = "persisted_notes"
        payload["_bom_grounded_from_context"] = True
        return payload

    payload["_bom_direct_reply"] = (
        "I can build the BOM, but `this` is not grounded to a prior diagram or workload yet.\n"
        "Please share the workload or diagram context plus rough sizing for OCPU, memory, storage, "
        "and any load balancer, database, or Object Storage requirements."
    )
    payload["_bom_context_source"] = "unresolved_followup"
    payload["_bom_grounded_from_context"] = False
    return payload

def _bom_followup_should_hydrate_from_context(
    *,
    prompt: str,
    user_message: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> bool:
    prepared = _prepare_bom_tool_args(args={"prompt": prompt}, user_message=user_message, context=context, decision_context=decision_context)
    return bool(prepared.get("_bom_grounded_from_context")) and prepared.get("_bom_context_source") in {"latest_diagram", "persisted_notes"}

def _is_bom_revision_request(prompt: str, user_message: str, context: dict[str, Any] | None) -> bool:
    if not _mentions_bom_work_product(" ".join([str(prompt or ""), str(user_message or "")])):
        return False
    if _is_pure_download_or_link_request(user_message):
        return False
    msg = f" {str(user_message or prompt or '').lower()} "
    revision_markers = (
        " feedback", " pushback", " customer asked", " customer requested", " asked for", " only have",
        " you have", " missing", " too low", " too small", " should have", " should be", " need more",
        " needs more", " new bom", " new xlsx", " new workbook", " new version", " updated bom",
        " updated xlsx", " update bom", " update the bom", " update xlsx", " current bom", " current xlsx",
        " regenerate", " rebuild", " revise", " revision", " incorrect", " wrong", " not correct",
        " fix the bom", " replace the bom",
    )
    if any(marker in msg for marker in revision_markers):
        return True
    latest = context_store.latest_bom_work_product(context or {}) if isinstance(context, dict) else None
    return latest is not None and _latest_bom_fact_mismatches(context)

def _mentions_bom_work_product(text: str) -> bool:
    msg = str(text or "").lower()
    return any(
        marker in msg
        for marker in (
            "bom", "bill of materials", "xlsx", "xlxs", "xlsc", "excel", "spreadsheet", "workbook", "pricing", "priced", "sku",
        )
    )

def _is_pure_download_or_link_request(user_message: str) -> bool:
    msg = f" {str(user_message or '').lower()} "
    if not any(marker in msg for marker in ("download", "share", "link", "url", "presigned", "pre-signed")):
        return False
    revision_markers = (
        " new ", " updated ", " update ", " regenerate", " rebuild", " revise", " revision", " incorrect",
        " wrong", " not correct", " fix ", " replace ", " current bom", " current xlsx", " current workbook",
    )
    if any(marker in msg for marker in revision_markers):
        return False
    generation_verbs = ("build", "create", "generate", "draft", "make")
    if any(verb in msg for verb in generation_verbs) and _mentions_bom_work_product(user_message):
        return False
    return True

def _extract_turn_corrections(user_message: str) -> list[str]:
    text = str(user_message or "").strip()
    if not text:
        return []
    corrections: list[str] = []
    for pattern, label in (
        (r"\b\d+(?:[.,]\d+)?\s*(?:tb|tib)\s+(?:of\s+)?storage\b", "storage"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:gb|gib)\s+(?:of\s+)?(?:object\s+)?storage\b", "storage"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:tb|tib)\s+(?:of\s+)?memory\b", "memory"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:gb|gib)\s+(?:of\s+)?(?:ram|memory)\b", "memory"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:ocpu|ocpus|cpu|cpus|cores?)\b", "compute"),
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            item = f"{label}: {value}"
            if item not in corrections:
                corrections.append(item)
    return corrections[:12]

def _build_bom_revision_prompt(
    *,
    prompt: str,
    user_message: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> str:
    archie = context_store.get_archie_state(context or {}) if isinstance(context, dict) else {}
    facts = archie.get("client_facts", {}) if isinstance(archie.get("client_facts"), dict) else {}
    facts_summary = str(archie.get("facts_summary", "") or "").strip()
    latest = context_store.latest_bom_work_product(context or {}) if isinstance(context, dict) else None
    mismatches = _latest_bom_fact_mismatches(context, as_list=True)
    lines = [
        "Revise the current BOM/XLSX work product from accumulated client facts.",
        "Treat newer client facts as authoritative over the prior BOM baseline.",
        "Update missing or incorrect items and return a final structured bom_payload.",
    ]
    cleaned_prompt = _strip_injected_guidance_blocks(prompt or user_message).strip()
    if cleaned_prompt:
        lines.append(f"User revision request: {cleaned_prompt}")
    turn_corrections = _extract_turn_corrections(user_message or prompt)
    if turn_corrections:
        lines.append("[Corrected Facts From Current Turn]")
        lines.extend(f"- {item}" for item in turn_corrections)
        lines.append("[End Corrected Facts From Current Turn]")
    if facts_summary:
        lines.append(f"Facts summary: {facts_summary}")
    if facts:
        lines.append("[Accumulated Client Facts]")
        lines.append(json.dumps(facts, ensure_ascii=True, sort_keys=True, indent=2)[:4000])
        lines.append("[End Accumulated Client Facts]")
    archie_context = _build_archie_specialist_context(context, decision_context=decision_context)
    if archie_context:
        lines.append("[Current Archie Context]")
        lines.append(archie_context)
        lines.append("[End Current Archie Context]")
    if latest:
        baseline = latest.get("baseline", {}) if isinstance(latest.get("baseline"), dict) else {}
        lines.append("[Prior BOM Baseline]")
        lines.append(json.dumps(baseline, ensure_ascii=True, sort_keys=True, indent=2)[:4000])
        lines.append("[End Prior BOM Baseline]")
    if mismatches:
        lines.append("Explicit deltas/mismatches to correct:")
        lines.extend(f"- {item}" for item in mismatches)
    current_decision_context = dict(decision_context or {})
    constraints = dict(current_decision_context.get("constraints", {}) or {})
    if (
        str(current_decision_context.get("goal", "") or "").strip()
        or list(current_decision_context.get("success_criteria", []) or [])
        or any(value not in (None, "", [], {}) for value in constraints.values())
    ):
        lines.append(
            f"Current decision context: {decision_context_builder.summarize_decision_context(current_decision_context)}"
        )
    return "\n".join(lines).strip()

def _latest_bom_fact_mismatches(context: dict[str, Any] | None, *, as_list: bool = False) -> list[str] | bool:
    if not isinstance(context, dict):
        return [] if as_list else False
    archie = context_store.get_archie_state(context)
    facts = archie.get("client_facts", {}) if isinstance(archie.get("client_facts"), dict) else {}
    latest = context_store.latest_bom_work_product(context)
    if not facts or not latest:
        return [] if as_list else False
    baseline = latest.get("baseline", {}) if isinstance(latest.get("baseline"), dict) else {}
    searchable = json.dumps(baseline, ensure_ascii=True, sort_keys=True).lower()
    mismatches: list[str] = []

    fact_region = str(facts.get("region", "") or facts.get("geography", "") or "").strip()
    baseline_region = str(baseline.get("region", "") or "").strip()
    if fact_region and baseline_region and fact_region.lower() != baseline_region.lower():
        mismatches.append(f"region changed from {baseline_region} to {fact_region}")
    if fact_region and not baseline_region:
        mismatches.append(f"region/geography fact is {fact_region} but prior BOM has no region baseline")

    platform = str(facts.get("platform", "") or "").strip()
    if platform and all(token not in searchable for token in ("vmware", "vxrail", "esxi")):
        mismatches.append(f"platform is {platform}; prior BOM baseline does not reflect VMware/VxRail source context")

    security = facts.get("security", {}) if isinstance(facts.get("security"), dict) else {}
    if security.get("waf") and "waf" not in searchable and "web application firewall" not in searchable:
        mismatches.append("WAF is required in current facts but missing from prior BOM baseline")
    if security.get("bastion") and "bastion" not in searchable:
        mismatches.append("bastion is required in current facts but missing from prior BOM baseline")

    connectivity = facts.get("connectivity", {}) if isinstance(facts.get("connectivity"), dict) else {}
    for key, label in (("mpls", "MPLS"), ("sd_wan", "SD-WAN"), ("fastconnect", "FastConnect"), ("vpn", "VPN")):
        if connectivity.get(key) and label.lower() not in searchable.replace("_", "-"):
            mismatches.append(f"{label} connectivity is in current facts but not represented in prior BOM baseline")

    dr = facts.get("dr", {}) if isinstance(facts.get("dr"), dict) else {}
    if dr and all(token not in searchable for token in ("dr", "disaster", "restore", "backup")):
        mismatches.append("DR/restore requirements are in current facts but missing from prior BOM baseline")

    exclusions = facts.get("scope_exclusions") if isinstance(facts.get("scope_exclusions"), list) else []
    if exclusions:
        mismatches.append("scope exclusions to honor: " + ", ".join(str(item) for item in exclusions))
    return mismatches if as_list else bool(mismatches)

def _summarize_note_text(note_text: str, *, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", str(note_text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."

def _record_saved_note_context(
    *,
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    note_key: str,
    note_text: str,
    decision_context: dict[str, Any] | None,
) -> None:
    context = context_store.read_context(store, customer_id, customer_name)
    note_summary = _summarize_note_text(note_text)
    archie = context_store.get_archie_state(context)
    prior_summary = str(archie.get("engagement_summary", "") or "").strip()
    if note_summary and note_summary not in prior_summary:
        merged = f"{prior_summary} {note_summary}".strip() if prior_summary else note_summary
        context_store.set_archie_engagement_summary(context, _summarize_note_text(merged, limit=480), note_summary=note_summary)
    elif note_summary:
        context_store.set_archie_engagement_summary(context, prior_summary or note_summary, note_summary=note_summary)
    _record_infrastructure_profile_if_present(context, note_text)
    context_store.set_archie_decision_state(
        context,
        constraints=dict((decision_context or {}).get("constraints", {}) or {}),
        assumptions=list((decision_context or {}).get("assumptions", []) or []),
    )
    context_store.append_change_record(
        context,
        {
            "id": str(uuid.uuid4()),
            "timestamp": _now(),
            "status": "recorded",
            "change_request": note_summary or "Notes saved.",
            "source": "save_notes",
            "note_key": note_key,
            "impacted_tools": [],
        },
    )
    context_store.refresh_archie_memory(context)
    context_store.write_context(store, customer_id, context)

def _build_archie_specialist_context(
    context: dict[str, Any] | None,
    *,
    decision_context: dict[str, Any] | None,
) -> str:
    if not isinstance(context, dict):
        return ""
    archie = context_store.get_archie_state(context)
    lines: list[str] = []
    engagement_summary = str(archie.get("engagement_summary", "") or "").strip()
    if engagement_summary:
        lines.append(f"Engagement summary: {engagement_summary}")
    latest_notes_summary = str(archie.get("latest_notes_summary", "") or "").strip()
    if latest_notes_summary and latest_notes_summary != engagement_summary:
        lines.append(f"Latest notes: {latest_notes_summary}")
    facts_summary = str(archie.get("facts_summary", "") or "").strip()
    if facts_summary:
        lines.append(f"Accumulated client facts: {facts_summary}")
    lines.extend(_infrastructure_profile_context_lines(context))
    resolved = archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []
    if resolved:
        lines.append("Resolved Archie decisions:")
        for item in resolved[-5:]:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("question_id", "") or item.get("id", "") or "question").strip()
            answer = str(item.get("final_answer", "") or item.get("suggested_answer", "") or "").strip()
            if question_id and answer:
                lines.append(f"- {question_id}: {answer}")
    if isinstance(decision_context, dict) and decision_context:
        lines.append(decision_context_builder.summarize_decision_context(decision_context))
    return "\n".join(line for line in lines if str(line).strip()).strip()

def _tool_primary_input_key(tool_name: str) -> str | None:
    if tool_name == "generate_diagram":
        return "bom_text"
    if tool_name == "generate_bom":
        return "prompt"
    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        return "feedback"
    if tool_name == "generate_terraform":
        return "prompt"
    return None

def _clean_tool_user_request(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    user_message: str,
) -> str:
    payload = dict(args or {})
    key = _tool_primary_input_key(tool_name)
    raw = ""
    if key:
        raw = str(payload.get(key, "") or "")
    if not raw.strip():
        raw = str(user_message or "")
    return _strip_injected_guidance_blocks(raw).strip()

def _tool_goal_label(tool_name: str) -> str:
    labels = {
        "generate_diagram": "Architecture diagram",
        "generate_bom": "Bill of materials",
        "generate_pov": "Customer POV draft",
        "generate_jep": "Joint execution plan",
        "generate_waf": "Well-Architected review",
        "generate_terraform": "Terraform draft",
    }
    return labels.get(tool_name, tool_name)

def _build_architect_brief(
    *,
    tool_name: str,
    user_request: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    current_decision_context = dict(decision_context or {})
    assumptions = _merge_assumption_lists(
        list(current_decision_context.get("assumptions", []) or []),
        [],
    )
    missing_inputs = list(current_decision_context.get("missing_inputs", []) or [])
    success_criteria = list(current_decision_context.get("success_criteria", []) or [])
    architect_context = _build_archie_specialist_context(
        context,
        decision_context=current_decision_context,
    )
    return {
        "tool_name": tool_name,
        "goal": str(current_decision_context.get("goal", "") or user_request or _tool_goal_label(tool_name)),
        "deliverable": _tool_goal_label(tool_name),
        "user_request": user_request,
        "user_notes": user_request,
        "architect_context": architect_context,
        "assumptions": assumptions,
        "missing_inputs": missing_inputs,
        "success_criteria": success_criteria,
        "risk_level": str(current_decision_context.get("risk_level", "") or "low"),
        "assumption_mode": bool(current_decision_context.get("assumption_mode", False)),
        "requires_user_confirmation": bool(current_decision_context.get("requires_user_confirmation", False)),
    }

def _render_architect_brief_text(architect_brief: dict[str, Any] | None) -> str:
    brief = dict(architect_brief or {})
    if not brief:
        return ""
    lines = ["[Architect Brief]"]
    goal = str(brief.get("goal", "") or "").strip()
    if goal:
        lines.append(f"Goal: {goal}")
    deliverable = str(brief.get("deliverable", "") or "").strip()
    if deliverable:
        lines.append(f"Deliverable: {deliverable}")
    user_notes = str(brief.get("user_notes", "") or "").strip()
    if user_notes:
        lines.append(f"User notes/request: {user_notes}")
    architect_context = str(brief.get("architect_context", "") or "").strip()
    if architect_context:
        lines.append("Architect context:")
        lines.append(architect_context)
    assumptions = list(brief.get("assumptions", []) or [])
    if assumptions:
        lines.append("Assumptions:")
        lines.extend(
            f"- {item.get('statement', '').strip()} (risk: {item.get('risk', 'low')})"
            for item in assumptions
            if isinstance(item, dict) and str(item.get("statement", "")).strip()
        )
    success_criteria = [str(item).strip() for item in brief.get("success_criteria", []) or [] if str(item).strip()]
    if success_criteria:
        lines.append("Success criteria:")
        lines.extend(f"- {item}" for item in success_criteria)
    missing_inputs = [str(item).strip() for item in brief.get("missing_inputs", []) or [] if str(item).strip()]
    if missing_inputs:
        lines.append("Missing inputs:")
        lines.extend(f"- {item}" for item in missing_inputs)
    lines.append(f"Risk level: {str(brief.get('risk_level', '') or 'low')}")
    lines.append("[End Architect Brief]")
    return "\n".join(lines)

def _append_archie_context_block(text: str, archie_context: str) -> str:
    if not archie_context.strip():
        return text.strip()
    block = f"[Archie Shared Context]\n{archie_context}\n[End Archie Shared Context]"
    if block in text:
        return text.strip()
    return f"{text.strip()}\n\n{block}".strip()

def _enforce_memory_contract_on_tool_args(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(args or {})
    if tool_name not in _MEMORY_CONTRACT_TOOLS:
        return payload

    memory = context_store.get_archie_memory(context or {})
    memory_block = context_store.render_archie_memory(memory)
    memory_hash = context_store.archie_memory_hash(memory)
    payload["_memory_snapshot"] = memory
    payload["_memory_snapshot_hash"] = memory_hash
    payload["_memory_sections_injected"] = [
        key for key in ("client_facts", "architecture_state", "work_products", "assumptions") if isinstance(memory.get(key), dict)
    ]
    payload["_memory_facts_used"] = _memory_facts_used(memory)
    payload["_memory_unresolved_facts"] = list(((memory.get("assumptions", {}) or {}).get("unresolved_gaps", []) or []))
    payload["_memory_latest_baseline_used"] = _memory_latest_baseline_used(memory, tool_name)

    primary_key = _tool_primary_input_key(tool_name)
    if primary_key:
        current = _strip_injected_guidance_blocks(str(payload.get(primary_key, "") or "")).strip()
        if "[Archie Canonical Memory]" not in current:
            current = f"{current}\n\n{memory_block}".strip() if current else memory_block
        payload[primary_key] = current
    return payload

def _memory_facts_used(memory: dict[str, Any]) -> list[str]:
    facts = memory.get("client_facts", {}) if isinstance(memory.get("client_facts"), dict) else {}
    used: list[str] = []
    for key in ("region_geography", "platform", "sizing", "workloads", "connectivity", "dr", "security", "exclusions"):
        value = facts.get(key)
        if value not in (None, "", [], {}):
            used.append(key)
    return used

def _memory_latest_baseline_used(memory: dict[str, Any], tool_name: str) -> dict[str, Any]:
    work_products = memory.get("work_products", {}) if isinstance(memory.get("work_products"), dict) else {}
    if tool_name == "generate_bom":
        return dict(work_products.get("latest_bom", {}) or {})
    if tool_name == "generate_diagram":
        return dict(work_products.get("latest_bom", {}) or work_products.get("latest_diagram", {}) or {})
    if tool_name == "generate_waf":
        return dict(work_products.get("latest_diagram", {}) or {})
    if tool_name == "generate_terraform":
        return dict(work_products.get("latest_diagram", {}) or work_products.get("latest_bom", {}) or {})
    if tool_name == "generate_pov":
        return dict(work_products.get("latest_diagram", {}) or work_products.get("latest_bom", {}) or {})
    if tool_name == "generate_jep":
        return dict(work_products.get("latest_pov", {}) or work_products.get("latest_bom", {}) or {})
    return {}

def _hydrate_tool_args_from_context(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
    user_message: str,
) -> dict[str, Any]:
    payload = dict(args or {})
    clean_request = _clean_tool_user_request(
        tool_name=tool_name,
        args=payload,
        user_message=user_message,
    )
    architect_brief = _build_architect_brief(
        tool_name=tool_name,
        user_request=clean_request,
        context=context,
        decision_context=decision_context,
    )
    payload["_user_request_text"] = clean_request
    payload["_architect_brief"] = architect_brief
    payload["_archie_context_summary"] = str(architect_brief.get("architect_context", "") or "")

    primary_key = _tool_primary_input_key(tool_name)
    if primary_key and clean_request:
        payload[primary_key] = clean_request

    return payload

def _normalize_specialist_question(
    tool_name: str,
    raw_question: Any,
    *,
    index: int,
) -> dict[str, Any] | None:
    if isinstance(raw_question, dict):
        question = str(raw_question.get("question", "") or raw_question.get("prompt", "") or "").strip()
        if not question:
            return None
        raw_id = str(raw_question.get("id", "") or raw_question.get("question_id", "") or "").strip()
        return {
            "question_id": _stable_specialist_question_id(
                tool_name=tool_name,
                question=question,
                raw_id=raw_id,
                index=index,
            ),
            "question": question,
            "blocking": bool(raw_question.get("blocking", True)),
        }
    if isinstance(raw_question, str) and raw_question.strip():
        question = raw_question.strip()
        return {
            "question_id": _stable_specialist_question_id(
                tool_name=tool_name,
                question=question,
                raw_id="",
                index=index,
            ),
            "question": question,
            "blocking": True,
        }
    return None

def _stable_specialist_question_id(
    *,
    tool_name: str,
    question: str,
    raw_id: str,
    index: int,
) -> str:
    fallback = str(raw_id or f"{tool_name}.q{index}").strip()
    normalized = _normalize_specialist_question_id(fallback)
    if tool_name != "generate_bom":
        return fallback
    if normalized and not re.fullmatch(r"(generate\.bom\.)?q\d+", normalized):
        return fallback
    inferred = _infer_bom_question_id(question)
    return inferred or fallback

def _infer_bom_question_id(question: str) -> str:
    lowered = str(question or "").lower()
    if "region" in lowered:
        if any(token in lowered for token in ("single-region", "multi-region", "multi ad", "multi-ad", "topology")):
            return "regions.mode"
        return "constraints.region"
    if "gpu" in lowered or "non-gpu" in lowered or "accelerator" in lowered:
        return "bom.compute.gpu"
    if "ocpu" in lowered:
        return "bom.compute.ocpu"
    if "memory" in lowered or "ram" in lowered:
        return "bom.compute.memory"
    if "vpu" in lowered or "performance unit" in lowered:
        return "bom.storage.vpu"
    if "object storage" in lowered or "bucket" in lowered:
        return "bom.storage.object"
    if "load balancer" in lowered or re.search(r"\blb\b", lowered):
        return "bom.network.load_balancer"
    if "fastconnect" in lowered or "vpn" in lowered or "connectivity" in lowered or "on-prem" in lowered or "on prem" in lowered:
        return "bom.network.connectivity"
    if "budget" in lowered or "monthly" in lowered or "cost cap" in lowered or "spend" in lowered:
        return "bom.budget"
    if "storage" in lowered or "block volume" in lowered or "block" in lowered:
        return "bom.storage.block"
    if any(token in lowered for token in ("sizing", "quantity", "quantities")):
        return "workload.sizing"
    return ""

def _has_architecture_definition(context: dict[str, Any] | None) -> bool:
    agents = (context or {}).get("agents", {}) if isinstance(context, dict) else {}
    diagram = dict((agents or {}).get("diagram", {}) or {})
    return bool(str(diagram.get("diagram_key", "") or "").strip() or str(diagram.get("summary", "") or "").strip())

def _text_has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in markers)

def _pov_has_sufficient_context(
    *,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
    args: dict[str, Any],
    user_message: str,
) -> bool:
    combined = " ".join(
        part
        for part in (
            str(args.get("_user_request_text", "") or ""),
            user_message,
            str((args.get("_architect_brief", {}) or {}).get("architect_context", "") or ""),
            decision_context_builder.summarize_decision_context(decision_context),
            context_store.build_context_summary(context or {}),
        )
        if str(part).strip()
    ).lower()
    business_markers = (
        "industry",
        "customer",
        "business",
        "revenue",
        "outcome",
        "modernize",
        "migration",
        "latency",
        "scale",
        "resilience",
        "retail",
        "healthcare",
        "finance",
    )
    architecture_markers = (
        "oke",
        "kubernetes",
        "database",
        "load balancer",
        "waf",
        "object storage",
        "vcn",
        "private",
        "public",
        "multi-region",
        "autonomous database",
    )
    return _text_has_any_marker(combined, business_markers) and (
        _text_has_any_marker(combined, architecture_markers) or _has_architecture_definition(context)
    )

def _pov_targeted_questions() -> list[dict[str, Any]]:
    return [
        {
            "id": "pov.business_outcomes",
            "question": "What two or three business outcomes should the POV emphasize for this customer?",
            "blocking": True,
        },
        {
            "id": "pov.customer_profile",
            "question": "What customer context should anchor the story: industry, workload type, or strategic initiative?",
            "blocking": True,
        },
        {
            "id": "pov.scope",
            "question": "Should this POV stay high-level executive, or should it call out specific OCI services and deployment scope?",
            "blocking": True,
        },
    ]

def _terraform_scope_is_bounded(
    *,
    context: dict[str, Any] | None,
    args: dict[str, Any],
    decision_context: dict[str, Any] | None,
    user_message: str,
) -> bool:
    return _has_architecture_definition(context) and _terraform_scope_details_are_bounded(
        context=context,
        args=args,
        decision_context=decision_context,
        user_message=user_message,
    )

def _terraform_scope_details_are_bounded(
    *,
    context: dict[str, Any] | None,
    args: dict[str, Any],
    decision_context: dict[str, Any] | None,
    user_message: str,
) -> bool:
    combined = " ".join(
        part
        for part in (
            str(args.get("_user_request_text", "") or ""),
            user_message,
            str((args.get("_architect_brief", {}) or {}).get("architect_context", "") or ""),
            decision_context_builder.summarize_decision_context(decision_context),
            context_store.build_context_summary(context or {}),
        )
        if str(part).strip()
    ).lower()
    module_markers = ("module", "network", "vcn", "oke", "database", "subnet", "load balancer", "waf")
    state_markers = ("remote state", "state backend", "object storage backend", "terraform cloud", "local state")
    security_markers = ("private", "public", "nsg", "security list", "kms", "vault", "iam")
    return (
        _text_has_any_marker(combined, module_markers)
        and _text_has_any_marker(combined, state_markers)
        and _text_has_any_marker(combined, security_markers)
    )

def _terraform_targeted_questions() -> list[dict[str, Any]]:
    return [
        {
            "id": "terraform.module_scope",
            "question": "Which Terraform module boundary should Archie draft first: networking foundation, compute/app tier, database tier, or the full stack?",
            "blocking": True,
        },
        {
            "id": "terraform.state_backend",
            "question": "What should the Terraform state backend be: OCI Object Storage, Terraform Cloud, or local state for a draft?",
            "blocking": True,
        },
        {
            "id": "terraform.security_controls",
            "question": "What security defaults must be enforced in code: private-only networking, specific NSG posture, KMS/Vault usage, or tagging/IAM constraints?",
            "blocking": True,
        },
    ]

def _diagram_has_sufficient_context(
    *,
    context: dict[str, Any] | None,
    args: dict[str, Any],
    user_message: str,
) -> bool:
    if _has_architecture_definition(context):
        return True
    archie = context_store.get_archie_state(context or {})
    if any(
        str(archie.get(key, "") or "").strip()
        for key in ("engagement_summary", "latest_notes_summary")
    ):
        return True
    if list(archie.get("resolved_questions", []) or []):
        return True
    architect_context = str((args.get("_architect_brief", {}) or {}).get("architect_context", "") or "").strip()
    combined = " ".join(
        part
        for part in (
            str(args.get("_user_request_text", "") or ""),
            user_message,
            architect_context,
            context_store.build_context_summary(context or {}),
        )
        if str(part).strip()
    )
    return _diagram_request_has_topology_intent(combined)

def _specialist_question_bundle_from_result(
    *,
    tool_name: str,
    result_summary: str,
    result_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    data = dict(result_data or {})
    raw_questions: list[Any] = []
    if isinstance(data.get("questions"), list):
        raw_questions = list(data.get("questions", []))
    elif isinstance(data.get("blocking_questions"), list):
        raw_questions = list(data.get("blocking_questions", []))
    elif str(data.get("type", "") or "") == "question":
        raw_questions = [str(data.get("reply", "") or result_summary or "").strip()]

    bundle: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_questions, start=1):
        normalized = _normalize_specialist_question(tool_name, raw, index=idx)
        if _should_ignore_specialist_question(tool_name, normalized):
            continue
        if normalized:
            bundle.append(normalized)
    return bundle

def _should_ignore_specialist_question(tool_name: str, question: dict[str, Any] | None) -> bool:
    if tool_name != "generate_bom" or not isinstance(question, dict):
        return False
    question_id = str(question.get("question_id", "") or "").strip()
    return bool(question_id) and question_id in _specialist_question_id_aliases("bom.budget")

def _latest_resolved_answer_map(context: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(context, dict):
        return {}
    archie = context_store.get_archie_state(context)
    resolved = archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []
    latest: dict[str, dict[str, Any]] = {}
    for item in resolved:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id", "") or "").strip()
        if question_id:
            for alias in _specialist_question_id_aliases(question_id):
                latest[alias] = item
    return latest

def _resolved_answer_for_question(
    resolved: dict[str, dict[str, Any]],
    question_id: str,
) -> tuple[dict[str, Any] | None, str]:
    for alias in _specialist_question_id_aliases(question_id):
        prior = resolved.get(alias)
        if not isinstance(prior, dict):
            continue
        answer = str(prior.get("final_answer", "") or prior.get("suggested_answer", "") or "").strip()
        if answer:
            return prior, _coerce_specialist_answer(question_id, answer)
    return None, ""

def _standard_components_scope_answer() -> str:
    return (
        "all BOM-derived and standard reference architecture components: VCN, public/private subnets, "
        "load balancer, application compute or OKE, database, Object Storage, DRG/connectivity, "
        "WAF/security controls, Vault/KMS, logging, and monitoring"
    )

def _coerce_specialist_answer(question_id: str, answer: str) -> str:
    qid = _normalize_specialist_question_id(question_id)
    cleaned = str(answer or "").strip()
    if qid in {"components.scope", "workload.components"} and cleaned.lower() == "all":
        return _standard_components_scope_answer()
    topology_aliases = {"regions.mode", "region.mode", "topology.scope", "regions.count"}
    if qid in topology_aliases:
        lowered = cleaned.lower()
        if qid == "regions.count":
            if any(token in lowered for token in ("multi-region", "multi region", "two regions", "2 regions")):
                return "2"
            if any(token in lowered for token in ("single", "one region", "1 region", "single ad", "single-ad")):
                return "1"
        elif qid in {"regions.mode", "region.mode", "topology.scope"}:
            if lowered in {"1", "one", "one region"}:
                return "single-region"
            if lowered in {"2", "two", "two regions"}:
                return "multi-region"
    return cleaned

def _record_region_constraint_if_present(context: dict[str, Any], decision_context: dict[str, Any]) -> None:
    constraints = dict((decision_context or {}).get("constraints", {}) or {})
    region = str(constraints.get("region", "") or "").strip()
    if not region:
        return
    context_store.merge_archie_client_facts(context, {"region": region})
    prior, prior_answer = _resolved_answer_for_question(_latest_resolved_answer_map(context), "constraints.region")
    if isinstance(prior, dict) and prior_answer == region:
        return
    context_store.record_resolved_question(
        context,
        {
            "id": str(uuid.uuid4()),
            "question_id": "constraints.region",
            "question": "Preferred OCI region",
            "final_answer": region,
            "source": "archie_region_normalization",
            "confidence": "high",
            "timestamp": _now(),
        },
    )

def _record_infrastructure_profile_if_present(context: dict[str, Any], text: str) -> None:
    profile = _extract_infrastructure_profile(text)
    if profile:
        context_store.merge_archie_infrastructure_profile(context, profile)
    facts = _extract_client_facts(text, profile=profile)
    if facts:
        context_store.merge_archie_client_facts(context, facts)

def _extract_client_facts(text: str, *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = str(text or "")
    lower = raw.lower()
    facts: dict[str, Any] = {}
    infra = profile if isinstance(profile, dict) else {}
    if infra:
        facts["infrastructure"] = infra
        if infra.get("platform"):
            facts["platform"] = infra.get("platform")
        if isinstance(infra.get("connectivity"), dict):
            facts["connectivity"] = dict(infra.get("connectivity", {}) or {})
        if isinstance(infra.get("dr"), dict):
            facts["dr"] = dict(infra.get("dr", {}) or {})
        workloads = infra.get("workload_notes")
        if isinstance(workloads, list) and workloads:
            facts["workloads"] = list(workloads)

    region = _extract_region_or_geography(raw)
    if region:
        if region.startswith("oci:"):
            facts["region"] = region.removeprefix("oci:")
        else:
            facts["geography"] = region

    security: dict[str, Any] = {}
    if "waf" in lower or "web application firewall" in lower:
        security["waf"] = True
    if "bastion" in lower:
        security["bastion"] = True
    if "single ad" in lower or "single availability domain" in lower:
        security["identity_topology"] = "single AD"
    if "active directory" in lower or re.search(r"\bad\b", lower):
        security.setdefault("directory", "Active Directory")
    if security:
        facts["security"] = security

    os_mix = []
    if "linux" in lower:
        os_mix.append("Linux")
    if "windows" in lower:
        os_mix.append("Windows")
    if os_mix:
        facts["os_mix"] = os_mix

    databases = []
    if "sql" in lower or "sql server" in lower:
        databases.append("SQL Server")
    if "oracle db" in lower or "oracle database" in lower:
        databases.append("Oracle Database")
    if databases:
        facts["databases"] = databases

    exclusions = []
    exclusion_patterns = (
        (r"\b(?:exclude|excluding|out of scope|no)\s+gpu\b", "GPU"),
        (r"\b(?:exclude|excluding|out of scope|no)\s+database\b", "database"),
        (r"\b(?:exclude|excluding|out of scope|no)\s+dr\b", "DR"),
        (r"\b(?:exclude|excluding|out of scope|no)\s+waf\b", "WAF"),
    )
    for pattern, label in exclusion_patterns:
        if re.search(pattern, lower):
            exclusions.append(label)
    if exclusions:
        facts["scope_exclusions"] = exclusions
    return facts

def _extract_region_or_geography(text: str) -> str:
    raw = str(text or "")
    region = _extract_oci_region(raw)
    if region:
        return f"oci:{region}"
    lower = raw.lower()
    if "south africa" in lower or "za-" in lower:
        return "South Africa"
    if "united kingdom" in lower or " uk " in f" {lower} ":
        return "United Kingdom"
    if "europe" in lower or "emea" in lower:
        return "Europe/EMEA"
    return ""

def _extract_oci_region(text: str) -> str:
    match = re.search(r"\b([a-z]{2,}-[a-z]+-\d+)\b", str(text or ""), flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""

def _extract_infrastructure_profile(text: str) -> dict[str, Any]:
    raw = str(text or "")
    lower = raw.lower()
    profile: dict[str, Any] = {}

    platforms: list[str] = []
    if "vxrail" in lower or "vx rail" in lower:
        platforms.append("VxRail")
    if "vmware esxi" in lower or re.search(r"\besxi\b", lower):
        platforms.append("VMware ESXi")
    elif "vmware" in lower:
        platforms.append("VMware")
    if platforms:
        profile["platform"] = " / ".join(dict.fromkeys(platforms))

    cpu: dict[str, Any] = {}
    _set_number(cpu, "logical_cores", _extract_number(raw, r"\b(\d+(?:[.,]\d+)?)\s*(?:logical\s+)?(?:cpu\s+)?cores?\b"))
    _set_number(cpu, "sockets", _extract_number(raw, r"\b(\d+(?:[.,]\d+)?)\s*sockets?\b"))
    _set_number(cpu, "cores_per_socket", _extract_number(raw, r"\b(\d+(?:[.,]\d+)?)\s*cores?\s*per\s*socket\b"))
    _set_number(cpu, "used_ghz", _extract_number(raw, r"\b(?:used|consumed|utili[sz]ed)\s*(?:cpu\s*)?(?:capacity\s*)?[:=]?\s*(\d+(?:[.,]\d+)?)\s*ghz\b"))
    _set_number(cpu, "total_ghz", _extract_number(raw, r"\b(?:total|installed|available)\s*(?:cpu\s*)?(?:capacity\s*)?[:=]?\s*(\d+(?:[.,]\d+)?)\s*ghz\b"))
    model = _extract_processor_model(raw)
    if model:
        cpu["processor_model"] = model
    if cpu:
        profile["cpu"] = cpu

    memory = _extract_used_total_capacity(raw, ("memory", "ram"), default_unit="gb")
    if memory:
        profile["memory"] = memory

    storage = _extract_used_total_capacity(raw, ("storage", "datastore", "disk", "capacity"), default_unit="tb")
    if storage:
        profile["storage"] = storage

    connectivity = _extract_connectivity_profile(raw)
    if connectivity:
        profile["connectivity"] = connectivity

    dr = _extract_dr_profile(raw)
    if dr:
        profile["dr"] = dr

    workload_notes = _extract_workload_notes(raw)
    if workload_notes:
        profile["workload_notes"] = workload_notes

    if profile:
        profile["source"] = "chat_discovery"
        profile["updated_at"] = _now()
    return profile

def _set_number(target: dict[str, Any], key: str, value: float | None) -> None:
    if value is None or value <= 0:
        return
    target[key] = int(value) if float(value).is_integer() else value

def _extract_number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(str(match.group(1)).replace(",", ""))
    except Exception:
        return None

def _extract_processor_model(text: str) -> str:
    patterns = (
        r"\b(?:processor|cpu)\s*model\s*[:=-]\s*([^\n;,]+)",
        r"\b((?:intel\s+)?xeon[^\n;,]{0,80})",
        r"\b((?:amd\s+)?epyc[^\n;,]{0,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" .")
    return ""

def _extract_used_total_capacity(text: str, markers: tuple[str, ...], *, default_unit: str) -> dict[str, Any]:
    marker_expr = "|".join(re.escape(marker) for marker in markers)
    unit_expr = r"(tb|tib|gb|gib)"
    patterns = (
        rf"\b(?:{marker_expr})\b[^\n]{{0,80}}?\b(?:used|consumed|utili[sz]ed)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}[^\n]{{0,80}}?\b(?:total|installed|available|capacity)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}",
        rf"\b(?:used|consumed|utili[sz]ed)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}[^\n]{{0,80}}?\b(?:total|installed|available|capacity)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}[^\n]{{0,40}}?\b(?:{marker_expr})\b",
        rf"\b(?:{marker_expr})\b[^\n]{{0,80}}?\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\s*(?:used|consumed|utili[sz]ed)[^\n]{{0,80}}?\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\s*(?:total|installed|available|capacity)",
    )
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
        if not match:
            continue
        used = _capacity_to_unit(float(match.group(1).replace(",", "")), match.group(2), default_unit)
        total = _capacity_to_unit(float(match.group(3).replace(",", "")), match.group(4), default_unit)
        suffix = default_unit.lower()
        return {f"used_{suffix}": used, f"total_{suffix}": total}

    total_match = re.search(
        rf"\b(?:{marker_expr})\b[^\n]{{0,80}}?\b(?:total|installed|available|capacity)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    if total_match:
        total = _capacity_to_unit(float(total_match.group(1).replace(",", "")), total_match.group(2), default_unit)
        return {f"total_{default_unit.lower()}": total}

    standalone_values: list[float] = []
    standalone_patterns = (
        rf"\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\s*(?:of\s+)?(?:{marker_expr})\b",
        rf"\b(?:{marker_expr})\b[^,\n.;]{{0,40}}?\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\b",
    )
    for pattern in standalone_patterns:
        for match in re.finditer(pattern, str(text or ""), flags=re.IGNORECASE):
            matched_text = match.group(0).lower()
            trailing_text = str(text or "")[match.end():min(len(str(text or "")), match.end() + 32)].lower()
            if default_unit.lower() == "gb" and any(
                marker in f"{matched_text} {trailing_text}"
                for marker in ("block", "storage", "volume", "vsan", "hci", "capacity")
            ):
                continue
            value = _capacity_to_unit(float(match.group(1).replace(",", "")), match.group(2), default_unit)
            if value > 0:
                standalone_values.append(value)
    if standalone_values:
        return {f"total_{default_unit.lower()}": max(standalone_values)}
    return {}

def _capacity_to_unit(value: float, unit: str, target_unit: str) -> float:
    source = str(unit or "").lower()
    target = str(target_unit or "").lower()
    value_gb = value * 1024.0 if source in {"tb", "tib"} else value
    if target == "gb" and source in {"tb", "tib"} and value >= 128:
        value_gb = value
    converted = value_gb / 1024.0 if target == "tb" else value_gb
    return int(converted) if float(converted).is_integer() else round(converted, 2)

def _extract_connectivity_profile(text: str) -> dict[str, Any]:
    lower = str(text or "").lower()
    connectivity: dict[str, Any] = {}
    internet = re.search(r"\binternet(?:\s+bandwidth)?\s*[:=-]?\s*(\d+(?:[.,]\d+)?)\s*(gbps|mbps)\b", text, flags=re.IGNORECASE)
    if internet:
        unit = "Gbps" if internet.group(2).lower() == "gbps" else "Mbps"
        connectivity["internet_bandwidth"] = f"{internet.group(1).replace(',', '')} {unit}"
    if "mpls" in lower:
        connectivity["mpls"] = True
    if "sd-wan" in lower or "sd wan" in lower:
        connectivity["sd_wan"] = True
    if "fastconnect" in lower:
        connectivity["fastconnect"] = True
    if "vpn" in lower:
        connectivity["vpn"] = True
    return connectivity

def _extract_dr_profile(text: str) -> dict[str, Any]:
    lower = str(text or "").lower()
    dr: dict[str, Any] = {}
    if "cross-region" in lower or "cross region" in lower:
        dr["cross_region_restore"] = "restore" in lower or "dr" in lower or "disaster recovery" in lower
    sla = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:hour|hr|h)\s*(?:sla|rto|restore|recovery)\b", text, flags=re.IGNORECASE)
    if not sla:
        sla = re.search(r"\b(?:sla|rto|restore|recovery)\s*(?:of|within|<=|:)?\s*(\d+(?:[.,]\d+)?)\s*(?:hour|hr|h)s?\b", text, flags=re.IGNORECASE)
    if sla:
        hours = float(sla.group(1).replace(",", ""))
        dr["sla_hours"] = int(hours) if hours.is_integer() else hours
    elif "24h" in lower or "24 h" in lower or "24-hour" in lower:
        dr["sla_hours"] = 24
    return dr

def _extract_workload_notes(text: str) -> list[str]:
    lower = str(text or "").lower()
    markers = (
        ("dc", "domain controllers"),
        ("domain controller", "domain controllers"),
        ("sql", "SQL databases"),
        ("oracle db", "Oracle databases"),
        ("oracle database", "Oracle databases"),
        ("custom app", "custom applications"),
        ("patch repo", "patch repository"),
        ("file server", "file servers"),
    )
    notes: list[str] = []
    for token, label in markers:
        if token in lower and label not in notes:
            notes.append(label)
    return notes

def _infrastructure_profile_context_lines(context: dict[str, Any] | None) -> list[str]:
    if not isinstance(context, dict):
        return []
    archie = context_store.get_archie_state(context)
    profile = archie.get("infrastructure_profile", {}) if isinstance(archie.get("infrastructure_profile"), dict) else {}
    if not profile:
        return []
    lines = ["Infrastructure profile:"]
    platform = str(profile.get("platform", "") or "").strip()
    if platform:
        lines.append(f"- platform: {platform}")
    cpu = profile.get("cpu", {}) if isinstance(profile.get("cpu"), dict) else {}
    if cpu:
        bits = []
        for key, label in (
            ("logical_cores", "logical cores"),
            ("sockets", "sockets"),
            ("cores_per_socket", "cores/socket"),
            ("processor_model", "processor"),
            ("used_ghz", "used GHz"),
            ("total_ghz", "total GHz"),
        ):
            if cpu.get(key) not in (None, "", [], {}):
                bits.append(f"{label}={cpu.get(key)}")
        if bits:
            lines.append("- CPU: " + ", ".join(bits))
    memory = profile.get("memory", {}) if isinstance(profile.get("memory"), dict) else {}
    if memory:
        lines.append("- memory: " + ", ".join(f"{key}={value}" for key, value in memory.items() if value not in (None, "", [], {})))
    storage = profile.get("storage", {}) if isinstance(profile.get("storage"), dict) else {}
    if storage:
        lines.append("- storage: " + ", ".join(f"{key}={value}" for key, value in storage.items() if value not in (None, "", [], {})))
    connectivity = profile.get("connectivity", {}) if isinstance(profile.get("connectivity"), dict) else {}
    if connectivity:
        lines.append("- connectivity: " + ", ".join(f"{key}={value}" for key, value in connectivity.items() if value not in (None, "", [], {})))
    dr = profile.get("dr", {}) if isinstance(profile.get("dr"), dict) else {}
    if dr:
        lines.append("- DR: " + ", ".join(f"{key}={value}" for key, value in dr.items() if value not in (None, "", [], {})))
    workload_notes = [str(item).strip() for item in profile.get("workload_notes", []) or [] if str(item).strip()]
    if workload_notes:
        lines.append("- workload notes: " + ", ".join(workload_notes))
    return lines

def _first_profile_value(section: dict[str, Any], keys: tuple[str, ...]) -> Any:
    if not isinstance(section, dict):
        return None
    for key in keys:
        value = section.get(key)
        if value not in (None, "", [], {}):
            return value
    return None

def _largest_profile_value(section: dict[str, Any], keys: tuple[str, ...]) -> Any:
    if not isinstance(section, dict):
        return None
    numeric = [section.get(key) for key in keys if isinstance(section.get(key), (int, float))]
    if numeric:
        return max(numeric)
    return _first_profile_value(section, keys)

def _infrastructure_profile_ocpu_answer(profile: dict[str, Any]) -> str:
    cpu = profile.get("cpu", {}) if isinstance(profile, dict) and isinstance(profile.get("cpu"), dict) else {}
    value = _first_profile_value(cpu, ("logical_cores", "cores", "ocpu"))
    if value in (None, "", [], {}):
        return ""
    return f"{value:g} OCPU equivalent" if isinstance(value, (int, float)) else f"{value} OCPU equivalent"

def _infrastructure_profile_memory_answer(profile: dict[str, Any]) -> str:
    memory = profile.get("memory", {}) if isinstance(profile, dict) and isinstance(profile.get("memory"), dict) else {}
    value = _largest_profile_value(memory, ("used_gb", "total_gb"))
    if value in (None, "", [], {}):
        return ""
    return f"{value:g} GB RAM" if isinstance(value, (int, float)) else f"{value} GB RAM"

def _infrastructure_profile_storage_answer(profile: dict[str, Any]) -> str:
    storage = profile.get("storage", {}) if isinstance(profile, dict) and isinstance(profile.get("storage"), dict) else {}
    value = _largest_profile_value(storage, ("used_tb", "total_tb"))
    if value not in (None, "", [], {}):
        return f"{value:g} TB block storage" if isinstance(value, (int, float)) else f"{value} TB block storage"
    gb_value = _largest_profile_value(storage, ("used_gb", "total_gb"))
    if gb_value not in (None, "", [], {}):
        return f"{gb_value:g} GB block storage" if isinstance(gb_value, (int, float)) else f"{gb_value} GB block storage"
    return ""

def _infrastructure_profile_connectivity_answer(profile: dict[str, Any]) -> str:
    connectivity = profile.get("connectivity", {}) if isinstance(profile, dict) and isinstance(profile.get("connectivity"), dict) else {}
    if not connectivity:
        return ""
    parts: list[str] = []
    bandwidth = str(connectivity.get("internet_bandwidth", "") or "").strip()
    if bandwidth:
        parts.append(f"internet bandwidth {bandwidth}")
    if connectivity.get("mpls"):
        parts.append("MPLS")
    if connectivity.get("sd_wan"):
        parts.append("SD-WAN")
    if connectivity.get("fastconnect"):
        parts.append("FastConnect connectivity")
    if connectivity.get("vpn"):
        parts.append("site-to-site VPN")
    return ", ".join(parts)

def _infrastructure_profile_sizing_answer(profile: dict[str, Any]) -> str:
    parts = [
        _infrastructure_profile_ocpu_answer(profile),
        _infrastructure_profile_memory_answer(profile),
        _infrastructure_profile_storage_answer(profile),
    ]
    rendered = [part for part in parts if part]
    return ", ".join(rendered) if len(rendered) >= 2 else ""

def _component_labels_from_text(text: str) -> list[str]:
    lowered = str(text or "").lower()
    labels: list[str] = []
    markers = (
        ("oke", "OKE"),
        ("kubernetes", "OKE"),
        ("load balancer", "Load Balancer"),
        ("lb", "Load Balancer"),
        ("database", "Database"),
        ("autonomous database", "Autonomous Database"),
        ("adb", "Autonomous Database"),
        ("postgres", "PostgreSQL"),
        ("mysql", "MySQL"),
        ("object storage", "Object Storage"),
        ("bucket", "Object Storage"),
        ("waf", "WAF"),
        ("vcn", "VCN"),
        ("subnet", "Subnets"),
        ("drg", "DRG"),
        ("fastconnect", "FastConnect"),
        ("vpn", "VPN"),
        ("vault", "Vault/KMS"),
        ("kms", "Vault/KMS"),
        ("monitoring", "Monitoring"),
        ("logging", "Logging"),
        ("compute", "Compute"),
        ("app server", "Compute"),
        ("web server", "Compute"),
    )
    for token, label in markers:
        if token in lowered and label not in labels:
            labels.append(label)
    return labels

def _infer_components_scope_from_context(context: dict[str, Any], text: str) -> tuple[str, str, str]:
    labels = _component_labels_from_text(text)
    agents = context.get("agents", {}) if isinstance(context, dict) else {}
    bom = dict((agents or {}).get("bom", {}) or {})
    diagram = dict((agents or {}).get("diagram", {}) or {})
    if int(bom.get("line_item_count", 0) or 0) > 0:
        return _standard_components_scope_answer(), "prior BOM line items are available for component scope", "high"
    if int(diagram.get("node_count", 0) or 0) > 0 or str(diagram.get("deployment_summary", "") or "").strip():
        return _standard_components_scope_answer(), "latest generated architecture state is available for component scope", "high"
    if labels:
        return ", ".join(labels), "current BOM, notes, or architecture context names these OCI components", "high"
    lowered = str(text or "").lower()
    if "small site" in lowered or "small-site" in lowered or "reference architecture" in lowered:
        return _standard_components_scope_answer(), "standard small-site reference architecture provides the component scope", "medium"
    return "", "", "needs_confirmation"

def _extract_first_number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(str(match.group(1)).replace(",", ""))
    except Exception:
        return None

def _format_quantity(value: float, unit: str) -> str:
    rendered = f"{value:g}"
    return f"{rendered} {unit}"

def _extract_ocpu_answer(text: str) -> str:
    value = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*ocpus?\b")
    return _format_quantity(value, "OCPU") if value is not None and value > 0 else ""

def _extract_memory_answer(text: str) -> str:
    capacities = _extract_used_total_capacity(text, ("memory", "ram"), default_unit="gb")
    capacity_values = [value for key, value in capacities.items() if key.endswith("_gb") and isinstance(value, (int, float))]
    if capacity_values:
        return _format_quantity(max(capacity_values), "GB RAM")
    value = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\s*(?:memory|ram)\b")
    if value is None:
        value = _extract_first_number(text, r"\b(?:memory|ram)\s*(?:of|:|=)?\s*(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\b")
    return _format_quantity(value, "GB RAM") if value is not None and value > 0 else ""

def _extract_block_storage_answer(text: str) -> str:
    capacities = _extract_used_total_capacity(text, ("storage", "datastore", "disk", "capacity"), default_unit="tb")
    tb_values = [value for key, value in capacities.items() if key.endswith("_tb") and isinstance(value, (int, float))]
    if tb_values:
        return _format_quantity(max(tb_values), "TB block storage")
    tb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:tb|tib)\s*(?:block|block volume|volume|storage)\b")
    if tb is not None and tb > 0:
        return _format_quantity(tb, "TB block storage")
    gb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\s*(?:block|block volume|volume|storage)\b")
    if gb is None:
        gb = _extract_first_number(text, r"\b(?:block|block volume|volume|storage)\s*(?:of|:|=)?\s*(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\b")
    return _format_quantity(gb, "GB block storage") if gb is not None and gb > 0 else ""

def _extract_object_storage_answer(text: str) -> str:
    tb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:tb|tib)\s*object storage\b")
    if tb is not None and tb > 0:
        return _format_quantity(tb, "TB Object Storage")
    gb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\s*object storage\b")
    if gb is not None and gb > 0:
        return _format_quantity(gb, "GB Object Storage")
    lowered = str(text or "").lower()
    if "object storage" in lowered or "bucket" in lowered:
        return "include Object Storage"
    return ""

def _extract_vpu_answer(text: str) -> tuple[str, str]:
    value = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*vpus?\s*/?\s*(?:gb|gib)?\b")
    if value is not None and value > 0:
        return _format_quantity(value, "VPU/GB"), "current request provides Block Volume performance units"
    lowered = str(text or "").lower()
    if "balanced" in lowered and ("block" in lowered or "volume" in lowered or "storage" in lowered):
        return "Balanced Block Volume performance, 10 VPU/GB", "current request names Balanced Block Volume performance"
    return "", ""

def _combined_bom_sizing_answer(text: str) -> tuple[str, str, str]:
    parts: list[str] = []
    ocpu = _extract_ocpu_answer(text)
    memory = _extract_memory_answer(text)
    storage = _extract_block_storage_answer(text)
    if ocpu:
        parts.append(ocpu)
    if memory:
        parts.append(memory)
    if storage:
        parts.append(storage)
    if len(parts) >= 2:
        return ", ".join(parts), "current request/context provides multiple BOM sizing inputs", "high"
    if parts:
        return parts[0], "current request/context provides partial BOM sizing input", "medium"
    return "", "", "needs_confirmation"

def _suggest_answer_for_question(
    question: dict[str, Any],
    *,
    context: dict[str, Any],
    user_message: str,
) -> tuple[str, str, str]:
    question_id = str(question.get("question_id", "") or "").strip()
    prompt = str(question.get("question", "") or "").strip()
    resolved = _latest_resolved_answer_map(context)
    prior, answer = _resolved_answer_for_question(resolved, question_id)
    if isinstance(prior, dict) and answer:
        return answer, "prior Archie-approved decision", "high"

    archie = context_store.get_archie_state(context)
    infrastructure_profile = archie.get("infrastructure_profile", {}) if isinstance(archie.get("infrastructure_profile"), dict) else {}
    latest_decision_context = dict(context.get("latest_decision_context", {}) or {})
    constraints = dict(latest_decision_context.get("constraints", {}) or {})
    text = " ".join(
        part
        for part in (
            user_message,
            str(archie.get("engagement_summary", "") or ""),
            str(archie.get("latest_notes_summary", "") or ""),
            json.dumps(constraints, ensure_ascii=True, sort_keys=True),
            context_store.build_context_summary(context),
        )
        if str(part).strip()
    ).lower()
    qid = _normalize_specialist_question_id(question_id)
    prompt_lc = prompt.lower()

    if qid in _specialist_question_id_aliases("ha.ads"):
        if (
            re.search(r"\b(?:two|2)\b", text)
            and re.search(r"\bbm\.standard\.x9\.64\b", text)
            and re.search(r"\bfd\s*1\b", text)
            and re.search(r"\bfd\s*2\b", text)
        ):
            return (
                "two BM.Standard.X9.64 hosts; host 1 in FD1 using FD-local subnet; host 2 in FD2 using FD-local subnet",
                "current request explicitly provides BM host count and FD-local placement",
                "high",
            )

    if qid in _specialist_question_id_aliases("constraints.region") or (
        "region" in prompt_lc and not any(token in prompt_lc for token in ("single-region", "multi-region", "multi ad", "multi-ad", "topology"))
    ):
        region = str(constraints.get("region", "") or "").strip()
        if region:
            return region, "latest decision context already has the target OCI region", "high"
        if "bom" in str(user_message or "").lower() or "pricing" in str(user_message or "").lower():
            return (
                "pricing-only estimate; treat OCI pricing as region-consistent for this draft",
                "BOM pricing-only flow does not require a pinned OCI deployment region",
                "medium",
            )

    if qid in _specialist_question_id_aliases("regions.mode") or any(
        token in prompt_lc for token in ("single-region", "multi-region", "multi ad", "multi-ad", "topology")
    ):
        if any(token in text for token in ("multi-region", "multi region", "two regions", "2 regions")):
            if question_id == "regions.count":
                return "2", "current Archie context mentions a multi-region topology", "high"
            return "multi-region", "current Archie context mentions a multi-region topology", "high"
        region = str(constraints.get("region", "") or "").strip()
        if region or "single region" in text or "single-region" in text:
            if question_id == "regions.count":
                return "1", "latest decision context has a single primary region", "medium"
            return "single-region", "latest decision context has a single primary region", "medium"

    if qid in _specialist_question_id_aliases("bom.compute.gpu"):
        if any(token in text for token in ("non-gpu", "non gpu", "no gpu", "without gpu", "cpu-only", "cpu only")):
            return "non-GPU compute", "current request/context explicitly excludes GPU compute", "high"
        if "gpu" in text or "accelerator" in text:
            return "GPU compute", "current request/context mentions GPU or accelerator compute", "high"
        if infrastructure_profile:
            return "non-GPU compute", "no GPU or accelerator requirement is present in the saved infrastructure profile", "medium"

    if qid in _specialist_question_id_aliases("bom.compute.ocpu"):
        answer = _extract_ocpu_answer(text)
        if answer:
            return answer, "current request/context provides OCPU sizing", "high"
        answer = _infrastructure_profile_ocpu_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides CPU sizing", "high"

    if qid in _specialist_question_id_aliases("bom.compute.memory"):
        answer = _extract_memory_answer(text)
        if answer:
            return answer, "current request/context provides memory sizing", "high"
        answer = _infrastructure_profile_memory_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides memory sizing", "high"

    if qid in _specialist_question_id_aliases("bom.storage.block"):
        answer = _extract_block_storage_answer(text)
        if answer:
            return answer, "current request/context provides block storage sizing", "high"
        answer = _infrastructure_profile_storage_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides storage sizing", "high"

    if qid in _specialist_question_id_aliases("bom.storage.vpu"):
        answer, basis = _extract_vpu_answer(text)
        if answer:
            return answer, basis, "high"
        if _extract_block_storage_answer(text):
            return "Balanced Block Volume performance, 10 VPU/GB", "current BOM service default for block storage performance", "medium"

    if qid in _specialist_question_id_aliases("bom.network.load_balancer"):
        if any(token in text for token in ("no load balancer", "without load balancer", "no lb", "without lb")):
            return "do not include a load balancer", "current request/context excludes a load balancer", "high"
        if any(token in text for token in ("load balancer", "flexible lb", " lb ", "ingress", "public web", "external users", "internet")):
            return "include one OCI Flexible Load Balancer", "current request/context indicates ingress or load balancing", "high"

    if qid in _specialist_question_id_aliases("bom.storage.object"):
        if any(token in text for token in ("no object storage", "without object storage", "no bucket")):
            return "do not include Object Storage", "current request/context excludes Object Storage", "high"
        answer = _extract_object_storage_answer(text)
        if answer:
            return answer, "current request/context includes Object Storage or bucket scope", "high"

    if qid in _specialist_question_id_aliases("bom.network.connectivity"):
        answer = _infrastructure_profile_connectivity_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides connectivity facts", "high"
        if "fastconnect" in text:
            return "FastConnect connectivity", "current request/context mentions FastConnect", "high"
        if "vpn" in text:
            return "site-to-site VPN connectivity", "current request/context mentions VPN", "high"
        if any(token in text for token in ("on-prem", "on prem", "onprem", "drg")):
            return "private connectivity through DRG", "current request/context mentions on-premises connectivity or DRG", "medium"

    if qid in _specialist_question_id_aliases("workload.sizing"):
        answer, basis, confidence = _combined_bom_sizing_answer(text)
        if answer:
            return answer, basis, confidence
        answer = _infrastructure_profile_sizing_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides CPU, memory, and storage footprint", "high"

    if qid in _specialist_question_id_aliases("network.exposure") or "public, private, or both" in prompt_lc:
        has_private = "private" in text
        has_public = "public" in text or "internet" in text
        if has_private and has_public:
            return "both", "notes mention both private and public exposure", "medium"
        if has_private:
            return "private", "notes emphasize private networking/exposure", "high"
        if has_public:
            return "public", "notes mention public or internet ingress", "high"

    if question_id in {"workload.components", "components.scope"} or "major oci components" in prompt_lc:
        components, basis, confidence = _infer_components_scope_from_context(context, text)
        if components:
            return components, basis, confidence

    if question_id == "data.tier" or "data tier" in prompt_lc:
        if "autonomous database" in text or "adb" in text:
            return "Autonomous Database", "notes mention Autonomous Database", "high"
        if "postgres" in text:
            return "PostgreSQL", "notes mention PostgreSQL", "high"
        if "mysql" in text:
            return "MySQL", "notes mention MySQL", "high"
        if "database" in text or "data tier" in text:
            return "generic database node", "notes imply a data tier without a pinned engine", "medium"

    if qid in _specialist_question_id_aliases("bom.budget") or "budget" in prompt_lc or "monthly" in prompt_lc:
        if constraints.get("cost_max_monthly") is not None:
            return str(constraints.get("cost_max_monthly")), "latest decision context already has a monthly budget", "high"

    return "", "", "needs_confirmation"

def _apply_resolved_answers_to_tool_args(
    *,
    tool_name: str,
    args: dict[str, Any],
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(args or {})
    lines = ["[Archie Resolved Specialist Inputs]"]
    for item in answers:
        question_id = str(item.get("question_id", "") or item.get("id", "") or "question").strip()
        answer = str(item.get("final_answer", "") or item.get("suggested_answer", "") or "").strip()
        if question_id and answer:
            answer = _coerce_specialist_answer(question_id, answer)
            lines.append(f"- {question_id}: {answer}")
    lines.append("[End Archie Resolved Specialist Inputs]")
    block = "\n".join(lines)
    payload["_archie_question_retry"] = True
    if tool_name == "generate_diagram":
        payload["bom_text"] = f"{payload.get('bom_text', '')}\n\n{block}".strip()
    elif tool_name == "generate_bom":
        payload["prompt"] = f"{payload.get('prompt', '')}\n\n{block}".strip()
    elif tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        payload["feedback"] = f"{payload.get('feedback', '')}\n\n{block}".strip()
    elif tool_name == "generate_terraform":
        payload["prompt"] = f"{payload.get('prompt', '')}\n\n{block}".strip()
    return payload

def _resolved_inputs_from_answers(answers: list[dict[str, Any]]) -> list[dict[str, str]]:
    resolved_inputs: list[dict[str, str]] = []
    for item in answers:
        question_id = str(item.get("question_id", "") or item.get("id", "") or "question").strip()
        answer = str(item.get("final_answer", "") or item.get("suggested_answer", "") or "").strip()
        if not question_id or not answer:
            continue
        resolved_inputs.append(
            {
                "question_id": question_id,
                "question": str(item.get("question", "") or "").strip(),
                "answer": _coerce_specialist_answer(question_id, answer),
                "basis": str(item.get("basis", "") or "").strip(),
                "confidence": str(item.get("confidence", "") or "").strip(),
            }
        )
    return resolved_inputs

def _attach_bom_resolved_inputs(
    result_data: dict[str, Any],
    answers: list[dict[str, Any]],
) -> None:
    if not isinstance(result_data, dict):
        return
    payload = result_data.get("bom_payload")
    if not isinstance(payload, dict):
        return
    resolved_inputs = _resolved_inputs_from_answers(answers)
    if not resolved_inputs:
        return
    existing = payload.get("resolved_inputs") if isinstance(payload.get("resolved_inputs"), list) else []
    by_id: dict[str, dict[str, str]] = {
        str(item.get("question_id", "") or ""): dict(item)
        for item in existing
        if isinstance(item, dict) and str(item.get("question_id", "") or "").strip()
    }
    for item in resolved_inputs:
        by_id[item["question_id"]] = item
    payload["resolved_inputs"] = list(by_id.values())

def _decision_context_with_auto_answers(
    decision_context: dict[str, Any],
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    updated = dict(decision_context or {})
    if not updated or not answers:
        return updated
    missing = [str(item).strip() for item in updated.get("missing_inputs", []) or [] if str(item).strip()]
    remove_missing: set[str] = set()
    constraints = dict(updated.get("constraints", {}) or {})
    assumptions = list(updated.get("assumptions", []) or [])
    for item in answers:
        qid = _normalize_specialist_question_id(str(item.get("question_id", "") or item.get("id", "") or ""))
        answer = str(item.get("final_answer", "") or item.get("suggested_answer", "") or "").strip()
        if not answer:
            continue
        if qid in _specialist_question_id_aliases("constraints.region"):
            remove_missing.add("preferred OCI region")
            if re.fullmatch(r"[a-z]{2,}-[a-z]+-\d", answer):
                constraints["region"] = answer
            elif "pricing-only estimate" in answer.lower():
                assumptions = _merge_assumption_lists(
                    assumptions,
                    [
                        {
                            "id": "bom_region_pricing_consistent",
                            "statement": "Region not specified; BOM pricing is treated as region-consistent for this draft estimate.",
                            "reason": "BOM pricing-only flow can proceed without a pinned OCI deployment region.",
                            "risk": "low",
                        }
                    ],
                )
        if qid in _specialist_question_id_aliases("bom.budget"):
            remove_missing.add("monthly budget cap")
    if remove_missing:
        updated["missing_inputs"] = [item for item in missing if item not in remove_missing]
    if constraints:
        updated["constraints"] = constraints
    updated["assumptions"] = assumptions
    return updated

async def _mediate_specialist_questions(
    *,
    tool_name: str,
    args: dict[str, Any],
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str,
    user_message: str,
    max_refinements: int,
    decision_context: dict[str, Any],
    result_summary: str,
    artifact_key: str,
    result_data: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    questions = _specialist_question_bundle_from_result(
        tool_name=tool_name,
        result_summary=result_summary,
        result_data=result_data,
    )
    if not questions:
        return result_summary, artifact_key, result_data
    if isinstance(decision_context, dict) and decision_context:
        context_store.set_latest_decision_context(context, decision_context)

    auto_answered: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for item in questions:
        suggestion, basis, confidence = _suggest_answer_for_question(item, context=context, user_message=user_message)
        candidate = {
            **item,
            "specialist_path": tool_name,
            "request_intent": user_message,
            "suggested_answer": suggestion,
            "basis": basis,
            "confidence": confidence,
            "timestamp": _now(),
        }
        if suggestion and confidence in {"high", "medium"}:
            candidate["final_answer"] = suggestion
            auto_answered.append(candidate)
        else:
            unresolved.append(candidate)

    for item in auto_answered:
        context_store.record_resolved_question(
            context,
            {
                "id": str(uuid.uuid4()),
                **item,
                "source": "archie_auto_fill",
            },
        )

    if unresolved:
        checkpoint = _build_specialist_question_checkpoint(
            tool_name=tool_name,
            args=args,
            original_request=user_message,
            questions=[*auto_answered, *unresolved],
        )
        context_store.set_open_questions(context, [*auto_answered, *unresolved])
        context_store.set_pending_checkpoint(context, checkpoint)
        context_store.write_context(store, customer_id, context)
        result_data["archie_question_bundle"] = checkpoint
        return checkpoint["prompt"], "", result_data

    context_store.clear_pending_checkpoint(context)
    context_store.set_open_questions(context, [])
    decision_context = _decision_context_with_auto_answers(decision_context, auto_answered)
    context_store.set_latest_decision_context(context, decision_context)
    context_store.write_context(store, customer_id, context)
    rerun_args = _apply_resolved_answers_to_tool_args(tool_name=tool_name, args=args, answers=auto_answered)
    rerun_summary, rerun_key, rerun_data = await _execute_tool(
        tool_name,
        rerun_args,
        customer_id=customer_id,
        customer_name=customer_name,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        specialist_mode=specialist_mode,
        user_message=user_message,
        max_refinements=max_refinements,
        decision_context=decision_context,
    )
    rerun_data = dict(rerun_data or {})
    rerun_data["archie_auto_answers"] = auto_answered
    if tool_name == "generate_bom":
        _attach_bom_resolved_inputs(rerun_data, auto_answered)
    return rerun_summary, rerun_key, rerun_data

def _build_specialist_question_checkpoint(
    *,
    tool_name: str,
    args: dict[str, Any],
    original_request: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    rendered = ["Archie needs confirmation on the remaining specialist inputs before continuing."]
    for item in questions:
        question = str(item.get("question", "") or "").strip()
        if not question:
            continue
        rendered.append("")
        rendered.append(f"- Question ID: {item.get('question_id', '')}")
        rendered.append(f"  Question: {question}")
        suggestion = str(item.get("suggested_answer", "") or "").strip()
        if suggestion:
            rendered.append(f"  Suggested answer: {suggestion}")
        basis = str(item.get("basis", "") or "").strip()
        if basis:
            rendered.append(f"  Basis: {basis}")
        rendered.append(f"  Confidence: {item.get('confidence', 'needs_confirmation')}")
    rendered.append("")
    rendered.append("Reply `approve suggested answers` to accept Archie's suggestions, or answer inline as `question_id: answer`.")
    return {
        "id": str(uuid.uuid4()),
        "type": "specialist_questions",
        "status": "pending",
        "tool_name": tool_name,
        "tool_args": dict(args or {}),
        "original_request": original_request,
        "questions": [dict(item) for item in questions],
        "prompt": "\n".join(rendered),
        "options": ["approve suggested answers", "answer inline"],
    }

def _is_specialist_question_approve_message(user_message: str) -> bool:
    lowered = str(user_message or "").lower()
    return any(
        marker in lowered
        for marker in (
            "approve suggested answers",
            "use suggested answers",
            "use those answers",
            "approve answers",
        )
    )

def _is_checkpoint_reject_message(user_message: str) -> bool:
    msg = (user_message or "").lower()
    return "reject checkpoint" in msg or "revise input" in msg or "do not approve" in msg

def _message_requests_diagram_generation(msg: str) -> bool:
    if "drawio" in msg or "draw.io" in msg or "topology file" in msg:
        return True
    if "diagram" not in msg:
        return False
    if any(
        marker in msg
        for marker in (
            "generate diagram",
            "generate a diagram",
            "build diagram",
            "build a diagram",
            "create diagram",
            "create a diagram",
            "architecture diagram",
        )
    ):
        return True
    if "terraform" in msg and any(
        marker in msg for marker in ("latest diagram", "existing diagram", "current diagram", "approved diagram")
    ):
        return False
    return True

def _message_requests_diagram_revision(msg: str) -> bool:
    revision_marker = any(
        marker in msg
        for marker in (
            "does not show",
            "doesn't show",
            "doesnt show",
            "not showing",
            "missing",
            "add ",
            "update ",
            "revise ",
        )
    )
    visual_target = any(
        marker in msg
        for marker in (
            " bm",
            "bm.",
            "bare metal",
            "fault domain",
            " fd",
            "server",
            "host",
            "ocvs",
            "sddc",
            "esxi",
            "vsphere",
        )
    )
    return revision_marker and visual_target

def _requested_generation_tools(user_message: str) -> set[str]:
    msg = (user_message or "").lower()
    requested: set[str] = set()
    generation_or_export = any(token in msg for token in ("build", "create", "generate", "draft", "make", "export", "download"))
    bom_artifact_terms = (
        "bom",
        "bill of materials",
        "xlsx",
        "xlxs",
        "xlsc",
        "excel",
        "spreadsheet",
        "workbook",
    )
    bom_pricing_terms = ("pricing", "priced", "sku", "skus")
    if any(term in msg for term in bom_artifact_terms) or (
        generation_or_export and any(term in msg for term in bom_pricing_terms)
    ):
        requested.add("generate_bom")
    if _message_requests_diagram_generation(msg) or _message_requests_diagram_revision(msg):
        requested.add("generate_diagram")
    if "terraform" in msg or "iac" in msg:
        requested.add("generate_terraform")
    if "pov" in msg or "point of view" in msg:
        requested.add("generate_pov")
    if "jep" in msg or "joint execution plan" in msg:
        requested.add("generate_jep")
    if "waf" in msg or "well-architected" in msg or "well architected" in msg:
        requested.add("generate_waf")
    return requested

def _parse_specialist_answers_from_user(
    *,
    pending_checkpoint: dict[str, Any],
    user_message: str,
) -> list[dict[str, Any]]:
    questions = [dict(item) for item in list(pending_checkpoint.get("questions", []) or []) if isinstance(item, dict)]
    if _is_specialist_question_approve_message(user_message):
        answers: list[dict[str, Any]] = []
        for item in questions:
            suggested = str(item.get("suggested_answer", "") or "").strip()
            if suggested:
                answers.append({**item, "final_answer": suggested})
        return answers

    overrides = _parse_explicit_specialist_answers(
        pending_checkpoint=pending_checkpoint,
        user_message=user_message,
    )

    answers = []
    for item in questions:
        question_id = str(item.get("question_id", "") or "").strip()
        final_answer = overrides.get(question_id, "")
        if not final_answer and len(questions) == 1 and str(user_message or "").strip() and ":" not in str(user_message or ""):
            final_answer = str(user_message or "").strip()
        if not final_answer:
            final_answer = str(item.get("suggested_answer", "") or "").strip()
        if final_answer:
            final_answer = _coerce_specialist_answer(question_id, final_answer)
            answers.append({**item, "final_answer": final_answer})
    return answers

def _specialist_question_id_map(questions: list[dict[str, Any]]) -> dict[str, str]:
    question_ids = [
        str(item.get("question_id", "") or "").strip()
        for item in questions
        if str(item.get("question_id", "") or "").strip()
    ]
    question_id_map: dict[str, str] = {}
    for question_id in question_ids:
        for alias in _specialist_question_id_aliases(question_id):
            question_id_map.setdefault(alias, question_id)
    return question_id_map

def _parse_explicit_specialist_answers(
    *,
    pending_checkpoint: dict[str, Any],
    user_message: str,
) -> dict[str, str]:
    questions = [dict(item) for item in list(pending_checkpoint.get("questions", []) or []) if isinstance(item, dict)]
    question_id_map = _specialist_question_id_map(questions)
    overrides: dict[str, str] = {}
    for line in str(user_message or "").splitlines():
        parsed = _parse_specialist_answer_line(line, question_id_map)
        if not parsed:
            continue
        qid, value = parsed
        overrides[qid] = value
    return overrides

def _normalize_specialist_question_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", str(value or "").strip().lower()).strip(".")

def _specialist_question_id_aliases(question_id: str) -> set[str]:
    normalized = _normalize_specialist_question_id(question_id)
    aliases = {normalized} if normalized else set()
    alias_groups = (
        {"components.scope", "workload.components"},
        {"regions.mode", "region.mode", "topology.scope", "regions.count"},
        {"ha.ads", "availability.domains", "ha.availability.domains", "availability.domains.per.region"},
        {"constraints.region", "region", "preferred.region"},
        {"bom.compute.gpu", "compute.gpu", "gpu", "gpu.mode", "compute.type"},
        {"bom.compute.ocpu", "compute.ocpu", "ocpu", "workload.ocpu"},
        {"bom.compute.memory", "compute.memory", "memory", "ram", "workload.memory"},
        {"bom.storage.block", "storage.block", "block.storage", "block.volume", "storage"},
        {"bom.storage.vpu", "storage.vpu", "vpu", "block.vpu"},
        {"bom.network.load.balancer", "network.load.balancer", "load.balancer", "lb"},
        {"bom.storage.object", "storage.object", "object.storage", "bucket"},
        {"bom.network.connectivity", "network.connectivity", "connectivity", "on.prem.connectivity"},
        {"bom.budget", "budget", "monthly.budget", "cost.max.monthly"},
        {"workload.sizing", "bom.sizing", "sizing"},
    )
    for group in alias_groups:
        if normalized in group:
            aliases.update(group)
    parts = [part for part in normalized.split(".") if part]
    if len(parts) >= 2:
        first = parts[0]
        tail = ".".join(parts[1:])
        if first.endswith("s"):
            aliases.add(".".join([first[:-1], tail]))
        else:
            aliases.add(".".join([first + "s", tail]))
    return aliases

def _parse_specialist_answer_line(
    line: str,
    question_id_map: dict[str, str],
) -> tuple[str, str] | None:
    text = str(line or "").strip()
    if not text:
        return None
    for separator in (":", ","):
        if separator in text:
            raw_id, raw_answer = text.split(separator, 1)
            canonical = question_id_map.get(_normalize_specialist_question_id(raw_id))
            answer = raw_answer.strip()
            if canonical and answer:
                return canonical, answer

    for alias in sorted(question_id_map, key=len, reverse=True):
        display_alias = alias.replace(".", r"\s*\.\s*")
        match = re.match(rf"^\s*{display_alias}\s*\.\s+(.+?)\s*$", text, flags=re.IGNORECASE)
        if match:
            answer = match.group(1).strip()
            if answer:
                return question_id_map[alias], answer
    return None

def _message_supersedes_pending_specialist_questions(
    *,
    user_message: str,
    pending_checkpoint: dict[str, Any],
) -> bool:
    if not _requested_generation_tools(user_message):
        return False
    if _is_specialist_question_approve_message(user_message) or _is_checkpoint_reject_message(user_message):
        return False
    if _parse_explicit_specialist_answers(
        pending_checkpoint=pending_checkpoint,
        user_message=user_message,
    ):
        return False
    return True

def _is_specialist_question_retry_message(user_message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(user_message or "").lower()).strip()
    if not normalized:
        return False
    retry_messages = {
        "try again",
        "please try again",
        "try again please",
        "retry",
        "rerun",
        "run again",
        "continue",
        "go ahead",
        "proceed",
        "please continue",
        "please retry",
        "try it again",
        "run it again",
    }
    retry_phrases = ("try again", "retry", "rerun", "run again", "continue", "proceed")
    return normalized in retry_messages or any(phrase in normalized for phrase in retry_phrases)

def _recover_specialist_answers_from_history(
    *,
    pending_checkpoint: dict[str, Any],
    conversation_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    questions = [dict(item) for item in list(pending_checkpoint.get("questions", []) or []) if isinstance(item, dict)]
    required_ids = {
        str(item.get("question_id", "") or "").strip()
        for item in questions
        if str(item.get("question_id", "") or "").strip()
    }
    if not required_ids:
        return []

    prompt = str(pending_checkpoint.get("prompt", "") or "").strip()
    start_index = -1
    if prompt:
        for idx, turn in enumerate(conversation_history or []):
            if str(turn.get("role", "") or "") != "assistant":
                continue
            content = str(turn.get("content", "") or "")
            if content.strip() == prompt or prompt in content:
                start_index = idx

    candidate_turns = [
        turn
        for turn in list(conversation_history or [])[start_index + 1 :]
        if str(turn.get("role", "") or "") == "user"
    ]
    for turn in reversed(candidate_turns):
        overrides = _parse_explicit_specialist_answers(
            pending_checkpoint=pending_checkpoint,
            user_message=str(turn.get("content", "") or ""),
        )
        if required_ids <= set(overrides):
            return [
                {
                    **item,
                    "final_answer": _coerce_specialist_answer(
                        str(item.get("question_id", "") or "").strip(),
                        overrides[str(item.get("question_id", "") or "").strip()],
                    ),
                }
                for item in questions
                if str(item.get("question_id", "") or "").strip() in overrides
            ]
    return []

async def _handle_pending_specialist_questions(
    *,
    pending_checkpoint: dict[str, Any],
    user_message: str,
    conversation_history: list[dict[str, Any]],
    context: dict[str, Any],
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str,
    max_refinements: int,
) -> tuple[str, dict[str, Any] | None, str]:
    if _is_checkpoint_reject_message(user_message):
        context_store.clear_pending_checkpoint(context)
        context_store.set_open_questions(context, [])
        context_store.write_context(store, customer_id, context)
        return (
            "I cleared the pending specialist question batch. Revise the request and rerun when ready.",
            None,
            "",
        )

    answers: list[dict[str, Any]] = []
    if (
        not _parse_explicit_specialist_answers(
            pending_checkpoint=pending_checkpoint,
            user_message=user_message,
        )
        and _is_specialist_question_retry_message(user_message)
    ):
        answers = _recover_specialist_answers_from_history(
            pending_checkpoint=pending_checkpoint,
            conversation_history=conversation_history,
        )
    if not answers:
        answers = _parse_specialist_answers_from_user(
            pending_checkpoint=pending_checkpoint,
            user_message=user_message,
        )
    if not answers:
        return pending_checkpoint.get("prompt", ""), None, ""

    for item in answers:
        context_store.record_resolved_question(
            context,
            {
                "id": str(uuid.uuid4()),
                **item,
                "source": "user_confirmed",
                "timestamp": _now(),
                "request_intent": str(pending_checkpoint.get("original_request", "") or ""),
            },
        )
    context_store.clear_pending_checkpoint(context)
    context_store.set_open_questions(context, [])
    context_store.write_context(store, customer_id, context)

    tool_name = str(pending_checkpoint.get("tool_name", "") or "")
    tool_args = _apply_resolved_answers_to_tool_args(
        tool_name=tool_name,
        args=dict(pending_checkpoint.get("tool_args", {}) or {}),
        answers=answers,
    )
    result_summary, artifact_key, result_data = await _execute_tool(
        tool_name,
        tool_args,
        customer_id=customer_id,
        customer_name=customer_name,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        specialist_mode=specialist_mode,
        user_message=str(pending_checkpoint.get("original_request", "") or user_message),
        max_refinements=max_refinements,
        decision_context=decision_context_builder.build_decision_context(
            user_message=str(pending_checkpoint.get("original_request", "") or user_message),
            context=context,
        ),
    )
    return (
        result_summary,
        {
            "tool": tool_name,
            "args": tool_args,
            "result_summary": result_summary,
            "result_data": result_data,
        },
        artifact_key,
    )

def _build_decision_context_block(decision_context: dict[str, Any] | None) -> str:
    if not isinstance(decision_context, dict) or not decision_context:
        return ""
    return (
        "[Decision Context]\n"
        + json.dumps(decision_context, indent=2, ensure_ascii=True)
        + "\n[End Decision Context]\n"
    )

def _decision_context_hash(decision_context: dict[str, Any] | None) -> str:
    raw = json.dumps(decision_context or {}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

def _strip_injected_guidance_blocks(text: str) -> str:
    cleaned = str(text or "")
    for start, end in _INJECTED_GUIDANCE_BLOCKS:
        while True:
            start_idx = cleaned.find(start)
            if start_idx == -1:
                break
            end_idx = cleaned.find(end, start_idx)
            if end_idx == -1:
                cleaned = cleaned[:start_idx].rstrip()
                break
            cleaned = (cleaned[:start_idx] + cleaned[end_idx + len(end):]).strip()
    return cleaned.strip()

def _normalize_assumption_payload(assumption: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(assumption.get("id", "") or "").strip(),
        "statement": str(assumption.get("statement", "") or "").strip(),
        "reason": str(assumption.get("reason", "") or "").strip(),
        "risk": str(assumption.get("risk", "low") or "low").strip().lower(),
    }

def _merge_assumption_lists(
    existing: list[dict[str, Any]] | None,
    additions: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in [*(existing or []), *(additions or [])]:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_assumption_payload(raw)
        statement = normalized["statement"]
        if not statement:
            continue
        key = normalized["id"] or statement.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged

def _merge_decision_context(
    base_context: dict[str, Any] | None,
    overlay_context: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(base_context or {})
    overlay = dict(overlay_context or {})
    if not base:
        base = {
            "goal": "",
            "constraints": {},
            "assumptions": [],
            "success_criteria": [],
            "missing_inputs": [],
            "requires_user_confirmation": False,
        }

    base["goal"] = str(overlay.get("goal", "") or base.get("goal", "") or "")
    merged_constraints = dict(base.get("constraints", {}) or {})
    for key, value in dict(overlay.get("constraints", {}) or {}).items():
        if value not in (None, "", [], {}):
            merged_constraints[key] = value
    base["constraints"] = merged_constraints
    base["assumptions"] = _merge_assumption_lists(
        list(base.get("assumptions", []) or []),
        list(overlay.get("assumptions", []) or []),
    )
    base["success_criteria"] = list(dict.fromkeys([
        *list(base.get("success_criteria", []) or []),
        *list(overlay.get("success_criteria", []) or []),
    ]))
    base["missing_inputs"] = list(dict.fromkeys([
        *list(base.get("missing_inputs", []) or []),
        *list(overlay.get("missing_inputs", []) or []),
    ]))
    base["requires_user_confirmation"] = bool(
        overlay.get("requires_user_confirmation", base.get("requires_user_confirmation", False))
    )
    return base

def _bom_call_was_memory_revision(result_data: dict[str, Any]) -> bool:
    trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
    return str(trace.get("bom_context_source", result_data.get("bom_context_source", "")) or "") == "bom_revision"
