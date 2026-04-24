"""
agent/orchestrator_agent.py
----------------------------
Conversational orchestrator (Agent 0).

Accepts a natural-language SA message, decides which sub-agents to invoke
using a ReAct-style agentic loop, and returns a structured reply.

Conversation history is persisted per customer_id in OCI Object Storage at
  conversations/{customer_id}/history.json

Inter-agent calls:
  generate_diagram  → POST /message:send  (A2A v1.0 self-call via httpx)
  generate_pov      → pov_agent.generate_pov()     [in-process]
  generate_waf      → waf_agent.generate_waf()     [in-process]
  generate_jep      → jep_agent.generate_jep()     [in-process]
  save_notes        → document_store.save_note()   [in-process]
  get_summary       → context_store               [in-process]
  get_document      → document_store.get_latest_doc() [in-process]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable

from agent.persistence_objectstore import ObjectStoreBase
import agent.document_store as document_store
import agent.context_store as context_store
import agent.decision_context as decision_context_builder
import agent.jep_lifecycle as jep_lifecycle
from agent import critic_agent
from agent.orchestrator_skill_engine import (
    OrchestratorSkillDecision,
    OrchestratorSkillEngine,
)
from agent.skill_loader import select_skills_for_call
from agent.bom_service import get_shared_bom_service, new_trace_id

logger = logging.getLogger(__name__)
_PENDING_UPDATE_WORKFLOWS: dict[str, dict[str, Any]] = {}

# ── System message ─────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM_MSG = """\
You are **Archie**, an expert Oracle Cloud architect assistant.
You help users by chatting naturally, asking strong architecture questions,
and guiding engagements end-to-end with clear, practical advice.

User-facing behavior:
- Be conversational, concise, and architect-level.
- Explain tradeoffs, risks, assumptions, and recommended next steps.
- Do not expose internal tool names, tool-call JSON, or system mechanics unless the user explicitly asks.
- If the user asks for one deliverable, do only that deliverable unless they explicitly expand scope.
- If a prerequisite is missing, explain it clearly and ask for permission to proceed.

Internal execution policy (not user-visible):
- Available internal tools:
  save_notes, get_summary, generate_diagram, generate_bom,
  generate_pov, generate_jep, generate_waf, generate_terraform, get_document
- For every path tool call, use relevant specialist skill guidance to shape the specialist prompt.
- For every path tool call, run preflight and postflight skill checks.
- Treat skill checks as authoritative guardrails for allow/block behavior.
- After tool output returns, perform skill-informed quality review:
  if weak, inconsistent, or incomplete, critique and refine before presenting results.
- Prefer high-quality completion over first-pass acceptance.
- Never run unrelated generation paths in the same turn.

Change/update workflow policy:
- If user indicates a change like "we forgot/missed/add/update element," first inspect what already exists.
- Propose impacted outputs in order, in plain language.
- Ask for explicit confirmation before broad multi-output updates.
- Execute only the approved scope and summarize outcomes conversationally.

Prerequisite policy:
- Terraform requires existing architecture definition/diagram context.
- If prerequisite is missing, stop and request the required input/artifact first.

Output policy:
- Default output is natural Markdown prose.
- Keep internal execution details hidden by default.
- If user asks for technical/debug detail, provide a transparent summary of what was run and why.

When you need to take an internal action, output ONLY this JSON on a single line:
{"tool": "<name>", "args": {<key>: <value>}}

Tool contracts:
- save_notes {"text": "<notes text>"}
- get_summary {}
- generate_diagram {"bom_text": "<optional inline BOM/context for diagram updates>"}
- generate_bom {"prompt": "<workload sizing / BOM request>"}
- generate_pov {"feedback": "<optional update/correction text>"}
- generate_jep {"feedback": "<optional update/correction text>"}
- generate_waf {"feedback": "<optional update/correction text>"}
- generate_terraform {"prompt": "<optional module/constraints text>"}
- get_document {"type": "pov" | "jep" | "waf"}
"""

_SKILL_ENGINE = OrchestratorSkillEngine()


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_turn(
    *,
    customer_id: str,
    customer_name: str,
    user_message: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    a2a_base_url: str = "http://localhost:8080",
    max_tool_iterations: int = 5,
    specialist_mode: str = "legacy",
    max_refinements: int = 3,
) -> dict:
    """
    Process one SA message and return the orchestrator response.

    Returns:
        {
            "reply":          str,         # Markdown response to show the SA
            "tool_calls":     list[dict],  # tools invoked this turn
            "artifacts":      dict,        # {type: object_key} for newly produced artifacts
            "history_length": int,
        }
    """
    from agent.notifications import notify

    # Load conversation state
    history = document_store.load_conversation_history(store, customer_id)
    summary = document_store.load_conversation_summary(store, customer_id)
    context = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)

    new_turns: list[dict] = [
        {
            "role": "user",
            "content": user_message,
            "timestamp": _now(),
            "customer_name": customer_name,
        }
    ]
    tool_calls: list[dict] = []
    artifacts: dict = {}
    requested_tools = _requested_generation_tools(user_message)
    reply = ""
    forced_reply = ""
    forced_followup: dict[str, str] | None = None
    parallel_executed = False

    def _finalize_turn(reply_text: str) -> dict:
        new_turns.append({"role": "assistant", "content": reply_text, "timestamp": _now()})
        document_store.save_conversation_turns(store, customer_id, new_turns)
        return {
            "reply": reply_text,
            "tool_calls": tool_calls,
            "artifacts": artifacts,
            "history_length": len(history) + len(new_turns),
        }

    pending_checkpoint = context_store.get_pending_checkpoint(context)
    if pending_checkpoint and _is_checkpoint_approve_message(user_message):
        _resolve_pending_checkpoint(
            context=context,
            resolution="approved",
            note="User approved the pending checkpoint.",
        )
        await asyncio.to_thread(context_store.write_context, store, customer_id, context)
        return _finalize_turn(_checkpoint_resolution_reply(pending_checkpoint, approved=True))

    if pending_checkpoint and _is_checkpoint_reject_message(user_message):
        _resolve_pending_checkpoint(
            context=context,
            resolution="rejected",
            note="User rejected the pending checkpoint and will revise inputs.",
        )
        await asyncio.to_thread(context_store.write_context, store, customer_id, context)
        return _finalize_turn(_checkpoint_resolution_reply(pending_checkpoint, approved=False))

    decision_context = decision_context_builder.build_decision_context(
        user_message=user_message,
        context=context,
    )
    context_store.set_latest_decision_context(context, decision_context)
    await asyncio.to_thread(context_store.write_context, store, customer_id, context)
    prompt = _build_prompt(
        history,
        summary,
        user_message,
        decision_context=decision_context,
        pending_checkpoint=context_store.get_pending_checkpoint(context),
    )

    pending = _PENDING_UPDATE_WORKFLOWS.get(customer_id)
    if pending:
        if _is_update_cancel_message(user_message):
            _PENDING_UPDATE_WORKFLOWS.pop(customer_id, None)
            return _finalize_turn("Update workflow canceled. No specialist tools were executed.")

        if _is_update_confirm_message(user_message):
            _PENDING_UPDATE_WORKFLOWS.pop(customer_id, None)
            planned_tools = list(pending.get("tools", []) or [])
            change_request = str(pending.get("change_request", "") or "").strip()
            workflow_decision_context = decision_context_builder.build_decision_context(
                user_message=change_request or user_message,
                context=context,
            )
            for tool_name in planned_tools:
                tool_args = _update_tool_args(tool_name, change_request)
                result_summary, artifact_key, result_data = await _execute_tool(
                    tool_name,
                    tool_args,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    store=store,
                    text_runner=text_runner,
                    a2a_base_url=a2a_base_url,
                    specialist_mode=specialist_mode,
                    user_message=change_request or user_message,
                    max_refinements=max_refinements,
                    decision_context=workflow_decision_context,
                )
                tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result_summary": result_summary,
                        "result_data": result_data,
                    }
                )
                if artifact_key:
                    artifacts[tool_name] = artifact_key
                new_turns.append(
                    {
                        "role": "tool",
                        "tool": tool_name,
                        "result_summary": result_summary,
                        "timestamp": _now(),
                    }
                )

                followup = _extract_governor_followup(result_data)
                if followup:
                    if followup["kind"] == "blocked":
                        artifacts.pop(tool_name, None)
                    forced_reply = followup["message"]
                    break

            executed = ", ".join(planned_tools) if planned_tools else "(none)"
            if forced_reply:
                return _finalize_turn(forced_reply)
            return _finalize_turn(
                "Confirmed. I executed the approved update sequence in order and applied skill checks:\n"
                f"- Executed tools: {executed}\n"
                "- Review the tool outputs above and confirm if any additional updates are needed."
            )

        planned = ", ".join(pending.get("tools", [])) or "(none)"
        return _finalize_turn(
            "An update workflow is waiting for confirmation.\n"
            f"- Planned tools: {planned}\n"
            "- Reply `confirm update all` to proceed or `cancel update` to stop."
        )

    if _is_change_update_intent(user_message):
        ctx = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        planned_tools = _build_update_plan_from_context(ctx)
        if not planned_tools:
            return _finalize_turn(
                "I don't see existing generated artifacts for this customer yet, so I can't build an impact update plan.\n"
                "Generate a diagram/related artifacts first, then request a full update."
            )

        _PENDING_UPDATE_WORKFLOWS[customer_id] = {
            "tools": planned_tools,
            "change_request": user_message.strip(),
            "created_at": _now(),
        }
        ordered = "\n".join(f"{idx}. {tool}" for idx, tool in enumerate(planned_tools, start=1))
        return _finalize_turn(
            "I detected a change request and reviewed existing customer outputs. "
            "I can update all impacted elements in this order:\n"
            f"{ordered}\n\n"
            "Reply `confirm update all` to execute, or `cancel update`."
        )

    # Safe parallel fast-path:
    # When the SA explicitly asks for both POV and JEP in one request, these
    # document generations are independent and can run concurrently.
    parallel_tools = _parallel_plan_for_message(user_message)
    if parallel_tools:
        logger.info(
            "Orchestrator parallel tool plan: tools=%s customer=%s",
            [t["tool"] for t in parallel_tools],
            customer_id,
        )

        for tool in parallel_tools:
            context_summary = await asyncio.to_thread(
                _build_context_summary_for_skills, store, customer_id, customer_name
            )
            decision = _skill_preflight_for_tool(
                tool_name=tool["tool"],
                args=tool.get("args", {}),
                user_message=user_message,
                context_summary=context_summary,
            )
            if decision and decision.status == "block":
                forced_reply = _decision_pushback_text(decision)
                tool_calls.append(
                    {
                        "tool": tool["tool"],
                        "args": tool.get("args", {}),
                        "result_summary": forced_reply,
                        "result_data": {"skill_decision": asdict(decision)},
                    }
                )
                break

        if not forced_reply:
            parallel_results = await asyncio.gather(
                *[
                _execute_tool(
                    tool["tool"],
                    tool.get("args", {}),
                        customer_id=customer_id,
                        customer_name=customer_name,
                        store=store,
                        text_runner=text_runner,
                        a2a_base_url=a2a_base_url,
                    specialist_mode=specialist_mode,
                    user_message=user_message,
                    max_refinements=max_refinements,
                    decision_context=decision_context,
                )
                for tool in parallel_tools
            ]
            )
            parallel_executed = True
            pending_followup: dict[str, str] | None = None
            for tool, (result_summary, artifact_key, result_data) in zip(parallel_tools, parallel_results):
                tool_name = tool["tool"]
                tool_args = tool.get("args", {})
                notify(f"tool:{tool_name}", customer_id, result_summary)
                tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result_summary": result_summary,
                        "result_data": result_data,
                    }
                )
                if artifact_key:
                    artifacts[tool_name] = artifact_key
                new_turns.append(
                    {
                        "role": "tool",
                        "tool": tool_name,
                        "result_summary": result_summary,
                        "timestamp": _now(),
                    }
                )
                prompt = _append_tool_result(prompt, tool_name, result_summary)

                decision = _extract_blocking_skill_decision(result_data)
                if decision:
                    artifacts.pop(tool_name, None)
                    pending_followup = _prefer_followup(
                        pending_followup,
                        {"kind": "blocked", "message": _decision_pushback_text(decision)},
                    )
                    continue

                followup = _extract_governor_followup(result_data)
                if followup:
                    if followup["kind"] == "blocked":
                        artifacts.pop(tool_name, None)
                    pending_followup = _prefer_followup(pending_followup, followup)

            if pending_followup:
                forced_followup = pending_followup
                forced_reply = pending_followup["message"]

        if parallel_executed and not forced_reply:
            reply = _build_parallel_reply(tool_calls, decision_context=decision_context)
            return _finalize_turn(reply)

    if not forced_reply:
        for _iteration in range(max_tool_iterations):
            raw = await asyncio.to_thread(
                _call_text_runner,
                text_runner,
                prompt,
                ORCHESTRATOR_SYSTEM_MSG,
                "orchestrator",
            )
            tool_call = _parse_tool_call(raw)

            if tool_call is None:
                if "generate_diagram" in requested_tools:
                    if any(call.get("tool") == "generate_diagram" for call in tool_calls):
                        reply = _build_parallel_reply(tool_calls)
                        logger.info(
                            "Orchestrator rejected raw diagram text after tool execution customer=%s raw_preview=%s",
                            customer_id,
                            raw.strip()[:120],
                        )
                        break
                    tool_call = {"tool": "generate_diagram", "args": {"bom_text": user_message.strip()}}
                    logger.info(
                        "Orchestrator forced diagram tool after non-tool reply customer=%s raw_preview=%s",
                        customer_id,
                        raw.strip()[:120],
                    )
                else:
                    reply = raw.strip()
                    break

            tool_name = tool_call.get("tool", "")
            tool_args = tool_call.get("args", {})
            if (
                requested_tools
                and tool_name.startswith("generate_")
                and tool_name not in requested_tools
            ):
                forced_reply = (
                    "I limited execution to the tools you asked for in this turn. "
                    f"Skipped `{tool_name}` because it was not requested."
                )
                logger.info(
                    "Orchestrator tool gating: requested=%s skipped=%s customer=%s",
                    sorted(requested_tools),
                    tool_name,
                    customer_id,
                )
                break
            logger.info("Orchestrator tool call: %s args=%s customer=%s", tool_name, tool_args, customer_id)

            result_summary, artifact_key, result_data = await _execute_tool(
                tool_name,
                tool_args,
                customer_id=customer_id,
                customer_name=customer_name,
                store=store,
                text_runner=text_runner,
                a2a_base_url=a2a_base_url,
                specialist_mode=specialist_mode,
                user_message=user_message,
                max_refinements=max_refinements,
                decision_context=decision_context,
            )

            notify(f"tool:{tool_name}", customer_id, result_summary)

            tool_calls.append(
                {
                    "tool": tool_name,
                    "args": tool_args,
                    "result_summary": result_summary,
                    "result_data": result_data,
                }
            )
            if artifact_key:
                artifacts[tool_name] = artifact_key

            tool_turn = {
                "role": "tool",
                "tool": tool_name,
                "result_summary": result_summary,
                "timestamp": _now(),
            }
            new_turns.append(
                {
                    "role": "assistant",
                    "content": json.dumps(tool_call, separators=(",", ":")),
                    "timestamp": _now(),
                    "tool_call": tool_call,
                }
            )
            new_turns.append(tool_turn)

            decision = _extract_blocking_skill_decision(result_data)
            if decision:
                forced_followup = {
                    "kind": "blocked",
                    "message": _decision_pushback_text(decision),
                }
                forced_reply = _decision_pushback_text(decision)
                artifacts.pop(tool_name, None)
                break

            followup = _extract_governor_followup(result_data)
            if followup:
                forced_followup = followup
                if followup["kind"] == "blocked":
                    artifacts.pop(tool_name, None)
                forced_reply = followup["message"]
                break

            if tool_name == "generate_diagram" and requested_tools == {"generate_diagram"}:
                reply = result_summary
                break

            # Feed tool result back into next prompt
            prompt = _append_tool_result(prompt, tool_name, result_summary)

        else:
            if "generate_diagram" in requested_tools and tool_calls:
                reply = _build_parallel_reply(tool_calls, decision_context=decision_context)
            else:
                # Cap reached without a plain-text response — ask LLM for a summary
                raw = await asyncio.to_thread(
                    _call_text_runner,
                    text_runner,
                    prompt + "\n\nProvide a brief summary of what was accomplished.",
                    ORCHESTRATOR_SYSTEM_MSG,
                    "orchestrator",
                )
                reply = raw.strip()

    if forced_reply:
        if forced_followup and tool_calls:
            reply = _build_parallel_reply(
                tool_calls,
                decision_context=decision_context,
                followup=forced_followup,
            )
        else:
            reply = forced_reply

    return _finalize_turn(reply)


# ── Tool dispatch ─────────────────────────────────────────────────────────────

async def _execute_tool(
    tool_name: str,
    args: dict,
    *,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str = "legacy",
    user_message: str = "",
    max_refinements: int = 3,
    decision_context: dict[str, Any] | None = None,
) -> tuple[str, str, dict]:
    """
    Execute a tool call and return (result_summary, artifact_key).
    artifact_key is "" when no persistent artifact was produced.
    """
    if tool_name == "generate_jep":
        policy_block = await asyncio.to_thread(
            jep_lifecycle.generate_policy_block_payload,
            store,
            customer_id,
        )
        if policy_block is not None:
            blocked_data = {
                "jep_state": policy_block.get("jep_state", {}),
                "reason_codes": list(policy_block.get("reason_codes", [])),
                "required_next_step": policy_block.get("required_next_step", ""),
                "retry_instructions": list(policy_block.get("retry_instructions", [])),
                "lock_outcome": "blocked",
                "warnings": [],
            }
            blocked_data["trace"] = _build_tool_trace(
                tool_name=tool_name,
                result_data=blocked_data,
                max_refinements=max_refinements,
            )
            return (
                "JEP generation is locked because an approved JEP exists. Request revision first.",
                "",
                blocked_data,
            )

    path_id = _tool_to_path_id(tool_name)
    context_summary = ""
    preflight_decision = None
    if path_id:
        context_summary = await asyncio.to_thread(
            _build_context_summary_for_skills, store, customer_id, customer_name
        )
        preflight_decision = _skill_preflight_for_tool(
            tool_name=tool_name,
            args=args,
            user_message=user_message,
            context_summary=context_summary,
        )
        if preflight_decision and preflight_decision.status == "block":
            return (
                _decision_pushback_text(preflight_decision),
                "",
                {"skill_decision": asdict(preflight_decision)},
            )

    enriched_args = _inject_skill_into_tool_args(
        tool_name,
        args,
        user_message=user_message,
        decision_context=decision_context,
    )
    tool_text_runner = _runner_for_tool(text_runner, enriched_args)
    result_summary, artifact_key, result_data = await _execute_tool_core(
        tool_name,
        enriched_args,
        customer_id=customer_id,
        customer_name=customer_name,
        store=store,
        text_runner=tool_text_runner,
        a2a_base_url=a2a_base_url,
        specialist_mode=specialist_mode,
    )

    result_data = dict(result_data or {})
    applied_skills = list(enriched_args.get("_skill_injected", []) or [])
    result_data["skill_injected"] = bool(applied_skills)
    result_data["applied_skills"] = applied_skills
    result_data["skill_sections"] = dict(enriched_args.get("_skill_sections", {}) or {})
    result_data["skill_versions"] = dict(enriched_args.get("_skill_versions", {}) or {})
    result_data["skill_model_profile"] = str(enriched_args.get("_skill_model_profile", "") or "")
    result_data["decision_context"] = dict(decision_context or {})
    result_data["constraint_tags"] = list(enriched_args.get("_constraint_tags", []) or [])
    if preflight_decision:
        result_data["skill_preflight"] = asdict(preflight_decision)

    if path_id:
        postflight_decision = _SKILL_ENGINE.postflight_check(
            path_id=path_id,
            tool_result=result_summary,
            artifacts={"artifact_key": artifact_key},
            context_summary=context_summary,
        )
        result_data["skill_postflight"] = asdict(postflight_decision)
        if postflight_decision.status == "block":
            return (
                _decision_pushback_text(postflight_decision),
                "",
                result_data,
            )

        result_summary, artifact_key, result_data = await _critic_refine_if_needed(
            tool_name=tool_name,
            args=enriched_args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
            user_message=user_message,
            result_summary=result_summary,
            artifact_key=artifact_key,
            result_data=result_data,
            context_summary=context_summary,
            max_refinements=max_refinements,
            decision_context=decision_context or {},
        )

    _record_tool_decision_state(
        store=store,
        customer_id=customer_id,
        customer_name=customer_name,
        tool_name=tool_name,
        artifact_key=artifact_key,
        decision_context=decision_context or {},
        result_data=result_data,
    )
    _persist_tool_metadata(
        tool_name=tool_name,
        customer_id=customer_id,
        store=store,
        result_data=result_data,
    )
    result_data["trace"] = _build_tool_trace(
        tool_name=tool_name,
        result_data=result_data,
        max_refinements=max_refinements,
    )
    logger.info(
        "Tool trace tool=%s skills=%s profile=%s refinements=%s pass=%s",
        tool_name,
        result_data.get("applied_skills", []),
        result_data.get("skill_model_profile", ""),
        result_data.get("refinement_count", 0),
        (result_data.get("governor") or {}).get("overall_pass", True),
    )

    return result_summary, artifact_key, result_data


async def _execute_tool_core(
    tool_name: str,
    args: dict,
    *,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str,
) -> tuple[str, str, dict]:
    if specialist_mode == "langgraph":
        from agent import langgraph_specialists

        adapter_result = await langgraph_specialists.execute_tool(
            tool_name,
            args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        )
        if len(adapter_result) == 2:
            summary, key = adapter_result
            return summary, key, {}
        return adapter_result

    if tool_name == "save_notes":
        text = args.get("text", "")
        if not text.strip():
            return "No notes text provided.", "", {}
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        key = await asyncio.to_thread(
            document_store.save_note,
            store,
            customer_id,
            f"note_{ts}.md",
            text.encode("utf-8"),
        )
        return f"Notes saved. Key: {key}", key, {}

    if tool_name == "get_summary":
        ctx = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        summary = context_store.build_context_summary(ctx)
        return summary or "No engagement activity yet.", "", {}

    if tool_name == "generate_pov":
        from agent import pov_agent

        feedback = args.get("feedback", "")
        result = await asyncio.to_thread(
            pov_agent.generate_pov,
            customer_id,
            customer_name,
            store,
            text_runner,
            feedback=feedback,
        )
        key = result.get("key", "")
        return f"POV v{result.get('version')} saved. Key: {key}", key, {}

    if tool_name == "generate_diagram":
        s, k, d = await _call_generate_diagram(args, customer_id, a2a_base_url)
        return s, k, d

    if tool_name == "generate_waf":
        from agent import waf_agent

        feedback = args.get("feedback", "")
        result = await asyncio.to_thread(
            waf_agent.generate_waf,
            customer_id,
            customer_name,
            store,
            text_runner,
            feedback=feedback,
        )
        key = result.get("key", "")
        rating = result.get("overall_rating", "")
        return f"WAF review {rating} saved. Key: {key}", key, {}

    if tool_name == "generate_jep":
        from agent import jep_agent

        feedback = args.get("feedback", "")
        result = await asyncio.to_thread(
            jep_agent.generate_jep,
            customer_id,
            customer_name,
            store,
            text_runner,
            feedback=feedback,
        )
        key = result.get("key", "")
        jep_state = await asyncio.to_thread(jep_lifecycle.mark_generated, store, customer_id)
        return f"JEP v{result.get('version')} saved. Key: {key}", key, {
            "jep_state": jep_state,
            "reason_codes": [],
            "required_next_step": jep_state.get("required_next_step", ""),
            "lock_outcome": "allowed",
        }

    if tool_name == "generate_bom":
        prompt = str(args.get("prompt", "") or "").strip() or "Generate a BOM from current request context."
        service = get_shared_bom_service()
        response = await asyncio.to_thread(
            service.chat,
            message=prompt,
            conversation=[],
            trace_id=new_trace_id(),
            model_id="orchestrator-generate_bom",
            text_runner=text_runner,
        )
        result_type = str(response.get("type", "normal"))
        summary = str(response.get("reply", "")).strip() or "BOM response generated."
        if result_type == "final":
            summary = f"Final BOM prepared. {summary}"
        elif result_type == "question":
            summary = f"BOM clarification required. {summary}"
        elif "not ready" in summary.lower():
            summary = f"BOM data not ready. {summary}"
        return summary, "", response

    if tool_name == "generate_terraform":
        return (
            "Terraform generation is not yet enabled in legacy mode. "
            "Enable orchestrator.specialists_langgraph_enabled to use the v1.5 chain.",
            "",
            {},
        )

    if tool_name == "get_document":
        doc_type = args.get("type", "pov")
        content = await asyncio.to_thread(
            document_store.get_latest_doc,
            store,
            doc_type,
            customer_id,
        )
        if content is None:
            return f"No {doc_type.upper()} found for this customer.", "", {}
        preview = content[:500].strip()
        return f"{doc_type.upper()} content (first 500 chars):\n{preview}", "", {}

    return f"Unknown tool: {tool_name!r}", "", {}


def _tool_to_path_id(tool_name: str) -> str | None:
    if tool_name == "generate_diagram":
        return "diagram"
    if tool_name == "generate_bom":
        return "bom"
    if tool_name == "generate_pov":
        return "pov"
    if tool_name == "generate_jep":
        return "jep"
    if tool_name == "generate_waf":
        return "waf"
    if tool_name == "generate_terraform":
        return "terraform"
    if tool_name in {"get_summary", "get_document"}:
        return "summary_document"
    return None


def _build_context_summary_for_skills(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
) -> str:
    try:
        ctx = context_store.read_context(store, customer_id, customer_name)
        return context_store.build_context_summary(ctx)
    except Exception as exc:
        logger.warning("Failed to build context summary for skill checks: %s", exc)
        return ""


def _skill_preflight_for_tool(
    *,
    tool_name: str,
    args: dict,
    user_message: str,
    context_summary: str,
) -> OrchestratorSkillDecision | None:
    path_id = _tool_to_path_id(tool_name)
    if not path_id:
        return None
    return _SKILL_ENGINE.preflight_check(
        path_id=path_id,
        user_message=user_message,
        context_summary=context_summary,
        current_state={"tool": tool_name, "args": args},
    )


def _decision_pushback_text(decision: OrchestratorSkillDecision) -> str:
    lines = [decision.pushback_message.strip() or "This request is blocked by expert skill validation."]
    if decision.reasons:
        lines.append("")
        lines.append("Reasons:")
        for reason in decision.reasons:
            lines.append(f"- {reason}")
    if decision.retry_instructions:
        lines.append("")
        lines.append("Next steps:")
        for step in decision.retry_instructions:
            lines.append(f"- {step}")
    return "\n".join(lines).strip()


def _extract_blocking_skill_decision(result_data: dict | None) -> OrchestratorSkillDecision | None:
    if not isinstance(result_data, dict):
        return None
    candidate = result_data.get("skill_decision") or result_data.get("skill_postflight")
    if not isinstance(candidate, dict):
        return None
    if candidate.get("status") != "block":
        return None
    try:
        return OrchestratorSkillDecision(
            path_id=str(candidate.get("path_id", "")),
            phase=str(candidate.get("phase", "")),
            status="block",
            reasons=list(candidate.get("reasons", [])),
            pushback_message=str(candidate.get("pushback_message", "")),
            retry_instructions=list(candidate.get("retry_instructions", [])),
        )
    except Exception:
        return None


def _inject_skill_into_tool_args(
    tool_name: str,
    args: dict | None,
    *,
    user_message: str = "",
    decision_context: dict[str, Any] | None = None,
) -> dict:
    payload = dict(args or {})
    constraint_tags = decision_context_builder.derive_constraint_tags(decision_context)
    decision_block = _build_decision_context_block(decision_context)
    selection_message = " ".join([user_message.strip(), *constraint_tags]).strip()
    selected = select_skills_for_call(
        tool_name=tool_name,
        user_message=selection_message,
        tool_args=payload,
        max_skills=2,
    )
    payload["_decision_context"] = dict(decision_context or {})
    payload["_constraint_tags"] = constraint_tags
    if not selected:
        if decision_block:
            _inject_decision_block_into_payload(tool_name, payload, decision_block)
        return payload

    block_parts: list[str] = [
        decision_block,
        "[Skill Injection Contract]",
        "Incorporate ALL provided skills and meet the Quality Bar.",
        "If the target output contract is strict (for example: JSON-only or document-only Markdown), apply skill guidance implicitly and do not add meta commentary or section-reference prose.",
        "[End Skill Injection Contract]",
    ]
    model_profile = ""
    sections: dict[str, list[str]] = {}
    versions: dict[str, str] = {}
    for spec in selected:
        section_names = list(spec.sections.keys())
        sections[spec.name] = section_names
        section_listing = ", ".join(section_names) if section_names else "(no explicit sections)"
        version = str(spec.metadata.get("version", "") or "")
        versions[spec.name] = version
        description = str(spec.metadata.get("description", "") or "")
        block_parts.append(
            "\n[Injected Skill Guidance]\n"
            f"Skill: {spec.name}\n"
            f"Version: {version}\n"
            f"Description: {description}\n"
            f"Sections: {section_listing}\n"
            f"{spec.body}\n"
            "[End Skill Guidance]\n"
        )
        if not model_profile:
            model_profile = str(spec.metadata.get("model_profile", "")).strip()
    skill_block = "\n".join(part for part in block_parts if part.strip())
    payload["_skill_guidance_block"] = skill_block
    payload["_skill_sections"] = sections
    payload["_skill_versions"] = versions

    _inject_decision_block_into_payload(tool_name, payload, skill_block)
    payload["_skill_injected"] = [s.name for s in selected]
    if model_profile:
        payload["_skill_model_profile"] = model_profile
    return payload


def _call_text_runner(
    text_runner: Callable,
    prompt: str,
    system_message: str,
    model_profile: str = "orchestrator",
) -> str:
    try:
        return text_runner(prompt, system_message, model_profile)
    except TypeError:
        return text_runner(prompt, system_message)


def _runner_for_tool(text_runner: Callable, args: dict) -> Callable[[str, str], str]:
    model_profile = str(args.get("_skill_model_profile", "")).strip()
    if not model_profile:
        return lambda prompt, system_message: _call_text_runner(
            text_runner, prompt, system_message, "orchestrator"
        )
    return lambda prompt, system_message: _call_text_runner(
        text_runner, prompt, system_message, model_profile
    )


async def _critic_refine_if_needed(
    *,
    tool_name: str,
    args: dict,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str,
    user_message: str,
    result_summary: str,
    artifact_key: str,
    result_data: dict,
    context_summary: str,
    max_refinements: int,
    decision_context: dict[str, Any],
) -> tuple[str, str, dict]:
    if not tool_name.startswith("generate_"):
        return result_summary, artifact_key, result_data

    refinable_tools = {"generate_pov", "generate_jep", "generate_waf", "generate_terraform"}

    current_summary = result_summary
    current_key = artifact_key
    current_data = dict(result_data or {})
    governor_history: list[dict[str, Any]] = []
    warnings: list[str] = []
    refinement_count = 0

    while True:
        try:
            governor = await asyncio.to_thread(
                critic_agent.evaluate_tool_result,
                tool_name=tool_name,
                user_message=user_message,
                tool_args=args,
                result_summary=current_summary,
                result_data=current_data,
                decision_context=decision_context,
                text_runner=text_runner,
            )
        except Exception as exc:
            warnings.append(f"critic_error_fail_open: {exc}")
            break

        governor = _normalize_governor_result(governor)
        governor_history.append(governor)
        current_data["governor"] = governor
        current_data["last_critique"] = {
            "overall_pass": governor.get("overall_pass", True),
            "confidence": governor.get("confidence", 0),
            "issues": governor.get("issues", []),
            "suggestions": governor.get("suggestions", []),
            "critique_summary": governor.get("critique_summary", ""),
            "severity": governor.get("severity", "low"),
        }

        overall_status = str(governor.get("overall_status", "pass") or "pass")
        if overall_status in {"pass", "checkpoint_required", "blocked"}:
            break

        if tool_name not in refinable_tools:
            break
        if refinement_count >= max_refinements:
            warnings.append("max_refinements_reached_best_effort")
            remaining = governor.get("issues", []) if isinstance(governor, dict) else []
            if remaining:
                current_summary = (
                    f"{current_summary}\n\nBest-effort note: maximum refinements reached. "
                    "Remaining issues were identified by the governor."
                ).strip()
            break

        feedback = _build_critic_feedback(governor)
        if not feedback.strip():
            warnings.append("critic_returned_no_actionable_feedback")
            break

        retry_args = dict(args)
        if tool_name == "generate_terraform":
            retry_args["prompt"] = f"{retry_args.get('prompt', '')}\n\n[Governor Feedback]\n{feedback}\n".strip()
        else:
            retry_args["feedback"] = f"{retry_args.get('feedback', '')}\n\n[Governor Feedback]\n{feedback}\n".strip()
        guidance_block = str(args.get("_skill_guidance_block", "")).strip()
        if guidance_block:
            if tool_name == "generate_terraform":
                retry_args["prompt"] = f"{retry_args.get('prompt', '')}\n\n{guidance_block}".strip()
            else:
                retry_args["feedback"] = f"{retry_args.get('feedback', '')}\n\n{guidance_block}".strip()

        retry_runner = _runner_for_tool(text_runner, retry_args)
        retry_summary, retry_key, retry_data = await _execute_tool_core(
            tool_name,
            retry_args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=retry_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
        )
        refinement_count += 1
        retry_data = dict(retry_data or {})
        retry_data["critic_retry"] = {
            "attempt": refinement_count,
            "feedback": feedback,
        }
        retry_data["decision_context"] = dict(decision_context or {})
        retry_data["constraint_tags"] = list(args.get("_constraint_tags", []) or [])
        postflight = _SKILL_ENGINE.postflight_check(
            path_id=_tool_to_path_id(tool_name) or "",
            tool_result=retry_summary,
            artifacts={"artifact_key": retry_key},
            context_summary=context_summary,
        )
        retry_data["skill_postflight"] = asdict(postflight)
        current_summary, current_key, current_data = retry_summary, retry_key, retry_data
        if postflight.status == "block":
            return _decision_pushback_text(postflight), "", current_data

    current_data["refinement_count"] = refinement_count
    current_data["critic_history"] = governor_history
    current_data["governor_history"] = governor_history
    if governor_history:
        current_data["governor"] = governor_history[-1]
    if warnings:
        current_data["warnings"] = list(warnings)
    if governor_history and str(governor_history[-1].get("overall_status", "pass")) == "revise":
        current_data["best_effort"] = True
    return current_summary, current_key, current_data


def _build_critic_feedback(critic: dict[str, Any]) -> str:
    issues = [str(item).strip() for item in critic.get("issues", []) if str(item).strip()]
    suggestions = [str(item).strip() for item in critic.get("suggestions", []) if str(item).strip()]
    summary = str(critic.get("critique_summary", "")).strip()
    lines: list[str] = []
    if summary:
        lines.append(summary)
    if issues:
        lines.append("Issues:")
        lines.extend(f"- {issue}" for issue in issues)
    if suggestions:
        lines.append("Suggestions:")
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines).strip()


def _normalize_governor_result(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "overall_status": "pass",
            "security": {"status": "pass", "findings": [], "required_actions": []},
            "cost": {"status": "pass", "estimated_monthly_cost": None, "budget_target": None, "variance": None, "findings": []},
            "quality": {"status": "pass", "issues": [], "suggestions": [], "confidence": 0, "summary": "", "severity": "low"},
            "decision_summary": "",
            "reason_codes": [],
            "overall_pass": True,
            "confidence": 0,
            "issues": [],
            "suggestions": [],
            "critique_summary": "",
            "severity": "low",
        }
    if "overall_status" in payload:
        return payload
    overall_pass = bool(payload.get("overall_pass", True))
    severity = str(payload.get("severity", "low") or "low")
    confidence = int(payload.get("confidence", 0) or 0)
    issues = list(payload.get("issues", []) or [])
    suggestions = list(payload.get("suggestions", []) or [])
    critique_summary = str(payload.get("critique_summary", "") or "")
    return {
        "overall_status": "pass" if overall_pass else "revise",
        "security": {"status": "pass", "findings": [], "required_actions": []},
        "cost": {"status": "pass", "estimated_monthly_cost": None, "budget_target": None, "variance": None, "findings": []},
        "quality": {
            "status": "pass" if overall_pass else "revise",
            "issues": issues,
            "suggestions": suggestions,
            "confidence": confidence,
            "summary": critique_summary,
            "severity": severity,
            "overall_pass": overall_pass,
        },
        "decision_summary": critique_summary,
        "reason_codes": [],
        "overall_pass": overall_pass,
        "confidence": confidence,
        "issues": issues,
        "suggestions": suggestions,
        "critique_summary": critique_summary,
        "severity": severity,
    }


def _persist_tool_metadata(
    *,
    tool_name: str,
    customer_id: str,
    store: ObjectStoreBase,
    result_data: dict[str, Any],
) -> None:
    metadata = {
        "applied_skills": list(result_data.get("applied_skills", []) or []),
        "refinement_count": int(result_data.get("refinement_count", 0) or 0),
        "last_critique": result_data.get("last_critique", {}),
        "governor": result_data.get("governor", {}),
        "decision_context": result_data.get("decision_context", {}),
        "constraint_tags": list(result_data.get("constraint_tags", []) or []),
    }
    if tool_name == "generate_pov":
        document_store.merge_latest_doc_metadata(store, "pov", customer_id, metadata)
    elif tool_name == "generate_jep":
        document_store.merge_latest_doc_metadata(store, "jep", customer_id, metadata)
    elif tool_name == "generate_waf":
        document_store.merge_latest_doc_metadata(store, "waf", customer_id, metadata)
    elif tool_name == "generate_terraform":
        document_store.merge_latest_terraform_metadata(store, customer_id, metadata)


def _build_tool_trace(*, tool_name: str, result_data: dict[str, Any], max_refinements: int) -> dict[str, Any]:
    applied = list(result_data.get("applied_skills", []) or [])
    last_critique = result_data.get("last_critique", {}) or {}
    governor = result_data.get("governor", {}) or {}
    prior_trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
    trace = {
        **prior_trace,
        "path_id": _tool_to_path_id(tool_name) or "",
        "applied_skills": applied,
        "skill_versions": dict(result_data.get("skill_versions", {}) or {}),
        "model_profile": str(result_data.get("skill_model_profile", "") or ""),
        "model_id": str(prior_trace.get("model_id", "") or ""),
        "critic_enabled": tool_name in {"generate_pov", "generate_jep", "generate_waf", "generate_terraform"},
        "refinement_count": int(result_data.get("refinement_count", 0) or 0),
        "max_refinements": int(max_refinements),
        "overall_pass": bool(last_critique.get("overall_pass", True)),
        "critic_confidence": int(last_critique.get("confidence", 0) or 0),
        "warnings": list(result_data.get("warnings", []) or []),
        "decision_context": dict(result_data.get("decision_context", {}) or {}),
        "constraint_tags": list(result_data.get("constraint_tags", []) or []),
        "assumption_count": len((result_data.get("decision_context", {}) or {}).get("assumptions", []) or []),
        "governor": governor,
        "decision_log": result_data.get("decision_log", {}),
        "checkpoint": result_data.get("checkpoint"),
    }
    if tool_name == "generate_jep":
        trace["jep_state"] = result_data.get("jep_state", {})
        trace["reason_codes"] = list(result_data.get("reason_codes", []) or [])
        trace["required_next_step"] = str(result_data.get("required_next_step", "") or "")
        trace["lock_outcome"] = str(result_data.get("lock_outcome", "") or "")
    return trace


def _build_decision_context_block(decision_context: dict[str, Any] | None) -> str:
    if not isinstance(decision_context, dict) or not decision_context:
        return ""
    return (
        "[Decision Context]\n"
        + json.dumps(decision_context, indent=2, ensure_ascii=True)
        + "\n[End Decision Context]\n"
    )


def _inject_decision_block_into_payload(tool_name: str, payload: dict[str, Any], block: str) -> None:
    if not block.strip():
        return
    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        payload["feedback"] = f"{payload.get('feedback', '')}\n\n{block}".strip()
    elif tool_name == "generate_terraform":
        payload["prompt"] = f"{payload.get('prompt', '')}\n\n{block}".strip()
    elif tool_name in {"generate_diagram", "generate_bom"}:
        key = "bom_text" if tool_name == "generate_diagram" else "prompt"
        payload[key] = f"{payload.get(key, '')}\n\n{block}".strip()


def _record_tool_decision_state(
    *,
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    tool_name: str,
    artifact_key: str,
    decision_context: dict[str, Any],
    result_data: dict[str, Any],
) -> None:
    if not tool_name.startswith("generate_"):
        return
    context = context_store.read_context(store, customer_id, customer_name)
    context_store.set_latest_decision_context(context, decision_context)
    checkpoint = _checkpoint_from_result(tool_name=tool_name, decision_context=decision_context, result_data=result_data)
    if checkpoint:
        context_store.set_pending_checkpoint(context, checkpoint)
        result_data["checkpoint"] = checkpoint

    decision_log = {
        "id": str(uuid.uuid4()),
        "timestamp": _now(),
        "tool": tool_name,
        "decision_context_hash": _decision_context_hash(decision_context),
        "assumptions": list((decision_context or {}).get("assumptions", []) or []),
        "decision": str((result_data.get("governor", {}) or {}).get("decision_summary", "") or result_data.get("result_summary", "") or ""),
        "tradeoffs": list(((result_data.get("governor", {}) or {}).get("cost", {}) or {}).get("findings", []) or []),
        "security": dict(((result_data.get("governor", {}) or {}).get("security", {}) or {})),
        "cost": dict(((result_data.get("governor", {}) or {}).get("cost", {}) or {})),
        "checkpoint_status": checkpoint.get("status", "none") if checkpoint else "none",
        "artifact_refs": [artifact_key] if artifact_key else [],
    }
    context_store.append_decision_log(context, decision_log)
    context_store.write_context(store, customer_id, context)
    result_data["decision_log"] = decision_log


def _checkpoint_from_result(
    *,
    tool_name: str,
    decision_context: dict[str, Any],
    result_data: dict[str, Any],
) -> dict[str, Any] | None:
    governor = result_data.get("governor", {}) or {}
    if not isinstance(governor, dict):
        return None
    if str(governor.get("overall_status", "pass")) != "checkpoint_required":
        return None
    cost = governor.get("cost", {}) or {}
    estimated = cost.get("estimated_monthly_cost")
    budget = cost.get("budget_target")
    variance = cost.get("variance")
    decision_summary = str(governor.get("decision_summary", "") or "").strip()
    assumptions = _render_assumptions(decision_context, limit=3)
    has_cost_signal = any(value is not None for value in (estimated, budget, variance))
    if has_cost_signal:
        lines = [
            "Cost checkpoint required before final acceptance.",
            f"- Tool: {tool_name}",
            f"- Estimated monthly cost: {estimated}",
            f"- Budget target: {budget}",
            f"- Variance: {variance}",
        ]
        if assumptions:
            lines.append("- Basis: best-effort estimate from the current notes and assumptions.")
            lines.append("Assumptions applied:")
            lines.extend(f"- {assumption}" for assumption in assumptions)
        lines.append(
            "- Reply `approve checkpoint` to accept this tradeoff or revise the request and rerun."
        )
        prompt = "\n".join(lines)
        checkpoint_type = "cost_override"
    else:
        lines = [
            "Discovery checkpoint required before final acceptance.",
            f"- Tool: {tool_name}",
        ]
        if decision_summary:
            lines.append(f"- Why: {decision_summary}")
        if assumptions:
            lines.append("- Basis: best-effort draft built from sparse notes and unconfirmed assumptions.")
            lines.append("Assumptions applied:")
            lines.extend(f"- {assumption}" for assumption in assumptions)
        else:
            lines.append("- Basis: best-effort draft pending confirmation of requirements.")
        lines.append(
            "- Reply `approve checkpoint` to accept this draft direction or revise the request and rerun."
        )
        prompt = "\n".join(lines)
        checkpoint_type = "assumption_review"
    return {
        "id": str(uuid.uuid4()),
        "type": checkpoint_type,
        "status": "pending",
        "prompt": prompt,
        "recommended_action": "approve or revise input",
        "options": ["approve checkpoint", "revise input"],
        "decision_context_hash": _decision_context_hash(decision_context),
    }


def _extract_governor_followup(result_data: dict | None) -> dict[str, str] | None:
    if not isinstance(result_data, dict):
        return None
    governor = result_data.get("governor")
    if not isinstance(governor, dict):
        return None
    status = str(governor.get("overall_status", "pass") or "pass")
    if status == "blocked":
        return {"kind": "blocked", "message": _governor_blocked_reply(governor)}
    checkpoint = result_data.get("checkpoint")
    if status == "checkpoint_required" and isinstance(checkpoint, dict):
        return {"kind": "checkpoint_required", "message": str(checkpoint.get("prompt", "")).strip()}
    return None


def _governor_blocked_reply(governor: dict[str, Any]) -> str:
    security = governor.get("security", {}) or {}
    lines = [str(governor.get("decision_summary", "") or "The governor blocked this output.").strip()]
    findings = [str(item).strip() for item in security.get("findings", []) if str(item).strip()]
    actions = [str(item).strip() for item in security.get("required_actions", []) if str(item).strip()]
    if findings:
        lines.append("")
        lines.append("Security findings:")
        lines.extend(f"- {item}" for item in findings)
    if actions:
        lines.append("")
        lines.append("Required actions:")
        lines.extend(f"- {item}" for item in actions)
    return "\n".join(lines).strip()


def _decision_context_hash(decision_context: dict[str, Any] | None) -> str:
    raw = json.dumps(decision_context or {}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _resolve_pending_checkpoint(context: dict[str, Any], *, resolution: str, note: str) -> None:
    pending = context_store.get_pending_checkpoint(context)
    if not pending:
        return
    pending["status"] = resolution
    context_store.append_decision_log(
        context,
        {
            "id": str(uuid.uuid4()),
            "timestamp": _now(),
            "tool": "checkpoint_resolution",
            "decision_context_hash": pending.get("decision_context_hash", ""),
            "assumptions": [],
            "decision": note,
            "tradeoffs": [],
            "security": {},
            "cost": {},
            "checkpoint_status": resolution,
            "artifact_refs": [],
        },
    )
    context_store.clear_pending_checkpoint(context)


def _checkpoint_resolution_reply(checkpoint: dict[str, Any], *, approved: bool) -> str:
    if approved:
        return (
            "Checkpoint approved. I recorded the decision and cleared the pending tradeoff review.\n"
            f"- Checkpoint type: {checkpoint.get('type', 'checkpoint')}"
        )
    return (
        "Checkpoint rejected. I cleared the pending tradeoff review so you can revise the constraints and rerun.\n"
        f"- Checkpoint type: {checkpoint.get('type', 'checkpoint')}"
    )


async def _call_generate_diagram(
    args: dict,
    customer_id: str,
    a2a_base_url: str,
) -> tuple[str, str, dict]:
    """Call the drawing agent via A2A with clean user notes plus architect context."""
    try:
        import httpx
    except ImportError:
        return "httpx not installed — cannot call diagram agent.", "", {}

    bom_text = str(args.get("bom_text", "") or "")
    user_notes = _strip_injected_guidance_blocks(bom_text).strip()
    if not user_notes:
        user_notes = "Generate a diagram for this engagement."

    context_parts: list[str] = []
    decision_context = args.get("_decision_context")
    decision_summary = decision_context_builder.summarize_decision_context(decision_context)
    if decision_summary:
        context_parts.append(decision_summary)
    if _notes_request_best_effort_assumptions(user_notes):
        context_parts.append(
            "Assumption mode requested: apply standard safe OCI assumptions for a ballpark architecture. "
            "Ask only truly blocking questions when the workload/components are still unspecified."
        )
    payload = {
        "task_id": f"orch-{_now()}",
        "skill": "generate_diagram",
        "client_id": customer_id,
        "inputs": {
            "notes": user_notes,
            "context": "\n\n".join(part for part in context_parts if part.strip()),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{a2a_base_url}/api/a2a/task", json=payload)
        body = resp.json()
        status = str(body.get("status", "error") or "error").lower()
        outputs = body.get("outputs", {}) if isinstance(body.get("outputs"), dict) else {}
        task_id = str(body.get("task_id", "") or payload["task_id"])
        if status == "ok":
            key = str(outputs.get("object_key") or outputs.get("drawio_key") or "")
            if key:
                return f"Diagram generated. Key: {key}", key, {}
            return f"Diagram generated (task {task_id}).", "", {}
        if status == "need_clarification":
            questions = outputs.get("questions", []) if isinstance(outputs.get("questions"), list) else []
            result_data: dict[str, Any] = {"questions": questions}
            clarify_context = outputs.get("_clarify_context")
            if isinstance(clarify_context, dict):
                result_data["_clarify_context"] = clarify_context
            if questions:
                return _format_diagram_clarification_reply(questions), "", result_data
            return "Diagram clarification required before generation can continue.", "", result_data
        return f"Diagram generation returned status={status}.", "", {}
    except Exception as exc:
        logger.warning("Diagram A2A call failed: %s", exc)
        return f"Diagram generation failed: {exc}", "", {}


_INJECTED_GUIDANCE_BLOCKS: tuple[tuple[str, str], ...] = (
    ("[Decision Context]", "[End Decision Context]"),
    ("[Skill Injection Contract]", "[End Skill Injection Contract]"),
    ("[Injected Skill Guidance]", "[End Skill Guidance]"),
)


def _strip_injected_guidance_blocks(text: str) -> str:
    cleaned = str(text or "")
    for start, end in _INJECTED_GUIDANCE_BLOCKS:
        while True:
            start_idx = cleaned.find(start)
            if start_idx == -1:
                break
            end_idx = cleaned.find(end, start_idx)
            if end_idx == -1:
                cleaned = cleaned[:start_idx].rstrip()
                break
            cleaned = (cleaned[:start_idx] + cleaned[end_idx + len(end):]).strip()
    return cleaned.strip()


def _notes_request_best_effort_assumptions(notes: str) -> bool:
    lowered = str(notes or "").lower()
    markers = (
        "assumption",
        "assume",
        "ballpark",
        "ball park",
        "rough",
        "draft",
        "only got",
        "small set of info",
        "notes",
    )
    return any(marker in lowered for marker in markers)


def _extract_a2a_artifact_data(artifacts: list[dict[str, Any]], name: str) -> Any:
    for artifact in artifacts:
        if artifact.get("name") != name:
            continue
        parts = artifact.get("parts", [])
        if not isinstance(parts, list) or not parts:
            return None
        return parts[0].get("data")
    return None


def _extract_a2a_reply_text(artifacts: list[dict[str, Any]]) -> str:
    for artifact in artifacts:
        if artifact.get("name") != "reply":
            continue
        parts = artifact.get("parts", [])
        if not isinstance(parts, list) or not parts:
            return ""
        return str(parts[0].get("text", "") or "")
    return ""


def _extract_a2a_questions(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = _extract_a2a_artifact_data(artifacts, "questions")
    if not isinstance(payload, dict):
        return []
    questions = payload.get("questions", [])
    if not isinstance(questions, list):
        return []
    return [q for q in questions if isinstance(q, dict)]


def _format_diagram_clarification_reply(questions: list[dict[str, Any]]) -> str:
    lines = ["Diagram clarification required before generation can continue."]
    prompts = [
        str(question.get("question", "") or "").strip()
        for question in questions
        if str(question.get("question", "") or "").strip()
    ]
    if prompts:
        lines.append("")
        lines.append("Questions:")
        lines.extend(f"- {prompt}" for prompt in prompts)
    return "\n".join(lines)


# ── Prompt assembly ───────────────────────────────────────────────────────────

def _build_prompt(
    history: list[dict],
    summary: str,
    user_message: str,
    *,
    decision_context: dict[str, Any] | None = None,
    pending_checkpoint: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []

    if summary:
        parts.append(f"[Prior conversation summary]\n{summary}\n")
    decision_summary = decision_context_builder.summarize_decision_context(decision_context)
    if decision_summary:
        parts.append(f"[Current decision context]\n{decision_summary}\n")
    if pending_checkpoint:
        parts.append(
            "[Pending checkpoint]\n"
            f"{pending_checkpoint.get('prompt', '')}\n"
        )

    for turn in history:
        role = turn.get("role", "")
        if role == "user":
            parts.append(f"SA: {turn.get('content', '')}")
        elif role == "assistant":
            content = turn.get("content", "")
            if content:
                parts.append(f"ASSISTANT: {content}")
        elif role == "tool":
            parts.append(
                f"[Tool result: {turn.get('tool', '')}] "
                f"{turn.get('result_summary', '')}"
            )

    parts.append(f"SA: {user_message}")
    parts.append("ASSISTANT:")
    return "\n\n".join(parts)


def _append_tool_result(prompt: str, tool_name: str, result_summary: str) -> str:
    return prompt.rstrip("ASSISTANT:").rstrip() + (
        f"\n\n[Tool result: {tool_name}] {result_summary}\n\nASSISTANT:"
    )


def _build_parallel_reply(
    tool_calls: list[dict[str, Any]],
    *,
    decision_context: dict[str, Any] | None = None,
    followup: dict[str, str] | None = None,
) -> str:
    if not tool_calls:
        return "Requested tool execution completed."
    assumptions = _render_assumptions(decision_context, limit=3)
    if len(tool_calls) == 1 and followup is None:
        call = tool_calls[0]
        summary = str(call.get("result_summary", "") or "").strip()
        return summary or f"Completed `{call.get('tool', 'requested_tool')}`."

    lines = ["Completed the requested outputs:"]
    for call in tool_calls:
        tool_name = str(call.get("tool", "") or "requested_tool")
        summary = str(call.get("result_summary", "") or "").strip()
        if summary:
            lines.append(f"- `{tool_name}`: {summary}")
        else:
            lines.append(f"- `{tool_name}` completed.")
    if assumptions and (len(tool_calls) > 1 or followup is not None):
        lines.append("")
        lines.append("Assumptions applied:")
        lines.extend(f"- {assumption}" for assumption in assumptions)
    if followup:
        lines.append("")
        lines.append(str(followup.get("message", "")).strip())
    return "\n".join(lines)


def _parallel_plan_for_message(user_message: str) -> list[dict]:
    """
    Plan safe concurrent tool calls from explicit SA intent.
    """
    msg = user_message.lower()
    wants_bom = "bom" in msg or "bill of materials" in msg
    wants_explicit_diagram = any(
        term in msg for term in (
            "generate diagram",
            "build diagram",
            "create diagram",
            " bom and diagram",
            " diagram and bom",
            "architecture diagram",
            "drawio",
            "draw.io",
        )
    ) or (wants_bom and "diagram" in msg)
    if wants_bom and wants_explicit_diagram:
        if _request_references_existing_bom(user_message):
            return [{"tool": "generate_diagram", "args": {"bom_text": user_message.strip()}}]
        return [
            {"tool": "generate_bom", "args": {"prompt": user_message.strip()}},
            {"tool": "generate_diagram", "args": {"bom_text": user_message.strip()}},
        ]
    if wants_bom and not any(term in msg for term in ("pov", "jep", "waf", "terraform", "diagram")):
        return [{"tool": "generate_bom", "args": {"prompt": user_message.strip()}}]

    wants_pov = "pov" in msg or "point of view" in msg
    wants_jep = "jep" in msg or "joint execution plan" in msg
    if not (wants_pov and wants_jep):
        return []
    if any(term in msg for term in ("terraform", "diagram", "waf", "bom")):
        return []
    return [
        {"tool": "generate_pov", "args": {}},
        {"tool": "generate_jep", "args": {}},
    ]


def _requested_generation_tools(user_message: str) -> set[str]:
    """
    Infer explicitly requested generation tools from the current user turn.
    Used to prevent unrelated generation actions in the same turn.
    """
    msg = (user_message or "").lower()
    requested: set[str] = set()
    if "bom" in msg or "bill of materials" in msg:
        requested.add("generate_bom")
    if "diagram" in msg or "architecture" in msg or "drawio" in msg:
        requested.add("generate_diagram")
    if "terraform" in msg or "iac" in msg:
        requested.add("generate_terraform")
    if "pov" in msg or "point of view" in msg:
        requested.add("generate_pov")
    if "jep" in msg or "joint execution plan" in msg:
        requested.add("generate_jep")
    if "waf" in msg or "well-architected" in msg or "well architected" in msg:
        requested.add("generate_waf")
    return requested


def _is_change_update_intent(user_message: str) -> bool:
    msg = (user_message or "").lower()
    if _requested_generation_tools(user_message):
        return False
    has_change = any(token in msg for token in ("forgot", "missing", "add", "update", "change", "modify"))
    has_scope = any(token in msg for token in ("element", "component", "application", "system", "architecture"))
    has_direct_generate = any(token in msg for token in ("generate bom", "generate terraform", "generate diagram"))
    return has_change and has_scope and not has_direct_generate


def _is_update_confirm_message(user_message: str) -> bool:
    msg = (user_message or "").lower()
    return (
        "confirm update all" in msg
        or "confirm all updates" in msg
        or ("yes" in msg and "update" in msg and "all" in msg)
        or "proceed with updates" in msg
    )


def _is_update_cancel_message(user_message: str) -> bool:
    msg = (user_message or "").lower()
    return "cancel update" in msg or "stop update" in msg or "do not update" in msg


def _is_checkpoint_approve_message(user_message: str) -> bool:
    msg = (user_message or "").lower()
    return "approve checkpoint" in msg or "accept tradeoff" in msg or "approve cost override" in msg


def _is_checkpoint_reject_message(user_message: str) -> bool:
    msg = (user_message or "").lower()
    return "reject checkpoint" in msg or "revise input" in msg or "do not approve" in msg


def _build_update_plan_from_context(context: dict[str, Any]) -> list[str]:
    agents = context.get("agents", {}) if isinstance(context, dict) else {}
    available = set(agents.keys()) if isinstance(agents, dict) else set()
    tool_map = {
        "diagram": "generate_diagram",
        "waf": "generate_waf",
        "terraform": "generate_terraform",
        "pov": "generate_pov",
        "jep": "generate_jep",
    }
    ordered_paths = ["diagram", "waf", "terraform", "pov", "jep"]
    return [tool_map[path] for path in ordered_paths if path in available]


def _update_tool_args(tool_name: str, change_request: str) -> dict[str, Any]:
    if tool_name == "generate_diagram":
        return {"bom_text": change_request}
    if tool_name == "generate_terraform":
        return {"prompt": f"Apply architecture update: {change_request}"}
    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        return {"feedback": f"Update content for this approved architecture change: {change_request}"}
    return {}


def _render_assumptions(decision_context: dict[str, Any] | None, *, limit: int = 3) -> list[str]:
    if not isinstance(decision_context, dict):
        return []
    assumptions = decision_context.get("assumptions", []) or []
    rendered: list[str] = []
    for assumption in assumptions[:limit]:
        if not isinstance(assumption, dict):
            continue
        statement = str(assumption.get("statement", "") or "").strip()
        if not statement:
            continue
        risk = str(assumption.get("risk", "") or "").strip().lower()
        rendered.append(f"{statement} (risk: {risk or 'low'})")
    return rendered


def _prefer_followup(
    current: dict[str, str] | None,
    candidate: dict[str, str] | None,
) -> dict[str, str] | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    rank = {"blocked": 2, "checkpoint_required": 1}
    current_rank = rank.get(str(current.get("kind", "") or ""), 0)
    candidate_rank = rank.get(str(candidate.get("kind", "") or ""), 0)
    return candidate if candidate_rank > current_rank else current


def _request_references_existing_bom(user_message: str) -> bool:
    msg = str(user_message or "")
    msg_lc = msg.lower()
    if any(
        marker in msg_lc
        for marker in (
            "from this bom",
            "from the bom",
            "using this bom",
            "based on this bom",
            "bom below",
            "attached bom",
            "inline bom",
        )
    ):
        return True
    lines = [line.strip() for line in msg.splitlines() if line.strip()]
    return any(line.count("|") >= 4 for line in lines)


# ── Tool call parser ──────────────────────────────────────────────────────────

_TOOL_RE = re.compile(r'\{\s*"tool"\s*:.+?\}', re.DOTALL)
_TOOL_USE_RE = re.compile(r"<tool_use>\s*(\{.*?\})\s*</tool_use>", re.DOTALL | re.IGNORECASE)


def _parse_tool_call(text: str) -> dict | None:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = _normalize_tool_payload(json.loads(stripped))
            if parsed:
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    m_tool_use = _TOOL_USE_RE.search(text)
    if m_tool_use:
        try:
            parsed = _normalize_tool_payload(json.loads(m_tool_use.group(1)))
            if parsed:
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    m = _TOOL_RE.search(text)
    if not m:
        return None
    try:
        parsed = _normalize_tool_payload(json.loads(m.group()))
        if parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _normalize_tool_payload(parsed: object) -> dict | None:
    if not isinstance(parsed, dict):
        return None
    if "tool" in parsed:
        args = parsed.get("args", {})
        return {"tool": str(parsed.get("tool", "")), "args": args if isinstance(args, dict) else {}}
    if "name" in parsed:
        args = parsed.get("args", parsed.get("arguments", {}))
        return {"tool": str(parsed.get("name", "")), "args": args if isinstance(args, dict) else {}}
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
