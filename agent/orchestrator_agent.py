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
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable

from agent.persistence_objectstore import ObjectStoreBase
import agent.document_store as document_store
import agent.context_store as context_store
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

    prompt = _build_prompt(history, summary, user_message)
    reply = ""
    forced_reply = ""

    def _finalize_turn(reply_text: str) -> dict:
        new_turns.append({"role": "assistant", "content": reply_text, "timestamp": _now()})
        document_store.save_conversation_turns(store, customer_id, new_turns)
        return {
            "reply": reply_text,
            "tool_calls": tool_calls,
            "artifacts": artifacts,
            "history_length": len(history) + len(new_turns),
        }

    pending = _PENDING_UPDATE_WORKFLOWS.get(customer_id)
    if pending:
        if _is_update_cancel_message(user_message):
            _PENDING_UPDATE_WORKFLOWS.pop(customer_id, None)
            return _finalize_turn("Update workflow canceled. No specialist tools were executed.")

        if _is_update_confirm_message(user_message):
            _PENDING_UPDATE_WORKFLOWS.pop(customer_id, None)
            planned_tools = list(pending.get("tools", []) or [])
            change_request = str(pending.get("change_request", "") or "").strip()
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

            executed = ", ".join(planned_tools) if planned_tools else "(none)"
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
                    )
                    for tool in parallel_tools
                ]
            )
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
                    forced_reply = _decision_pushback_text(decision)
                    artifacts.pop(tool_name, None)
                    break

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
                    "content": raw.strip(),
                    "timestamp": _now(),
                    "tool_call": tool_call,
                }
            )
            new_turns.append(tool_turn)

            decision = _extract_blocking_skill_decision(result_data)
            if decision:
                forced_reply = _decision_pushback_text(decision)
                artifacts.pop(tool_name, None)
                break

            # Feed tool result back into next prompt
            prompt = _append_tool_result(prompt, tool_name, result_summary)

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
        (result_data.get("last_critique") or {}).get("overall_pass", True),
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
        s, k = await _call_generate_diagram(args, customer_id, a2a_base_url)
        return s, k, {}

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
) -> dict:
    payload = dict(args or {})
    selected = select_skills_for_call(
        tool_name=tool_name,
        user_message=user_message,
        tool_args=payload,
        max_skills=2,
    )
    if not selected:
        return payload

    block_parts: list[str] = [
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
    skill_block = "\n".join(block_parts)
    payload["_skill_guidance_block"] = skill_block
    payload["_skill_sections"] = sections
    payload["_skill_versions"] = versions

    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        payload["feedback"] = f"{payload.get('feedback', '')}{skill_block}".strip()
        payload["_skill_injected"] = [s.name for s in selected]
        if model_profile:
            payload["_skill_model_profile"] = model_profile
        return payload
    if tool_name == "generate_terraform":
        payload["prompt"] = f"{payload.get('prompt', '')}{skill_block}".strip()
        payload["_skill_injected"] = [s.name for s in selected]
        if model_profile:
            payload["_skill_model_profile"] = model_profile
        return payload
    if tool_name == "generate_diagram":
        payload["bom_text"] = f"{payload.get('bom_text', '')}\n\n{skill_block}".strip()
        payload["_skill_injected"] = [s.name for s in selected]
        if model_profile:
            payload["_skill_model_profile"] = model_profile
        return payload
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
) -> tuple[str, str, dict]:
    if tool_name not in {"generate_pov", "generate_jep", "generate_waf", "generate_terraform"}:
        return result_summary, artifact_key, result_data

    current_summary = result_summary
    current_key = artifact_key
    current_data = dict(result_data or {})
    critic_history: list[dict[str, Any]] = []
    warnings: list[str] = []
    refinement_count = 0
    passes = False

    while True:
        try:
            critic = await asyncio.to_thread(
                critic_agent.evaluate_tool_result,
                tool_name=tool_name,
                user_message=user_message,
                tool_args=args,
                result_summary=current_summary,
                result_data=current_data,
                text_runner=text_runner,
            )
        except Exception as exc:
            warnings.append(f"critic_error_fail_open: {exc}")
            break

        critic_history.append(critic)
        current_data["last_critique"] = critic
        current_data["critic"] = critic

        if critic.get("overall_pass", True):
            passes = True
            break

        if refinement_count >= max_refinements:
            warnings.append("max_refinements_reached_best_effort")
            remaining = critic.get("issues", []) if isinstance(critic, dict) else []
            if remaining:
                current_summary = (
                    f"{current_summary}\n\nBest-effort note: maximum refinements reached. "
                    "Remaining issues were identified by critic."
                ).strip()
            break

        feedback = _build_critic_feedback(critic)
        if not feedback.strip():
            warnings.append("critic_returned_no_actionable_feedback")
            break

        retry_args = dict(args)
        if tool_name == "generate_terraform":
            retry_args["prompt"] = (
                f"{retry_args.get('prompt', '')}\n\n[Critic Feedback]\n{feedback}\n"
            ).strip()
        elif tool_name == "generate_diagram":
            retry_args["bom_text"] = (
                f"{retry_args.get('bom_text', '')}\n\n[Critic Feedback]\n{feedback}\n"
            ).strip()
        else:
            retry_args["feedback"] = (
                f"{retry_args.get('feedback', '')}\n\n[Critic Feedback]\n{feedback}\n"
            ).strip()
        guidance_block = str(args.get("_skill_guidance_block", "")).strip()
        if guidance_block:
            if tool_name == "generate_terraform":
                retry_args["prompt"] = (
                    f"{retry_args.get('prompt', '')}\n\n{guidance_block}"
                ).strip()
            elif tool_name == "generate_diagram":
                retry_args["bom_text"] = (
                    f"{retry_args.get('bom_text', '')}\n\n{guidance_block}"
                ).strip()
            else:
                retry_args["feedback"] = (
                    f"{retry_args.get('feedback', '')}\n\n{guidance_block}"
                ).strip()

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
    current_data["critic_history"] = critic_history
    if critic_history:
        current_data["last_critique"] = critic_history[-1]
    if warnings:
        current_data["warnings"] = list(warnings)
    if not passes and critic_history:
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
    }
    if tool_name == "generate_jep":
        trace["jep_state"] = result_data.get("jep_state", {})
        trace["reason_codes"] = list(result_data.get("reason_codes", []) or [])
        trace["required_next_step"] = str(result_data.get("required_next_step", "") or "")
        trace["lock_outcome"] = str(result_data.get("lock_outcome", "") or "")
    return trace


async def _call_generate_diagram(
    args: dict,
    customer_id: str,
    a2a_base_url: str,
) -> tuple[str, str]:
    """Call the drawing agent via A2A v1.0 /message:send."""
    try:
        import httpx
    except ImportError:
        return "httpx not installed — cannot call diagram agent.", ""

    bom_text = args.get("bom_text", "")
    message_text = bom_text if bom_text.strip() else "Generate a diagram for this engagement."

    payload = {
        "jsonrpc": "2.0",
        "id": f"orch-{_now()}",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": message_text}],
                "contextId": customer_id,
            },
            "skill": "generate_diagram",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{a2a_base_url}/message:send", json=payload)
        data = resp.json()
        result = data.get("result", {})
        status = result.get("status", "UNKNOWN")
        task_id = result.get("id", "")
        if status == "COMPLETED":
            artifacts = result.get("artifacts", [])
            for art in artifacts:
                if art.get("name") == "drawio_key":
                    parts = art.get("parts", [{}])
                    key = (parts[0].get("data") or {}).get("key", "")
                    if key:
                        return f"Diagram generated. Key: {key}", key
            return f"Diagram generated (task {task_id}).", ""
        if status in ("WORKING", "SUBMITTED"):
            return f"Diagram generation started (task {task_id}). Poll /tasks/{task_id}.", ""
        return f"Diagram generation returned status={status}.", ""
    except Exception as exc:
        logger.warning("Diagram A2A call failed: %s", exc)
        return f"Diagram generation failed: {exc}", ""


# ── Prompt assembly ───────────────────────────────────────────────────────────

def _build_prompt(history: list[dict], summary: str, user_message: str) -> str:
    parts: list[str] = []

    if summary:
        parts.append(f"[Prior conversation summary]\n{summary}\n")

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


def _parallel_plan_for_message(user_message: str) -> list[dict]:
    """
    Plan safe concurrent tool calls from explicit SA intent.
    """
    msg = user_message.lower()
    wants_bom = "bom" in msg or "bill of materials" in msg
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
