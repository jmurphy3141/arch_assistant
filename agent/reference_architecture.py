from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.external_corpus_scorer import canonical_service_tag
from agent.oci_standards import get_icon_title


_MANIFEST_PATH = Path(__file__).resolve().parent / "standards" / "oracle_reference_bundle.json"

_GATEWAY_TYPES = {
    "internet gateway",
    "nat gateway",
    "service gateway",
    "drg",
    "dynamic routing gateway",
}
_EXTERNAL_TYPES = {
    "internet",
    "public internet",
    "on premises",
    "users",
    "admins",
    "browser",
    "workstation",
}
_MANAGED_SERVICE_TYPES = {
    "object storage",
    "logging",
    "monitoring",
    "iam",
    "vault",
    "api gateway",
    "queue",
    "streaming",
    "generative ai",
    "language",
    "document understanding",
    "notifications",
    "events",
}
_DATABASE_TYPES = {
    "database",
    "autonomous database",
    "mysql",
    "postgresql",
    "nosql",
    "oracle database",
    "db system",
}
_PUBLIC_INGRESS_TYPES = {"waf", "load balancer", "bastion"}
_TEXT_TOKEN_RE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class ReferenceSelection:
    standards_bundle_version: str
    standards_bundle_id: str
    standards_policy: str
    reference_family: str
    reference_label: str
    reference_confidence: float
    reference_mode: str
    family_keywords: list[str]
    family_constraints: dict[str, Any]
    approved_sources: list[dict[str, Any]]
    supported_families: list[str]
    multi_region_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "standards_bundle_version": self.standards_bundle_version,
            "standards_bundle_id": self.standards_bundle_id,
            "standards_policy": self.standards_policy,
            "reference_family": self.reference_family,
            "reference_label": self.reference_label,
            "reference_confidence": round(self.reference_confidence, 4),
            "reference_mode": self.reference_mode,
            "family_keywords": list(self.family_keywords),
            "family_constraints": dict(self.family_constraints),
            "approved_sources": list(self.approved_sources),
            "supported_families": list(self.supported_families),
            "multi_region_mode": self.multi_region_mode,
        }


@lru_cache(maxsize=1)
def load_standards_bundle() -> dict[str, Any]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def select_standards_bundle() -> dict[str, Any]:
    bundle = load_standards_bundle()
    return {
        "bundle_id": str(bundle.get("bundle_id", "") or ""),
        "bundle_version": str(bundle.get("bundle_version", "") or ""),
        "policy": str(bundle.get("policy", "curated_snapshot") or "curated_snapshot"),
        "approved_sources": list(bundle.get("approved_sources", []) or []),
        "supported_families": [
            str(family.get("id", "") or "")
            for family in (bundle.get("families") or [])
            if str(family.get("id", "") or "")
        ],
    }


def select_reference_architecture(
    *,
    text: str = "",
    items: list[Any] | None = None,
    deployment_hints: dict[str, Any] | None = None,
    orchestrator_hint: dict[str, Any] | None = None,
) -> ReferenceSelection:
    bundle = load_standards_bundle()
    traits = _derive_traits(text=text, items=items or [], deployment_hints=deployment_hints or {})
    scored = _score_families(bundle, traits)
    top = scored[0] if scored else None

    family_id = ""
    family_label = ""
    confidence = 0.0
    keywords: list[str] = []
    constraints: dict[str, Any] = {}
    mode = "best-effort-generic"
    multi_region_mode = _default_multi_region_mode(traits)

    if top and float(top["confidence"]) >= float(top["family"].get("min_confidence", 1.0)):
        family = top["family"]
        family_id = str(family.get("id", "") or "")
        family_label = str(family.get("label", family_id) or family_id)
        confidence = float(top["confidence"])
        keywords = list(top["keywords"])
        constraints = {
            "required_containers": list(family.get("required_containers", []) or []),
            "optional_containers": list(family.get("optional_containers", []) or []),
            "connector_lanes": list(family.get("connector_lanes", []) or []),
            "allowed_mutations": list(family.get("allowed_mutations", []) or []),
            "quality_assertions": list(family.get("quality_assertions", []) or []),
        }
        mode = "reference-backed"
        if family_id == "multi_region_oke_saas":
            multi_region_mode = _default_multi_region_mode(traits, family_id=family_id)

    hint = orchestrator_hint or {}
    if mode == "best-effort-generic" and str(hint.get("reference_mode", "") or "") == "reference-backed":
        hinted_family = str(hint.get("reference_family", "") or "")
        for family in bundle.get("families", []) or []:
            if str(family.get("id", "") or "") != hinted_family:
                continue
            family_id = hinted_family
            family_label = str(family.get("label", family_id) or family_id)
            confidence = max(float(hint.get("reference_confidence", 0) or 0), float(family.get("min_confidence", 0)))
            keywords = list(hint.get("family_keywords", []) or [])
            constraints = {
                "required_containers": list(family.get("required_containers", []) or []),
                "optional_containers": list(family.get("optional_containers", []) or []),
                "connector_lanes": list(family.get("connector_lanes", []) or []),
                "allowed_mutations": list(family.get("allowed_mutations", []) or []),
                "quality_assertions": list(family.get("quality_assertions", []) or []),
            }
            mode = "reference-backed"
            break

    bundle_info = select_standards_bundle()
    return ReferenceSelection(
        standards_bundle_version=bundle_info["bundle_version"],
        standards_bundle_id=bundle_info["bundle_id"],
        standards_policy=bundle_info["policy"],
        reference_family=family_id,
        reference_label=family_label,
        reference_confidence=confidence,
        reference_mode=mode,
        family_keywords=keywords,
        family_constraints=constraints,
        approved_sources=bundle_info["approved_sources"],
        supported_families=bundle_info["supported_families"],
        multi_region_mode=multi_region_mode,
    )


def build_reference_context_lines(selection: dict[str, Any] | ReferenceSelection) -> list[str]:
    payload = selection.as_dict() if isinstance(selection, ReferenceSelection) else dict(selection or {})
    lines = [
        "Oracle standards bundle is mandatory for this diagram path.",
        f"Standards bundle version: {payload.get('standards_bundle_version', '')}",
        f"Reference mode: {payload.get('reference_mode', 'best-effort-generic')}",
    ]
    family = str(payload.get("reference_family", "") or "")
    if family:
        lines.append(f"Reference family: {family}")
        lines.append(
            f"Reference confidence: {round(float(payload.get('reference_confidence', 0) or 0), 4)}"
        )
    constraints = payload.get("family_constraints") or {}
    lanes = list(constraints.get("connector_lanes", []) or [])
    assertions = list(constraints.get("quality_assertions", []) or [])
    if lanes:
        lines.append("Approved connector lanes: " + ", ".join(lanes))
    if assertions:
        lines.append("Family assertions: " + ", ".join(assertions))
    if family and payload.get("reference_mode") == "reference-backed":
        lines.append(
            "Reject generic topology invention. Populate only approved template slots and bounded mutations."
        )
    return lines


def render_reference_architecture(
    *,
    selection: dict[str, Any] | ReferenceSelection,
    items: list[Any],
    deployment_hints: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = selection.as_dict() if isinstance(selection, ReferenceSelection) else dict(selection or {})
    family_id = str(payload.get("reference_family", "") or "")
    hints = dict(deployment_hints or {})

    if family_id == "multi_region_oke_saas" and "multi_region_mode" not in hints:
        hints["multi_region_mode"] = str(payload.get("multi_region_mode", "") or "duplicate_drha")

    if family_id == "single_region_oke_app":
        spec = _render_single_region_template(items, hints, family_variant="oke")
    elif family_id == "classic_3tier_webapp":
        spec = _render_single_region_template(items, hints, family_variant="classic")
    elif family_id == "multi_region_oke_saas":
        spec = _render_multi_region_oke_template(items, hints)
    elif family_id == "hub_spoke_network":
        spec = _render_hub_spoke_template(items, hints)
    else:
        raise ValueError(f"Unsupported reference family: {family_id!r}")

    validation = validate_reference_architecture(
        spec=spec,
        items=items,
        selection=payload,
    )
    metadata = {
        **payload,
        "family_fit_score": float(validation.get("family_fit_score", 0.0) or 0.0),
        "validation": validation,
    }
    if not validation.get("overall_pass", False):
        raise ValueError(
            f"Reference-backed family invariants failed for {family_id}: "
            + ", ".join(validation.get("blocking_issues", []) or ["unknown validation failure"])
        )
    return spec, metadata


def validate_reference_architecture(
    *,
    spec: dict[str, Any],
    items: list[Any],
    selection: dict[str, Any] | ReferenceSelection,
) -> dict[str, Any]:
    payload = selection.as_dict() if isinstance(selection, ReferenceSelection) else dict(selection or {})
    family_id = str(payload.get("reference_family", "") or "")
    selection_confirm = select_reference_architecture(items=items, deployment_hints={}, orchestrator_hint=payload)

    blocking_issues: list[str] = []
    visual_issues: list[str] = []
    standards_issues: list[str] = []
    expected = {str(getattr(item, "id", "")).strip() for item in (items or []) if str(getattr(item, "id", "")).strip()}

    node_ids = set(_iter_node_ids(spec))
    missing_items = sorted(expected - node_ids)
    if missing_items:
        blocking_issues.append("missing_input_items")
        visual_issues.append(f"Missing input-backed nodes: {', '.join(missing_items)}")

    public_ids = set(_collect_public_node_ids(spec))
    private_ids = set(_collect_private_node_ids(spec))
    for item in items or []:
        layer = str(getattr(item, "layer", "") or "").lower()
        item_id = str(getattr(item, "id", "") or "")
        item_type = str(getattr(item, "oci_type", "") or "")
        title = get_icon_title(item_type)
        if layer == "ingress" and item_id and item_id not in public_ids and item_type.lower() in _PUBLIC_INGRESS_TYPES:
            standards_issues.append(f"Public ingress service not in public subnet: {item_id}")
        if layer in {"compute", "data", "async"} and item_id and item_id in public_ids:
            standards_issues.append(f"Private service exposed in public subnet: {item_id}")
        if title is None and item_type.lower() not in _EXTERNAL_TYPES:
            standards_issues.append(f"Missing approved OCI icon mapping: {item_type}")

    if family_id in {"single_region_oke_app", "classic_3tier_webapp"}:
        if not _has_gateway(spec, "internet gateway"):
            standards_issues.append("internet_gateway_missing")
        if not _has_gateway(spec, "nat gateway"):
            standards_issues.append("nat_gateway_missing")
        if not _has_gateway(spec, "service gateway"):
            standards_issues.append("service_gateway_missing")
        if not public_ids:
            standards_issues.append("public_ingress_subnet_missing")
        if not private_ids:
            standards_issues.append("private_private_subnets_missing")
    if family_id == "multi_region_oke_saas" and len(spec.get("regions", []) or []) < 2:
        blocking_issues.append("secondary_region_missing")
    if family_id == "hub_spoke_network":
        if not _has_gateway(spec, "drg"):
            standards_issues.append("drg_missing")
        if len((spec.get("regions") or [{}])[0].get("compartments", []) or []) < 3:
            blocking_issues.append("hub_spoke_compartments_missing")

    family_fit_score = 1.0 if selection_confirm.reference_family == family_id else 0.35
    overall_pass = not blocking_issues and not standards_issues
    return {
        "overall_pass": overall_pass,
        "family_fit_score": round(family_fit_score, 4),
        "reference_mode": payload.get("reference_mode", "best-effort-generic"),
        "visual_quality": {
            "status": "pass" if not visual_issues else "fail",
            "issues": visual_issues,
        },
        "standards_quality": {
            "status": "pass" if not standards_issues else "fail",
            "issues": standards_issues,
        },
        "family_quality": {
            "status": "pass" if family_fit_score >= 0.9 else "fail",
            "selected_family": family_id,
            "confirmed_family": selection_confirm.reference_family,
        },
        "blocking_issues": blocking_issues + standards_issues,
    }


def _derive_traits(*, text: str, items: list[Any], deployment_hints: dict[str, Any]) -> dict[str, Any]:
    tokens = {
        token
        for token in _TEXT_TOKEN_RE.split((text or "").lower())
        if len(token) >= 3
    }
    service_tags: set[str] = set()
    raw_types: set[str] = set()
    for item in items:
        oci_type = str(getattr(item, "oci_type", "") or "").lower()
        if not oci_type:
            continue
        raw_types.add(oci_type)
        tag = canonical_service_tag(oci_type)
        if tag:
            service_tags.add(tag)
        if "container engine" in oci_type:
            service_tags.add("oke")
        if "object storage" in oci_type:
            service_tags.add("object_storage")
        if "load balancer" in oci_type:
            service_tags.add("load_balancer")

    if {"oke", "kubernetes"} & tokens:
        service_tags.add("oke")
    if {"waf"} & tokens:
        service_tags.add("waf")
    if {"database", "postgres", "mysql", "adb"} & tokens:
        service_tags.add("database")
    if {"load", "balancer", "lb"} & tokens:
        service_tags.add("load_balancer")
    if {"object", "storage"} <= tokens or "object_storage" in tokens:
        service_tags.add("object_storage")
    if "bastion" in tokens:
        service_tags.add("bastion")
    if {"hub", "spoke"} <= tokens or "hub_spoke" in tokens:
        service_tags.add("hub_spoke")
    if {"onprem", "fastconnect", "drg", "vpn"} & tokens:
        service_tags.add("drg")
    if {"saas"} & tokens:
        service_tags.add("saas")
    if {"web", "app"} <= tokens or "webapp" in tokens:
        service_tags.add("web_app")

    region_count = 1
    hinted_regions = deployment_hints.get("regions")
    if isinstance(hinted_regions, list) and hinted_regions:
        region_count = max(region_count, len(hinted_regions))
    if deployment_hints.get("multi_region_mode"):
        region_count = max(region_count, 2)
    if any(phrase in (text or "").lower() for phrase in ("multi-region", "multi region", "two regions", "secondary region", "dr site")):
        region_count = max(region_count, 2)

    return {
        "tokens": tokens,
        "service_tags": service_tags,
        "raw_types": raw_types,
        "region_count": region_count,
        "availability_domains": int(deployment_hints.get("availability_domains_per_region", 1) or 1),
        "has_onprem": bool({"on_prem", "onprem", "fastconnect", "vpn"} & tokens or "drg" in service_tags),
        "dr_signal": any(phrase in (text or "").lower() for phrase in ("disaster recovery", "dr ", "failover", "standby")),
    }


def _score_families(bundle: dict[str, Any], traits: dict[str, Any]) -> list[dict[str, Any]]:
    service_tags = set(traits.get("service_tags", set()) or set())
    tokens = set(traits.get("tokens", set()) or set())
    region_count = int(traits.get("region_count", 1) or 1)
    scored: list[dict[str, Any]] = []

    for family in bundle.get("families", []) or []:
        required = set(family.get("required_tags", []) or [])
        preferred = set(family.get("preferred_tags", []) or [])
        disallowed = set(family.get("disallowed_tags", []) or [])
        family_id = str(family.get("id", "") or "")

        confidence = 0.05
        matched_keywords: list[str] = []
        missing_required = required - service_tags
        if required:
            confidence += 0.45 * (len(required & service_tags) / len(required))
        confidence += min(0.25, 0.06 * len(preferred & service_tags))
        confidence -= 0.18 * len(disallowed & service_tags)
        confidence -= 0.12 * len(missing_required)

        if family_id == "single_region_oke_app":
            if region_count == 1:
                confidence += 0.14
            if "oke" in service_tags:
                matched_keywords.append("oke")
        elif family_id == "multi_region_oke_saas":
            if region_count >= 2:
                confidence += 0.2
                matched_keywords.append("multi-region")
            if "saas" in service_tags or "saas" in tokens:
                confidence += 0.08
                matched_keywords.append("saas")
            if region_count < 2:
                confidence -= 0.3
        elif family_id == "classic_3tier_webapp":
            if region_count == 1:
                confidence += 0.12
            if "web_app" in service_tags or "web" in tokens:
                confidence += 0.08
                matched_keywords.append("web-app")
        elif family_id == "hub_spoke_network":
            if "hub_spoke" in service_tags or {"hub", "spoke"} <= tokens:
                confidence += 0.22
                matched_keywords.extend(["hub", "spoke"])
            if not ({"hub", "spoke"} <= tokens or "hub_spoke" in service_tags):
                confidence -= 0.1

        confidence = max(0.0, min(confidence, 0.99))
        scored.append(
            {
                "family": family,
                "confidence": round(confidence, 4),
                "keywords": matched_keywords,
            }
        )

    return sorted(
        scored,
        key=lambda item: (-float(item["confidence"]), str(item["family"].get("id", ""))),
    )


def _default_multi_region_mode(traits: dict[str, Any], family_id: str = "") -> str:
    if family_id == "multi_region_oke_saas":
        if traits.get("dr_signal"):
            return "duplicate_drha"
        return "split_workloads"
    if traits.get("dr_signal"):
        return "duplicate_drha"
    return "split_workloads"


def _render_single_region_template(items: list[Any], hints: dict[str, Any], *, family_variant: str) -> dict[str, Any]:
    slots = _classify_slots(items)
    ads = max(1, int(hints.get("availability_domains_per_region", 1) or 1))
    deployment_type = "multi_ad" if ads > 1 else "single_ad"

    regional_subnets = []
    if slots["public_ingress"]:
        regional_subnets.append(
            {
                "id": "public_ingress",
                "label": "Public Ingress Subnet",
                "tier": "public_ingress",
                "nodes": slots["public_ingress"],
            }
        )

    ad_subnets = [
        {
            "id": "private_app",
            "label": "Private App Subnet",
            "tier": "app",
            "nodes": slots["app"],
        },
        {
            "id": "private_data",
            "label": "Private Data Subnet",
            "tier": "db",
            "nodes": slots["data"],
        },
    ]

    availability_domains = []
    for idx in range(ads):
        availability_domains.append(
            {
                "id": f"ad{idx + 1}_box",
                "label": f"Availability Domain {idx + 1}",
                "subnets": ad_subnets if idx == 0 else _duplicate_nodes_with_suffix(ad_subnets, suffix=f"_ad{idx + 1}"),
            }
        )

    region = {
        "id": "region_primary",
        "label": "OCI Region",
        "regional_subnets": regional_subnets,
        "availability_domains": availability_domains,
        "gateways": slots["gateways"],
        "oci_services": slots["managed_services"],
    }
    if family_variant == "oke" and not any(node["type"] == "container engine" for node in slots["app"]):
        region["availability_domains"][0]["subnets"][0]["nodes"].append(
            {"id": "oke_cluster_template", "type": "container engine", "label": "OKE Cluster"}
        )
    return {
        "deployment_type": deployment_type,
        "page": {"width": 1654, "height": 1169},
        "regions": [region],
        "external": slots["external"],
        "edges": _build_standard_edges(slots, multi_region=False),
    }


def _render_multi_region_oke_template(items: list[Any], hints: dict[str, Any]) -> dict[str, Any]:
    slots = _classify_slots(items)
    region_names = list(hints.get("regions", []) or [])[:2]
    if len(region_names) < 2:
        region_names = ["Primary Region", "Secondary Region"]
    regions: list[dict[str, Any]] = []
    for idx, region_name in enumerate(region_names):
        suffix = "a" if idx == 0 else "b"
        regions.append(
            {
                "id": f"region_{suffix}",
                "label": str(region_name),
                "regional_subnets": [
                    {
                        "id": f"public_ingress_{suffix}",
                        "label": "Public Ingress Subnet",
                        "tier": "public_ingress",
                        "nodes": _duplicate_node_list(slots["public_ingress"], suffix=f"_{suffix}"),
                    }
                ],
                "availability_domains": [
                    {
                        "id": f"ad1_{suffix}",
                        "label": "Availability Domain 1",
                        "subnets": [
                            {
                                "id": f"private_app_{suffix}",
                                "label": "Private App Subnet",
                                "tier": "app",
                                "nodes": _duplicate_node_list(slots["app"], suffix=f"_{suffix}"),
                            },
                            {
                                "id": f"private_data_{suffix}",
                                "label": "Private Data Subnet",
                                "tier": "db",
                                "nodes": _duplicate_node_list(slots["data"], suffix=f"_{suffix}"),
                            },
                        ],
                    }
                ],
                "gateways": _duplicate_node_list(slots["gateways"], suffix=f"_{suffix}"),
                "oci_services": _duplicate_node_list(slots["managed_services"], suffix=f"_{suffix}"),
            }
        )

    return {
        "deployment_type": "multi_region",
        "page": {"width": 1654, "height": 1169},
        "regions": regions,
        "external": slots["external"],
        "edges": _build_standard_edges(slots, multi_region=True),
    }


def _render_hub_spoke_template(items: list[Any], hints: dict[str, Any]) -> dict[str, Any]:
    slots = _classify_slots(items)
    hub_nodes = slots["managed_services"][:]
    if slots["gateways"]:
        hub_nodes.extend(slots["gateways"][:1])
    spoke_a = slots["app"][: max(1, len(slots["app"]) // 2 or 1)]
    spoke_b = slots["app"][len(spoke_a):] or slots["data"][:1]
    if not spoke_b:
        spoke_b = [{"id": "spoke_b_compute", "type": "compute", "label": "Spoke B Workload"}]

    region = {
        "id": "region_primary",
        "label": "OCI Region",
        "compartments": [
            {
                "id": "hub_compartment",
                "label": "Hub Compartment",
                "gateways": slots["gateways"],
                "subnets": [
                    {
                        "id": "hub_subnet",
                        "label": "Hub Services Subnet",
                        "tier": "app",
                        "nodes": hub_nodes,
                    }
                ],
            },
            {
                "id": "spoke_a_compartment",
                "label": "Spoke A Compartment",
                "gateways": [],
                "subnets": [
                    {
                        "id": "spoke_a_subnet",
                        "label": "Spoke A Private Subnet",
                        "tier": "app",
                        "nodes": spoke_a,
                    }
                ],
            },
            {
                "id": "spoke_b_compartment",
                "label": "Spoke B Compartment",
                "gateways": [],
                "subnets": [
                    {
                        "id": "spoke_b_subnet",
                        "label": "Spoke B Private Subnet",
                        "tier": "db",
                        "nodes": spoke_b,
                    }
                ],
            },
        ],
        "shared_services": [],
        "regional_subnets": [],
        "availability_domains": [],
        "gateways": [],
        "oci_services": [],
    }

    return {
        "deployment_type": "multi_compartment",
        "page": {"width": 1654, "height": 1169},
        "regions": [region],
        "external": slots["external"],
        "edges": [
            {"id": "e_onprem_hub", "source": "on_prem", "target": "hub_compartment", "label": "FastConnect"},
            {"id": "e_hub_spoke_a", "source": "hub_compartment", "target": "spoke_a_compartment", "label": "Hub-Spoke"},
            {"id": "e_hub_spoke_b", "source": "hub_compartment", "target": "spoke_b_compartment", "label": "Hub-Spoke"},
        ],
    }


def _classify_slots(items: list[Any]) -> dict[str, list[dict[str, Any]]]:
    public_ingress: list[dict[str, Any]] = []
    app: list[dict[str, Any]] = []
    data: list[dict[str, Any]] = []
    managed_services: list[dict[str, Any]] = []
    gateways: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []

    for item in items or []:
        item_id = str(getattr(item, "id", "") or "")
        item_type = str(getattr(item, "oci_type", "") or "")
        layer = str(getattr(item, "layer", "") or "").lower()
        node = {"id": item_id, "type": item_type, "label": str(getattr(item, "label", "") or item_id)}
        type_lc = item_type.lower()

        if type_lc in _GATEWAY_TYPES:
            gateways.append(node)
        elif layer == "external" or type_lc in _EXTERNAL_TYPES:
            external.append(node)
        elif type_lc in _PUBLIC_INGRESS_TYPES or (layer == "ingress" and type_lc not in _MANAGED_SERVICE_TYPES):
            public_ingress.append(node)
        elif type_lc in _MANAGED_SERVICE_TYPES:
            managed_services.append(node)
        elif type_lc in _DATABASE_TYPES or layer == "data":
            data.append(node)
        else:
            app.append(node)

    if public_ingress and not any(node["type"].lower() == "internet gateway" for node in gateways):
        gateways.append({"id": "igw_template", "type": "internet gateway", "label": "Internet Gateway"})
    if (app or data or managed_services) and not any(node["type"].lower() == "nat gateway" for node in gateways):
        gateways.append({"id": "nat_template", "type": "nat gateway", "label": "NAT Gateway"})
    if managed_services and not any(node["type"].lower() == "service gateway" for node in gateways):
        gateways.append({"id": "sgw_template", "type": "service gateway", "label": "Service Gateway"})
    if any(node["type"].lower() == "drg" for node in gateways) and not any(node["id"] == "on_prem" for node in external):
        external.append({"id": "on_prem", "type": "on premises", "label": "On-Premises"})
    if public_ingress and not any(node["id"] == "internet" for node in external):
        external.append({"id": "internet", "type": "internet", "label": "Internet"})
    if any(node["type"].lower() == "bastion" for node in public_ingress) and not any(node["id"] == "admins" for node in external):
        external.append({"id": "admins", "type": "admins", "label": "Admins"})

    return {
        "public_ingress": public_ingress,
        "app": app,
        "data": data,
        "managed_services": managed_services,
        "gateways": gateways,
        "external": external,
    }


def _build_standard_edges(slots: dict[str, list[dict[str, Any]]], *, multi_region: bool) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    if slots["public_ingress"] and any(ext["id"] == "internet" for ext in slots["external"]):
        edges.append(
            {
                "id": "e_internet_ingress",
                "source": "internet",
                "target": slots["public_ingress"][0]["id"],
                "label": "HTTPS/443",
            }
        )
    if slots["public_ingress"] and slots["app"]:
        edges.append(
            {
                "id": "e_ingress_app",
                "source": slots["public_ingress"][0]["id"],
                "target": slots["app"][0]["id"],
                "label": "App Traffic",
            }
        )
    if slots["app"] and slots["data"]:
        edges.append(
            {
                "id": "e_app_data",
                "source": slots["app"][0]["id"],
                "target": slots["data"][0]["id"],
                "label": "SQL/HTTPS",
            }
        )
    if slots["app"] and slots["managed_services"]:
        edges.append(
            {
                "id": "e_app_services",
                "source": slots["app"][0]["id"],
                "target": slots["managed_services"][0]["id"],
                "label": "OCI API",
            }
        )
    if any(ext["id"] == "on_prem" for ext in slots["external"]) and any(gw["type"].lower() == "drg" for gw in slots["gateways"]):
        edges.append(
            {
                "id": "e_onprem_drg",
                "source": "on_prem",
                "target": next(gw["id"] for gw in slots["gateways"] if gw["type"].lower() == "drg"),
                "label": "FastConnect/VPN",
            }
        )
    if multi_region and slots["data"]:
        edges.append(
            {
                "id": "e_inter_region_replication",
                "source": f"{slots['data'][0]['id']}_a",
                "target": f"{slots['data'][0]['id']}_b",
                "label": "Replication",
            }
        )
    return edges


def _duplicate_nodes_with_suffix(subnets: list[dict[str, Any]], *, suffix: str) -> list[dict[str, Any]]:
    duplicated: list[dict[str, Any]] = []
    for subnet in subnets:
        duplicated.append(
            {
                "id": f"{subnet['id']}{suffix}",
                "label": subnet["label"],
                "tier": subnet["tier"],
                "nodes": _duplicate_node_list(subnet.get("nodes", []), suffix=suffix),
            }
        )
    return duplicated


def _duplicate_node_list(nodes: list[dict[str, Any]], *, suffix: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{node['id']}{suffix}",
            "type": node["type"],
            "label": node["label"],
        }
        for node in nodes
    ]


def _iter_node_ids(spec: dict[str, Any]) -> list[str]:
    node_ids: list[str] = []
    for ext in spec.get("external", []) or []:
        node_ids.append(str(ext.get("id", "") or ""))
    for region in spec.get("regions", []) or []:
        for subnet in region.get("regional_subnets", []) or []:
            node_ids.extend(str(node.get("id", "") or "") for node in subnet.get("nodes", []) or [])
        for ad in region.get("availability_domains", []) or []:
            for subnet in ad.get("subnets", []) or []:
                node_ids.extend(str(node.get("id", "") or "") for node in subnet.get("nodes", []) or [])
        for comp in region.get("compartments", []) or []:
            for subnet in comp.get("subnets", []) or []:
                node_ids.extend(str(node.get("id", "") or "") for node in subnet.get("nodes", []) or [])
        node_ids.extend(str(node.get("id", "") or "") for node in region.get("oci_services", []) or [])
        node_ids.extend(str(node.get("id", "") or "") for node in region.get("shared_services", []) or [])
        node_ids.extend(str(node.get("id", "") or "") for node in region.get("gateways", []) or [])
    return [node_id for node_id in node_ids if node_id]


def _collect_public_node_ids(spec: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for region in spec.get("regions", []) or []:
        for subnet in region.get("regional_subnets", []) or []:
            ids.extend(str(node.get("id", "") or "") for node in subnet.get("nodes", []) or [])
    return [node_id for node_id in ids if node_id]


def _collect_private_node_ids(spec: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for region in spec.get("regions", []) or []:
        for ad in region.get("availability_domains", []) or []:
            for subnet in ad.get("subnets", []) or []:
                ids.extend(str(node.get("id", "") or "") for node in subnet.get("nodes", []) or [])
        for comp in region.get("compartments", []) or []:
            for subnet in comp.get("subnets", []) or []:
                ids.extend(str(node.get("id", "") or "") for node in subnet.get("nodes", []) or [])
    return [node_id for node_id in ids if node_id]


def _has_gateway(spec: dict[str, Any], gateway_type: str) -> bool:
    target = gateway_type.lower()
    for region in spec.get("regions", []) or []:
        for gateway in region.get("gateways", []) or []:
            if str(gateway.get("type", "") or "").lower() == target:
                return True
        for comp in region.get("compartments", []) or []:
            for gateway in comp.get("gateways", []) or []:
                if str(gateway.get("type", "") or "").lower() == target:
                    return True
    return False
