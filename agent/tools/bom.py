"""
agent/tools/bom.py
------------------
ToolHandler implementation for the generate_bom pipeline.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
from typing import Any

from agent import archie_memory, consistency_contract, sub_agent_client
from agent.bom_service import BomService, CacheSnapshot, DEFAULT_PRICE_TABLE
from agent.persistence_objectstore import ObjectStoreBase
from skillforge.types import MemorySnapshot, ToolResult


logger = logging.getLogger(__name__)

CONFIRMED_CONTEXT_FIELDS = (
    "instance_count",
    "shapes",
    "storage_gb",
    "vcpu_count",
    "memory_gb",
    "customer_name",
    "oci_services_in_scope",
    "workload_type",
    "ha_dr_mode",
    "region",
    "database_type",
    "node_count",
)


class BomHandler:
    def __init__(
        self,
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
        text_runner: Any,
        a2a_base_url: str = "",
    ) -> None:
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name
        self._text_runner = text_runner
        self._a2a_base_url = a2a_base_url

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        ctx = context
        decision_context = memory.decision_context if memory else {}
        # Forge's ToolHandler signature does not include the user turn text.
        # archie_wiring.py will inject it into tool args before this handler runs.
        user_message = str(args.get("_user_message", "") or "")
        explicit_request = user_message or str(args.get("prompt") or "")
        conflicts = consistency_contract.request_conflicts(explicit_request, ctx)
        if conflicts:
            clarification = (
                "The BOM request conflicts with an approved upstream decision. "
                "Confirm an impact update before changing dependent artifacts."
            )
            return ToolResult(
                summary=clarification,
                status="needs_input",
                clarification=clarification,
                data={
                    "consistency_conflicts": conflicts,
                    "required_next_step": "confirm update all",
                },
            )
        if user_message:
            args = {**args, "prompt": user_message}

        args = archie_memory._prepare_bom_tool_args(
            args=args,
            user_message=user_message,
            context=ctx,
            decision_context=decision_context,
        )
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name="generate_bom",
            args=args,
            context=ctx,
            decision_context=decision_context,
            user_message=user_message,
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name="generate_bom",
            args=args,
            context=ctx,
        )

        if args.get("_bom_direct_reply"):
            message = str(args["_bom_direct_reply"])
            return ToolResult(
                summary=message,
                status="needs_input",
                clarification=message,
            )

        # Preserve the exact direct request through Forge review retries.  The
        # deterministic BOM parser and sizing guard must evaluate customer
        # values, not reviewer-invented defaults.
        prompt = user_message or str(args.get("prompt") or "")

        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            prompt = (
                f"[AUTHORITATIVE USER REQUEST]\n{prompt}\n"
                "[/AUTHORITATIVE USER REQUEST]\n\n"
                "[SCOPED REVIEW FEEDBACK]\n"
                f"{correction}\n"
                "Apply this feedback only where it corrects a requirement "
                "explicitly present in the authoritative user request. Do not "
                "add services, sizes, quantities, storage, bandwidth, or output "
                "requirements.\n"
                "[/SCOPED REVIEW FEEDBACK]"
            ).strip()
        args = {**args, "prompt": prompt}

        engagement_context = memory.raw if memory else {}
        prompt = _hydrate_bom_task(
            str(args.get("prompt") or ""),
            args=args,
            context=ctx,
            memory_raw=engagement_context,
        )
        structured_inputs = _extract_explicit_bom_inputs(explicit_request)
        structured_inputs = _apply_selected_poc_defaults(structured_inputs, ctx)
        direct_sized_request = bool(
            user_message
            and re.search(r"\b\d+(?:\.\d+)?\s*ocpus?\b", user_message, re.IGNORECASE)
            and re.search(r"\b\d+(?:\.\d+)?\s*(?:gb|tb)\b", user_message, re.IGNORECASE)
        )
        if direct_sized_request:
            # Preserve the selected POC contract even for a fully sized direct
            # request.  The current turn controls quantities, while the
            # engagement contract controls required service identity/scope.
            prompt = user_message
        if structured_inputs.get("strict_scope"):
            engagement_context = {"structured_inputs": structured_inputs}
        parsed: dict[str, Any] = {}
        bom_payload: dict[str, Any] = {}
        validation_error = ""
        for attempt in range(2):
            try:
                body = await sub_agent_client.call_sub_agent(
                    "bom",
                    prompt,
                    engagement_context=engagement_context,
                    trace_id=trace_id,
                )
            except sub_agent_client.SubAgentError as exc:
                return _fallback_bom_result(
                    prompt=prompt,
                    args=args,
                    trace_id=trace_id,
                    reason=f"BOM sub-agent failed: {exc}",
                    explicit_structured_inputs=structured_inputs,
                )

            if body.get("status") == "needs_input":
                clarification = str(body.get("result") or "")
                return ToolResult(
                    summary=clarification or "BOM needs more input.",
                    status="needs_input",
                    clarification=clarification,
                )

            try:
                raw_result = body.get("result") or "{}"
                parsed = (
                    json.loads(raw_result)
                    if isinstance(raw_result, str)
                    else dict(raw_result)
                    if isinstance(raw_result, dict)
                    else {}
                )
            except (TypeError, json.JSONDecodeError) as exc:
                return _fallback_bom_result(
                    prompt=prompt,
                    args=args,
                    trace_id=trace_id,
                    reason=f"BOM sub-agent returned invalid JSON: {exc}",
                    explicit_structured_inputs=structured_inputs,
                )
            bom_payload = _flag_unverified_skus(_extract_bom_payload(parsed))
            validation_error = _validate_bom_result(parsed, bom_payload)
            if not validation_error:
                break
            if "monthly_total arithmetic error" not in validation_error or attempt == 1:
                return _fallback_bom_result(
                    prompt=prompt,
                    args=args,
                    trace_id=trace_id,
                    reason=f"BOM validation failed: {validation_error}",
                    incomplete_status="blocked",
                    explicit_structured_inputs=structured_inputs,
                )
            prompt = (
                "[CORRECTION FROM PYTHON VALIDATION]\n"
                f"{validation_error}. Recalculate monthly_total: hourly SKUs use quantity * unit_price * 730, "
                "monthly SKUs use quantity * unit_price.\n\n"
                f"{prompt}"
            )
        if validation_error:
            return _fallback_bom_result(
                prompt=prompt,
                args=args,
                trace_id=trace_id,
                reason=f"BOM validation failed: {validation_error}",
                incomplete_status="blocked",
                explicit_structured_inputs=structured_inputs,
            )
        if not consistency_contract.required_components(ctx):
            bom_payload = _enrich_bom_payload_for_prompt(
                bom_payload,
                prompt="\n".join(
                    part
                    for part in (
                        prompt,
                        user_message,
                    )
                    if part
                ),
            )
        bom_payload = _flag_unverified_skus(bom_payload)
        bom_payload = consistency_contract.ensure_bom_scope(bom_payload, ctx)
        consistency_report = consistency_contract.validate_bom(bom_payload, ctx)
        if consistency_report["verdict"] != "pass":
            return ToolResult(
                summary="BOM cross-artifact consistency validation failed.",
                status="blocked",
                data={
                    "bom_payload": bom_payload,
                    "structured_inputs": structured_inputs,
                    "consistency_report": consistency_report,
                    "validation_stage": "cross_artifact_consistency",
                },
            )
        sizing_repaired = False
        sizing_error = _validate_explicit_bom_inputs(bom_payload, structured_inputs)
        if sizing_error:
            bom_payload = _repair_explicit_bom_inputs(bom_payload, structured_inputs)
            sizing_repaired = True
            sizing_error = _validate_explicit_bom_inputs(bom_payload, structured_inputs)
        if sizing_error:
            return ToolResult(
                summary=f"BOM explicit sizing mismatch: {sizing_error}",
                status="blocked",
                data={
                    "validation_error": sizing_error,
                    "structured_inputs": structured_inputs,
                    "bom_payload": bom_payload,
                    "trace_id": trace_id,
                },
            )
        prices_from = str(parsed.get("prices_from") or bom_payload.get("prices_from") or "fallback_cache")
        bom_payload["prices_from"] = prices_from
        line_items = bom_payload.get("line_items") or []
        service_count = len(line_items)
        service_names = ", ".join(
            str(item.get("description") or item.get("sku") or "")[:30]
            for item in line_items[:6]
        )
        if len(line_items) > 6:
            service_names += f", +{len(line_items) - 6} more"
        monthly = bom_payload.get("monthly_total") or 0
        pricing_status = str(bom_payload.get("pricing_status") or "priced")
        bom_summary = (
            f"BOM generated ({service_count} services, ${monthly:,.2f}/mo priced subtotal, {pricing_status}): "
            f"{service_names}."
            if service_names else
            f"BOM generated ({service_count} services, ${monthly:,.2f}/mo)."
        )

        return ToolResult(
            summary=bom_summary,
            status="ok",
            data={
                "bom_payload": bom_payload,
                "prices_from": prices_from,
                "bom_context_source": str(
                    args.get("_bom_context_source") or "direct_request"
                ),
                "structured_inputs": structured_inputs,
                "sizing_repair": (
                    "deterministic_explicit_fields" if sizing_repaired else "not_required"
                ),
                "consistency_report": consistency_report,
                "consistency_contract_revision": int(
                    consistency_contract.get_contract(ctx).get("revision", 0) or 0
                ),
            },
            artifact_key=str(parsed.get("artifact_key") or bom_payload.get("artifact_key") or bom_payload.get("xlsx_key") or ""),
        )


def _extract_bom_payload(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    payload = parsed.get("bom_payload")
    if isinstance(payload, dict):
        return payload
    return parsed


def _extract_explicit_bom_inputs(text: str) -> dict[str, Any]:
    source = str(text or "")
    lower = source.lower()
    instance_count = BomService._extract_server_count(lower)
    ocpu_match = re.search(r"\b(\d+(?:\.\d+)?)\s*ocpus?\b", lower)
    memory_match = re.search(r"\b(\d+(?:\.\d+)?)\s*gb\s*(?:ram|memory)\b", lower)
    region_match = re.search(r"\b[a-z]{2,}-[a-z]+-\d+\b", source, re.IGNORECASE)
    block_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(gb|tb)\s*(?:balanced\s+)?block\s+(?:volume|storage)\b", lower)
    object_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(gb|tb)\s*(?:standard\s+)?object\s+storage\b", lower)

    def capacity(match: re.Match[str] | None) -> float | None:
        if not match:
            return None
        value = float(match.group(1))
        return value * 1024.0 if match.group(2).lower() == "tb" else value

    per_ocpu = float(ocpu_match.group(1)) if ocpu_match else None
    per_memory = float(memory_match.group(1)) if memory_match else None
    block_gb = capacity(block_match)
    object_gb = capacity(object_match)
    return {
        "region": region_match.group(0) if region_match else "",
        "compute": {
            "instance_count": instance_count,
            "ocpu_per_instance": per_ocpu,
            "ocpu": per_ocpu * instance_count if per_ocpu else None,
        },
        "memory": {
            "gb_per_instance": per_memory,
            "gb": per_memory * instance_count if per_memory else None,
        },
        "storage": {
            "block_gb": block_gb,
            "block_tb": (block_gb / 1024.0) if block_gb is not None else None,
            "object_gb": object_gb,
            "object_tb": (object_gb / 1024.0) if object_gb is not None else None,
        },
    }


def _apply_selected_poc_defaults(
    structured: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build the exact structured POC handoff from the selected contract."""
    components = consistency_contract.required_components(context)
    service_ids = sorted(
        str(item.get("service_id") or "") for item in components if item.get("service_id")
    )
    if not service_ids:
        return structured

    output = copy.deepcopy(structured)
    contract = consistency_contract.get_contract(context)
    output["region"] = str(output.get("region") or contract.get("region") or "").strip()
    output["strict_scope"] = True
    output["scope"] = "poc"
    output["architecture_option"] = "selected POC consistency contract"
    output["target_service_ids"] = service_ids
    output["target_services"] = [
        consistency_contract.display_name(service_id) for service_id in service_ids
    ]
    output["output_format"] = "xlsx"

    compute = output.setdefault("compute", {})
    memory = output.setdefault("memory", {})
    storage = output.setdefault("storage", {})
    if any(service_id.startswith("compute.") for service_id in service_ids):
        instance_count = int(compute.get("instance_count") or 1)
        compute["instance_count"] = instance_count
        compute["ocpu"] = float(compute.get("ocpu") or 4.0)
        compute["ocpu_per_instance"] = float(
            compute.get("ocpu_per_instance") or compute["ocpu"] / instance_count
        )
        memory["gb"] = float(memory.get("gb") or 32.0)
        memory["gb_per_instance"] = float(
            memory.get("gb_per_instance") or memory["gb"] / instance_count
        )
        compute["shape"] = (
            "VM.Standard.E5.Flex"
            if "compute.vm.standard.e5.flex" in service_ids
            else "VM.Standard.E6.Flex"
        )
    if "storage.block" in service_ids and not storage.get("block_gb"):
        storage["block_gb"] = 500.0
        storage["block_tb"] = 500.0 / 1024.0
    if "storage.object" in service_ids and not storage.get("object_gb"):
        storage["object_gb"] = 1024.0
        storage["object_tb"] = 1.0
    output["assumptions"] = [
        "Single-AD minimum viable POC sizing.",
        "One compute VM at 4 OCPU and 32 GB when compute is selected and sizing is absent.",
        "500 GB Balanced Block Volume and 1 TB Object Storage only when selected and sizing is absent.",
    ]
    return output


def _validate_explicit_bom_inputs(payload: dict[str, Any], structured: dict[str, Any]) -> str:
    if not structured:
        return ""
    rows = [row for row in payload.get("line_items", []) or [] if isinstance(row, dict)]

    def total_for(skus: set[str]) -> float:
        return sum(float(row.get("quantity") or 0) for row in rows if str(row.get("sku") or "").upper() in skus)

    expected = {
        "ocpu": ((structured.get("compute") or {}).get("ocpu")),
        "memory_gb": ((structured.get("memory") or {}).get("gb")),
        "block_gb": ((structured.get("storage") or {}).get("block_gb")),
        "object_gb": ((structured.get("storage") or {}).get("object_gb")),
    }
    produced = {
        "ocpu": total_for({"B93113", "B97384", "B111129", "B94176", "B93297"}),
        "memory_gb": total_for({"B93114", "B97385", "B111130", "B94177", "B93298"}),
        "block_gb": total_for({"B91961"}),
        "object_gb": total_for({"B91628"}),
    }
    for key, required in expected.items():
        if required is None:
            continue
        if abs(float(produced[key]) - float(required)) > 0.0001:
            return f"{key} requested={float(required):g}, produced={float(produced[key]):g}"
    requested_region = str(structured.get("region") or "")
    produced_region = str(payload.get("region") or "")
    if requested_region and produced_region.casefold() != requested_region.casefold():
        return f"region requested={requested_region}, produced={produced_region or 'missing'}"
    expected_count = int(((structured.get("compute") or {}).get("instance_count")) or 1)
    compute_rows = [row for row in rows if str(row.get("sku") or "").upper() in {"B93113", "B97384", "B111129", "B94176", "B93297"}]
    if compute_rows and int(compute_rows[0].get("instance_count") or 1) != expected_count:
        return f"instance_count requested={expected_count}, produced={int(compute_rows[0].get('instance_count') or 1)}"
    return ""


def _repair_explicit_bom_inputs(
    payload: dict[str, Any],
    structured: dict[str, Any],
) -> dict[str, Any]:
    """Repair only quantities and region explicitly supplied by the customer."""
    repaired = copy.deepcopy(payload)
    compute = structured.get("compute") if isinstance(structured.get("compute"), dict) else {}
    memory = structured.get("memory") if isinstance(structured.get("memory"), dict) else {}
    storage = structured.get("storage") if isinstance(structured.get("storage"), dict) else {}
    required_by_sku_group = (
        ({"B93113", "B97384", "B111129", "B94176", "B93297"}, compute.get("ocpu")),
        ({"B93114", "B97385", "B111130", "B94177", "B93298"}, memory.get("gb")),
        ({"B91961"}, storage.get("block_gb")),
        ({"B91628"}, storage.get("object_gb")),
    )
    instance_count = int(compute.get("instance_count") or 1)
    for sku_group, required in required_by_sku_group:
        if required is None:
            continue
        matching = [
            row
            for row in repaired.get("line_items", []) or []
            if isinstance(row, dict)
            and str(row.get("sku") or "").upper() in sku_group
        ]
        if not matching:
            continue
        matching[0]["quantity"] = float(required)
        for duplicate in matching[1:]:
            repaired["line_items"].remove(duplicate)
        if sku_group & {
            "B93113", "B97384", "B111129", "B94176", "B93297",
            "B93114", "B97385", "B111130", "B94177", "B93298",
        }:
            matching[0]["instance_count"] = instance_count
    region = str(structured.get("region") or "").strip()
    if region:
        repaired["region"] = region
    return BomService()._normalize_payload(repaired)


def _hydrate_bom_task(
    task: str,
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> str:
    block = _build_bom_confirmed_context(args=args, context=context, memory_raw=memory_raw)
    contract = consistency_contract.get_contract(context)
    contract_block = ""
    if contract:
        contract_block = (
            "[AUTHORITATIVE ENGAGEMENT CONSISTENCY CONTRACT]\n"
            "Preserve every required service identity. Do not substitute database or connectivity products.\n"
            f"{json.dumps(contract, ensure_ascii=True, sort_keys=True)}\n"
            "[/AUTHORITATIVE ENGAGEMENT CONSISTENCY CONTRACT]"
        )
    previous = _find_context_value(
        ("previous_bom_payload", "bom_payload", "bom"),
        args=args,
        context=context,
        memory_raw=memory_raw,
    )
    parts = [block, contract_block]
    if previous not in (None, "", [], {}):
        parts.append(
            "[PREVIOUS BOM - PRESERVE UNLESS CHANGING: "
            f"{json.dumps(previous, sort_keys=True, default=str)}]"
        )
    parts.append(str(task or ""))
    return "\n\n".join(part for part in parts if str(part).strip()).strip()


def _build_bom_confirmed_context(
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> str:
    value = lambda *keys, default="not stated": _context_value_or_default(  # noqa: E731
        keys,
        default=default,
        args=args,
        context=context,
        memory_raw=memory_raw,
    )
    return "\n".join(
        [
            "[CONFIRMED CONTEXT]",
            f"Shape: {value('compute_shape', 'shape', 'shapes')}",
            f"OCPU: {value('ocpu_count', 'ocpus', 'cpu_count')}",
            f"Memory: {value('memory_gb')}",
            f"Region: {value('region')}",
            f"HA mode: {value('ha_mode', 'ha_dr_mode')}",
            f"Budget: {value('budget')}",
            f"Storage: {value('storage_requirements', 'storage_gb')}",
            f"Workloads: {value('workloads', 'workload_type')}",
            f"License: {value('license_type')}",
            "[/CONFIRMED CONTEXT]",
        ]
    )


def _flag_unverified_skus(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    payload = copy.deepcopy(payload)
    changed = False
    for item in payload.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        sku = str(item.get("sku") or "")
        if sku and not re.match(r"^B\d{4,6}$", sku):
            changed = True
            item["sku_unverified"] = True
            item["unit_price"] = None
            item["extended_price"] = None
            item["pricing_status"] = "unpriced"
            item["price_source"] = "unpriced_catalog"
    return BomService()._normalize_payload(payload) if changed else payload


def _validate_bom_result(result: dict[str, Any], bom_payload: dict[str, Any]) -> str:
    if "bom_payload" not in result and not bom_payload:
        return "missing bom_payload"
    line_items = bom_payload.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        return "bom_payload.line_items is empty"
    for item in line_items:
        if not isinstance(item, dict):
            return f"line item is not an object: {item!r}"
        sku = str(item.get("sku") or "")
        if not sku:
            return f"line item missing sku: {item}"
        qty = float(item.get("quantity") or item.get("qty") or 0)
        raw_price = item.get("unit_price") if "unit_price" in item else item.get("price")
        price = float(raw_price) if raw_price not in (None, "") else None
        if qty <= 0:
            return f"quantity <= 0 for {item.get('sku')}"
        if price is None and str(item.get("pricing_status") or "") == "unpriced":
            continue
        if price is None or price <= 0:
            return f"unit_price <= 0 for {item.get('sku')}"
    def _monthly_multiplier(item: dict[str, Any]) -> float:
        billing = str(item.get("billing_unit") or item.get("metric") or "hour").lower()
        return 730.0 if "hour" in billing else 1.0

    computed = sum(
        float(i.get("quantity") or i.get("qty") or 0)
        * float(i.get("unit_price") or i.get("price") or 0)
        * _monthly_multiplier(i)
        for i in line_items
        if (i.get("unit_price") if "unit_price" in i else i.get("price")) not in (None, "")
    )
    stated = float(bom_payload.get("monthly_total") or 0)
    if abs(computed - stated) / max(computed, 1) >= 0.005:
        return f"monthly_total arithmetic error: computed={computed:.2f}, stated={stated:.2f}"
    return ""


def _context_value_or_default(
    keys: tuple[str, ...],
    *,
    default: str,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> Any:
    value = _find_context_value(keys, args=args, context=context, memory_raw=memory_raw)
    return default if not _has_context_value(value) else value


def _find_context_value(
    keys: tuple[str, ...],
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> Any:
    for source in _context_sources(args=args, context=context, memory_raw=memory_raw):
        for key in keys:
            if key in source and _has_context_value(source.get(key)):
                return source[key]
    return None


def _context_sources(
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source in (memory_raw, context, args):
        if isinstance(source, dict):
            sources.append(source)
            for key in ("facts", "constraints", "decision_context", "requirements", "agents", "bom", "sizing"):
                nested = source.get(key)
                if isinstance(nested, dict):
                    sources.append(nested)
    return sources


def _has_context_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return False
    return True


def _validation_blocked(tool: str, error: str) -> ToolResult:
    return ToolResult(
        summary=f"{tool.upper()} validation failed: {error}",
        status="blocked",
        data={"validation_error": error, "validation_stage": "python_contract"},
    )


def _fallback_bom_result(
    *,
    prompt: str,
    args: dict[str, Any],
    trace_id: str,
    reason: str,
    incomplete_status: str = "needs_input",
    explicit_structured_inputs: dict[str, Any] | None = None,
) -> ToolResult:
    service = BomService()
    service._cache = CacheSnapshot(  # local deterministic fallback; avoids network during recovery
        pricing_table=service._unpriced_default_catalog(),
        shapes_text="OCI compute shapes catalog unavailable; using fallback guidance.",
        services_text="OCI services catalog unavailable; using fallback guidance.",
        refreshed_at=time.time(),
        source="fallback_local",
    )
    try:
        fallback_inputs = _structured_bom_inputs_from_args(args)
        if fallback_inputs:
            response = service.generate_from_inputs(
                inputs=fallback_inputs,
                trace_id=trace_id,
                model_id="local-fallback",
            )
        else:
            response = service.chat(
                message=prompt,
                conversation=[],
                trace_id=trace_id,
                model_id="local-fallback",
            )
    except Exception as exc:
        return ToolResult(
            summary=(
                "I could not complete the BOM because the specialist failed and "
                f"the local fallback also failed: {exc}"
            ),
            status="blocked",
            data={"fallback_reason": reason, "fallback_error": str(exc)},
        )

    data = dict(response or {})
    payload = data.get("bom_payload") if isinstance(data.get("bom_payload"), dict) else {}
    if payload:
        payload = _flag_unverified_skus(payload)
        payload["prices_from"] = "fallback_local"
        data["bom_payload"] = payload
    trace = data.get("trace", {}) if isinstance(data.get("trace"), dict) else {}
    trace.update(
        {
            "fallback_used": True,
            "fallback_reason": reason,
            "prices_from": "fallback_local",
        }
    )
    data["trace"] = trace
    data["fallback_reason"] = reason
    data["prices_from"] = "fallback_local"
    if explicit_structured_inputs:
        data["structured_inputs"] = copy.deepcopy(explicit_structured_inputs)

    if str(data.get("type", "") or "").lower() == "final" and payload:
        return ToolResult(
            summary=(
                "Final BOM prepared using the local pricing fallback. "
                "Review line items, then export XLSX."
            ),
            status="ok",
            data=data,
            artifact_key="",
        )

    reply = str(data.get("reply") or "").strip()
    if incomplete_status == "blocked":
        if "validation failed:" in reason.lower():
            data["validation_error"] = reason.split(":", 1)[-1].strip()
            data["validation_stage"] = "python_contract"
        return ToolResult(
            summary=reason,
            status="blocked",
            data=data,
        )
    clarification = (
        "I couldn't reach the BOM specialist, and the local fallback needs a bit more sizing detail."
    )
    if reply and "BOM data is not ready" not in reply:
        clarification = f"{clarification} {reply}"
    return ToolResult(
        summary=clarification,
        status="needs_input",
        clarification=clarification,
        data=data,
    )


def _structured_bom_inputs_from_args(args: dict[str, Any]) -> dict[str, Any]:
    for key in ("inputs", "structured_inputs"):
        value = args.get(key)
        if isinstance(value, dict) and value:
            return dict(value)
    if any(isinstance(args.get(key), dict) for key in ("compute", "memory", "storage")):
        return {
            key: copy.deepcopy(args.get(key))
            for key in (
                "region",
                "architecture_option",
                "compute",
                "memory",
                "storage",
                "connectivity",
                "dr",
                "workloads",
                "os_mix",
                "target_services",
                "workload_service_mapping",
                "output_format",
            )
            if key in args
        }
    return {}


def _enrich_bom_payload_for_prompt(payload: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    text = str(prompt or "").lower()
    line_items = payload.get("line_items")
    if not isinstance(line_items, list):
        return payload
    if not line_items:
        return payload
    present = {
        str(row.get("sku") or "").strip().upper()
        for row in line_items
        if isinstance(row, dict)
    }
    service = BomService()
    price_table = dict(DEFAULT_PRICE_TABLE)

    def _append_missing(sku: str, quantity: float, category: str, notes: str) -> None:
        if sku in present:
            return
        line_items.append(service._build_line(sku, quantity, price_table, category, notes))
        present.add(sku)

    if "waf" in text or "web application firewall" in text:
        _append_missing("BWAF01", 1.0, "network", "Prompt requested WAF coverage")
    if (
        ("database" in text or "data tier" in text or re.search(r"\bdb\b", text))
        and "postgres" not in text
        and "mysql" not in text
    ):
        _append_missing("B99060", 2.0, "database", "Prompt requested database layer")

    payload["line_items"] = line_items
    return service._normalize_payload(payload)
