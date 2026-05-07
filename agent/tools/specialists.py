"""
agent/tools/specialists.py
--------------------------
ToolHandler implementations for POV, JEP, and WAF specialist sub-agents.
"""
from __future__ import annotations

import asyncio
from typing import Any

from agent import archie_memory, document_store, sub_agent_client
from agent.persistence_objectstore import ObjectStoreBase
from agent.sub_agent_client import SubAgentError
from skillforge.types import MemorySnapshot, ToolResult


class _SpecialistHandler:
    """Base pattern for sub-agent specialist tools (pov, jep, waf)."""

    def __init__(
        self,
        agent_name: str,
        doc_type: str,
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
    ) -> None:
        self._agent_name = agent_name
        self._doc_type = doc_type
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        if self._agent_name == "jep":
            import agent.jep_lifecycle as jep_lifecycle

            policy_block = await asyncio.to_thread(
                jep_lifecycle.generate_policy_block_payload,
                self._store,
                self._customer_id,
            )
            if policy_block is not None:
                return ToolResult(
                    summary=(
                        "JEP generation is locked because an approved JEP exists. "
                        "Request revision first."
                    ),
                    status="blocked",
                    data={
                        "jep_state": policy_block.get("jep_state", {}),
                        "reason_codes": list(policy_block.get("reason_codes", [])),
                        "required_next_step": policy_block.get(
                            "required_next_step", ""
                        ),
                        "lock_outcome": "blocked",
                    },
                )

        ctx = context
        decision_context = memory.decision_context if memory else {}
        user_message = str(args.get("_user_message", "") or "")
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name=f"generate_{self._agent_name}",
            args=args,
            context=ctx,
            decision_context=decision_context,
            user_message=user_message,
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name=f"generate_{self._agent_name}",
            args=args,
            context=ctx,
        )

        if (
            self._agent_name == "pov"
            and ctx
            and not archie_memory._pov_has_sufficient_context(
                context=ctx,
                decision_context=decision_context,
                args=args,
                user_message=user_message,
            )
        ):
            clarify = "POV clarification required before Archie drafts the customer narrative."
            return ToolResult(
                summary=clarify,
                status="needs_input",
                clarification=clarify,
                data={"questions": archie_memory._pov_targeted_questions()},
            )

        feedback = str(args.get("feedback", "") or "")
        raw_request = feedback or _default_request(self._agent_name)

        try:
            response = await sub_agent_client.call_sub_agent(
                self._agent_name,
                task=raw_request,
                engagement_context={
                    "customer_id": self._customer_id,
                    "customer_name": self._customer_name,
                    "feedback": feedback,
                    "architect_brief": dict(args.get("_architect_brief", {}) or {}),
                },
                trace_id=trace_id,
            )
        except SubAgentError as exc:
            return ToolResult(
                summary=f"{self._agent_name.upper()} sub-agent error: {exc}",
                status="blocked",
            )

        if str(response.get("status") or "").lower() == "needs_input":
            clarification = str(response.get("result") or "")
            return ToolResult(
                summary=clarification or f"{self._agent_name.upper()} needs more input.",
                status="needs_input",
                clarification=clarification,
            )

        saved = await asyncio.to_thread(
            document_store.save_doc,
            self._store,
            self._doc_type,
            self._customer_id,
            str(response.get("result") or ""),
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        key = str(saved.get("key", "") or "")

        if self._agent_name == "jep":
            import agent.jep_lifecycle as jep_lifecycle

            jep_state = await asyncio.to_thread(
                jep_lifecycle.mark_generated,
                self._store,
                self._customer_id,
            )
            response.update({"jep_state": jep_state, "lock_outcome": "allowed"})

        return ToolResult(
            summary=f"{self._agent_name.upper()} v{saved.get('version')} saved.",
            status="ok",
            artifact_key=key,
            data=response,
        )


class PovHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("pov", "pov", store, customer_id, customer_name)


class JepHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("jep", "jep", store, customer_id, customer_name)


class WafHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("waf", "waf", store, customer_id, customer_name)


def _default_request(agent_name: str) -> str:
    if agent_name == "pov":
        return "Generate a customer POV from current engagement context."
    return f"Generate the {agent_name.upper()} from current engagement context."
