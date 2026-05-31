"""
agent/tools/bom.py
------------------
ToolHandler implementation for the generate_bom pipeline.
"""
from __future__ import annotations

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

        confirmed_context_block = _build_confirmed_context_block(
            memory.raw if memory else {},
            context=ctx,
        )
        prompt = str(args.get("prompt") or "")

        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            prompt = (
                f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n"
                f"{prompt}"
            ).strip()
        if confirmed_context_block:
            prompt = f"{confirmed_context_block}{prompt}".strip()
        args = {**args, "prompt": prompt}

        try:
            body = await sub_agent_client.call_sub_agent(
                "bom",
                prompt,
                engagement_context=memory.raw if memory else {},
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
            parsed = json.loads(body.get("result") or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult(
                summary=f"BOM sub-agent returned invalid JSON: {exc}",
                status="blocked",
            )
        bom_payload = _extract_bom_payload(parsed)
        arithmetic_error = _verify_bom_arithmetic(bom_payload)
        if arithmetic_error:
            logger.warning(arithmetic_error)
            return ToolResult(
                summary=arithmetic_error,
                status="blocked",
                data={"arithmetic_error": arithmetic_error},
            )
        bom_payload = _enrich_bom_payload_for_prompt(
            bom_payload,
            prompt="\n".join(
                part
                for part in (
                    str(args.get("prompt") or ""),
                    user_message,
                )
                if part
            ),
        )
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
                "bom_context_source": str(
                    args.get("_bom_context_source") or "direct_request"
                ),
            },
            artifact_key=str(bom_payload.get("xlsx_key") or ""),
        )


def _extract_bom_payload(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    payload = parsed.get("bom_payload")
    if isinstance(payload, dict):
        return payload
    return parsed


def _build_confirmed_context_block(
    memory_raw: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> str:
    values: dict[str, Any] = {}
    for source in _confirmed_context_sources(memory_raw, context):
        for field in CONFIRMED_CONTEXT_FIELDS:
            if field not in values and _has_confirmed_value(source.get(field)):
                values[field] = source[field]
    if not values:
        return ""
    lines = ["[CONFIRMED CONTEXT]"]
    lines.extend(
        f"  {field}: {values[field]}"
        for field in CONFIRMED_CONTEXT_FIELDS
        if field in values
    )
    lines.append("[END CONFIRMED CONTEXT]")
    return "\n".join(lines) + "\n\n"


def _confirmed_context_sources(
    memory_raw: dict[str, Any],
    context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if isinstance(memory_raw, dict):
        sources.append(memory_raw)
    if isinstance(context, dict):
        sources.append(context)
        for key in ("facts", "constraints", "decision_context", "requirements"):
            nested = context.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
    return sources


def _has_confirmed_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (str, bytes)) and not str(value).strip():
        return False
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return False
    return True


def _verify_bom_arithmetic(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None
    line_items = payload.get("line_items") or []
    if not line_items:
        return None
    try:
        stated_total = float(payload.get("monthly_total") or 0)
    except (TypeError, ValueError):
        return None
    if stated_total == 0:
        return None

    computed = 0.0
    for item in line_items:
        if not isinstance(item, dict):
            continue
        try:
            qty = float(item.get("qty") or item.get("quantity") or 0)
            price = float(item.get("unit_price") or item.get("price") or 0)
        except (TypeError, ValueError):
            continue
        billing = str(
            item.get("billing_unit") or item.get("metric") or "monthly"
        ).lower()
        multiplier = 730 if "hour" in billing else 1
        computed += qty * price * multiplier

    if computed == 0:
        return None
    pct_diff = abs(computed - stated_total) / max(computed, 0.01)
    if pct_diff <= 0.005:
        return None
    return (
        f"BOM arithmetic mismatch: line items sum to ${computed:,.2f}/mo "
        f"but monthly_total is ${stated_total:,.2f}/mo "
        f"({pct_diff * 100:.1f}% off). Recompute monthly_total."
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
