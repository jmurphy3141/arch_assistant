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
import hashlib
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
        "client_facts": {},
        "memory": _empty_archie_memory(),
        "facts_summary": "",
        "infrastructure_profile": {},
        "work_products": {"bom": {"latest_version": 0, "versions": []}},
        "latest_approved_constraints": {},
        "latest_approved_assumptions": [],
        "open_questions": [],
        "resolved_questions": [],
        "change_history": [],
        "update_batches": [],
        "pending_update": None,
    }


def _empty_archie_memory() -> dict[str, Any]:
    return {
        "client_facts": {
            "sizing": {},
            "workloads": [],
            "platform": "",
            "os_mix": [],
            "databases": [],
            "connectivity": {},
            "region_geography": "",
            "dr": {},
            "security": {},
            "exclusions": [],
        },
        "architecture_state": {
            "components": [],
            "relationships": [],
            "data_flows": [],
            "exposure": "",
            "explicitly_unnecessary_services": [],
        },
        "work_products": {
            "latest_bom": {},
            "latest_diagram": {},
            "latest_waf": {},
            "latest_terraform": {},
            "latest_pov": {},
            "latest_jep": {},
        },
        "assumptions": {
            "sizing_basis": "",
            "regional_pricing_assumption": "",
            "unresolved_gaps": [],
            "latest_approved": [],
        },
        "memory_summary": "",
        "updated_at": "",
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


def reset_context(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str = "",
) -> dict:
    """Overwrite current and legacy context keys with a fresh empty context."""
    context = _empty_context(customer_id, customer_name)
    write_context(store, customer_id, context)
    return context


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
    refresh_archie_memory(context)
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


def merge_archie_infrastructure_profile(
    context: dict[str, Any],
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    incoming = profile if isinstance(profile, dict) else {}
    if not incoming:
        return context

    existing = archie.get("infrastructure_profile")
    if not isinstance(existing, dict):
        existing = {}
    archie["infrastructure_profile"] = _deep_merge_dict(existing, incoming)
    refresh_archie_memory(context)
    return context


def merge_archie_client_facts(
    context: dict[str, Any],
    facts: dict[str, Any] | None,
) -> dict[str, Any]:
    archie = get_archie_state(context)
    incoming = facts if isinstance(facts, dict) else {}
    if not incoming:
        return context

    existing = archie.get("client_facts")
    if not isinstance(existing, dict):
        existing = {}
    merged = _deep_merge_dict(existing, incoming)
    archie["client_facts"] = merged
    archie["facts_summary"] = _summarize_client_facts(merged)
    refresh_archie_memory(context)
    return context


def refresh_archie_memory(context: dict[str, Any]) -> dict[str, Any]:
    """Rebuild canonical ``archie.memory`` from durable Archie state."""
    archie = get_archie_state(context)
    facts = archie.get("client_facts", {}) if isinstance(archie.get("client_facts"), dict) else {}
    infra = archie.get("infrastructure_profile", {}) if isinstance(archie.get("infrastructure_profile"), dict) else {}
    agents = context.get("agents", {}) if isinstance(context.get("agents"), dict) else {}
    latest_decision_context = context.get("latest_decision_context", {})
    constraints = latest_decision_context.get("constraints", {}) if isinstance(latest_decision_context, dict) else {}

    client_facts = {
        "sizing": dict(facts.get("infrastructure", infra) or {}) if isinstance(facts.get("infrastructure", infra), dict) else {},
        "workloads": _memory_list(facts.get("workloads") or infra.get("workload_notes")),
        "platform": str(facts.get("platform") or infra.get("platform") or "").strip(),
        "os_mix": _memory_list(facts.get("os_mix")) or _memory_os_mix(facts),
        "databases": _memory_list(facts.get("databases")) or _memory_database_mix(facts),
        "connectivity": _memory_dict(facts.get("connectivity") or infra.get("connectivity")),
        "region_geography": str(
            facts.get("region")
            or facts.get("geography")
            or constraints.get("region")
            or ""
        ).strip(),
        "dr": _memory_dict(facts.get("dr") or infra.get("dr")),
        "security": _memory_dict(facts.get("security")),
        "exclusions": _memory_list(facts.get("scope_exclusions")),
    }

    diagram = agents.get("diagram", {}) if isinstance(agents.get("diagram"), dict) else {}
    bom = agents.get("bom", {}) if isinstance(agents.get("bom"), dict) else {}
    waf = agents.get("waf", {}) if isinstance(agents.get("waf"), dict) else {}
    terraform = agents.get("terraform", {}) if isinstance(agents.get("terraform"), dict) else {}
    pov = agents.get("pov", {}) if isinstance(agents.get("pov"), dict) else {}
    jep = agents.get("jep", {}) if isinstance(agents.get("jep"), dict) else {}
    components = _memory_components_from_state(client_facts, diagram, latest_bom_work_product(context))
    architecture_state = {
        "components": components,
        "relationships": _memory_list(diagram.get("relationships")),
        "data_flows": _memory_list(diagram.get("data_flows")),
        "exposure": _memory_exposure(client_facts, diagram),
        "explicitly_unnecessary_services": list(client_facts["exclusions"]),
    }

    approved_assumptions = [
        dict(item)
        for item in list(archie.get("latest_approved_assumptions", []) or [])
        if isinstance(item, dict)
    ]
    decision_assumptions = [
        dict(item)
        for item in list((latest_decision_context or {}).get("assumptions", []) or [])
        if isinstance(item, dict)
    ] if isinstance(latest_decision_context, dict) else []
    assumptions = {
        "sizing_basis": _memory_sizing_basis(client_facts),
        "regional_pricing_assumption": _memory_regional_pricing_assumption(client_facts, approved_assumptions + decision_assumptions),
        "unresolved_gaps": _memory_list((latest_decision_context or {}).get("missing_inputs") if isinstance(latest_decision_context, dict) else []),
        "latest_approved": approved_assumptions or decision_assumptions,
    }

    memory = {
        "client_facts": client_facts,
        "architecture_state": architecture_state,
        "work_products": {
            "latest_bom": _memory_latest_bom(latest_bom_work_product(context), bom),
            "latest_diagram": _memory_compact_work_product(diagram),
            "latest_waf": _memory_compact_work_product(waf),
            "latest_terraform": _memory_compact_work_product(terraform),
            "latest_pov": _memory_compact_work_product(pov),
            "latest_jep": _memory_compact_work_product(jep),
        },
        "assumptions": assumptions,
        "memory_summary": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    memory["memory_summary"] = _summarize_archie_memory(memory)
    archie["memory"] = memory
    return context


def get_archie_memory(context: dict[str, Any]) -> dict[str, Any]:
    refresh_archie_memory(context)
    archie = get_archie_state(context)
    memory = archie.get("memory")
    return memory if isinstance(memory, dict) else _empty_archie_memory()


def render_archie_memory(memory: dict[str, Any] | None) -> str:
    payload = memory if isinstance(memory, dict) else _empty_archie_memory()
    return (
        "[Archie Canonical Memory]\n"
        "Use the provided memory as the source of truth. Do not ask for information already present in memory "
        "unless the user clearly indicates it has changed.\n"
        f"{json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)}\n"
        "[End Archie Canonical Memory]"
    )


def archie_memory_hash(memory: dict[str, Any] | None) -> str:
    payload = memory if isinstance(memory, dict) else _empty_archie_memory()
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _memory_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _memory_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [item for item in value if item not in (None, "", [], {})]
    if value in (None, "", [], {}):
        return []
    return [value]


def _memory_os_mix(facts: dict[str, Any]) -> list[str]:
    text = json.dumps(facts, ensure_ascii=True).lower()
    labels = []
    for token, label in (("linux", "Linux"), ("windows", "Windows"), ("oracle linux", "Oracle Linux")):
        if token in text and label not in labels:
            labels.append(label)
    return labels


def _memory_database_mix(facts: dict[str, Any]) -> list[str]:
    text = json.dumps(facts, ensure_ascii=True).lower()
    labels = []
    for token, label in (("oracle", "Oracle Database"), ("sql", "SQL Server"), ("postgres", "PostgreSQL"), ("mysql", "MySQL")):
        if token in text and label not in labels:
            labels.append(label)
    return labels


def _memory_components_from_state(
    client_facts: dict[str, Any],
    diagram: dict[str, Any],
    latest_bom: dict[str, Any] | None,
) -> list[str]:
    components: list[str] = []
    for item in _memory_list(diagram.get("components")):
        text = str(item).strip()
        if text and text not in components:
            components.append(text)
    baseline = latest_bom.get("baseline", {}) if isinstance(latest_bom, dict) and isinstance(latest_bom.get("baseline"), dict) else {}
    for row in list(baseline.get("line_items", []) or [])[:50]:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or row.get("category") or row.get("sku") or "").strip()
        if desc and desc not in components:
            components.append(desc)
    for workload in client_facts.get("workloads", []) or []:
        text = str(workload).strip()
        if text and text not in components:
            components.append(text)
    return components[:80]


def _memory_exposure(client_facts: dict[str, Any], diagram: dict[str, Any]) -> str:
    explicit = str(diagram.get("exposure", "") or "").strip()
    if explicit:
        return explicit
    connectivity = client_facts.get("connectivity", {}) if isinstance(client_facts.get("connectivity"), dict) else {}
    if connectivity.get("internet_bandwidth") or connectivity.get("internet"):
        return "public internet plus private connectivity"
    if any(connectivity.get(key) for key in ("mpls", "sd_wan", "fastconnect", "vpn")):
        return "private connectivity"
    return ""


def _memory_sizing_basis(client_facts: dict[str, Any]) -> str:
    sizing = client_facts.get("sizing", {}) if isinstance(client_facts.get("sizing"), dict) else {}
    if not sizing:
        return ""
    return "Use the largest explicit capacity values present in memory for first-pass BOM sizing."


def _memory_regional_pricing_assumption(
    client_facts: dict[str, Any],
    assumptions: list[dict[str, Any]],
) -> str:
    for item in assumptions:
        statement = str(item.get("statement", "") or "").strip()
        if "pricing" in statement.lower() or "region" in statement.lower():
            return statement
    region = str(client_facts.get("region_geography", "") or "").strip()
    if region:
        return f"Use {region} as the region/geography anchor; if exact regional pricing is unavailable, state the pricing assumption."
    return ""


def _memory_latest_bom(latest_bom: dict[str, Any] | None, bom_agent: dict[str, Any]) -> dict[str, Any]:
    if isinstance(latest_bom, dict) and latest_bom:
        return {
            "version": latest_bom.get("version"),
            "created_at": latest_bom.get("created_at"),
            "grounding": latest_bom.get("grounding"),
            "context_source": latest_bom.get("context_source"),
            "baseline": latest_bom.get("baseline", {}),
            "xlsx": latest_bom.get("xlsx", bom_agent.get("bom_xlsx", {})),
        }
    return _memory_compact_work_product(bom_agent)


def _memory_compact_work_product(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    keep = (
        "version",
        "key",
        "diagram_key",
        "artifact_ref",
        "summary",
        "overall_rating",
        "prefix_key",
        "xlsx_artifact_key",
        "xlsx_filename",
        "bom_xlsx",
        "estimated_monthly_cost",
        "line_item_count",
        "context_source",
    )
    return {key: value.get(key) for key in keep if value.get(key) not in (None, "", [], {})}


def _summarize_archie_memory(memory: dict[str, Any]) -> str:
    facts = memory.get("client_facts", {}) if isinstance(memory.get("client_facts"), dict) else {}
    parts: list[str] = []
    region = str(facts.get("region_geography", "") or "").strip()
    if region:
        parts.append(f"region/geography={region}")
    platform = str(facts.get("platform", "") or "").strip()
    if platform:
        parts.append(f"platform={platform}")
    if facts.get("workloads"):
        parts.append("workloads=" + ", ".join(str(item) for item in list(facts.get("workloads", []) or [])[:6]))
    sizing = facts.get("sizing", {}) if isinstance(facts.get("sizing"), dict) else {}
    if sizing:
        parts.append("sizing=" + json.dumps(sizing, ensure_ascii=True, sort_keys=True)[:240])
    connectivity = facts.get("connectivity", {}) if isinstance(facts.get("connectivity"), dict) else {}
    if connectivity:
        parts.append("connectivity=" + json.dumps(connectivity, ensure_ascii=True, sort_keys=True)[:180])
    dr = facts.get("dr", {}) if isinstance(facts.get("dr"), dict) else {}
    if dr:
        parts.append("dr=" + json.dumps(dr, ensure_ascii=True, sort_keys=True)[:160])
    security = facts.get("security", {}) if isinstance(facts.get("security"), dict) else {}
    if security:
        parts.append("security=" + json.dumps(security, ensure_ascii=True, sort_keys=True)[:160])
    latest_bom = ((memory.get("work_products", {}) or {}).get("latest_bom", {}) if isinstance(memory.get("work_products"), dict) else {})
    if isinstance(latest_bom, dict) and latest_bom:
        parts.append(f"latest_bom=v{latest_bom.get('version', '?')} grounding={latest_bom.get('grounding', latest_bom.get('context_source', 'unknown'))}")
    return "; ".join(parts)[:1200]


def _summarize_client_facts(facts: dict[str, Any]) -> str:
    if not isinstance(facts, dict) or not facts:
        return ""
    parts: list[str] = []
    region = str(facts.get("region", "") or facts.get("geography", "") or "").strip()
    if region:
        parts.append(f"region/geography={region}")
    platform = str(facts.get("platform", "") or "").strip()
    if platform:
        parts.append(f"platform={platform}")
    workloads = facts.get("workloads")
    if isinstance(workloads, list) and workloads:
        parts.append("workloads=" + ", ".join(str(item) for item in workloads[:6]))
    sizing = facts.get("infrastructure")
    if isinstance(sizing, dict):
        cpu = sizing.get("cpu", {}) if isinstance(sizing.get("cpu"), dict) else {}
        memory = sizing.get("memory", {}) if isinstance(sizing.get("memory"), dict) else {}
        storage = sizing.get("storage", {}) if isinstance(sizing.get("storage"), dict) else {}
        size_bits = []
        if cpu.get("logical_cores"):
            size_bits.append(f"cores={cpu.get('logical_cores')}")
        if memory.get("used_gb") or memory.get("total_gb"):
            size_bits.append(f"memory_gb={memory.get('used_gb') or memory.get('total_gb')}")
        if storage.get("used_tb") or storage.get("total_tb"):
            size_bits.append(f"storage_tb={storage.get('used_tb') or storage.get('total_tb')}")
        if size_bits:
            parts.append("sizing=" + ", ".join(size_bits))
    connectivity = facts.get("connectivity")
    if isinstance(connectivity, dict) and connectivity:
        parts.append(
            "connectivity="
            + ", ".join(f"{key}={value}" for key, value in connectivity.items() if value not in (None, "", [], {}))
        )
    security = facts.get("security")
    if isinstance(security, dict) and security:
        parts.append(
            "security="
            + ", ".join(f"{key}={value}" for key, value in security.items() if value not in (None, "", [], {}))
        )
    dr = facts.get("dr")
    if isinstance(dr, dict) and dr:
        parts.append(
            "dr=" + ", ".join(f"{key}={value}" for key, value in dr.items() if value not in (None, "", [], {}))
        )
    exclusions = facts.get("scope_exclusions")
    if isinstance(exclusions, list) and exclusions:
        parts.append("excluded=" + ", ".join(str(item) for item in exclusions[:6]))
    return "; ".join(parts)[:900]


def _deep_merge_dict(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (incoming or {}).items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(dict(merged.get(key, {}) or {}), value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            current = [item for item in merged.get(key, []) if item not in (None, "", [], {})]
            for item in value:
                if item not in (None, "", [], {}) and item not in current:
                    current.append(item)
            merged[key] = current
        elif isinstance(value, str) and isinstance(merged.get(key), str):
            current = str(merged.get(key, "") or "").strip()
            incoming_text = str(value or "").strip()
            if current and incoming_text and incoming_text.lower() in current.lower():
                merged[key] = current
            elif current and incoming_text and current.lower() in incoming_text.lower():
                merged[key] = incoming_text
            else:
                merged[key] = value
        else:
            merged[key] = value
    return merged


def record_bom_work_product(
    context: dict[str, Any],
    *,
    bom_payload: dict[str, Any],
    context_source: str = "",
    grounding: str = "",
    xlsx: dict[str, Any] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if not isinstance(bom_payload, dict) or not bom_payload:
        return context
    archie = get_archie_state(context)
    work_products = archie.get("work_products")
    if not isinstance(work_products, dict):
        work_products = {}
    bom = work_products.get("bom")
    if not isinstance(bom, dict):
        bom = {"latest_version": 0, "versions": []}
    versions = bom.get("versions")
    if not isinstance(versions, list):
        versions = []
    next_version = int(bom.get("latest_version", 0) or 0) + 1
    record = {
        "version": next_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context_source": str(context_source or "").strip(),
        "grounding": str(grounding or "").strip() or _infer_bom_grounding(context_source),
        "baseline": _compact_bom_baseline(bom_payload),
    }
    if isinstance(xlsx, dict) and xlsx:
        record["xlsx"] = dict(xlsx)
    versions.append(record)
    bom["latest_version"] = next_version
    bom["versions"] = versions[-limit:]
    bom["latest"] = record
    work_products["bom"] = bom
    archie["work_products"] = work_products
    refresh_archie_memory(context)
    return context


def attach_bom_xlsx_to_latest(
    context: dict[str, Any],
    xlsx: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(xlsx, dict) or not xlsx:
        return context
    archie = get_archie_state(context)
    work_products = archie.get("work_products")
    if not isinstance(work_products, dict):
        return context
    bom = work_products.get("bom")
    if not isinstance(bom, dict):
        return context
    latest = bom.get("latest")
    if isinstance(latest, dict):
        latest["xlsx"] = dict(xlsx)
    versions = bom.get("versions")
    if isinstance(versions, list) and versions and isinstance(versions[-1], dict):
        versions[-1]["xlsx"] = dict(xlsx)
    refresh_archie_memory(context)
    return context


def latest_bom_work_product(context: dict[str, Any]) -> dict[str, Any] | None:
    archie = get_archie_state(context)
    work_products = archie.get("work_products")
    if not isinstance(work_products, dict):
        return None
    bom = work_products.get("bom")
    if not isinstance(bom, dict):
        return None
    latest = bom.get("latest")
    if isinstance(latest, dict):
        return latest
    versions = bom.get("versions")
    if isinstance(versions, list):
        for item in reversed(versions):
            if isinstance(item, dict):
                return item
    return None


def _infer_bom_grounding(context_source: str) -> str:
    source = str(context_source or "").strip().lower()
    if "revision" in source:
        return "revision-grounded"
    if source and source != "direct_request":
        return "context-grounded"
    return "generic"


def _compact_bom_baseline(payload: dict[str, Any]) -> dict[str, Any]:
    line_items = []
    for item in list(payload.get("line_items", []) or [])[:80]:
        if not isinstance(item, dict):
            continue
        line_items.append(
            {
                "sku": item.get("sku"),
                "description": item.get("description"),
                "category": item.get("category"),
                "quantity": item.get("quantity"),
                "metric": item.get("metric"),
                "notes": item.get("notes"),
            }
        )
    return {
        "currency": payload.get("currency", "USD"),
        "region": payload.get("region") or payload.get("oci_region"),
        "line_items": line_items,
        "totals": dict(payload.get("totals", {}) or {}) if isinstance(payload.get("totals"), dict) else {},
        "assumptions": list(payload.get("assumptions", []) or [])[:20],
        "resolved_inputs": list(payload.get("resolved_inputs", []) or [])[:30]
        if isinstance(payload.get("resolved_inputs"), list)
        else [],
    }


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
            "client_facts",
            "facts_summary",
            "infrastructure_profile",
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

    infra_profile = archie.get("infrastructure_profile", {}) or {}
    if isinstance(infra_profile, dict) and infra_profile:
        lines.append(
            "  • Infrastructure Profile: "
            f"{json.dumps(infra_profile, ensure_ascii=True, sort_keys=True)[:520]}"
        )

    facts_summary = str(archie.get("facts_summary", "") or "").strip()
    if facts_summary:
        lines.append(f"  • Client Facts: {facts_summary}")

    memory = get_archie_memory(context)
    memory_summary = str(memory.get("memory_summary", "") or "").strip()
    if memory_summary and memory_summary != facts_summary:
        lines.append(f"  • Archie Memory: {memory_summary}")

    latest_bom = latest_bom_work_product(context)
    if latest_bom:
        baseline = latest_bom.get("baseline", {}) if isinstance(latest_bom.get("baseline"), dict) else {}
        lines.append(
            "  • Latest BOM Work Product: "
            f"v{latest_bom.get('version', '?')}, "
            f"grounding={latest_bom.get('grounding', 'unknown')}, "
            f"line_items={len(list(baseline.get('line_items', []) or []))}, "
            f"xlsx={'yes' if isinstance(latest_bom.get('xlsx'), dict) else 'no'}"
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
    refresh_archie_memory(normalized)
    return normalized
