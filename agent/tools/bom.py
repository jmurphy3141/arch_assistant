"""
agent/tools/bom.py
------------------
ToolHandler implementation for the generate_bom pipeline.
"""
from __future__ import annotations

import json
from typing import Any

from agent import archie_memory, sub_agent_client
from agent.persistence_objectstore import ObjectStoreBase
from skillforge.types import MemorySnapshot, ToolResult


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

        try:
            body = await sub_agent_client.call_sub_agent(
                "bom",
                str(args.get("prompt") or ""),
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
        return ToolResult(
            summary="BOM generated with structured payload.",
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
