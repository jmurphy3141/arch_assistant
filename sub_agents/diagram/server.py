from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from agent.bom_parser import ServiceItem, freeform_arch_text_to_llm_input

try:
    from agent.intent_compiler import compile_intent
except ImportError:
    from agent.intent_compiler import compile_intent_to_flat_spec as compile_intent
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import DrawioValidationError, generate_drawio
from agent.llm_inference_client import run_inference
from agent.layout_intent import LayoutIntentError, validate_layout_intent
from sub_agents.base import make_agent_app
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


def _clean_json(raw: str) -> str:
    text = str(raw or "").strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        return fenced.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


_diagram_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_diagram_llm = _diagram_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_diagram_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = (
    _SYSTEM_PROMPT.read_text(encoding="utf-8") if _SYSTEM_PROMPT.exists() else ""
)

_VALID_LAYOUT_LAYERS = {"async", "compute", "data", "external", "ingress"}

_CONTRACT_SERVICE_TYPES = {
    "compute.vm.standard.e5.flex": "compute",
    "compute.vm.standard.e6.flex": "compute",
    "compute.vm": "compute",
    "database.postgresql": "database",
    "database.autonomous": "database",
    "database.mysql": "database",
    "database.oracle": "database",
    "network.load_balancer.flexible": "load balancer",
    "security.waf": "waf",
    "storage.object": "object storage",
    "storage.block": "block volume",
    "network.vpn.site_to_site": "vpn",
    "network.fastconnect": "fastconnect",
    "observability.logging": "logging",
    "observability.monitoring": "monitoring",
    "container.oke": "oke",
}


card = AgentCard(
    name="diagram",
    description="Generates an OCI architecture draw.io diagram from a workload description.",
    inputs={
        "required": ["task"],
        "optional": ["diagram_name", "customer_id", "trace_id"],
    },
    output="draw.io XML string",
    llm_model_id=_model_id,
)


def _component_clarification_questions(task: str) -> list[dict[str, Any]]:
    lowered = str(task or "").lower()
    questions = [
        {
            "id": "workload.components",
            "question": "What major OCI components need to appear in the diagram (for example OKE, load balancer, database, Object Storage, WAF, VCN, or subnets)?",
            "blocking": True,
        }
    ]
    if any(marker in lowered for marker in ("update", "updated", "revise", "revision", "correct", "fix", "icon", "icons")):
        questions.insert(
            0,
            {
                "id": "diagram.source",
                "question": "Which existing diagram should be updated, or should I regenerate it from a fresh component list?",
                "blocking": True,
            },
        )
    return questions


def _explicit_layout_intent(task: str, items: list[Any]) -> dict[str, Any] | None:
    """Build a bounded layout intent when the user already supplied the topology.

    Detailed chat requests do not need an LLM to rediscover the service list and
    standard three-tier placement that the deterministic parser already found.
    Keeping this path deliberately strict leaves sparse or unusual requests on
    the existing inference/clarification path.
    """
    lowered = re.sub(r"\s+", " ", str(task or "")).lower()
    item_types = {str(getattr(item, "oci_type", "") or "").lower() for item in items}
    required_signals = (
        "load balancer",
        "private",
        "database",
        "vcn",
        "gateway",
        "route table",
        "nsg",
    )
    if "include" not in lowered or not all(signal in lowered for signal in required_signals):
        return None
    if len(item_types) < 8 or not {"compute", "database", "load balancer"}.issubset(item_types):
        return None

    groups = [
        {"id": "pub_sub_box", "label": "Public Subnet", "order": 1},
        {"id": "app_sub_box", "label": "Private Application Subnet", "order": 2},
        {"id": "db_sub_box", "label": "Private Database Subnet", "order": 3},
    ]
    placements: list[dict[str, Any]] = []
    group_by_type = {
        "waf": "pub_sub_box",
        "load balancer": "pub_sub_box",
        "network security group": "app_sub_box",
        "route table": "app_sub_box",
        "compute": "app_sub_box",
        "database": "db_sub_box",
        "block volume": "db_sub_box",
        "poc boundary": "db_sub_box",
    }
    for item in items:
        oci_type = str(getattr(item, "oci_type", "") or "").lower()
        placement = {
            "id": str(getattr(item, "id", "") or ""),
            "oci_type": oci_type,
            "layer": str(getattr(item, "layer", "data") or "data"),
        }
        group = group_by_type.get(oci_type)
        if group:
            placement["group"] = group
        placements.append(placement)

    ids_by_type = {
        str(getattr(item, "oci_type", "") or "").lower(): str(getattr(item, "id", "") or "")
        for item in items
    }
    flow_types = ("internet", "waf", "load balancer", "compute", "database")
    flow_ids = [ids_by_type[oci_type] for oci_type in flow_types if ids_by_type.get(oci_type)]
    edges = [
        {
            "id": f"flow_{index}",
            "source": source,
            "target": target,
            "label": "",
        }
        for index, (source, target) in enumerate(zip(flow_ids, flow_ids[1:]), start=1)
    ]

    return {
        "schema_version": "1.0",
        "deployment_hints": {
            "region_count": 1,
            "availability_domains_per_region": 1,
            "dr_enabled": False,
            "on_prem_connectivity": "none",
        },
        "groups": groups,
        "placements": placements,
        "assumptions": [],
        "edges": edges,
        "fixed_edges_policy": True,
    }


def _contract_layout_intent(task: str, items: list[Any]) -> dict[str, Any] | None:
    if "[authoritative cross-artifact requirements]" not in str(task or "").lower():
        return None
    lowered_task = str(task or "").lower()
    contract_match = re.search(
        r"\[authoritative cross-artifact requirements\](.*?)"
        r"\[/authoritative cross-artifact requirements\]",
        lowered_task,
        flags=re.DOTALL,
    )
    contract_text = contract_match.group(1) if contract_match else ""
    required_ids = set(re.findall(r"\(([a-z0-9_.]+)\)", contract_text))
    allowed_types = {
        _CONTRACT_SERVICE_TYPES[service_id]
        for service_id in required_ids
        if service_id in _CONTRACT_SERVICE_TYPES
    }
    if "vpn" in lowered_task and not any(str(getattr(item, "oci_type", "")).lower() == "vpn" for item in items):
        items.append(ServiceItem("vpn_1", "vpn", "Site-to-Site VPN (IPSec)", "external"))
    if "network.fastconnect" in required_ids and not any(
        str(getattr(item, "oci_type", "")).lower() == "fastconnect" for item in items
    ):
        # The freeform parser commonly represents private connectivity as a
        # generic DRG. Preserve the selected-POC identity explicitly so the
        # diagram cannot silently turn FastConnect into VPN or omit it.
        items.append(ServiceItem("fastconnect_1", "fastconnect", "FastConnect / DRG", "external"))
    if allowed_types:
        # The selected-POC contract is authoritative. The freeform parser's
        # generic best-practice additions must not introduce unselected WAF,
        # gateways, monitoring, storage, or other billable services.
        items[:] = [
            item
            for item in items
            if str(getattr(item, "oci_type", "") or "").lower() in allowed_types
        ]
    compute_match = re.search(
        r"(\d+(?:\.\d+)?)\s+ocpu total across\s+(\d+)\s+instances?\s+"
        r"\((\d+(?:\.\d+)?)\s+ocpu each\)",
        lowered_task,
    )
    memory_match = re.search(
        r"(\d+(?:\.\d+)?)\s+gb memory total across\s+(\d+)\s+instances?\s+"
        r"\((\d+(?:\.\d+)?)\s+gb each\)",
        lowered_task,
    )
    compute_shape = (
        "VM.Standard.E6.Flex"
        if "compute.vm.standard.e6.flex" in required_ids
        else "VM.Standard.E5.Flex"
    )
    for item in items:
        if str(getattr(item, "oci_type", "") or "").lower() == "compute" and compute_match:
            memory_suffix = f" / {memory_match.group(3)} GB each" if memory_match else ""
            item.label = (
                f"{compute_shape} ×{compute_match.group(2)}\n"
                f"{compute_match.group(1)} OCPU total / "
                f"{compute_match.group(3)} OCPU each{memory_suffix}"
            )
        if str(getattr(item, "oci_type", "") or "").lower() == "database":
            database_labels = (
                ("database.oracle", "Oracle Base Database Service"),
                ("database.postgresql", "PostgreSQL DB System"),
                ("database.mysql", "MySQL HeatWave DB System"),
                ("database.autonomous", "Autonomous Database"),
            )
            authoritative_label = next(
                (label for service_id, label in database_labels if service_id in required_ids),
                "",
            )
            if authoritative_label:
                item.label = authoritative_label
    groups = [
        {"id": "pub_sub_box", "label": "Public Ingress Subnet", "order": 1},
        {"id": "app_sub_box", "label": "Private Application Subnet", "order": 2},
        {"id": "db_sub_box", "label": "Private Database Subnet", "order": 3},
    ]
    group_by_type = {
        "waf": "pub_sub_box",
        "load balancer": "pub_sub_box",
        "compute": "app_sub_box",
        "block volume": "app_sub_box",
        "database": "db_sub_box",
        "network security group": "app_sub_box",
        "route table": "app_sub_box",
    }
    placements = []
    ids_by_type: dict[str, str] = {}
    for item in items:
        item_id = str(getattr(item, "id", "") or "")
        oci_type = str(getattr(item, "oci_type", "") or "").lower()
        if not item_id or not oci_type:
            continue
        placement = {
            "id": item_id,
            "oci_type": oci_type,
            "layer": str(getattr(item, "layer", "data") or "data"),
        }
        if group_by_type.get(oci_type):
            placement["group"] = group_by_type[oci_type]
        placements.append(placement)
        ids_by_type.setdefault(oci_type, item_id)
    flow_types = ("fastconnect", "internet", "waf", "load balancer", "compute", "database")
    flow_ids = [ids_by_type[item_type] for item_type in flow_types if ids_by_type.get(item_type)]
    edges = [
        {"id": f"contract_flow_{index}", "source": source, "target": target, "label": ""}
        for index, (source, target) in enumerate(zip(flow_ids, flow_ids[1:]), start=1)
    ]
    return {
        "schema_version": "1.0",
        "deployment_hints": {
            "region_count": 1,
            "availability_domains_per_region": 1,
            "dr_enabled": False,
            "on_prem_connectivity": (
                "fastconnect"
                if "network.fastconnect" in required_ids
                else "vpn" if "vpn" in str(task or "").lower() else "none"
            ),
        },
        "groups": groups,
        "placements": placements,
        "assumptions": [
            {
                "id": "authoritative_consistency_contract",
                "statement": "Topology compiled from the authoritative engagement consistency contract.",
                "reason": "Preserve selected POC and finalized BOM decisions.",
                "risk": "low",
            }
        ],
        "edges": edges,
        "fixed_edges_policy": True,
    }


def _ground_layout_placement_layers(spec: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    """Replace LLM-invented layer names with each parsed component's valid layer."""
    grounded = json.loads(json.dumps(spec))
    items_by_id = {str(getattr(item, "id", "") or ""): item for item in items}
    for placement in grounded.get("placements", []) or []:
        if not isinstance(placement, dict):
            continue
        item = items_by_id.get(str(placement.get("id") or ""))
        parsed_layer = str(getattr(item, "layer", "") or "").lower() if item else ""
        if parsed_layer in _VALID_LAYOUT_LAYERS:
            placement["layer"] = parsed_layer
            continue
        layer = str(placement.get("layer") or "").lower()
        if layer not in _VALID_LAYOUT_LAYERS:
            oci_type = str(placement.get("oci_type") or "").lower()
            placement["layer"] = (
                "external"
                if any(token in oci_type for token in ("vpn", "gateway", "internet", "poc boundary"))
                else "data"
            )
    return grounded


async def handle(req: A2ARequest) -> A2AResponse:
    try:
        items, prompt = freeform_arch_text_to_llm_input(req.task)
    except ValueError as exc:
        return A2AResponse(
            result=json.dumps(_component_clarification_questions(req.task), ensure_ascii=False),
            status="needs_input",
            trace={
                "agent": card.name,
                "trace_id": req.trace_id,
                "error_type": exc.__class__.__name__,
                "stage": "freeform_input_parsing",
            },
        )

    spec = _contract_layout_intent(req.task, items)
    generation_mode = "deterministic_contract_topology"
    if spec is None:
        spec = _explicit_layout_intent(req.task, items)
        generation_mode = "deterministic_explicit_topology"
    if spec is None:
        generation_mode = "llm_layout_intent"
        raw = await asyncio.to_thread(
            run_inference,
            prompt,
            endpoint=str(_main_inference.get("service_endpoint") or ""),
            model_id=_model_id,
            compartment_id=str(_main_config.get("compartment_id") or ""),
            max_tokens=int(
                _first_present(
                    _diagram_llm.get("max_tokens"),
                    _main_inference.get("max_tokens"),
                    default=4000,
                )
            ),
            temperature=float(
                _first_present(
                    _diagram_llm.get("temperature"),
                    _main_inference.get("temperature"),
                    default=0.0,
                )
            ),
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
        spec = json.loads(_clean_json(raw))

    if spec.get("status") == "need_clarification":
        questions_json = json.dumps(spec.get("questions", []), ensure_ascii=False)
        return A2AResponse(
            result=questions_json,
            status="needs_input",
            trace={
                "agent": card.name,
                "trace_id": req.trace_id,
                "llm_status": "need_clarification",
            },
        )

    if "placements" in spec:
        spec = _ground_layout_placement_layers(spec, items)
        try:
            intent = validate_layout_intent(spec, items)
            spec = compile_intent(intent, items)
        except LayoutIntentError as exc:
            return A2AResponse(
                result=str(exc),
                status="blocked",
                trace={
                    "agent": card.name,
                    "trace_id": req.trace_id,
                    "error_type": exc.__class__.__name__,
                    "stage": "layout_intent_validation",
                },
            )

    if generation_mode in {"deterministic_explicit_topology", "deterministic_contract_topology"}:
        regions = spec.get("regions") if isinstance(spec.get("regions"), list) else []
        region_match = re.search(r"\b[a-z]{2,}-[a-z]+-\d+\b", req.task, flags=re.IGNORECASE)
        for region in regions:
            if isinstance(region, dict) and region_match:
                region["label"] = region_match.group(0).lower()
            ads = region.get("availability_domains") if isinstance(region, dict) else []
            if isinstance(ads, list) and ads and isinstance(ads[0], dict):
                ads[0]["label"] = "Availability Domain 1 — Single-AD POC Boundary"
                for subnet in ads[0].get("subnets", []):
                    if isinstance(subnet, dict) and isinstance(subnet.get("nodes"), list):
                        subnet["nodes"] = [
                            node
                            for node in subnet["nodes"]
                            if str(node.get("type") or "").lower() != "poc boundary"
                        ]

    items_by_id = {item.id: item for item in items}
    draw_dict = await asyncio.to_thread(spec_to_draw_dict, spec, items_by_id)

    diagram_name = "diagram"
    context_name = (
        req.engagement_context.get("diagram_name")
        if isinstance(req.engagement_context, dict)
        else None
    )
    if context_name:
        diagram_name = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(context_name)).strip("._")
            or "diagram"
        )

    with tempfile.TemporaryDirectory(prefix="diagram-sub-agent-") as tmpdir:
        drawio_path = Path(tmpdir) / f"{diagram_name}.drawio"
        try:
            await asyncio.to_thread(generate_drawio, draw_dict, drawio_path)
        except DrawioValidationError as exc:
            return A2AResponse(
                result=str(exc),
                status="blocked",
                trace={
                    "agent": card.name,
                    "trace_id": req.trace_id,
                    "error_type": exc.__class__.__name__,
                    "stage": "drawio_validation",
                },
            )
        drawio_xml = await asyncio.to_thread(drawio_path.read_text, encoding="utf-8")

    return A2AResponse(
        result=drawio_xml,
        status="ok",
        trace={
            "agent": card.name,
            "trace_id": req.trace_id,
            "generation_mode": generation_mode,
            "node_count": len(draw_dict.get("nodes", [])),
            "edge_count": len(draw_dict.get("edges", [])),
        },
    )


app = make_agent_app(card, handle)
