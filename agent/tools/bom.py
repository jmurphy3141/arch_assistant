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
from typing import Any

from agent import archie_memory, sub_agent_client
from agent.bom_service import BomService, DEFAULT_PRICE_TABLE
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

        prompt = str(args.get("prompt") or "")

        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            prompt = (
                f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n"
                f"{prompt}"
            ).strip()
        args = {**args, "prompt": prompt}

        engagement_context = memory.raw if memory else {}
        prompt = _hydrate_bom_task(
            str(args.get("prompt") or ""),
            args=args,
            context=ctx,
            memory_raw=engagement_context,
        )
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
                return ToolResult(
                    summary=f"BOM sub-agent failed: {exc}",
                    status="blocked",
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
                return ToolResult(
                    summary=f"BOM sub-agent returned invalid JSON: {exc}",
                    status="blocked",
                )
            bom_payload = _flag_unverified_skus(_extract_bom_payload(parsed))
            validation_error = _validate_bom_result(parsed, bom_payload)
            if not validation_error:
                break
            if "monthly_total arithmetic error" not in validation_error or attempt == 1:
                return _validation_blocked("bom", validation_error)
            prompt = (
                "[CORRECTION FROM PYTHON VALIDATION]\n"
                f"{validation_error}. Recalculate monthly_total: hourly SKUs use quantity * unit_price * 730, "
                "monthly SKUs use quantity * unit_price.\n\n"
                f"{prompt}"
            )
        if validation_error:
            return _validation_blocked("bom", validation_error)
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
        bom_summary = (
            f"BOM generated ({service_count} services, ${monthly:,.2f}/mo): "
            f"{service_names}."
            if service_names else
            f"BOM generated ({service_count} services, ${monthly:,.2f}/mo)."
        )

        from agent import context_store as _cs
        assumptions_text = " ".join(str(a) for a in (bom_payload.get("assumptions") or []))
        first_compute = next(
            (item for item in line_items if "ocpu" in str(item.get("description", "")).lower()),
            {},
        )
        _cs.set_resolved_decisions(ctx, sizing={
            "shape_family": str(args.get("compute_shape") or first_compute.get("sku") or "E5.Flex"),
            "ha_multiplier_applied": "active-active" in str(args.get("ha_dr_mode", "")).lower(),
            "byol_confirmed": "byol" in assumptions_text.lower(),
            "monthly_total": monthly,
            "region": str(bom_payload.get("region") or "us-chicago-1"),
        })

        return ToolResult(
            summary=bom_summary,
            status="ok",
            data={
                "bom_payload": bom_payload,
                "prices_from": prices_from,
                "bom_context_source": str(
                    args.get("_bom_context_source") or "direct_request"
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


def _hydrate_bom_task(
    task: str,
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> str:
    block = _build_bom_confirmed_context(args=args, context=context, memory_raw=memory_raw)
    previous = _find_context_value(
        ("previous_bom_payload", "bom_payload", "bom"),
        args=args,
        context=context,
        memory_raw=memory_raw,
    )
    parts = [block]
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
            f"Region: {value('region', default='us-chicago-1')}",
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
    for item in payload.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        sku = str(item.get("sku") or "")
        if sku and not re.match(r"^B\d{4,6}$", sku):
            item["sku_unverified"] = True
    return payload


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
        price = float(item.get("unit_price") or item.get("price") or 0)
        if qty <= 0:
            return f"quantity <= 0 for {item.get('sku')}"
        if price <= 0:
            return f"unit_price <= 0 for {item.get('sku')}"
    if not (result.get("artifact_key") or bom_payload.get("artifact_key") or bom_payload.get("xlsx_key")):
        return "missing artifact_key"
    def _monthly_multiplier(item: dict[str, Any]) -> float:
        billing = str(item.get("billing_unit") or item.get("metric") or "hour").lower()
        return 730.0 if "hour" in billing else 1.0

    computed = sum(
        float(i.get("quantity") or i.get("qty") or 0)
        * float(i.get("unit_price") or i.get("price") or 0)
        * _monthly_multiplier(i)
        for i in line_items
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
    if "database" in text or "data tier" in text or re.search(r"\bdb\b", text):
        _append_missing("B99060", 2.0, "database", "Prompt requested database layer")

    payload["line_items"] = line_items
    return service._normalize_payload(payload)
