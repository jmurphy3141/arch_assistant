"""
agent/context_store.py
-----------------------
Shared context file for the OCI agent fleet.

Every agent in the fleet reads and writes a single JSON file per customer:
    context/{customer_id}/context.json

This file serves three purposes:
  1. Prevents note re-ingestion — each agent tracks which notes it has
     already incorporated in its own ``notes_incorporated`` list.
  2. Passes structured output references between agents — diagram key, doc
     versions, BOM summary — without agents having to re-read full outputs.
  3. Provides a concise context summary that is injected into every prompt so
     each agent is aware of what the rest of the fleet has produced.

Typical agent run:
    context   = read_context(store, customer_id)
    new_keys, new_text = get_new_notes(store, context, agent_name)
    summary   = build_context_summary(context)
    # ... build prompt using summary + new_text + own previous output ...
    # ... generate output ...
    context   = record_agent_run(context, agent_name, new_keys, agent_data)
    write_context(store, customer_id, context)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from agent.document_store import list_notes
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)


# ── Schema helpers ────────────────────────────────────────────────────────────

CONTEXT_SCHEMA_VERSION = "1.0"


def _empty_archie_state() -> dict[str, Any]:
    return {
        "engagement_summary": "",
        "latest_notes_summary": "",
        "latest_approved_constraints": {},
        "latest_approved_assumptions": [],
        "open_questions": [],
        "resolved_questions": [],
        "change_history": [],
        "update_batches": [],
        "pending_update": None,
    }


def _context_key(customer_id: str) -> str:
    return f"customers/{customer_id}/context/context.json"


def _legacy_context_key(customer_id: str) -> str:
    return f"context/{customer_id}/context.json"


def _empty_context(customer_id: str, customer_name: str = "") -> dict:
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "customer_id":    customer_id,
        "customer_name":  customer_name,
        "last_updated":   "",
        "agents":         {},
        "archie":         _empty_archie_state(),
        "latest_decision_context": {},
        "decision_log":   [],
        "pending_checkpoint": None,
    }


# ── Read / write ──────────────────────────────────────────────────────────────

def read_context(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str = "",
) -> dict:
    """
    Read the context file for a customer.
    Returns an empty context structure if none exists yet.
    ``customer_name`` is only used when creating a fresh context.
    """
    key = _context_key(customer_id)
    legacy_key = _legacy_context_key(customer_id)
    try:
        return _normalize_context(json.loads(store.get(key).decode("utf-8")), customer_id, customer_name)
    except KeyError:
        try:
            return _normalize_context(json.loads(store.get(legacy_key).decode("utf-8")), customer_id, customer_name)
        except KeyError:
            return _empty_context(customer_id, customer_name)


def write_context(
    store: ObjectStoreBase,
    customer_id: str,
    context: dict,
) -> None:
    """Write the context file back to the bucket."""
    context = _normalize_context(context, customer_id, context.get("customer_name", ""))
    context["last_updated"] = datetime.now(timezone.utc).isoformat()
    key = _context_key(customer_id)
    legacy_key = _legacy_context_key(customer_id)
    payload = json.dumps(context, indent=2).encode("utf-8")
    store.put(key, payload, "application/json")
    if legacy_key != key:
        store.put(legacy_key, payload, "application/json")
    logger.debug("Context written: customer_id=%s", customer_id)


# ── Note diffing ──────────────────────────────────────────────────────────────

def get_new_notes(
    store: ObjectStoreBase,
    context: dict,
    agent_name: str,
) -> tuple[list[str], str]:
    """
    Return notes that this agent has NOT yet incorporated.

    Each agent maintains its own ``notes_incorporated`` list inside
    ``context["agents"][agent_name]``, so agents are independent — POV
    seeing note A does not prevent JEP from also seeing note A.

    Returns:
        (new_note_keys, new_notes_text)
        new_note_keys: list of bucket keys for notes not yet seen by this agent.
        new_notes_text: concatenated text of those notes, formatted for prompts.
                        Empty string if no new notes.
    """
    customer_id = context["customer_id"]
    all_notes   = list_notes(store, customer_id)
    all_keys    = {n["key"] for n in all_notes}

    already_seen = set(
        context.get("agents", {})
        .get(agent_name, {})
        .get("notes_incorporated", [])
    )
    new_keys = [k for k in sorted(all_keys) if k not in already_seen]

    if not new_keys:
        return [], ""

    key_to_name = {n["key"]: n["name"] for n in all_notes}
    parts: list[str] = []
    for key in new_keys:
        try:
            content = store.get(key).decode("utf-8", errors="replace")
            parts.append(f"=== {key_to_name.get(key, key)} ===\n{content}\n")
        except KeyError:
            logger.warning("Note key not found in store: %s", key)

    return new_keys, "\n".join(parts)


# ── Context update ────────────────────────────────────────────────────────────

def record_agent_run(
    context: dict,
    agent_name: str,
    new_note_keys: list[str],
    agent_data: dict,
) -> dict:
    """
    Record the results of an agent run into the context dict (in-place).

    Merges ``new_note_keys`` into the agent's ``notes_incorporated`` list.
    Sets ``last_run`` timestamp.
    Merges ``agent_data`` into the agent's section.

    Returns the modified context dict (same object).
    """
    agents = context.setdefault("agents", {})
    existing = agents.get(agent_name, {})

    merged_notes = sorted(
        set(existing.get("notes_incorporated", [])) | set(new_note_keys)
    )

    agents[agent_name] = {
        **existing,
        **agent_data,
        "notes_incorporated": merged_notes,
        "last_run":           datetime.now(timezone.utc).isoformat(),
    }
    return context


def get_archie_state(context: dict[str, Any]) -> dict[str, Any]:
    archie = context.get("archie")
    if not isinstance(archie, dict):
        archie = _empty_archie_state()
        context["archie"] = archie
    for key, value in _empty_archie_state().items():
        archie.setdefault(key, value() if callable(value) else value)
    return archie


def set_archie_engagement_summary(
    context: dict[str, Any],
    summary: str,
    *,
    note_summary: str = "",
) -> dict[str, Any]:
    archie = get_archie_state(context)
    summary_text = str(summary or "").strip()
    if summary_text:
        archie["engagement_summary"] = summary_text
    note_text = str(note_summary or "").strip()
    if note_text:
        archie["latest_notes_summary"] = note_text
    return context


def set_archie_decision_state(
    context: dict[str, Any],
    *,
    constraints: dict[str, Any] | None = None,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    if isinstance(constraints, dict):
        archie["latest_approved_constraints"] = dict(constraints)
    if isinstance(assumptions, list):
        archie["latest_approved_assumptions"] = [dict(item) for item in assumptions if isinstance(item, dict)]
    return context


def set_open_questions(
    context: dict[str, Any],
    questions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    archie["open_questions"] = [dict(item) for item in (questions or []) if isinstance(item, dict)]
    return context


def record_resolved_question(
    context: dict[str, Any],
    record: dict[str, Any],
    *,
    limit: int = 100,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    resolved = archie.get("resolved_questions", [])
    if not isinstance(resolved, list):
        resolved = []
    question_id = str(record.get("question_id", "") or "")
    prior_id = ""
    if question_id:
        for item in reversed(resolved):
            if str(item.get("question_id", "") or "") == question_id:
                prior_id = str(item.get("id", "") or "")
                if prior_id and not item.get("superseded_by"):
                    item["superseded_by"] = str(record.get("id", "") or "")
                break
    enriched = dict(record)
    if prior_id and not enriched.get("supersedes"):
        enriched["supersedes"] = prior_id
    resolved.append(enriched)
    archie["resolved_questions"] = resolved[-limit:]
    open_questions = [
        item for item in list(archie.get("open_questions", []) or [])
        if str(item.get("question_id", item.get("id", "")) or "") != question_id
    ]
    archie["open_questions"] = open_questions
    return context


def append_change_record(
    context: dict[str, Any],
    record: dict[str, Any],
    *,
    limit: int = 50,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    current = archie.get("change_history", [])
    if not isinstance(current, list):
        current = []
    current.append(dict(record or {}))
    archie["change_history"] = current[-limit:]
    return context


def append_update_batch(
    context: dict[str, Any],
    batch: dict[str, Any],
    *,
    limit: int = 25,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    current = archie.get("update_batches", [])
    if not isinstance(current, list):
        current = []
    current.append(dict(batch or {}))
    archie["update_batches"] = current[-limit:]
    return context


def set_pending_update(
    context: dict[str, Any],
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    archie["pending_update"] = dict(payload) if isinstance(payload, dict) else None
    return context


def get_pending_update(context: dict[str, Any]) -> dict[str, Any] | None:
    archie = get_archie_state(context)
    pending = archie.get("pending_update")
    if not isinstance(pending, dict):
        return None
    return pending


def clear_pending_update(context: dict[str, Any]) -> dict[str, Any]:
    archie = get_archie_state(context)
    archie["pending_update"] = None
    return context


def set_latest_decision_context(context: dict, decision_context: dict[str, Any]) -> dict:
    context["latest_decision_context"] = dict(decision_context or {})
    return context


def append_decision_log(
    context: dict,
    entry: dict[str, Any],
    *,
    limit: int = 25,
) -> dict:
    current = context.get("decision_log", [])
    if not isinstance(current, list):
        current = []
    current.append(dict(entry or {}))
    context["decision_log"] = current[-limit:]
    return context


def get_pending_checkpoint(context: dict) -> dict[str, Any] | None:
    pending = context.get("pending_checkpoint")
    if not isinstance(pending, dict):
        return None
    return pending


def set_pending_checkpoint(context: dict, checkpoint: dict[str, Any] | None) -> dict:
    context["pending_checkpoint"] = dict(checkpoint) if isinstance(checkpoint, dict) else None
    return context


def clear_pending_checkpoint(context: dict) -> dict:
    context["pending_checkpoint"] = None
    return context


# ── Prompt summary ────────────────────────────────────────────────────────────

def build_context_summary(context: dict) -> str:
    """
    Build a concise text block summarising all prior agent outputs.
    Injected near the top of every agent's prompt so the LLM knows the
    full engagement state without reading every output in full.
    """
    agents = context.get("agents", {})
    archie = get_archie_state(context)
    has_archie_state = any(
        archie.get(key)
        for key in (
            "engagement_summary",
            "latest_notes_summary",
            "latest_approved_constraints",
            "latest_approved_assumptions",
            "open_questions",
            "resolved_questions",
            "change_history",
        )
    )
    if not agents and not has_archie_state:
        return ""

    lines = ["Prior agent outputs (use as engagement context):"]
    engagement_summary = str(archie.get("engagement_summary", "") or "").strip()
    latest_notes_summary = str(archie.get("latest_notes_summary", "") or "").strip()
    if engagement_summary:
        lines.append(f"  • Archie Engagement Summary: {engagement_summary}")
    elif latest_notes_summary:
        lines.append(f"  • Archie Notes Summary: {latest_notes_summary}")

    if "diagram" in agents:
        d = agents["diagram"]
        diagram_bits = [
            f"  • Architecture Diagram (v{d.get('version', '?')}): ",
            f"{d.get('node_count', '?')} nodes, ",
            f"diagram_name={d.get('diagram_name', '?')!r}, ",
            f"key={d.get('diagram_key', 'not yet generated')}",
        ]
        deployment_summary = str(d.get("deployment_summary", "") or "").strip()
        if deployment_summary:
            diagram_bits.append(f", deployment={deployment_summary}")
        reference_family = str(d.get("reference_family", "") or "").strip()
        if reference_family:
            reference_mode = str(d.get("reference_mode", "") or "best-effort").strip()
            diagram_bits.append(f", reference={reference_family} ({reference_mode})")
        assumption_count = len(d.get("assumptions_used", []) or [])
        if assumption_count:
            diagram_bits.append(f", assumptions={assumption_count}")
        lines.append("".join(diagram_bits))

    if "bom" in agents:
        b = agents["bom"]
        summary = str(b.get("summary", "") or "").strip()
        line = (
            f"  • BOM (v{b.get('version', '?')}): "
            f"type={b.get('result_type', '?')}, "
            f"estimated_monthly_cost={b.get('estimated_monthly_cost', 'unknown')}, "
            f"line_items={b.get('line_item_count', '?')}, "
            f"payload_ref={b.get('payload_ref', b.get('trace_id', '?'))}"
        )
        if summary:
            line += f", summary={summary!r}"
        lines.append(line)

    if "pov" in agents:
        p = agents["pov"]
        summary = p.get("summary", "")
        lines.append(
            f"  • POV (v{p.get('version', '?')}): "
            + (f"{summary}  " if summary else "")
            + f"key={p.get('key', '?')}"
        )

    if "jep" in agents:
        j = agents["jep"]
        lines.append(
            f"  • JEP (v{j.get('version', '?')}): "
            f"{j.get('duration_days', '?')}-day POC, "
            f"BOM={j.get('bom_source', 'stub')}, "
            f"key={j.get('key', '?')}"
        )

    if "terraform" in agents:
        t = agents["terraform"]
        lines.append(
            f"  • Terraform (v{t.get('version', '?')}): "
            f"{t.get('file_count', '?')} files, "
            f"prefix={t.get('prefix_key', '?')}"
        )

    if "waf" in agents:
        w = agents["waf"]
        lines.append(
            f"  • WAF Review (v{w.get('version', '?')}): "
            f"overall={w.get('overall_rating', '?')}, "
            f"key={w.get('key', '?')}"
        )

    latest_decision_context = context.get("latest_decision_context", {})
    if isinstance(latest_decision_context, dict) and latest_decision_context:
        constraints = latest_decision_context.get("constraints", {}) or {}
        assumptions = latest_decision_context.get("assumptions", []) or []
        lines.append(
            "  • Latest Decision Context: "
            f"goal={latest_decision_context.get('goal', '')!r}, "
            f"region={constraints.get('region') or 'unspecified'}, "
            f"budget={constraints.get('cost_max_monthly') if constraints.get('cost_max_monthly') is not None else 'unspecified'}, "
            f"assumptions={len(assumptions)}"
        )

    approved_constraints = archie.get("latest_approved_constraints", {}) or {}
    approved_assumptions = archie.get("latest_approved_assumptions", []) or []
    if approved_constraints or approved_assumptions:
        lines.append(
            "  • Archie Approved State: "
            f"constraints={json.dumps(approved_constraints, ensure_ascii=True, sort_keys=True)[:220]}, "
            f"assumptions={len(list(approved_assumptions or []))}"
        )

    resolved_questions = archie.get("resolved_questions", []) or []
    if resolved_questions:
        latest = resolved_questions[-3:]
        rendered = []
        for item in latest:
            question_id = str(item.get("question_id", "") or item.get("id", "") or "question")
            answer = str(item.get("final_answer", "") or item.get("final_user_answer", "") or item.get("suggested_answer", "") or "").strip()
            if answer:
                rendered.append(f"{question_id}={answer}")
        if rendered:
            lines.append("  • Archie Resolved Questions: " + "; ".join(rendered))

    open_questions = archie.get("open_questions", []) or []
    if open_questions:
        prompts = []
        for item in open_questions[:3]:
            prompt = str(item.get("question", "") or item.get("prompt", "") or "").strip()
            if prompt:
                prompts.append(prompt)
        if prompts:
            lines.append("  • Archie Open Questions: " + " | ".join(prompts))

    change_history = archie.get("change_history", []) or []
    if change_history:
        latest_change = change_history[-1]
        lines.append(
            "  • Archie Latest Change Batch: "
            f"status={latest_change.get('status', 'recorded')}, "
            f"request={str(latest_change.get('change_request', '') or '')[:180]}"
        )

    pending_checkpoint = get_pending_checkpoint(context)
    if pending_checkpoint:
        lines.append(
            "  • Pending Checkpoint: "
            f"type={pending_checkpoint.get('type', '?')}, "
            f"status={pending_checkpoint.get('status', '?')}, "
            f"recommended_action={pending_checkpoint.get('recommended_action', '')!r}"
        )

    return "\n".join(lines)


def _normalize_context(context: dict[str, Any], customer_id: str, customer_name: str = "") -> dict[str, Any]:
    normalized = dict(context or {})
    normalized.setdefault("schema_version", CONTEXT_SCHEMA_VERSION)
    normalized.setdefault("customer_id", customer_id)
    normalized.setdefault("customer_name", customer_name)
    normalized.setdefault("last_updated", "")
    normalized.setdefault("agents", {})
    archie = normalized.get("archie")
    if not isinstance(archie, dict):
        archie = _empty_archie_state()
    for key, value in _empty_archie_state().items():
        archie.setdefault(key, value() if callable(value) else value)
    normalized["archie"] = archie
    normalized.setdefault("latest_decision_context", {})
    normalized.setdefault("decision_log", [])
    normalized.setdefault("pending_checkpoint", None)
    return normalized
