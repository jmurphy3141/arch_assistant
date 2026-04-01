"""
agent/layout_intent.py
-----------------------
LayoutIntent schema and validator for the Option 1 architecture.

The LLM outputs ONLY a compact LayoutIntent JSON (what services exist, which
layer/group each belongs to, and topology hints).  Deterministic code in
intent_compiler.py then expands it into a legacy flat layout spec that the
existing layout engine and draw.io generator accept without modification.

NeedClarification JSON (returned instead of LayoutIntent when blocking info is missing):
  {"status": "need_clarification", "questions": [{"id": "...", "question": "...", "blocking": true}]}

Allowed question IDs: regions.count, regions.mode, ha.ads, connectivity.onprem, dr.rpo_rto
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

VALID_LAYERS = frozenset(["external", "ingress", "compute", "async", "data"])
VALID_GROUPS = frozenset(["pub_sub_box", "app_sub_box", "db_sub_box", "none"])
VALID_CONNECTIVITY = frozenset(["fastconnect", "vpn", "none", "unknown"])
VALID_QUESTION_IDS = frozenset(
    ["regions.count", "regions.mode", "ha.ads", "connectivity.onprem", "dr.rpo_rto"]
)

GROUP_LABELS = {
    "pub_sub_box": "Public Subnet",
    "app_sub_box": "App Subnet",
    "db_sub_box":  "DB Subnet",
}


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class DeploymentHints:
    region_count: int = 1
    availability_domains_per_region: int = 1
    dr_enabled: bool = False
    on_prem_connectivity: str = "unknown"   # fastconnect | vpn | none | unknown


@dataclass
class Placement:
    id: str
    oci_type: str
    layer: str                  # external | ingress | compute | async | data
    group: Optional[str] = None # pub_sub_box | app_sub_box | db_sub_box | None


@dataclass
class Assumption:
    id: str
    statement: str
    reason: str
    risk: str = ""


@dataclass
class LayoutIntent:
    schema_version: str
    deployment_hints: DeploymentHints
    placements: list[Placement]
    assumptions: list[Assumption] = field(default_factory=list)
    fixed_edges_policy: bool = True


# ── Exception ──────────────────────────────────────────────────────────────────

class LayoutIntentError(ValueError):
    """Raised when a raw dict cannot be validated as a LayoutIntent."""


# ── Validator ─────────────────────────────────────────────────────────────────

def validate_layout_intent(data: dict, items: list | None = None) -> LayoutIntent:
    """
    Parse a raw dict into a validated LayoutIntent.

    items: list[ServiceItem] — when supplied, every ServiceItem id must appear
           exactly once in placements.  Pass None or [] to skip this check.

    Raises LayoutIntentError on validation failures.
    """
    if not isinstance(data, dict):
        raise LayoutIntentError("LayoutIntent must be a JSON object")

    schema_version = str(data.get("schema_version", "1.0"))

    # ── deployment_hints ──────────────────────────────────────────────────────
    hints_raw = data.get("deployment_hints") or {}
    conn = str(hints_raw.get("on_prem_connectivity", "unknown"))
    if conn not in VALID_CONNECTIVITY:
        conn = "unknown"

    hints = DeploymentHints(
        region_count=int(hints_raw.get("region_count", 1) or 1),
        availability_domains_per_region=int(
            hints_raw.get("availability_domains_per_region", 1) or 1
        ),
        dr_enabled=bool(hints_raw.get("dr_enabled", False)),
        on_prem_connectivity=conn,
    )

    # ── placements ────────────────────────────────────────────────────────────
    placements_raw = data.get("placements", [])
    if not isinstance(placements_raw, list):
        raise LayoutIntentError("'placements' must be a list")

    placements: list[Placement] = []
    seen_ids: set[str] = set()

    for p in placements_raw:
        pid      = str(p.get("id", "")).strip()
        oci_type = str(p.get("oci_type", "")).strip()
        layer    = str(p.get("layer", "")).strip()
        group    = p.get("group") or None
        if group in ("none", "null"):   # LLMs sometimes emit the string "null"
            group = None

        if not pid:
            raise LayoutIntentError("Each placement must have a non-empty 'id'")
        if pid in seen_ids:
            raise LayoutIntentError(f"Duplicate placement id: {pid!r}")
        if layer not in VALID_LAYERS:
            raise LayoutIntentError(
                f"Unknown layer {layer!r} for placement {pid!r}. "
                f"Valid layers: {sorted(VALID_LAYERS)}"
            )
        if group is not None and group not in VALID_GROUPS - {"none"}:
            raise LayoutIntentError(
                f"Unknown group {group!r} for placement {pid!r}. "
                f"Valid groups: {sorted(VALID_GROUPS - {'none'})}"
            )

        seen_ids.add(pid)
        placements.append(Placement(id=pid, oci_type=oci_type, layer=layer, group=group))

    # ── ServiceItem id coverage check ─────────────────────────────────────────
    # Auto-fill any items the LLM dropped rather than hard-failing the pipeline.
    if items:
        item_ids = {i.id for i in items}
        missing = item_ids - seen_ids
        if missing:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning("LLM dropped placements — auto-filling: %s", sorted(missing))
            items_by_id = {i.id: i for i in items}
            # Default group by layer
            _layer_to_group = {
                "ingress": "pub_sub_box",
                "compute": "app_sub_box",
                "data":    "db_sub_box",
            }
            for mid in sorted(missing):
                si = items_by_id[mid]
                layer = si.layer if si.layer in VALID_LAYERS else "data"
                group = _layer_to_group.get(layer)  # None for external/async
                seen_ids.add(mid)
                placements.append(Placement(id=mid, oci_type=si.oci_type,
                                            layer=layer, group=group))

    # ── assumptions ───────────────────────────────────────────────────────────
    assumptions: list[Assumption] = []
    for a in (data.get("assumptions") or []):
        assumptions.append(Assumption(
            id=str(a.get("id", "")),
            statement=str(a.get("statement", "")),
            reason=str(a.get("reason", "")),
            risk=str(a.get("risk", "")),
        ))

    fixed_edges_policy = bool(data.get("fixed_edges_policy", True))

    return LayoutIntent(
        schema_version=schema_version,
        deployment_hints=hints,
        placements=placements,
        assumptions=assumptions,
        fixed_edges_policy=fixed_edges_policy,
    )
