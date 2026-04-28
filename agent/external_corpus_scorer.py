from __future__ import annotations

import json
from pathlib import Path
from typing import Any


INTERNAL_BOX_TAGS = {
    "_region_box": "region",
    "_region_stub": "region_stub",
    "_compartment_box": "compartment",
    "_vcn_box": "vcn",
    "_ad_box": "ad",
    "_fd_box": "fd",
    "_subnet_box": "subnet",
}

SERVICE_WEIGHTS = {
    "oke": 2.5,
    "waf": 1.5,
    "bastion": 1.5,
    "generative_ai": 2.0,
    "document_understanding": 2.0,
    "language": 1.5,
    "dns": 1.0,
    "load_balancer": 1.0,
    "database": 1.0,
    "object_storage": 1.0,
    "remote_peering": 1.5,
    "internet_gateway": 0.5,
    "nat_gateway": 0.5,
    "service_gateway": 0.5,
    "drg": 0.5,
    "compute": 0.5,
    "monitoring": 0.5,
    "user_group": 0.5,
}
CORE_BOX_TAGS = {"region", "vcn", "subnet", "ad", "fd"}


def canonical_service_tag(raw: str | None) -> str | None:
    token = (raw or "").strip().lower()
    if not token:
        return None

    checks = [
        ("document understanding", "document_understanding"),
        ("generative ai", "generative_ai"),
        ("autonomous transaction processing", "database"),
        ("postgresql", "database"),
        ("container engine for kubernetes", "oke"),
        ("container engine", "oke"),
        ("oke", "oke"),
        ("flexible load balancer", "load_balancer"),
        ("load balancer", "load_balancer"),
        ("internet gateway", "internet_gateway"),
        ("service gateway", "service_gateway"),
        ("nat gateway", "nat_gateway"),
        ("remote peering", "remote_peering"),
        ("object storage", "object_storage"),
        ("document understanding", "document_understanding"),
        ("language", "language"),
        ("bastion", "bastion"),
        ("waf", "waf"),
        ("dns", "dns"),
        ("database", "database"),
        ("drg", "drg"),
        ("user group", "user_group"),
        ("virtual machine", "compute"),
        ("compute", "compute"),
        ("monitoring", "monitoring"),
    ]

    for needle, tag in checks:
        if needle in token:
            return tag
    return None


def canonical_box_tag(raw: str | None) -> str | None:
    token = (raw or "").strip().lower()
    if not token:
        return None

    checks = [
        ("grouping - tenancy", "tenancy"),
        ("grouping - compartment", "compartment"),
        ("grouping - oci region", "region"),
        ("grouping - vcn", "vcn"),
        ("grouping - subnet", "subnet"),
        ("availability domain", "ad"),
        ("fault domain", "fd"),
    ]
    for needle, tag in checks:
        if needle in token:
            return tag
    return None


def _classify_subnet(label: str | None, tier: str | None = None) -> str | None:
    label_lc = (label or "").lower()
    tier_lc = (tier or "").lower()
    if "public" in label_lc:
        return "public"
    if "private" in label_lc:
        return "private"
    if tier_lc in {"public_ingress", "bastion"}:
        return "public"
    if tier_lc in {"private_ingress", "app", "db", "data", "compute"}:
        return "private"
    return None


def _count_external_groupings(report: list[dict[str, Any]], needle: str) -> int:
    return sum(1 for item in report if needle in str(item.get("icon_title", "")).lower())


def extract_external_traits(spec: dict[str, Any], report: list[dict[str, Any]]) -> dict[str, Any]:
    pages = [page for page in spec.get("pages", []) if page.get("page_type") == "physical"]
    elements = [element for page in pages for element in page.get("elements", [])]

    service_tags: set[str] = set()
    box_tags: set[str] = set()
    public_subnet_count = 0
    private_subnet_count = 0

    for item in report:
        box_tag = canonical_box_tag(item.get("icon_title"))
        if box_tag:
            box_tags.add(box_tag)

        service_tag = canonical_service_tag(item.get("query") or item.get("icon_title"))
        if service_tag:
            service_tags.add(service_tag)

    for element in elements:
        if str(element.get("query", "")).lower() == "subnet":
            subnet_class = _classify_subnet(element.get("value") or element.get("label"))
            if subnet_class == "public":
                public_subnet_count += 1
            elif subnet_class == "private":
                private_subnet_count += 1

    return {
        "region_count": _count_external_groupings(report, "grouping - oci region"),
        "vcn_count": _count_external_groupings(report, "grouping - vcn"),
        "subnet_count": _count_external_groupings(report, "grouping - subnet"),
        "ad_count": _count_external_groupings(report, "availability domain"),
        "fd_count": _count_external_groupings(report, "fault domain"),
        "edge_count": sum(1 for item in report if item.get("kind") == "edge"),
        "box_tags": sorted(box_tags),
        "service_tags": sorted(service_tags),
        "public_subnet_count": public_subnet_count,
        "private_subnet_count": private_subnet_count,
    }


def extract_internal_traits(draw_dict: dict[str, Any]) -> dict[str, Any]:
    boxes = draw_dict.get("boxes", [])
    nodes = draw_dict.get("nodes", [])
    edges = draw_dict.get("edges", [])

    service_tags = sorted(
        {
            tag
            for node in nodes
            for tag in [canonical_service_tag(node.get("type"))]
            if tag
        }
    )
    box_tags = sorted(
        {
            INTERNAL_BOX_TAGS[box_type]
            for box in boxes
            for box_type in [box.get("box_type")]
            if box_type in INTERNAL_BOX_TAGS
        }
    )

    public_subnet_count = 0
    private_subnet_count = 0
    for box in boxes:
        if box.get("box_type") != "_subnet_box":
            continue
        subnet_class = _classify_subnet(box.get("label"), box.get("tier"))
        if subnet_class == "public":
            public_subnet_count += 1
        elif subnet_class == "private":
            private_subnet_count += 1

    return {
        "region_count": sum(1 for box in boxes if box.get("box_type") == "_region_box"),
        "vcn_count": sum(1 for box in boxes if box.get("box_type") == "_vcn_box"),
        "subnet_count": sum(1 for box in boxes if box.get("box_type") == "_subnet_box"),
        "ad_count": sum(1 for box in boxes if box.get("box_type") == "_ad_box"),
        "fd_count": sum(1 for box in boxes if box.get("box_type") == "_fd_box"),
        "edge_count": len(edges),
        "box_tags": box_tags,
        "service_tags": service_tags,
        "public_subnet_count": public_subnet_count,
        "private_subnet_count": private_subnet_count,
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def _weighted_jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    intersection = left & right
    intersection_weight = sum(SERVICE_WEIGHTS.get(tag, 1.0) for tag in intersection)
    union_weight = sum(SERVICE_WEIGHTS.get(tag, 1.0) for tag in union)
    if union_weight <= 0:
        return 1.0
    return intersection_weight / union_weight


def _closeness(candidate: int, reference: int, tolerance: int) -> float:
    if tolerance <= 0:
        return 1.0 if candidate == reference else 0.0
    return max(0.0, 1.0 - (abs(candidate - reference) / tolerance))


def score_trait_alignment(candidate_traits: dict[str, Any], reference_traits: dict[str, Any]) -> dict[str, Any]:
    candidate_services = set(candidate_traits.get("service_tags", []))
    reference_services = set(reference_traits.get("service_tags", []))
    candidate_boxes = set(candidate_traits.get("box_tags", [])) & CORE_BOX_TAGS
    reference_boxes = set(reference_traits.get("box_tags", [])) & CORE_BOX_TAGS

    components = {
        "region_count": _closeness(candidate_traits["region_count"], reference_traits["region_count"], 2),
        "vcn_count": _closeness(candidate_traits["vcn_count"], reference_traits["vcn_count"], 2),
        "subnet_count": _closeness(candidate_traits["subnet_count"], reference_traits["subnet_count"], 4),
        "public_subnets": _closeness(
            candidate_traits["public_subnet_count"], reference_traits["public_subnet_count"], 2
        ),
        "private_subnets": _closeness(
            candidate_traits["private_subnet_count"], reference_traits["private_subnet_count"], 3
        ),
        "edge_count": _closeness(candidate_traits["edge_count"], reference_traits["edge_count"], 8),
        "multi_region": 1.0
        if (candidate_traits["region_count"] > 1) == (reference_traits["region_count"] > 1)
        else 0.0,
        "service_tags": _weighted_jaccard(candidate_services, reference_services),
        "box_tags": _jaccard(candidate_boxes, reference_boxes),
    }
    weights = {
        "region_count": 2.0,
        "vcn_count": 1.0,
        "subnet_count": 1.5,
        "public_subnets": 1.0,
        "private_subnets": 1.0,
        "edge_count": 0.0,
        "multi_region": 2.0,
        "service_tags": 4.0,
        "box_tags": 1.0,
    }

    weighted_score = sum(components[name] * weights[name] for name in components)
    total_weight = sum(weights.values())
    score = round((weighted_score / total_weight) * 100, 2)

    return {
        "score": score,
        "components": components,
        "matched_service_tags": sorted(candidate_services & reference_services),
        "missing_service_tags": sorted(reference_services - candidate_services),
        "extra_service_tags": sorted(candidate_services - reference_services),
    }


def load_external_example_profiles(skill_root: str | Path) -> dict[str, dict[str, Any]]:
    skill_root = Path(skill_root)
    specs_dir = skill_root / "assets" / "examples" / "specs"
    output_dir = skill_root / "assets" / "examples" / "output"

    profiles: dict[str, dict[str, Any]] = {}
    for spec_path in sorted(specs_dir.glob("*.json")):
        example_name = spec_path.stem
        report_path = output_dir / f"{example_name}.report.json"
        if not report_path.exists():
            continue
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        profiles[example_name] = extract_external_traits(spec, report)
    return profiles


def rank_reference_profiles(
    candidate_traits: dict[str, Any],
    reference_profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for name, reference_traits in reference_profiles.items():
        result = score_trait_alignment(candidate_traits, reference_traits)
        ranked.append(
            {
                "name": name,
                "score": result["score"],
                "components": result["components"],
                "matched_service_tags": result["matched_service_tags"],
                "missing_service_tags": result["missing_service_tags"],
                "extra_service_tags": result["extra_service_tags"],
            }
        )
    return sorted(ranked, key=lambda item: (-float(item["score"]), str(item["name"])))
