"""
agent/tools/terraform.py
------------------------
ToolHandler implementation for the generate_terraform pipeline.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agent import archie_memory, document_store, sub_agent_client
from agent.persistence_objectstore import ObjectStoreBase
from agent.sub_agent_client import SubAgentError
from skillforge.types import MemorySnapshot, ToolResult


class TerraformHandler:
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
        user_message = str(args.get("_user_message", "") or "")
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name="generate_terraform",
            args=args,
            context=ctx,
            decision_context=decision_context,
            user_message=user_message,
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name="generate_terraform",
            args=args,
            context=ctx,
        )

        if (
            ctx
            and archie_memory._has_architecture_definition(ctx)
            and not archie_memory._terraform_scope_is_bounded(
                context=ctx,
                args=args,
                decision_context=decision_context,
                user_message=user_message,
            )
        ):
            return ToolResult(
                summary="Terraform scope clarification required.",
                status="needs_input",
                clarification=(
                    "Please clarify which modules or resources to include in the "
                    "Terraform bundle."
                ),
            )

        raw_prompt = str(args.get("prompt", "") or "")
        task = raw_prompt or str(
            args.get("_user_request_text", "")
            or "Generate Terraform for the current architecture."
        )

        try:
            response = await sub_agent_client.call_sub_agent(
                "terraform",
                task=task,
                engagement_context={
                    "customer_id": self._customer_id,
                    "customer_name": self._customer_name,
                    "architect_brief": dict(args.get("_architect_brief", {}) or {}),
                },
                trace_id=trace_id,
            )
        except SubAgentError as exc:
            return ToolResult(
                summary=f"Terraform sub-agent error: {exc}",
                status="blocked",
            )

        if str(response.get("status") or "").lower() == "needs_input":
            clarification = str(response.get("result") or "")
            return ToolResult(
                summary=clarification or "Terraform needs more input.",
                status="needs_input",
                clarification=clarification,
            )

        from agent.archie_loop import _parse_terraform_sub_agent_result

        files = _parse_terraform_sub_agent_result(response.get("result"))
        saved = await asyncio.to_thread(
            document_store.save_terraform_bundle,
            self._store,
            self._customer_id,
            files,
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        key = str((saved.get("files") or {}).get("main.tf") or saved.get("latest_key") or "")
        return ToolResult(
            summary=f"Terraform bundle v{saved.get('version')} saved.",
            status="ok",
            artifact_key=key,
            data={
                "terraform_files": files,
                "terraform_bundle": saved,
            },
        )
