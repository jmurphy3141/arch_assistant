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
from typing import Optional

from agent.document_store import list_notes
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)


# ── Schema helpers ────────────────────────────────────────────────────────────

CONTEXT_SCHEMA_VERSION = "1.0"


def _context_key(customer_id: str) -> str:
    return f"context/{customer_id}/context.json"


def _empty_context(customer_id: str, customer_name: str = "") -> dict:
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "customer_id":    customer_id,
        "customer_name":  customer_name,
        "last_updated":   "",
        "agents":         {},
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
    try:
        return json.loads(store.get(key).decode("utf-8"))
    except KeyError:
        return _empty_context(customer_id, customer_name)


def write_context(
    store: ObjectStoreBase,
    customer_id: str,
    context: dict,
) -> None:
    """Write the context file back to the bucket."""
    context["last_updated"] = datetime.now(timezone.utc).isoformat()
    key = _context_key(customer_id)
    store.put(
        key,
        json.dumps(context, indent=2).encode("utf-8"),
        "application/json",
    )
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


# ── Prompt summary ────────────────────────────────────────────────────────────

def build_context_summary(context: dict) -> str:
    """
    Build a concise text block summarising all prior agent outputs.
    Injected near the top of every agent's prompt so the LLM knows the
    full engagement state without reading every output in full.
    """
    agents = context.get("agents", {})
    if not agents:
        return ""

    lines = ["Prior agent outputs (use as engagement context):"]

    if "diagram" in agents:
        d = agents["diagram"]
        lines.append(
            f"  • Architecture Diagram (v{d.get('version', '?')}): "
            f"{d.get('node_count', '?')} nodes, "
            f"diagram_name={d.get('diagram_name', '?')!r}, "
            f"key={d.get('diagram_key', 'not yet generated')}"
        )

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

    return "\n".join(lines)
