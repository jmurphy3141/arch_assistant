"""Canonical cross-artifact decisions for one Archie engagement.

The contract is deliberately deterministic.  It converts selected POC service
labels and finalized BOM rows into stable identifiers that downstream tools can
validate without asking an LLM to reconcile artifacts.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "1.0"

_SERVICE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("database.autonomous", ("autonomous database", "autonomous transaction processing")),
    ("database.postgresql", ("database with postgresql", "postgresql db", "postgresql", "postgres")),
    ("database.mysql", ("mysql heatwave", "mysql db", "mysql")),
    ("database.oracle", ("base database service", "oracle database")),
    ("network.load_balancer.flexible", ("flexible load balancer", "load balancer")),
    ("security.waf", ("web application firewall", "oci waf", "waf policy")),
    ("compute.vm.standard.e5.flex", ("vm.standard.e5.flex", "standard - e5", "e5.flex")),
    ("compute.vm.standard.e6.flex", ("vm.standard.e6.flex", "standard - e6", "e6.flex")),
    ("compute.vm", ("compute instance", "application server", "web server", "compute")),
    ("storage.object", ("object storage",)),
    ("storage.block", ("block volume", "block storage")),
    ("network.vpn.site_to_site", ("site-to-site vpn", "site to site vpn", "ipsec")),
    ("network.fastconnect", ("fastconnect",)),
    ("observability.logging", ("logging analytics", "oci logging", "audit logging", "logging")),
    ("observability.monitoring", ("oci monitoring", "monitoring", "apm")),
    ("container.oke", ("container engine for kubernetes", "oke")),
)

_DISPLAY_NAMES = {
    "database.autonomous": "Autonomous Database",
    "database.postgresql": "PostgreSQL",
    "database.mysql": "MySQL HeatWave",
    "database.oracle": "Oracle Base Database Service",
    "network.load_balancer.flexible": "Flexible Load Balancer",
    "security.waf": "OCI WAF",
    "compute.vm.standard.e5.flex": "VM.Standard.E5.Flex",
    "compute.vm.standard.e6.flex": "VM.Standard.E6.Flex",
    "compute.vm": "OCI Compute",
    "storage.object": "Object Storage",
    "storage.block": "Block Volume",
    "network.vpn.site_to_site": "Site-to-Site VPN",
    "network.fastconnect": "FastConnect",
    "observability.logging": "OCI Logging",
    "observability.monitoring": "OCI Monitoring",
    "container.oke": "Container Engine for Kubernetes (OKE)",
}


def canonical_service_id(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    for service_id, aliases in _SERVICE_ALIASES:
        if any(alias in text for alias in aliases):
            return service_id
    return ""


def display_name(service_id: str) -> str:
    return _DISPLAY_NAMES.get(service_id, service_id)


def selected_poc(context: dict[str, Any]) -> dict[str, Any]:
    archie = context.get("archie", {}) if isinstance(context, dict) else {}
    resolved = archie.get("resolved_decisions", {}) if isinstance(archie, dict) else {}
    poc = resolved.get("poc", {}) if isinstance(resolved, dict) else {}
    option = poc.get("selected_option") if isinstance(poc, dict) else None
    return dict(option) if isinstance(option, dict) else {}


def get_contract(context: dict[str, Any]) -> dict[str, Any]:
    archie = context.get("archie", {}) if isinstance(context, dict) else {}
    contract = archie.get("consistency_contract") if isinstance(archie, dict) else None
    if isinstance(contract, dict) and contract:
        return copy.deepcopy(contract)
    option = selected_poc(context)
    return contract_from_poc(option) if option else {}


def contract_from_poc(option: dict[str, Any], *, artifact_key: str = "") -> dict[str, Any]:
    services = list(option.get("oci_services", []) or [])
    components = []
    seen: set[str] = set()
    for label in services:
        service_id = canonical_service_id(label)
        if not service_id or service_id in seen:
            continue
        seen.add(service_id)
        components.append({
            "service_id": service_id,
            "display_name": display_name(service_id),
            "required": True,
            "source": "selected_poc",
        })
    grounding = option.get("grounding") if isinstance(option.get("grounding"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "selected_poc": {
            "name": str(option.get("option_name") or ""),
            "artifact_key": artifact_key,
        },
        "region": str(grounding.get("region") or ""),
        "ha_mode": str(grounding.get("ha_mode") or "single-AD POC"),
        "components": components,
        "assumptions": [],
        "artifact_bindings": {},
    }


def record_selected_poc(
    context: dict[str, Any],
    option: dict[str, Any],
    *,
    artifact_key: str = "",
) -> dict[str, Any]:
    archie = context.setdefault("archie", {})
    incoming = contract_from_poc(option, artifact_key=artifact_key)
    prior = archie.get("consistency_contract")
    if isinstance(prior, dict):
        same_name = (prior.get("selected_poc") or {}).get("name") == incoming["selected_poc"]["name"]
        incoming["revision"] = int(prior.get("revision", 0) or 0) + (0 if same_name else 1)
        incoming["artifact_bindings"] = dict(prior.get("artifact_bindings", {}) or {})
    archie["consistency_contract"] = incoming
    return incoming


def required_components(context: dict[str, Any]) -> list[dict[str, Any]]:
    contract = get_contract(context)
    return [dict(item) for item in contract.get("components", []) or [] if isinstance(item, dict) and item.get("required")]


def bom_coverage_ids(payload: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for row in list(payload.get("line_items", []) or []) + list(payload.get("scope_items", []) or []):
        if not isinstance(row, dict):
            continue
        service_id = str(row.get("canonical_service_id") or "") or canonical_service_id(
            f"{row.get('description', '')} {row.get('notes', '')}"
        )
        if service_id:
            found.add(service_id)
    return found


def ensure_bom_scope(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Add transparent coverage rows and safe assumed sizing; never invent SKUs."""
    output = copy.deepcopy(payload)
    line_items = [row for row in output.get("line_items", []) or [] if isinstance(row, dict)]
    scope_items = [row for row in output.get("scope_items", []) or [] if isinstance(row, dict)]
    output["line_items"] = line_items
    output["scope_items"] = scope_items

    for row in line_items:
        service_id = canonical_service_id(f"{row.get('description', '')} {row.get('notes', '')}")
        if service_id:
            row["canonical_service_id"] = service_id

    covered = bom_coverage_ids(output)
    for component in required_components(context):
        service_id = str(component.get("service_id") or "")
        if not service_id or service_id in covered:
            continue
        assumptions: list[str] = []
        sizing: dict[str, Any] = {}
        pricing_status = "scope_only"
        if service_id == "database.postgresql":
            sizing = {"systems": 1, "ocpu": 2, "memory_gb": 32, "storage_gb": 100}
            assumptions = [
                "Assumed one OCI Database with PostgreSQL system for POC validation.",
                "Assumed 2 OCPU, 32 GB memory, 100 GB storage, and single-AD deployment.",
            ]
            pricing_status = "authoritative_sku_required"
            line_items.append({
                "sku": "",
                "canonical_service_id": service_id,
                "description": "OCI Database with PostgreSQL - assumed POC system",
                "category": "database",
                "metric": "OCPU (pricing pending authoritative SKU)",
                "quantity": 2.0,
                "unit_price": 0.0,
                "monthly_multiplier": 1,
                "extended_price": 0.0,
                "instance_count": 1,
                "pricing_status": pricing_status,
                "notes": "Assumed 2 OCPU, 32 GB memory, 100 GB storage, single-AD; no substitute SKU used.",
            })
        elif service_id == "network.vpn.site_to_site":
            sizing = {"drg_count": 1, "ipsec_connections": 1}
            assumptions = ["Assumed one DRG and one Site-to-Site IPSec connection."]
        elif service_id == "observability.logging":
            sizing = {"ingestion_gb_month": 10}
            assumptions = ["Assumed 10 GB per month of logging ingestion for the POC."]
        elif service_id == "observability.monitoring":
            sizing = {"datapoints_million_month": 100}
            assumptions = ["Assumed 100 million monitoring datapoints per month for the POC."]
        scope_items.append({
            "canonical_service_id": service_id,
            "description": display_name(service_id),
            "quantity": 1,
            "pricing_status": pricing_status,
            "assumed_sizing": sizing,
            "assumptions": assumptions,
            "source": "selected_poc",
        })
        covered.add(service_id)
    return output


def validate_bom(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    required = {str(item.get("service_id") or "") for item in required_components(context)}
    represented = bom_coverage_ids(payload)
    missing = sorted(required - represented)
    unexpected = sorted(represented - required) if required else []
    contradictions: list[str] = []
    if "database.postgresql" in required and "database.autonomous" in represented:
        contradictions.append("Autonomous Database cannot substitute for selected PostgreSQL")
    if "network.vpn.site_to_site" in required and "network.fastconnect" in represented:
        contradictions.append("FastConnect cannot substitute for selected Site-to-Site VPN")
    return {
        "verdict": "pass" if not missing and not unexpected and not contradictions else "blocked",
        "required_components": sorted(required),
        "represented_components": sorted(represented),
        "missing_components": missing,
        "unexpected_components": unexpected,
        "contradictions": contradictions,
    }


def record_final_bom(
    context: dict[str, Any],
    payload: dict[str, Any],
    *,
    artifact_key: str,
) -> dict[str, Any]:
    archie = context.setdefault("archie", {})
    contract = get_contract(context)
    contract.setdefault("artifact_bindings", {})["bom"] = artifact_key
    contract["bom"] = {
        "region": payload.get("region"),
        "components": sorted(bom_coverage_ids(payload)),
        "line_items": [
            {
                key: row.get(key)
                for key in ("sku", "canonical_service_id", "description", "quantity", "metric", "instance_count")
            }
            for row in payload.get("line_items", []) or []
            if isinstance(row, dict)
        ],
        "scope_items": copy.deepcopy(payload.get("scope_items", []) or []),
    }
    contract["updated_at"] = datetime.now(timezone.utc).isoformat()
    archie["consistency_contract"] = contract
    return contract


def record_final_diagram(
    context: dict[str, Any],
    *,
    artifact_key: str,
    represented_components: list[str],
    ha_mode: str,
) -> dict[str, Any]:
    archie = context.setdefault("archie", {})
    contract = get_contract(context)
    contract.setdefault("artifact_bindings", {})["diagram"] = artifact_key
    contract["diagram"] = {
        "components": sorted(set(represented_components)),
        "ha_mode": ha_mode,
    }
    contract["updated_at"] = datetime.now(timezone.utc).isoformat()
    archie["consistency_contract"] = contract
    return contract


def bind_artifact(context: dict[str, Any], artifact_type: str, artifact_key: str) -> dict[str, Any]:
    archie = context.setdefault("archie", {})
    contract = get_contract(context)
    if not contract:
        return {}
    contract.setdefault("artifact_bindings", {})[str(artifact_type)] = str(artifact_key)
    contract["updated_at"] = datetime.now(timezone.utc).isoformat()
    archie["consistency_contract"] = contract
    return contract


def request_conflicts(text: str, context: dict[str, Any]) -> list[dict[str, str]]:
    required = {str(item.get("service_id") or "") for item in required_components(context)}
    requested_ids = {
        service_id
        for service_id, aliases in _SERVICE_ALIASES
        if any(alias in str(text or "").lower() for alias in aliases)
    }
    if any(item.startswith("compute.vm.standard.") for item in required):
        requested_ids.discard("compute.vm")
    conflicts = []
    contract = get_contract(context)
    region_match = re.search(r"\b[a-z]{2,}-[a-z]+-\d+\b", str(text or ""), re.IGNORECASE)
    contract_region = str(contract.get("region") or "").strip()
    if region_match and contract_region and region_match.group(0).casefold() != contract_region.casefold():
        conflicts.append({
            "field": "region",
            "upstream_value": contract_region,
            "requested_value": region_match.group(0),
            "source": "selected_poc",
        })
    unexpected_requested = requested_ids - required
    if "database.postgresql" in required:
        unexpected_requested.discard("database.autonomous")
    for service_id in sorted(unexpected_requested) if required else []:
        conflicts.append({
            "field": "services",
            "upstream_value": ", ".join(sorted(required)),
            "requested_value": service_id,
            "source": "selected_poc",
        })
    if "database.postgresql" in required and "database.autonomous" in requested_ids:
        conflicts.append({
            "field": "database.service",
            "upstream_value": "PostgreSQL",
            "requested_value": "Autonomous Database",
            "source": "selected_poc",
        })
    return conflicts
