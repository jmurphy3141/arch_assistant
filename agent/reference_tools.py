"""Deterministic native-agent lookups over existing OCI reference data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.bom_service import get_shared_bom_service
from agent.reference_architecture import (
    load_standards_bundle,
    select_reference_architecture,
)
from skillforge import ArgSchema
from skillforge.registry import ToolSpec
from skillforge.types import MemorySnapshot, ToolResult


_SHAPES_PATH = Path(__file__).parent / "standards" / "oci_compute_shapes.json"
_REQUIRED_FAMILIES = ("E5", "E6", "A1", "X9")


def lookup_compute_shapes() -> dict[str, Any]:
    """Return required compute families and ranges from the checked-in catalog."""
    catalog = json.loads(_SHAPES_PATH.read_text(encoding="utf-8"))
    matches: dict[str, dict[str, Any]] = {}
    for shape in catalog.get("flex_vm_shapes", []) or []:
        if not isinstance(shape, dict):
            continue
        family = _family_name(shape)
        if family not in _REQUIRED_FAMILIES or family in matches:
            continue
        matches[family] = {
            "family": family,
            "shape": str(shape.get("shape") or ""),
            "architecture": _architecture_label(shape),
            "processor": str(shape.get("processor") or "unknown/TBD"),
            "generation": str(shape.get("generation") or "unknown/TBD"),
            "ocpu_min": shape.get("ocpu_min"),
            "ocpu_max": shape.get("ocpu_max"),
            "memory_min_gb": shape.get("memory_min_gb"),
            "memory_max_gb": shape.get("memory_max_gb"),
        }
    return {
        "source": str(_SHAPES_PATH),
        "last_updated": str((catalog.get("_meta") or {}).get("last_updated") or ""),
        "shapes": [matches[name] for name in _REQUIRED_FAMILIES if name in matches],
    }


def lookup_price(sku_or_service: str) -> dict[str, Any]:
    """Read the shared live pricing cache; never synthesize or backfill a price."""
    query = str(sku_or_service or "").strip()
    table, cache_source, refreshed_at = _live_pricing_table()
    exact = table.get(query.upper()) if query else None
    if exact is not None:
        return _price_result(
            query=query,
            sku=query.upper(),
            row=exact,
            cache_source=cache_source,
            refreshed_at=refreshed_at,
        )

    query_lower = query.lower()
    matches = [
        _price_result(
            query=query,
            sku=sku,
            row=row,
            cache_source=cache_source,
            refreshed_at=refreshed_at,
        )
        for sku, row in sorted(table.items())
        if query_lower and query_lower in str(row.get("description") or "").lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return {
            "query": query,
            "pricing_status": "multiple_matches",
            "unit_price": "TBD",
            "matches": matches,
            "cache_source": cache_source,
            "refreshed_at": refreshed_at,
        }
    return {
        "query": query,
        "pricing_status": "unpriced/TBD",
        "unit_price": "TBD",
        "matches": [],
        "cache_source": cache_source,
        "refreshed_at": refreshed_at,
    }


def lookup_reference_architecture(query: str) -> dict[str, Any]:
    """Match a query through the existing Oracle reference-family selector."""
    selection = select_reference_architecture(text=str(query or ""))
    if selection.reference_mode != "reference-backed" or not selection.reference_family:
        return {"query": str(query or ""), "match": None, "matches": []}
    bundle = load_standards_bundle()
    family = next(
        (
            dict(item)
            for item in bundle.get("families", []) or []
            if str(item.get("id") or "") == selection.reference_family
        ),
        {},
    )
    match = {
        "id": selection.reference_family,
        "label": selection.reference_label,
        "confidence": round(selection.reference_confidence, 4),
        "template": str(family.get("template") or ""),
        "required_containers": list(family.get("required_containers") or []),
        "connector_lanes": list(family.get("connector_lanes") or []),
        "quality_assertions": list(family.get("quality_assertions") or []),
        "bundle_id": str(bundle.get("bundle_id") or ""),
        "bundle_version": str(bundle.get("bundle_version") or ""),
        "approved_sources": list(bundle.get("approved_sources") or []),
    }
    return {"query": str(query or ""), "match": match, "matches": [match]}


def get_reference_tool_specs() -> tuple[ToolSpec, ...]:
    """Return native-only schemas and handlers for deterministic reference lookups."""
    return (
        ToolSpec(
            name="lookup_compute_shapes",
            handler=_handle_compute_shapes,
            description="Look up OCI E5, E6, A1, and X9 shape ranges and processors.",
        ),
        ToolSpec(
            name="lookup_price",
            handler=_handle_price,
            description="Look up a current cached OCI unit price; unknown prices are TBD.",
            args={
                "sku_or_service": ArgSchema(
                    description="OCI SKU or service-description fragment.",
                    type="string",
                    required=True,
                )
            },
        ),
        ToolSpec(
            name="lookup_reference_architecture",
            handler=_handle_reference_architecture,
            description="Match a workload to the curated Oracle reference bundle.",
            args={
                "query": ArgSchema(
                    description="Workload, topology, and OCI service keywords.",
                    type="string",
                    required=True,
                )
            },
        ),
    )


async def _handle_compute_shapes(
    args: dict[str, Any],
    *,
    memory: MemorySnapshot | None,
    context: dict[str, Any],
    trace_id: str,
) -> ToolResult:
    data = lookup_compute_shapes()
    return ToolResult(
        summary=f"Retrieved {len(data['shapes'])} OCI compute shape families.",
        status="ok",
        data=data,
    )


async def _handle_price(
    args: dict[str, Any],
    *,
    memory: MemorySnapshot | None,
    context: dict[str, Any],
    trace_id: str,
) -> ToolResult:
    data = lookup_price(str(args.get("sku_or_service") or ""))
    return ToolResult(
        summary=(
            f"Pricing lookup status: {data.get('pricing_status', 'unpriced/TBD')}."
        ),
        status="ok",
        data=data,
    )


async def _handle_reference_architecture(
    args: dict[str, Any],
    *,
    memory: MemorySnapshot | None,
    context: dict[str, Any],
    trace_id: str,
) -> ToolResult:
    data = lookup_reference_architecture(str(args.get("query") or ""))
    return ToolResult(
        summary=(
            f"Matched Oracle reference family {data['match']['id']}."
            if data.get("match")
            else "No matching Oracle reference family was found."
        ),
        status="ok",
        data=data,
    )


def _live_pricing_table() -> tuple[dict[str, dict[str, Any]], str, int | None]:
    service = get_shared_bom_service()
    with service._lock:
        snapshot = service._cache
        if snapshot is None:
            return {}, "none", None
        return (
            {sku: dict(row) for sku, row in snapshot.pricing_table.items()},
            str(snapshot.source or "unknown"),
            int(snapshot.refreshed_at),
        )


def _price_result(
    *,
    query: str,
    sku: str,
    row: dict[str, Any],
    cache_source: str,
    refreshed_at: int | None,
) -> dict[str, Any]:
    unit_price = row.get("unit_price")
    priced = unit_price not in (None, "")
    return {
        "query": query,
        "sku": sku,
        "description": str(row.get("description") or sku),
        "unit_price": float(unit_price) if priced else "TBD",
        "metric": str(row.get("metric") or ""),
        "pricing_status": "priced" if priced else "unpriced/TBD",
        "price_source": str(row.get("source") or cache_source),
        "cache_source": cache_source,
        "refreshed_at": refreshed_at,
    }


def _family_name(shape: dict[str, Any]) -> str:
    text = " ".join(
        str(shape.get(key) or "") for key in ("shape", "family", "generation")
    ).lower()
    for family in _REQUIRED_FAMILIES:
        if family.lower() in text:
            return family
    return ""


def _architecture_label(shape: dict[str, Any]) -> str:
    text = " ".join(
        str(shape.get(key) or "")
        for key in ("family", "processor", "generation")
    ).lower()
    if "ampere" in text or "arm" in text:
        return "Ampere/Arm"
    if "amd" in text:
        return "AMD x86"
    if "intel" in text or "xeon" in text:
        return "Intel x86"
    return "unknown/TBD"
