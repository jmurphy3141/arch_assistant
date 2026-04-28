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
from agent.skill_loader import discover_skills, select_skills_for_call
from agent.bom_service import get_shared_bom_service, new_trace_id
from agent.reference_architecture import (
    build_reference_context_lines,
    select_reference_architecture,
    select_standards_bundle,
)

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
_ARCHITECTURE_TOOLS = {
    "generate_diagram",
    "generate_bom",
    "generate_pov",
    "generate_jep",
    "generate_waf",
    "generate_terraform",
    "get_document",
}
_MANDATORY_SKILL_FALLBACKS = {
    "generate_diagram": ("diagram_for_oci", "orchestrator"),
    "generate_bom": ("oci_bom_expert", "orchestrator"),
    "generate_pov": ("oci_customer_pov_writer", "orchestrator"),
    "generate_jep": ("oci_jep_writer", "orchestrator"),
    "generate_waf": ("oci_waf_reviewer", "orchestrator"),
    "generate_terraform": ("terraform_for_oci", "orchestrator"),
    "get_document": ("orchestrator",),
}


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

    async def _run_generation_step(
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        scenario_label: str = "",
    ) -> dict[str, Any]:
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
        call = {
            "tool": tool_name,
            "args": tool_args,
            "result_summary": result_summary,
            "result_data": result_data,
            "artifact_key": artifact_key,
        }
        if scenario_label:
            call["scenario_label"] = scenario_label
        tool_calls.append(call)
        new_turns.append(
            {
                "role": "tool",
                "tool": tool_name,
                "result_summary": result_summary,
                "timestamp": _now(),
                **({"scenario_label": scenario_label} if scenario_label else {}),
            }
        )
        if artifact_key:
            artifacts[tool_name] = artifact_key
        return call

    pending_checkpoint = context_store.get_pending_checkpoint(context)
    if pending_checkpoint and str(pending_checkpoint.get("type", "") or "") == "specialist_questions":
        specialist_reply, specialist_call, specialist_artifact = await _handle_pending_specialist_questions(
            pending_checkpoint=pending_checkpoint,
            user_message=user_message,
            context=context,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
            max_refinements=max_refinements,
        )
        if specialist_reply:
            if specialist_call:
                tool_calls.append(specialist_call)
                if specialist_artifact:
                    artifacts[specialist_call["tool"]] = specialist_artifact
                new_turns.append(
                    {
                        "role": "tool",
                        "tool": specialist_call["tool"],
                        "result_summary": specialist_call["result_summary"],
                        "timestamp": _now(),
                    }
                )
            return _finalize_turn(specialist_reply)

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
    context_store.set_archie_decision_state(
        context,
        constraints=dict(decision_context.get("constraints", {}) or {}),
        assumptions=list(decision_context.get("assumptions", []) or []),
    )
    await asyncio.to_thread(context_store.write_context, store, customer_id, context)
    prompt = _build_prompt(
        history,
        summary,
        user_message,
        decision_context=decision_context,
        pending_checkpoint=context_store.get_pending_checkpoint(context),
    )

    if _is_recall_intent(user_message) and not requested_tools:
        return _finalize_turn(_build_recall_reply(context))

    pending = context_store.get_pending_update(context) or _PENDING_UPDATE_WORKFLOWS.get(customer_id)
    if pending:
        if _is_update_cancel_message(user_message):
            _PENDING_UPDATE_WORKFLOWS.pop(customer_id, None)
            context_store.clear_pending_update(context)
            context_store.append_change_record(
                context,
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": _now(),
                    "status": "canceled",
                    "change_request": str(pending.get("change_request", "") or "").strip(),
                    "impacted_tools": list(pending.get("tools", []) or []),
                },
            )
            await asyncio.to_thread(context_store.write_context, store, customer_id, context)
            return _finalize_turn("Update workflow canceled. No specialist tools were executed.")

        if _is_update_confirm_message(user_message):
            _PENDING_UPDATE_WORKFLOWS.pop(customer_id, None)
            context_store.clear_pending_update(context)
            planned_tools = list(pending.get("tools", []) or [])
            change_request = str(pending.get("change_request", "") or "").strip()
            change_record = {
                "id": str(pending.get("id", "") or str(uuid.uuid4())),
                "timestamp": _now(),
                "status": "applied",
                "change_request": change_request,
                "impacted_tools": planned_tools,
                "superseded_decision_ids": _infer_superseded_decision_ids(context, change_request),
            }
            context_store.append_change_record(context, change_record)
            context_store.append_update_batch(context, change_record)
            await asyncio.to_thread(context_store.write_context, store, customer_id, context)
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
                "Confirmed. I executed the approved update sequence in order using the Archie dependency plan:\n"
                f"- Executed tools: {executed}\n"
                "- Review the tool outputs above and confirm if any additional updates are needed."
            )

        planned = ", ".join(pending.get("tools", [])) or "(none)"
        return _finalize_turn(
            "An Archie update plan is waiting for confirmation.\n"
            f"- Planned tools: {planned}\n"
            "- Reply `confirm update all` to proceed or `cancel update` to stop."
        )

    if _is_change_update_intent(user_message):
        ctx = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        planned_tools = _build_update_plan_from_context(ctx, change_request=user_message)
        if not planned_tools:
            return _finalize_turn(
                "I don't see existing generated artifacts for this customer yet, so I can't build an impact update plan.\n"
                "Generate a diagram/related artifacts first, then request a full update."
            )

        change_batch = {
            "id": str(uuid.uuid4()),
            "tools": planned_tools,
            "change_request": user_message.strip(),
            "created_at": _now(),
            "status": "pending_confirmation",
            "impacted_tools": planned_tools,
        }
        _PENDING_UPDATE_WORKFLOWS[customer_id] = dict(change_batch)
        context_store.set_pending_update(ctx, change_batch)
        context_store.append_change_record(
            ctx,
            {
                "id": change_batch["id"],
                "timestamp": change_batch["created_at"],
                "status": "pending_confirmation",
                "change_request": change_batch["change_request"],
                "impacted_tools": planned_tools,
            },
        )
        await asyncio.to_thread(context_store.write_context, store, customer_id, ctx)
        ordered = "\n".join(f"{idx}. {tool}" for idx, tool in enumerate(planned_tools, start=1))
        return _finalize_turn(
            "I compared the new information against the latest recorded Archie decisions and artifacts. "
            "These outputs are impacted and would be regenerated in this order:\n"
            f"{ordered}\n\n"
            "Reply `confirm update all` to execute, or `cancel update`."
        )

    workflow_plan = _generation_workflow_plan_for_message(
        user_message=user_message,
        requested_tools=requested_tools,
        context=context,
        decision_context=decision_context,
    )
    if workflow_plan:
        if workflow_plan.get("status") == "ask":
            return _finalize_turn(str(workflow_plan.get("message", "") or "").strip())

        scenarios = list(workflow_plan.get("scenarios", []) or [])
        sequence = list(workflow_plan.get("sequence", []) or [])
        bom_feeds_diagram = bool(workflow_plan.get("bom_feeds_diagram", False))

        for scenario in scenarios:
            scenario_label = str(scenario.get("label", "") or "Scenario").strip()
            scenario_text = str(scenario.get("text", "") or user_message).strip()
            last_bom_call: dict[str, Any] | None = None
            diagram_available_this_scenario = _has_architecture_definition(context)

            for tool_name in sequence:
                if tool_name == "generate_bom":
                    tool_args = {
                        "prompt": _build_scenario_bom_prompt(
                            scenario_label=scenario_label,
                            scenario_text=scenario_text,
                            user_message=user_message,
                        ),
                        "_bom_context_source": "scenario_request",
                        "_bom_grounded_from_context": True,
                    }
                    call = await _run_generation_step(tool_name, tool_args, scenario_label=scenario_label)
                    last_bom_call = call
                    if (
                        "generate_diagram" in sequence
                        and bom_feeds_diagram
                        and not _bom_result_can_feed_diagram(
                            str(call.get("result_summary", "") or ""),
                            call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {},
                        )
                    ):
                        break
                    continue

                if tool_name == "generate_diagram":
                    if bom_feeds_diagram and last_bom_call is not None:
                        tool_args = {
                            "bom_text": _build_diagram_bom_text_from_bom_result(
                                scenario_label=scenario_label,
                                scenario_text=scenario_text,
                                user_message=user_message,
                                bom_summary=str(last_bom_call.get("result_summary", "") or ""),
                                bom_result_data=last_bom_call.get("result_data", {})
                                if isinstance(last_bom_call.get("result_data"), dict)
                                else {},
                            )
                        }
                    else:
                        tool_args = {"bom_text": scenario_text or user_message.strip()}
                    call = await _run_generation_step(tool_name, tool_args, scenario_label=scenario_label)
                    diagram_available_this_scenario = bool(call.get("artifact_key")) or not _workflow_call_is_blocked(call)
                    if not diagram_available_this_scenario:
                        break
                    continue

                if tool_name == "generate_waf":
                    if not diagram_available_this_scenario:
                        break
                    tool_args = {"feedback": _build_downstream_workflow_prompt(tool_name, scenario_text, user_message)}
                    call = await _run_generation_step(tool_name, tool_args, scenario_label=scenario_label)
                    if _workflow_call_is_blocked(call):
                        break
                    continue

                if tool_name == "generate_terraform":
                    if not diagram_available_this_scenario:
                        break
                    tool_args = {"prompt": _build_downstream_workflow_prompt(tool_name, scenario_text, user_message)}
                    call = await _run_generation_step(tool_name, tool_args, scenario_label=scenario_label)
                    if _workflow_call_is_blocked(call):
                        break
                    continue

                if tool_name in {"generate_pov", "generate_jep"}:
                    tool_args = {"feedback": _build_downstream_workflow_prompt(tool_name, scenario_text, user_message)}
                    call = await _run_generation_step(tool_name, tool_args, scenario_label=scenario_label)
                    if _workflow_call_is_blocked(call):
                        break

        return _finalize_turn(
            _build_generation_workflow_reply(
                workflow_plan,
                tool_calls,
                decision_context=decision_context,
            )
        )

    paired_bom_diagram_plan = _bom_diagram_pair_plan_for_message(user_message)
    if paired_bom_diagram_plan:
        for scenario in paired_bom_diagram_plan:
            scenario_label = str(scenario.get("label", "") or "Scenario").strip()
            scenario_text = str(scenario.get("text", "") or user_message).strip()
            bom_args = {
                "prompt": _build_scenario_bom_prompt(
                    scenario_label=scenario_label,
                    scenario_text=scenario_text,
                    user_message=user_message,
                ),
                "_bom_context_source": "scenario_request",
                "_bom_grounded_from_context": True,
            }
            bom_summary, bom_artifact_key, bom_result_data = await _execute_tool(
                "generate_bom",
                bom_args,
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
            notify("tool:generate_bom", customer_id, bom_summary)
            bom_call = {
                "tool": "generate_bom",
                "args": bom_args,
                "result_summary": bom_summary,
                "result_data": bom_result_data,
                "scenario_label": scenario_label,
                "artifact_key": bom_artifact_key,
            }
            tool_calls.append(bom_call)
            new_turns.append(
                {
                    "role": "tool",
                    "tool": "generate_bom",
                    "result_summary": bom_summary,
                    "timestamp": _now(),
                    "scenario_label": scenario_label,
                }
            )
            if bom_artifact_key:
                artifacts["generate_bom"] = bom_artifact_key

            if not _bom_result_can_feed_diagram(bom_summary, bom_result_data):
                continue

            diagram_args = {
                "bom_text": _build_diagram_bom_text_from_bom_result(
                    scenario_label=scenario_label,
                    scenario_text=scenario_text,
                    user_message=user_message,
                    bom_summary=bom_summary,
                    bom_result_data=bom_result_data,
                )
            }
            diagram_summary, diagram_artifact_key, diagram_result_data = await _execute_tool(
                "generate_diagram",
                diagram_args,
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
            notify("tool:generate_diagram", customer_id, diagram_summary)
            diagram_call = {
                "tool": "generate_diagram",
                "args": diagram_args,
                "result_summary": diagram_summary,
                "result_data": diagram_result_data,
                "scenario_label": scenario_label,
                "artifact_key": diagram_artifact_key,
            }
            tool_calls.append(diagram_call)
            new_turns.append(
                {
                    "role": "tool",
                    "tool": "generate_diagram",
                    "result_summary": diagram_summary,
                    "timestamp": _now(),
                    "scenario_label": scenario_label,
                }
            )
            if diagram_artifact_key:
                artifacts["generate_diagram"] = diagram_artifact_key

        return _finalize_turn(
            _build_paired_bom_diagram_reply(
                paired_bom_diagram_plan,
                tool_calls,
                decision_context=decision_context,
            )
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
                tool_name.startswith("generate_")
                and not requested_tools
                and _is_architecture_chat_only_request(user_message, decision_context)
            ):
                reply = _build_architecture_chat_reply(
                    user_message=user_message,
                    decision_context=decision_context,
                )
                break
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
                reply = _build_parallel_reply(tool_calls, decision_context=decision_context)
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
    context: dict[str, Any] | None = None
    if tool_name == "generate_bom":
        context = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        args = _prepare_bom_tool_args(
            args=args,
            user_message=user_message,
            context=context,
            decision_context=decision_context,
        )
    if tool_name in {"save_notes", "generate_diagram", "generate_bom", "generate_pov", "generate_jep", "generate_waf", "generate_terraform"}:
        context = context or await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        args = _hydrate_tool_args_from_context(
            tool_name=tool_name,
            args=args,
            context=context,
            decision_context=decision_context,
            user_message=user_message,
        )
    if (
        tool_name == "generate_diagram"
        and context is not None
        and not _diagram_has_sufficient_context(
            context=context,
            args=args,
            user_message=user_message,
        )
    ):
        return (
            "Please upload or paste BOM/resource details first, or describe the workload/components you want in the diagram.",
            "",
            {},
        )
    if (
        tool_name == "generate_pov"
        and context is not None
        and not _pov_has_sufficient_context(
            context=context,
            decision_context=decision_context,
            args=args,
            user_message=user_message,
        )
    ):
        return await _mediate_specialist_questions(
            tool_name=tool_name,
            args=args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
            user_message=user_message,
            max_refinements=max_refinements,
            decision_context=dict(decision_context or {}),
            result_summary="POV clarification required before Archie drafts the customer narrative.",
            artifact_key="",
            result_data={"questions": _pov_targeted_questions(), "decision_context": dict(decision_context or {})},
            context=context,
        )
    if (
        tool_name == "generate_terraform"
        and context is not None
        and _has_architecture_definition(context)
        and not _terraform_scope_is_bounded(
            context=context,
            args=args,
            decision_context=decision_context,
            user_message=user_message,
        )
    ):
        return await _mediate_specialist_questions(
            tool_name=tool_name,
            args=args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
            user_message=user_message,
            max_refinements=max_refinements,
            decision_context=dict(decision_context or {}),
            result_summary="Terraform clarification required before Archie drafts the implementation bundle.",
            artifact_key="",
            result_data={"questions": _terraform_targeted_questions(), "decision_context": dict(decision_context or {})},
            context=context,
        )
    expert_mode = _build_expert_mode_metadata(
        tool_name=tool_name,
        args=args,
        user_message=user_message,
        decision_context=decision_context,
    )
    if _is_architecture_tool(tool_name) and not str(expert_mode.get("standards_bundle_version", "") or "").strip():
        return (
            "Architecture expert mode is blocked because no Oracle standards bundle is selected.",
            "",
            {
                "expert_mode": expert_mode,
                "standards_bundle_version": "",
                "reference_mode": "blocked",
            },
        )
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
        expert_mode=expert_mode,
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
    merged_decision_context = _merge_decision_context(decision_context, result_data.get("decision_context"))
    merged_constraint_tags = decision_context_builder.derive_constraint_tags(merged_decision_context)
    applied_skills = list(enriched_args.get("_skill_injected", []) or [])
    result_data["skill_injected"] = bool(applied_skills)
    result_data["applied_skills"] = applied_skills
    result_data["skill_sections"] = dict(enriched_args.get("_skill_sections", {}) or {})
    result_data["skill_versions"] = dict(enriched_args.get("_skill_versions", {}) or {})
    result_data["skill_model_profile"] = str(enriched_args.get("_skill_model_profile", "") or "")
    result_data["decision_context"] = merged_decision_context
    result_data["constraint_tags"] = merged_constraint_tags or list(enriched_args.get("_constraint_tags", []) or [])
    result_data["expert_mode"] = dict(enriched_args.get("_expert_mode", {}) or {})
    result_data["standards_bundle_version"] = str(enriched_args.get("_standards_bundle_version", "") or "")
    result_data["reference_architecture"] = dict(enriched_args.get("_reference_architecture", {}) or {})
    result_data["reference_family"] = str(enriched_args.get("_reference_family", "") or result_data.get("reference_family", "") or "")
    result_data["reference_confidence"] = float(enriched_args.get("_reference_confidence", result_data.get("reference_confidence", 0)) or 0)
    result_data["reference_mode"] = str(enriched_args.get("_reference_mode", result_data.get("reference_mode", "")) or "")
    if preflight_decision:
        result_data["skill_preflight"] = asdict(preflight_decision)

    if tool_name == "save_notes":
        _record_saved_note_context(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            note_key=artifact_key,
            note_text=str(args.get("text", "") or ""),
            decision_context=merged_decision_context,
        )

    if tool_name.startswith("generate_") and not bool(enriched_args.get("_archie_question_retry")):
        context = context or await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        result_summary, artifact_key, result_data = await _mediate_specialist_questions(
            tool_name=tool_name,
            args=enriched_args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
            user_message=user_message,
            max_refinements=max_refinements,
            decision_context=merged_decision_context,
            result_summary=result_summary,
            artifact_key=artifact_key,
            result_data=result_data,
            context=context,
        )

    if isinstance(result_data.get("archie_question_bundle"), dict):
        result_data["trace"] = _build_tool_trace(
            tool_name=tool_name,
            result_data=result_data,
            max_refinements=max_refinements,
        )
        return result_summary, artifact_key, result_data
    if isinstance(result_data.get("archie_auto_answers"), list):
        return result_summary, artifact_key, result_data

    if path_id:
        postflight_decision = _SKILL_ENGINE.postflight_check(
            path_id=path_id,
            tool_result=result_summary,
            artifacts={"artifact_key": artifact_key},
            context_summary=context_summary,
            tool_args=enriched_args,
            result_data=result_data,
        )
        result_data["skill_postflight"] = asdict(postflight_decision)
        if postflight_decision.status == "block":
            _record_tool_decision_state(
                store=store,
                customer_id=customer_id,
                customer_name=customer_name,
                tool_name=tool_name,
                artifact_key="",
                decision_context=merged_decision_context,
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
            decision_context=merged_decision_context,
        )

    _record_tool_decision_state(
        store=store,
        customer_id=customer_id,
        customer_name=customer_name,
        tool_name=tool_name,
        artifact_key=artifact_key,
        decision_context=merged_decision_context,
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

        feedback = str(args.get("_user_request_text", "") or args.get("feedback", "") or "")
        result = await asyncio.to_thread(
            pov_agent.generate_pov,
            customer_id,
            customer_name,
            store,
            text_runner,
            feedback=feedback,
            architect_brief=dict(args.get("_architect_brief", {}) or {}),
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
        response = await _execute_bom_tool_request(
            args=args,
            text_runner=text_runner,
            model_id="orchestrator-generate_bom",
        )
        summary = _summarize_bom_tool_response(response)
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


def _bom_response_needs_refresh(response: dict[str, Any] | None) -> bool:
    if not isinstance(response, dict):
        return False
    trace = response.get("trace", {}) if isinstance(response.get("trace"), dict) else {}
    if trace.get("cache_ready") is False:
        return True
    return "not ready" in str(response.get("reply", "") or "").lower()


def _summarize_bom_tool_response(response: dict[str, Any] | None) -> str:
    if not isinstance(response, dict):
        return "BOM response generated."
    summary = str(response.get("reply", "")).strip() or "BOM response generated."
    if response.get("error_code") == "bom_data_init_failed":
        return summary
    result_type = str(response.get("type", "normal") or "normal")
    lowered = summary.lower()
    if result_type == "final" and not lowered.startswith("final bom prepared"):
        return f"Final BOM prepared. {summary}"
    if result_type == "question" and not lowered.startswith("bom clarification required"):
        return f"BOM clarification required. {summary}"
    if "not ready" in lowered and not lowered.startswith("bom data not ready"):
        return f"BOM data not ready. {summary}"
    return summary


async def _execute_bom_tool_request(
    *,
    args: dict[str, Any],
    text_runner: Callable,
    model_id: str,
) -> dict[str, Any]:
    trace_id = new_trace_id()
    context_source = str(args.get("_bom_context_source", "") or "direct_request")
    direct_reply = str(args.get("_bom_direct_reply", "") or "").strip()
    if direct_reply:
        return {
            "type": "question",
            "reply": direct_reply,
            "trace_id": trace_id,
            "trace": {
                "model_id": model_id,
                "type": "question",
                "repair_attempts": 0,
                "cache_ready": None,
                "cache_source": "unknown",
                "latency_ms": 0,
                "bom_cache_status_before_attempt": "not_checked",
                "bom_cache_refresh_attempted": False,
                "bom_cache_refresh_status": "not_attempted",
                "bom_context_source": context_source,
                "bom_retry_count": 0,
                "bom_retry_succeeded": False,
            },
            "bom_context_source": context_source,
        }

    clean_request = str(args.get("_user_request_text", "") or args.get("prompt", "") or "").strip()
    architect_brief = dict(args.get("_architect_brief", {}) or {})
    prompt = _compose_specialist_request_text(
        clean_request=clean_request or "Generate a BOM from current request context.",
        architect_brief=architect_brief if context_source == "direct_request" else {},
    )
    if not prompt:
        prompt = "Generate a BOM from current request context."
    service = get_shared_bom_service()
    cache_before = await asyncio.to_thread(service.health)
    cache_status_before_attempt = "ready" if cache_before.get("ready") else "not_ready"
    response = await asyncio.to_thread(
        service.chat,
        message=prompt,
        conversation=[],
        trace_id=trace_id,
        model_id=model_id,
        text_runner=text_runner,
    )

    refresh_attempted = False
    refresh_status = "not_attempted"
    retry_count = 0
    retry_succeeded = False
    if _bom_response_needs_refresh(response):
        refresh_attempted = True
        try:
            refresh_result = await asyncio.to_thread(service.refresh_data)
            refresh_status = "succeeded" if refresh_result.get("ready") else "failed"
        except Exception as exc:
            logger.warning("Archie BOM cache refresh failed: %s", exc)
            refresh_status = "failed"
        if refresh_status == "succeeded":
            retry_count = 1
            response = await asyncio.to_thread(
                service.chat,
                message=prompt,
                conversation=[],
                trace_id=trace_id,
                model_id=model_id,
                text_runner=text_runner,
            )
            retry_succeeded = not _bom_response_needs_refresh(response)
        if refresh_status != "succeeded" or not retry_succeeded:
            response = {
                "type": "normal",
                "reply": (
                    "I could not initialize the internal OCI BOM pricing data for this chat request. "
                    "Retry in a moment; if it persists, the BOM service data source needs attention."
                ),
                "trace_id": trace_id,
                "error_code": "bom_data_init_failed",
                "trace": {
                    "model_id": model_id,
                    "type": "normal",
                    "repair_attempts": 0,
                    "cache_ready": False,
                    "cache_source": "none",
                    "latency_ms": 0,
                },
            }

    trace = response.get("trace", {}) if isinstance(response.get("trace"), dict) else {}
    trace.update(
        {
            "bom_cache_status_before_attempt": cache_status_before_attempt,
            "bom_cache_refresh_attempted": refresh_attempted,
            "bom_cache_refresh_status": refresh_status,
            "bom_context_source": context_source,
            "bom_retry_count": retry_count,
            "bom_retry_succeeded": retry_succeeded,
        }
    )
    response["trace"] = trace
    response["bom_context_source"] = context_source
    return response


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


_BOM_DEICTIC_MARKERS: tuple[str, ...] = (
    "for this",
    "from this",
    "use this",
    "use that",
    "for that",
    "from that",
    "this diagram",
    "that diagram",
    "previous diagram",
    "latest diagram",
)


def _is_bom_deictic_followup(prompt: str, user_message: str) -> bool:
    combined = " ".join(part.strip().lower() for part in (user_message, prompt) if str(part).strip())
    if not combined:
        return False
    if "bom" not in combined and "bill of materials" not in combined and "cost" not in combined and "pricing" not in combined:
        return False
    return any(marker in combined for marker in _BOM_DEICTIC_MARKERS)


def _has_meaningful_decision_context(decision_context: dict[str, Any] | None) -> bool:
    if not isinstance(decision_context, dict):
        return False
    if str(decision_context.get("goal", "") or "").strip():
        return True
    if list(decision_context.get("success_criteria", []) or []):
        return True
    constraints = dict(decision_context.get("constraints", {}) or {})
    return any(value not in (None, "", [], {}) for value in constraints.values())


def _diagram_context_supports_bom(
    diagram_ctx: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> bool:
    if not isinstance(diagram_ctx, dict):
        return False
    if not str(diagram_ctx.get("diagram_key", "") or "").strip():
        return False
    if int(diagram_ctx.get("node_count", 0) or 0) > 0:
        return True
    for key in ("deployment_summary", "spec_summary", "reference_family", "decision_context_summary", "summary"):
        if str(diagram_ctx.get(key, "") or "").strip():
            return True
    if list(diagram_ctx.get("assumptions_used", []) or []):
        return True
    return _has_meaningful_decision_context(decision_context)


def _format_bom_followup_clarification() -> str:
    return (
        "I can build the BOM, but `this` is not grounded to a prior diagram or workload yet.\n"
        "Please share the workload or diagram context plus rough sizing for OCPU, memory, storage, "
        "and any load balancer, database, or Object Storage requirements."
    )


def _summarize_diagram_scope(diagram_ctx: dict[str, Any]) -> str:
    parts: list[str] = []
    deployment_summary = str(diagram_ctx.get("deployment_summary", "") or "").strip()
    if deployment_summary:
        parts.append(deployment_summary)
    reference_family = str(diagram_ctx.get("reference_family", "") or "").strip()
    if reference_family:
        parts.append(f"reference family={reference_family}")
    node_count = int(diagram_ctx.get("node_count", 0) or 0)
    if node_count > 0:
        parts.append(f"node_count={node_count}")
    spec_summary = str(diagram_ctx.get("spec_summary", "") or "").strip()
    if spec_summary and spec_summary not in parts:
        parts.append(spec_summary)
    return ", ".join(parts)


def _build_bom_followup_prompt(
    *,
    prompt: str,
    diagram_ctx: dict[str, Any],
    decision_context: dict[str, Any] | None,
) -> str:
    current_decision_context = dict(decision_context or {})
    lines = [
        "Generate BOM for the latest OCI architecture diagram.",
        "Treat this as a best-effort OCI BOM draft/finalization request, not a generic clarification-only question.",
        "Use existing BOM draft defaults for missing numeric sizing and surface assumptions or checkpoint items instead of refusing the draft.",
    ]
    cleaned_prompt = _strip_injected_guidance_blocks(prompt).strip()
    if cleaned_prompt:
        lines.append(f"User follow-up: {cleaned_prompt}")
    lines.append("[Latest Diagram Context]")
    lines.append(f"- diagram_key: {diagram_ctx.get('diagram_key', '')}")
    scope_summary = _summarize_diagram_scope(diagram_ctx)
    if scope_summary:
        lines.append(f"- scope_summary: {scope_summary}")
    prior_decision_summary = str(diagram_ctx.get("decision_context_summary", "") or "").strip()
    if prior_decision_summary:
        lines.append(f"- prior_decision_context: {prior_decision_summary}")
    if _has_meaningful_decision_context(current_decision_context):
        lines.append(
            f"- current_decision_context: {decision_context_builder.summarize_decision_context(current_decision_context)}"
        )
    assumptions = _merge_assumption_lists(
        list(diagram_ctx.get("assumptions_used", []) or []),
        list(current_decision_context.get("assumptions", []) or []),
    )
    if assumptions:
        lines.append("- assumptions already applied:")
        lines.extend(
            f"  - {item.get('statement', '').strip()} (risk: {item.get('risk', 'low')})"
            for item in assumptions
            if str(item.get("statement", "")).strip()
        )
    lines.append("[End Latest Diagram Context]")
    return "\n".join(lines).strip()


def _prepare_bom_tool_args(
    *,
    args: dict[str, Any] | None,
    user_message: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(args or {})
    prompt = str(payload.get("prompt", "") or "").strip() or str(user_message or "").strip()
    payload["prompt"] = prompt or "Generate a BOM from current request context."
    payload["_bom_context_source"] = str(payload.get("_bom_context_source", "") or "direct_request")

    if bool(payload.get("_bom_grounded_from_context")):
        return payload

    if not _is_bom_deictic_followup(prompt, user_message):
        return payload

    diagram_ctx = dict(((context or {}).get("agents", {}) or {}).get("diagram", {}) or {})
    if _diagram_context_supports_bom(diagram_ctx, decision_context):
        payload["prompt"] = _build_bom_followup_prompt(
            prompt=prompt,
            diagram_ctx=diagram_ctx,
            decision_context=decision_context,
        )
        payload["_bom_context_source"] = "latest_diagram"
        payload["_bom_grounded_from_context"] = True
        return payload

    payload["_bom_direct_reply"] = _format_bom_followup_clarification()
    payload["_bom_context_source"] = "unresolved_followup"
    payload["_bom_grounded_from_context"] = False
    return payload


def _summarize_note_text(note_text: str, *, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", str(note_text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _record_saved_note_context(
    *,
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    note_key: str,
    note_text: str,
    decision_context: dict[str, Any] | None,
) -> None:
    context = context_store.read_context(store, customer_id, customer_name)
    note_summary = _summarize_note_text(note_text)
    archie = context_store.get_archie_state(context)
    prior_summary = str(archie.get("engagement_summary", "") or "").strip()
    if note_summary and note_summary not in prior_summary:
        merged = f"{prior_summary} {note_summary}".strip() if prior_summary else note_summary
        context_store.set_archie_engagement_summary(context, _summarize_note_text(merged, limit=480), note_summary=note_summary)
    elif note_summary:
        context_store.set_archie_engagement_summary(context, prior_summary or note_summary, note_summary=note_summary)
    context_store.set_archie_decision_state(
        context,
        constraints=dict((decision_context or {}).get("constraints", {}) or {}),
        assumptions=list((decision_context or {}).get("assumptions", []) or []),
    )
    context_store.append_change_record(
        context,
        {
            "id": str(uuid.uuid4()),
            "timestamp": _now(),
            "status": "recorded",
            "change_request": note_summary or "Notes saved.",
            "source": "save_notes",
            "note_key": note_key,
            "impacted_tools": [],
        },
    )
    context_store.write_context(store, customer_id, context)


def _build_archie_specialist_context(
    context: dict[str, Any] | None,
    *,
    decision_context: dict[str, Any] | None,
) -> str:
    if not isinstance(context, dict):
        return ""
    archie = context_store.get_archie_state(context)
    lines: list[str] = []
    engagement_summary = str(archie.get("engagement_summary", "") or "").strip()
    if engagement_summary:
        lines.append(f"Engagement summary: {engagement_summary}")
    latest_notes_summary = str(archie.get("latest_notes_summary", "") or "").strip()
    if latest_notes_summary and latest_notes_summary != engagement_summary:
        lines.append(f"Latest notes: {latest_notes_summary}")
    resolved = archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []
    if resolved:
        lines.append("Resolved Archie decisions:")
        for item in resolved[-5:]:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("question_id", "") or item.get("id", "") or "question").strip()
            answer = str(item.get("final_answer", "") or item.get("suggested_answer", "") or "").strip()
            if question_id and answer:
                lines.append(f"- {question_id}: {answer}")
    if isinstance(decision_context, dict) and decision_context:
        lines.append(decision_context_builder.summarize_decision_context(decision_context))
    return "\n".join(line for line in lines if str(line).strip()).strip()


def _tool_primary_input_key(tool_name: str) -> str | None:
    if tool_name == "generate_diagram":
        return "bom_text"
    if tool_name == "generate_bom":
        return "prompt"
    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        return "feedback"
    if tool_name == "generate_terraform":
        return "prompt"
    return None


def _clean_tool_user_request(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    user_message: str,
) -> str:
    payload = dict(args or {})
    key = _tool_primary_input_key(tool_name)
    raw = ""
    if key:
        raw = str(payload.get(key, "") or "")
    if not raw.strip():
        raw = str(user_message or "")
    return _strip_injected_guidance_blocks(raw).strip()


def _tool_goal_label(tool_name: str) -> str:
    labels = {
        "generate_diagram": "Architecture diagram",
        "generate_bom": "Bill of materials",
        "generate_pov": "Customer POV draft",
        "generate_jep": "Joint execution plan",
        "generate_waf": "Well-Architected review",
        "generate_terraform": "Terraform draft",
    }
    return labels.get(tool_name, tool_name)


def _build_architect_brief(
    *,
    tool_name: str,
    user_request: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    current_decision_context = dict(decision_context or {})
    assumptions = _merge_assumption_lists(
        list(current_decision_context.get("assumptions", []) or []),
        [],
    )
    missing_inputs = list(current_decision_context.get("missing_inputs", []) or [])
    success_criteria = list(current_decision_context.get("success_criteria", []) or [])
    architect_context = _build_archie_specialist_context(
        context,
        decision_context=current_decision_context,
    )
    return {
        "tool_name": tool_name,
        "goal": str(current_decision_context.get("goal", "") or user_request or _tool_goal_label(tool_name)),
        "deliverable": _tool_goal_label(tool_name),
        "user_request": user_request,
        "user_notes": user_request,
        "architect_context": architect_context,
        "assumptions": assumptions,
        "missing_inputs": missing_inputs,
        "success_criteria": success_criteria,
        "risk_level": str(current_decision_context.get("risk_level", "") or "low"),
        "assumption_mode": bool(current_decision_context.get("assumption_mode", False)),
        "requires_user_confirmation": bool(current_decision_context.get("requires_user_confirmation", False)),
    }


def _render_architect_brief_text(architect_brief: dict[str, Any] | None) -> str:
    brief = dict(architect_brief or {})
    if not brief:
        return ""
    lines = ["[Architect Brief]"]
    goal = str(brief.get("goal", "") or "").strip()
    if goal:
        lines.append(f"Goal: {goal}")
    deliverable = str(brief.get("deliverable", "") or "").strip()
    if deliverable:
        lines.append(f"Deliverable: {deliverable}")
    user_notes = str(brief.get("user_notes", "") or "").strip()
    if user_notes:
        lines.append(f"User notes/request: {user_notes}")
    architect_context = str(brief.get("architect_context", "") or "").strip()
    if architect_context:
        lines.append("Architect context:")
        lines.append(architect_context)
    assumptions = list(brief.get("assumptions", []) or [])
    if assumptions:
        lines.append("Assumptions:")
        lines.extend(
            f"- {item.get('statement', '').strip()} (risk: {item.get('risk', 'low')})"
            for item in assumptions
            if isinstance(item, dict) and str(item.get("statement", "")).strip()
        )
    success_criteria = [str(item).strip() for item in brief.get("success_criteria", []) or [] if str(item).strip()]
    if success_criteria:
        lines.append("Success criteria:")
        lines.extend(f"- {item}" for item in success_criteria)
    missing_inputs = [str(item).strip() for item in brief.get("missing_inputs", []) or [] if str(item).strip()]
    if missing_inputs:
        lines.append("Missing inputs:")
        lines.extend(f"- {item}" for item in missing_inputs)
    lines.append(f"Risk level: {str(brief.get('risk_level', '') or 'low')}")
    lines.append("[End Architect Brief]")
    return "\n".join(lines)


def _append_archie_context_block(text: str, archie_context: str) -> str:
    if not archie_context.strip():
        return text.strip()
    block = f"[Archie Shared Context]\n{archie_context}\n[End Archie Shared Context]"
    if block in text:
        return text.strip()
    return f"{text.strip()}\n\n{block}".strip()


def _hydrate_tool_args_from_context(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
    user_message: str,
) -> dict[str, Any]:
    payload = dict(args or {})
    clean_request = _clean_tool_user_request(
        tool_name=tool_name,
        args=payload,
        user_message=user_message,
    )
    architect_brief = _build_architect_brief(
        tool_name=tool_name,
        user_request=clean_request,
        context=context,
        decision_context=decision_context,
    )
    payload["_user_request_text"] = clean_request
    payload["_architect_brief"] = architect_brief
    payload["_archie_context_summary"] = str(architect_brief.get("architect_context", "") or "")

    primary_key = _tool_primary_input_key(tool_name)
    if primary_key and clean_request:
        payload[primary_key] = clean_request

    return payload


def _normalize_specialist_question(
    tool_name: str,
    raw_question: Any,
    *,
    index: int,
) -> dict[str, Any] | None:
    if isinstance(raw_question, dict):
        question = str(raw_question.get("question", "") or raw_question.get("prompt", "") or "").strip()
        if not question:
            return None
        return {
            "question_id": str(raw_question.get("id", "") or f"{tool_name}.q{index}"),
            "question": question,
            "blocking": bool(raw_question.get("blocking", True)),
        }
    if isinstance(raw_question, str) and raw_question.strip():
        return {
            "question_id": f"{tool_name}.q{index}",
            "question": raw_question.strip(),
            "blocking": True,
        }
    return None


def _has_architecture_definition(context: dict[str, Any] | None) -> bool:
    agents = (context or {}).get("agents", {}) if isinstance(context, dict) else {}
    diagram = dict((agents or {}).get("diagram", {}) or {})
    return bool(str(diagram.get("diagram_key", "") or "").strip() or str(diagram.get("summary", "") or "").strip())


def _text_has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in markers)


def _pov_has_sufficient_context(
    *,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
    args: dict[str, Any],
    user_message: str,
) -> bool:
    combined = " ".join(
        part
        for part in (
            str(args.get("_user_request_text", "") or ""),
            user_message,
            str((args.get("_architect_brief", {}) or {}).get("architect_context", "") or ""),
            decision_context_builder.summarize_decision_context(decision_context),
            context_store.build_context_summary(context or {}),
        )
        if str(part).strip()
    ).lower()
    business_markers = (
        "industry",
        "customer",
        "business",
        "revenue",
        "outcome",
        "modernize",
        "migration",
        "latency",
        "scale",
        "resilience",
        "retail",
        "healthcare",
        "finance",
    )
    architecture_markers = (
        "oke",
        "kubernetes",
        "database",
        "load balancer",
        "waf",
        "object storage",
        "vcn",
        "private",
        "public",
        "multi-region",
        "autonomous database",
    )
    return _text_has_any_marker(combined, business_markers) and (
        _text_has_any_marker(combined, architecture_markers) or _has_architecture_definition(context)
    )


def _pov_targeted_questions() -> list[dict[str, Any]]:
    return [
        {
            "id": "pov.business_outcomes",
            "question": "What two or three business outcomes should the POV emphasize for this customer?",
            "blocking": True,
        },
        {
            "id": "pov.customer_profile",
            "question": "What customer context should anchor the story: industry, workload type, or strategic initiative?",
            "blocking": True,
        },
        {
            "id": "pov.scope",
            "question": "Should this POV stay high-level executive, or should it call out specific OCI services and deployment scope?",
            "blocking": True,
        },
    ]


def _terraform_scope_is_bounded(
    *,
    context: dict[str, Any] | None,
    args: dict[str, Any],
    decision_context: dict[str, Any] | None,
    user_message: str,
) -> bool:
    return _has_architecture_definition(context) and _terraform_scope_details_are_bounded(
        context=context,
        args=args,
        decision_context=decision_context,
        user_message=user_message,
    )


def _terraform_scope_details_are_bounded(
    *,
    context: dict[str, Any] | None,
    args: dict[str, Any],
    decision_context: dict[str, Any] | None,
    user_message: str,
) -> bool:
    combined = " ".join(
        part
        for part in (
            str(args.get("_user_request_text", "") or ""),
            user_message,
            str((args.get("_architect_brief", {}) or {}).get("architect_context", "") or ""),
            decision_context_builder.summarize_decision_context(decision_context),
            context_store.build_context_summary(context or {}),
        )
        if str(part).strip()
    ).lower()
    module_markers = ("module", "network", "vcn", "oke", "database", "subnet", "load balancer", "waf")
    state_markers = ("remote state", "state backend", "object storage backend", "terraform cloud", "local state")
    security_markers = ("private", "public", "nsg", "security list", "kms", "vault", "iam")
    return (
        _text_has_any_marker(combined, module_markers)
        and _text_has_any_marker(combined, state_markers)
        and _text_has_any_marker(combined, security_markers)
    )


def _terraform_targeted_questions() -> list[dict[str, Any]]:
    return [
        {
            "id": "terraform.module_scope",
            "question": "Which Terraform module boundary should Archie draft first: networking foundation, compute/app tier, database tier, or the full stack?",
            "blocking": True,
        },
        {
            "id": "terraform.state_backend",
            "question": "What should the Terraform state backend be: OCI Object Storage, Terraform Cloud, or local state for a draft?",
            "blocking": True,
        },
        {
            "id": "terraform.security_controls",
            "question": "What security defaults must be enforced in code: private-only networking, specific NSG posture, KMS/Vault usage, or tagging/IAM constraints?",
            "blocking": True,
        },
    ]


def _diagram_has_sufficient_context(
    *,
    context: dict[str, Any] | None,
    args: dict[str, Any],
    user_message: str,
) -> bool:
    if _has_architecture_definition(context):
        return True
    archie = context_store.get_archie_state(context or {})
    if any(
        str(archie.get(key, "") or "").strip()
        for key in ("engagement_summary", "latest_notes_summary")
    ):
        return True
    if list(archie.get("resolved_questions", []) or []):
        return True
    architect_context = str((args.get("_architect_brief", {}) or {}).get("architect_context", "") or "").strip()
    combined = " ".join(
        part
        for part in (
            str(args.get("_user_request_text", "") or ""),
            user_message,
            architect_context,
            context_store.build_context_summary(context or {}),
        )
        if str(part).strip()
    )
    return _diagram_request_has_topology_intent(combined)


def _specialist_question_bundle_from_result(
    *,
    tool_name: str,
    result_summary: str,
    result_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    data = dict(result_data or {})
    raw_questions: list[Any] = []
    if isinstance(data.get("questions"), list):
        raw_questions = list(data.get("questions", []))
    elif isinstance(data.get("blocking_questions"), list):
        raw_questions = list(data.get("blocking_questions", []))
    elif str(data.get("type", "") or "") == "question":
        raw_questions = [str(data.get("reply", "") or result_summary or "").strip()]

    bundle: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_questions, start=1):
        normalized = _normalize_specialist_question(tool_name, raw, index=idx)
        if normalized:
            bundle.append(normalized)
    return bundle


def _latest_resolved_answer_map(context: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(context, dict):
        return {}
    archie = context_store.get_archie_state(context)
    resolved = archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []
    latest: dict[str, dict[str, Any]] = {}
    for item in resolved:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id", "") or "").strip()
        if question_id:
            latest[question_id] = item
    return latest


def _suggest_answer_for_question(
    question: dict[str, Any],
    *,
    context: dict[str, Any],
    user_message: str,
) -> tuple[str, str, str]:
    question_id = str(question.get("question_id", "") or "").strip()
    prompt = str(question.get("question", "") or "").strip()
    resolved = _latest_resolved_answer_map(context)
    prior = resolved.get(question_id)
    if isinstance(prior, dict):
        answer = str(prior.get("final_answer", "") or prior.get("suggested_answer", "") or "").strip()
        if answer:
            return answer, "prior Archie-approved decision", "high"

    archie = context_store.get_archie_state(context)
    latest_decision_context = dict(context.get("latest_decision_context", {}) or {})
    constraints = dict(latest_decision_context.get("constraints", {}) or {})
    text = " ".join(
        part
        for part in (
            user_message,
            str(archie.get("engagement_summary", "") or ""),
            str(archie.get("latest_notes_summary", "") or ""),
            json.dumps(constraints, ensure_ascii=True, sort_keys=True),
            context_store.build_context_summary(context),
        )
        if str(part).strip()
    ).lower()

    if question_id in {"regions.count", "topology.scope"} or "region" in prompt.lower():
        if any(token in text for token in ("multi-region", "multi region", "two regions", "2 regions")):
            if question_id == "regions.count":
                return "2", "current Archie context mentions a multi-region topology", "high"
            return "multi-region", "current Archie context mentions a multi-region topology", "high"
        region = str(constraints.get("region", "") or "").strip()
        if region or "single region" in text or "single-region" in text:
            if question_id == "regions.count":
                return "1", "latest decision context has a single primary region", "medium"
            return "single-region", "latest decision context has a single primary region", "medium"

    if question_id == "network.exposure" or "public, private, or both" in prompt.lower():
        has_private = "private" in text
        has_public = "public" in text or "internet" in text
        if has_private and has_public:
            return "both", "notes mention both private and public exposure", "medium"
        if has_private:
            return "private", "notes emphasize private networking/exposure", "high"
        if has_public:
            return "public", "notes mention public or internet ingress", "high"

    if question_id == "workload.components" or "major oci components" in prompt.lower():
        components: list[str] = []
        markers = (
            ("oke", "OKE"),
            ("load balancer", "Load Balancer"),
            ("database", "Database"),
            ("object storage", "Object Storage"),
            ("waf", "WAF"),
            ("vcn", "VCN"),
        )
        for token, label in markers:
            if token in text and label not in components:
                components.append(label)
        if components:
            return ", ".join(components), "latest notes already mention these OCI components", "medium"

    if question_id == "data.tier" or "data tier" in prompt.lower():
        if "autonomous database" in text or "adb" in text:
            return "Autonomous Database", "notes mention Autonomous Database", "high"
        if "postgres" in text:
            return "PostgreSQL", "notes mention PostgreSQL", "high"
        if "mysql" in text:
            return "MySQL", "notes mention MySQL", "high"
        if "database" in text or "data tier" in text:
            return "generic database node", "notes imply a data tier without a pinned engine", "medium"

    if "budget" in prompt.lower() or "monthly" in prompt.lower():
        if constraints.get("cost_max_monthly") is not None:
            return str(constraints.get("cost_max_monthly")), "latest decision context already has a monthly budget", "high"

    return "", "", "needs_confirmation"


def _apply_resolved_answers_to_tool_args(
    *,
    tool_name: str,
    args: dict[str, Any],
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(args or {})
    lines = ["[Archie Resolved Specialist Inputs]"]
    for item in answers:
        question_id = str(item.get("question_id", "") or item.get("id", "") or "question").strip()
        answer = str(item.get("final_answer", "") or item.get("suggested_answer", "") or "").strip()
        if question_id and answer:
            lines.append(f"- {question_id}: {answer}")
    lines.append("[End Archie Resolved Specialist Inputs]")
    block = "\n".join(lines)
    payload["_archie_question_retry"] = True
    if tool_name == "generate_diagram":
        payload["bom_text"] = f"{payload.get('bom_text', '')}\n\n{block}".strip()
    elif tool_name == "generate_bom":
        payload["prompt"] = f"{payload.get('prompt', '')}\n\n{block}".strip()
    elif tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        payload["feedback"] = f"{payload.get('feedback', '')}\n\n{block}".strip()
    elif tool_name == "generate_terraform":
        payload["prompt"] = f"{payload.get('prompt', '')}\n\n{block}".strip()
    return payload


async def _mediate_specialist_questions(
    *,
    tool_name: str,
    args: dict[str, Any],
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str,
    user_message: str,
    max_refinements: int,
    decision_context: dict[str, Any],
    result_summary: str,
    artifact_key: str,
    result_data: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    questions = _specialist_question_bundle_from_result(
        tool_name=tool_name,
        result_summary=result_summary,
        result_data=result_data,
    )
    if not questions:
        return result_summary, artifact_key, result_data

    auto_answered: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for item in questions:
        suggestion, basis, confidence = _suggest_answer_for_question(item, context=context, user_message=user_message)
        candidate = {
            **item,
            "specialist_path": tool_name,
            "request_intent": user_message,
            "suggested_answer": suggestion,
            "basis": basis,
            "confidence": confidence,
            "timestamp": _now(),
        }
        if suggestion and confidence in {"high", "medium"}:
            candidate["final_answer"] = suggestion
            auto_answered.append(candidate)
        else:
            unresolved.append(candidate)

    for item in auto_answered:
        context_store.record_resolved_question(
            context,
            {
                "id": str(uuid.uuid4()),
                **item,
                "source": "archie_auto_fill",
            },
        )

    if unresolved:
        checkpoint = _build_specialist_question_checkpoint(
            tool_name=tool_name,
            args=args,
            original_request=user_message,
            questions=[*auto_answered, *unresolved],
        )
        context_store.set_open_questions(context, [*auto_answered, *unresolved])
        context_store.set_pending_checkpoint(context, checkpoint)
        context_store.write_context(store, customer_id, context)
        result_data["archie_question_bundle"] = checkpoint
        return checkpoint["prompt"], "", result_data

    context_store.clear_pending_checkpoint(context)
    context_store.set_open_questions(context, [])
    context_store.write_context(store, customer_id, context)
    rerun_args = _apply_resolved_answers_to_tool_args(tool_name=tool_name, args=args, answers=auto_answered)
    rerun_summary, rerun_key, rerun_data = await _execute_tool(
        tool_name,
        rerun_args,
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
    rerun_data = dict(rerun_data or {})
    rerun_data["archie_auto_answers"] = auto_answered
    return rerun_summary, rerun_key, rerun_data


def _build_specialist_question_checkpoint(
    *,
    tool_name: str,
    args: dict[str, Any],
    original_request: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    rendered = ["Archie needs confirmation on the remaining specialist inputs before continuing."]
    for item in questions:
        question = str(item.get("question", "") or "").strip()
        if not question:
            continue
        rendered.append("")
        rendered.append(f"- Question ID: {item.get('question_id', '')}")
        rendered.append(f"  Question: {question}")
        suggestion = str(item.get("suggested_answer", "") or "").strip()
        if suggestion:
            rendered.append(f"  Suggested answer: {suggestion}")
        basis = str(item.get("basis", "") or "").strip()
        if basis:
            rendered.append(f"  Basis: {basis}")
        rendered.append(f"  Confidence: {item.get('confidence', 'needs_confirmation')}")
    rendered.append("")
    rendered.append("Reply `approve suggested answers` to accept Archie's suggestions, or answer inline as `question_id: answer`.")
    return {
        "id": str(uuid.uuid4()),
        "type": "specialist_questions",
        "status": "pending",
        "tool_name": tool_name,
        "tool_args": dict(args or {}),
        "original_request": original_request,
        "questions": [dict(item) for item in questions],
        "prompt": "\n".join(rendered),
        "options": ["approve suggested answers", "answer inline"],
    }


def _is_specialist_question_approve_message(user_message: str) -> bool:
    lowered = str(user_message or "").lower()
    return any(
        marker in lowered
        for marker in (
            "approve suggested answers",
            "use suggested answers",
            "use those answers",
            "approve answers",
        )
    )


def _parse_specialist_answers_from_user(
    *,
    pending_checkpoint: dict[str, Any],
    user_message: str,
) -> list[dict[str, Any]]:
    questions = [dict(item) for item in list(pending_checkpoint.get("questions", []) or []) if isinstance(item, dict)]
    if _is_specialist_question_approve_message(user_message):
        answers: list[dict[str, Any]] = []
        for item in questions:
            suggested = str(item.get("suggested_answer", "") or "").strip()
            if suggested:
                answers.append({**item, "final_answer": suggested})
        return answers

    overrides: dict[str, str] = {}
    for line in str(user_message or "").splitlines():
        if ":" not in line:
            continue
        question_id, answer = line.split(":", 1)
        qid = question_id.strip()
        value = answer.strip()
        if qid and value:
            overrides[qid] = value

    answers = []
    for item in questions:
        question_id = str(item.get("question_id", "") or "").strip()
        final_answer = overrides.get(question_id, "")
        if not final_answer and len(questions) == 1 and str(user_message or "").strip() and ":" not in str(user_message or ""):
            final_answer = str(user_message or "").strip()
        if not final_answer:
            final_answer = str(item.get("suggested_answer", "") or "").strip()
        if final_answer:
            answers.append({**item, "final_answer": final_answer})
    return answers


async def _handle_pending_specialist_questions(
    *,
    pending_checkpoint: dict[str, Any],
    user_message: str,
    context: dict[str, Any],
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str,
    max_refinements: int,
) -> tuple[str, dict[str, Any] | None, str]:
    if _is_checkpoint_reject_message(user_message):
        context_store.clear_pending_checkpoint(context)
        context_store.set_open_questions(context, [])
        context_store.write_context(store, customer_id, context)
        return (
            "I cleared the pending specialist question batch. Revise the request and rerun when ready.",
            None,
            "",
        )

    answers = _parse_specialist_answers_from_user(
        pending_checkpoint=pending_checkpoint,
        user_message=user_message,
    )
    if not answers:
        return pending_checkpoint.get("prompt", ""), None, ""

    for item in answers:
        context_store.record_resolved_question(
            context,
            {
                "id": str(uuid.uuid4()),
                **item,
                "source": "user_confirmed",
                "timestamp": _now(),
                "request_intent": str(pending_checkpoint.get("original_request", "") or ""),
            },
        )
    context_store.clear_pending_checkpoint(context)
    context_store.set_open_questions(context, [])
    context_store.write_context(store, customer_id, context)

    tool_name = str(pending_checkpoint.get("tool_name", "") or "")
    tool_args = _apply_resolved_answers_to_tool_args(
        tool_name=tool_name,
        args=dict(pending_checkpoint.get("tool_args", {}) or {}),
        answers=answers,
    )
    result_summary, artifact_key, result_data = await _execute_tool(
        tool_name,
        tool_args,
        customer_id=customer_id,
        customer_name=customer_name,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        specialist_mode=specialist_mode,
        user_message=str(pending_checkpoint.get("original_request", "") or user_message),
        max_refinements=max_refinements,
        decision_context=decision_context_builder.build_decision_context(
            user_message=str(pending_checkpoint.get("original_request", "") or user_message),
            context=context,
        ),
    )
    return (
        result_summary,
        {
            "tool": tool_name,
            "args": tool_args,
            "result_summary": result_summary,
            "result_data": result_data,
        },
        artifact_key,
    )


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


def _is_architecture_tool(tool_name: str) -> bool:
    return tool_name in _ARCHITECTURE_TOOLS


def _build_expert_mode_metadata(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    user_message: str,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if not _is_architecture_tool(tool_name):
        return {}

    bundle = select_standards_bundle()
    metadata: dict[str, Any] = {
        "enabled": True,
        "tool_name": tool_name,
        "mandatory_skill_injection": True,
        "standards_bundle_id": str(bundle.get("bundle_id", "") or ""),
        "standards_bundle_version": str(bundle.get("bundle_version", "") or ""),
        "standards_policy": str(bundle.get("policy", "curated_snapshot") or "curated_snapshot"),
        "approved_sources": list(bundle.get("approved_sources", []) or []),
        "supported_families": list(bundle.get("supported_families", []) or []),
    }
    if tool_name != "generate_diagram":
        return metadata

    payload = dict(args or {})
    selection_text = "\n".join(
        part
        for part in (
            user_message or "",
            str(payload.get("bom_text", "") or ""),
            decision_context_builder.summarize_decision_context(decision_context),
        )
        if part and str(part).strip()
    )
    selection = select_reference_architecture(
        text=selection_text,
        deployment_hints=dict(payload.get("deployment_hints", {}) or {}),
    ).as_dict()
    metadata.update(selection)
    return metadata


def _apply_expert_mode_to_payload(
    *,
    tool_name: str,
    payload: dict[str, Any],
    expert_mode: dict[str, Any],
) -> None:
    if not expert_mode:
        return
    payload["_expert_mode"] = dict(expert_mode)
    payload["_standards_bundle_version"] = str(expert_mode.get("standards_bundle_version", "") or "")
    payload["_reference_architecture"] = dict(expert_mode)
    if tool_name == "generate_diagram":
        payload["_reference_family"] = str(expert_mode.get("reference_family", "") or "")
        payload["_reference_confidence"] = float(expert_mode.get("reference_confidence", 0) or 0)
        payload["_reference_mode"] = str(expert_mode.get("reference_mode", "best-effort-generic") or "best-effort-generic")
        payload["_reference_constraints"] = dict(expert_mode.get("family_constraints", {}) or {})


def _mandatory_skill_specs(
    *,
    tool_name: str,
) -> list[Any]:
    fallback_names = _MANDATORY_SKILL_FALLBACKS.get(tool_name, ())
    available = {spec.name: spec for spec in discover_skills()}
    selected = [available[name] for name in fallback_names if name in available]
    return selected


def _inject_skill_into_tool_args(
    tool_name: str,
    args: dict | None,
    *,
    user_message: str = "",
    decision_context: dict[str, Any] | None = None,
    expert_mode: dict[str, Any] | None = None,
) -> dict:
    payload = dict(args or {})
    _apply_expert_mode_to_payload(tool_name=tool_name, payload=payload, expert_mode=dict(expert_mode or {}))
    constraint_tags = decision_context_builder.derive_constraint_tags(decision_context)
    decision_block = _build_decision_context_block(decision_context)
    selection_message = " ".join([user_message.strip(), *constraint_tags]).strip()
    selected = select_skills_for_call(
        tool_name=tool_name,
        user_message=selection_message,
        tool_args=payload,
        max_skills=3,
    )
    if _is_architecture_tool(tool_name):
        fallback_specs = _mandatory_skill_specs(tool_name=tool_name)
        existing = {spec.name for spec in selected}
        for spec in fallback_specs:
            if spec.name not in existing:
                selected.append(spec)
                existing.add(spec.name)
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
            tool_args=args,
            result_data=retry_data,
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
        "expert_mode": dict(result_data.get("expert_mode", {}) or {}),
        "standards_bundle_version": str(result_data.get("standards_bundle_version", "") or ""),
        "reference_family": str(result_data.get("reference_family", "") or ""),
        "reference_confidence": float(result_data.get("reference_confidence", 0) or 0),
        "reference_mode": str(result_data.get("reference_mode", "") or ""),
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
        "expert_mode": dict(result_data.get("expert_mode", {}) or {}),
        "standards_bundle_version": str(result_data.get("standards_bundle_version", "") or ""),
        "reference_family": str(result_data.get("reference_family", "") or ""),
        "reference_confidence": float(result_data.get("reference_confidence", 0) or 0),
        "reference_mode": str(result_data.get("reference_mode", "") or ""),
        "reference_architecture": dict(result_data.get("reference_architecture", {}) or {}),
        "family_fit_score": float(((result_data.get("reference_architecture", {}) or {}).get("family_fit_score", 0)) or 0),
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
    if tool_name == "generate_bom":
        trace["bom_cache_status_before_attempt"] = str(prior_trace.get("bom_cache_status_before_attempt", "") or "")
        trace["bom_cache_refresh_attempted"] = bool(prior_trace.get("bom_cache_refresh_attempted", False))
        trace["bom_cache_refresh_status"] = str(prior_trace.get("bom_cache_refresh_status", "") or "")
        trace["bom_context_source"] = str(prior_trace.get("bom_context_source", result_data.get("bom_context_source", "")) or "")
        trace["bom_retry_count"] = int(prior_trace.get("bom_retry_count", 0) or 0)
        trace["bom_retry_succeeded"] = bool(prior_trace.get("bom_retry_succeeded", False))
    if tool_name == "generate_diagram":
        trace["backend_error_message"] = str(result_data.get("backend_error_message", "") or "")
        trace["diagram_recovery_status"] = str(result_data.get("diagram_recovery_status", "none") or "none")
        trace["assumptions_used"] = list(result_data.get("assumptions_used", []) or [])
        trace["recovery_attempt_count"] = int(result_data.get("recovery_attempt_count", 0) or 0)
        trace["final_disposition"] = str(result_data.get("diagram_final_disposition", "") or "")
    return trace


def _build_decision_context_block(decision_context: dict[str, Any] | None) -> str:
    if not isinstance(decision_context, dict) or not decision_context:
        return ""
    return (
        "[Decision Context]\n"
        + json.dumps(decision_context, indent=2, ensure_ascii=True)
        + "\n[End Decision Context]\n"
    )


def _compose_specialist_request_text(
    *,
    clean_request: str,
    architect_brief: dict[str, Any] | None,
    include_missing_inputs: bool = True,
) -> str:
    brief_block = _render_architect_brief_text(architect_brief)
    if not brief_block:
        return clean_request.strip()
    rendered = [part for part in (clean_request.strip(), brief_block) if part]
    if not include_missing_inputs:
        rendered_text = "\n\n".join(rendered)
        rendered_text = re.sub(
            r"\nMissing inputs:\n(?:- .+\n?)+",
            "\n",
            rendered_text,
            flags=re.MULTILINE,
        )
        return rendered_text.strip()
    return "\n\n".join(rendered).strip()


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


def _infer_diagram_name_from_key(artifact_key: str) -> str:
    parts = [part for part in str(artifact_key or "").split("/") if part]
    if len(parts) >= 3 and re.fullmatch(r"v\d+", parts[-2]):
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return ""


def _summarize_diagram_deployment(
    result_data: dict[str, Any] | None,
) -> tuple[str, str]:
    result = dict(result_data or {})
    spec = result.get("spec", {}) if isinstance(result.get("spec"), dict) else {}
    render_manifest = result.get("render_manifest", {}) if isinstance(result.get("render_manifest"), dict) else {}
    deployment_type = str(spec.get("deployment_type", "") or "").strip()
    node_count = int(render_manifest.get("node_count", 0) or 0)
    layer_count = len(list(render_manifest.get("layers", []) or []))
    parts: list[str] = []
    if deployment_type:
        parts.append(deployment_type)
    if node_count > 0:
        parts.append(f"{node_count} nodes")
    if layer_count > 0:
        parts.append(f"{layer_count} layers")
    deployment_summary = ", ".join(parts)
    return deployment_summary, json.dumps(spec, ensure_ascii=True, sort_keys=True)[:400] if spec else ""


def _record_shared_agent_state(
    *,
    context: dict[str, Any],
    tool_name: str,
    artifact_key: str,
    decision_context: dict[str, Any],
    result_data: dict[str, Any],
) -> None:
    existing = dict((context.get("agents", {}) or {}).get("diagram" if tool_name == "generate_diagram" else "bom", {}) or {})
    if tool_name == "generate_diagram":
        recovery_status = str(result_data.get("diagram_recovery_status", "none") or "none")
        if recovery_status in {"needs_clarification", "backend_error"} or not artifact_key:
            return
        deployment_summary, spec_summary = _summarize_diagram_deployment(result_data)
        record = {
            "version": int(existing.get("version", 0) or 0) + 1,
            "diagram_key": artifact_key,
            "artifact_ref": artifact_key,
            "diagram_name": _infer_diagram_name_from_key(artifact_key),
            "node_count": int(((result_data.get("render_manifest", {}) or {}).get("node_count", 0)) or 0),
            "deployment_summary": deployment_summary,
            "spec_summary": spec_summary,
            "reference_family": str(result_data.get("reference_family", "") or ""),
            "reference_mode": str(result_data.get("reference_mode", "") or ""),
            "assumptions_used": _merge_assumption_lists(
                list((decision_context or {}).get("assumptions", []) or []),
                list(result_data.get("assumptions_used", []) or []),
            ),
            "decision_context_hash": _decision_context_hash(decision_context),
            "decision_context_summary": decision_context_builder.summarize_decision_context(decision_context),
            "summary": deployment_summary or "Latest architecture diagram available for downstream follow-up work.",
        }
        context_store.record_agent_run(context, "diagram", [], record)
        return

    if tool_name == "generate_bom":
        payload = result_data.get("bom_payload", {}) if isinstance(result_data.get("bom_payload"), dict) else {}
        if str(result_data.get("type", "") or "") != "final" or not payload:
            return
        totals = payload.get("totals", {}) if isinstance(payload.get("totals"), dict) else {}
        trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
        record = {
            "version": int(existing.get("version", 0) or 0) + 1,
            "result_type": "final",
            "summary": str(result_data.get("reply", "") or "").strip() or "Final BOM prepared.",
            "estimated_monthly_cost": totals.get("estimated_monthly_cost"),
            "line_item_count": len(list(payload.get("line_items", []) or [])),
            "assumption_count": len(list(payload.get("assumptions", []) or [])),
            "payload_ref": f"trace:{result_data.get('trace_id', '')}" if str(result_data.get("trace_id", "") or "").strip() else "",
            "trace_id": str(result_data.get("trace_id", "") or ""),
            "context_source": str(trace.get("bom_context_source", result_data.get("bom_context_source", "")) or ""),
            "decision_context_hash": _decision_context_hash(decision_context),
        }
        context_store.record_agent_run(context, "bom", [], record)


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
    context_store.set_archie_decision_state(
        context,
        constraints=dict((decision_context or {}).get("constraints", {}) or {}),
        assumptions=list((decision_context or {}).get("assumptions", []) or []),
    )
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
    _record_shared_agent_state(
        context=context,
        tool_name=tool_name,
        artifact_key=artifact_key,
        decision_context=decision_context,
        result_data=result_data,
    )
    context_store.write_context(store, customer_id, context)
    result_data["decision_log"] = decision_log


def _checkpoint_needed_for_result(
    *,
    tool_name: str,
    decision_context: dict[str, Any],
    governor: dict[str, Any],
) -> bool:
    constraints = dict((decision_context or {}).get("constraints", {}) or {})
    assumptions = list((decision_context or {}).get("assumptions", []) or [])
    cost = dict(governor.get("cost", {}) or {})
    has_budget_signal = any(value is not None for value in (
        constraints.get("cost_max_monthly"),
        cost.get("budget_target"),
        cost.get("estimated_monthly_cost"),
        cost.get("variance"),
    ))
    if has_budget_signal and str(cost.get("status", "pass") or "pass") == "checkpoint_required":
        return True

    security = dict(governor.get("security", {}) or {})
    if str(security.get("status", "pass") or "pass") == "blocked":
        return False
    if list(constraints.get("compliance_requirements", []) or []) and list((decision_context or {}).get("missing_inputs", []) or []):
        return True
    if any(str(item.get("risk", "") or "").strip().lower() == "high" for item in assumptions if isinstance(item, dict)):
        return True
    if tool_name == "generate_terraform":
        return bool(list((decision_context or {}).get("missing_inputs", []) or []))
    return False


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
    if not _checkpoint_needed_for_result(
        tool_name=tool_name,
        decision_context=decision_context,
        governor=governor,
    ):
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
    architect_brief = dict(args.get("_architect_brief", {}) or {})
    bom_text = str(args.get("bom_text", "") or "")
    user_notes = str(architect_brief.get("user_notes", "") or "").strip() or _strip_injected_guidance_blocks(bom_text).strip()
    if not user_notes:
        user_notes = "Generate a diagram for this engagement."

    context_parts: list[str] = []
    decision_context = args.get("_decision_context")
    architect_context = str(architect_brief.get("architect_context", "") or "").strip()
    if architect_context:
        context_parts.append(architect_context)
    if _notes_request_best_effort_assumptions(user_notes) or bool(architect_brief.get("assumption_mode", False)):
        context_parts.append(
            "Assumption mode requested: apply standard safe OCI assumptions for a ballpark architecture. "
            "Ask only truly blocking questions when the workload/components are still unspecified."
        )
    assumptions = _render_assumptions(architect_brief, limit=6)
    if assumptions:
        context_parts.append("Architect assumptions:\n" + "\n".join(f"- {item}" for item in assumptions))
    missing_inputs = [str(item).strip() for item in architect_brief.get("missing_inputs", []) or [] if str(item).strip()]
    if missing_inputs:
        context_parts.append("Still missing:\n" + "\n".join(f"- {item}" for item in missing_inputs))
    reference_architecture = dict(args.get("_reference_architecture", {}) or {})
    if reference_architecture:
        context_parts.append("\n".join(build_reference_context_lines(reference_architecture)))
    payload = {
        "task_id": f"orch-{_now()}",
        "skill": "generate_diagram",
        "client_id": customer_id,
        "inputs": {
            "notes": user_notes,
            "context": "\n\n".join(part for part in context_parts if part.strip()),
            "reference_architecture": reference_architecture,
            "standards_bundle_version": str(args.get("_standards_bundle_version", "") or ""),
        },
    }

    try:
        body = await _post_diagram_a2a_task(payload=payload, a2a_base_url=a2a_base_url)
        status = str(body.get("status", "error") or "error").lower()
        outputs = body.get("outputs", {}) if isinstance(body.get("outputs"), dict) else {}
        task_id = str(body.get("task_id", "") or payload["task_id"])
        if status == "ok":
            key = str(outputs.get("object_key") or outputs.get("drawio_key") or "")
            result_data = _diagram_result_payload_from_outputs(outputs, final_disposition="completed")
            if key:
                return f"Diagram generated. Key: {key}", key, result_data
            return f"Diagram generated (task {task_id}).", "", result_data
        if status == "need_clarification":
            questions = outputs.get("questions", []) if isinstance(outputs.get("questions"), list) else []
            result_data: dict[str, Any] = {
                "questions": questions,
                "diagram_recovery_status": "needs_clarification",
                "diagram_final_disposition": "needs_clarification",
                "backend_error_message": "",
                "assumptions_used": [],
                "recovery_attempt_count": 0,
            }
            clarify_context = outputs.get("_clarify_context")
            if isinstance(clarify_context, dict):
                result_data["_clarify_context"] = clarify_context
            if questions:
                return _format_diagram_clarification_reply(questions), "", result_data
            return "Diagram clarification required before generation can continue.", "", result_data
        backend_error_message = _sanitize_diagram_backend_error_message(
            str(body.get("error_message", "") or outputs.get("error_message", "") or f"Diagram generation returned status={status}.")
        )

        if _diagram_request_has_contradiction(user_notes):
            questions = _diagram_clarification_questions(
                user_notes=user_notes,
                backend_error_message=backend_error_message,
            )
            result_data = {
                "questions": questions,
                "backend_error_message": backend_error_message,
                "diagram_recovery_status": "needs_clarification",
                "assumptions_used": [],
                "recovery_attempt_count": 0,
                "diagram_final_disposition": "needs_clarification",
            }
            return _format_diagram_clarification_reply(questions), "", result_data

        assumptions_used = _diagram_retry_assumptions(
            user_notes=user_notes,
            decision_context=decision_context,
            backend_error_message=backend_error_message,
        )
        should_retry = bool(assumptions_used) and not _is_diagram_system_error(backend_error_message)
        if should_retry:
            retry_context_parts = list(context_parts)
            retry_context_parts.append(_build_diagram_recovery_context(assumptions_used))
            retry_payload = {
                **payload,
                "task_id": f"{payload['task_id']}-retry1",
                "inputs": {
                    **payload["inputs"],
                    "context": "\n\n".join(part for part in retry_context_parts if part.strip()),
                },
            }
            retry_body = await _post_diagram_a2a_task(payload=retry_payload, a2a_base_url=a2a_base_url)
            retry_status = str(retry_body.get("status", "error") or "error").lower()
            retry_outputs = retry_body.get("outputs", {}) if isinstance(retry_body.get("outputs"), dict) else {}
            retry_task_id = str(retry_body.get("task_id", "") or retry_payload["task_id"])
            merged_decision_context = _merge_decision_context(
                decision_context,
                {
                    "goal": str((decision_context or {}).get("goal", "") or user_notes),
                    "constraints": dict((decision_context or {}).get("constraints", {}) or {}),
                    "assumptions": assumptions_used,
                    "success_criteria": list((decision_context or {}).get("success_criteria", []) or []),
                    "missing_inputs": [],
                    "requires_user_confirmation": bool((decision_context or {}).get("requires_user_confirmation", False)),
                },
            )
            if retry_status == "ok":
                key = str(retry_outputs.get("object_key") or retry_outputs.get("drawio_key") or "")
                result_data = _diagram_result_payload_from_outputs(
                    retry_outputs,
                    backend_error_message=backend_error_message,
                    diagram_recovery_status="retried_with_assumptions",
                    assumptions_used=assumptions_used,
                    recovery_attempt_count=1,
                    final_disposition="completed_with_assumptions",
                )
                result_data["decision_context"] = merged_decision_context
                if key:
                    return f"Diagram generated. Key: {key}", key, result_data
                return f"Diagram generated (task {retry_task_id}).", "", result_data
            if retry_status == "need_clarification":
                questions = retry_outputs.get("questions", []) if isinstance(retry_outputs.get("questions"), list) else []
                result_data = {
                    "questions": questions,
                    "backend_error_message": backend_error_message,
                    "diagram_recovery_status": "needs_clarification",
                    "assumptions_used": assumptions_used,
                    "recovery_attempt_count": 1,
                    "diagram_final_disposition": "needs_clarification",
                    "decision_context": merged_decision_context,
                }
                clarify_context = retry_outputs.get("_clarify_context")
                if isinstance(clarify_context, dict):
                    result_data["_clarify_context"] = clarify_context
                if questions:
                    return _format_diagram_clarification_reply(questions), "", result_data
            backend_error_message = _sanitize_diagram_backend_error_message(
                str(retry_body.get("error_message", "") or retry_outputs.get("error_message", "") or backend_error_message)
            )
            error_reply, next_steps = _build_diagram_error_reply(
                backend_error_message=backend_error_message,
                attempted_recovery=True,
            )
            result_data = {
                "backend_error_message": backend_error_message,
                "diagram_recovery_status": "backend_error",
                "assumptions_used": assumptions_used,
                "recovery_attempt_count": 1,
                "diagram_final_disposition": "backend_error",
                "decision_context": merged_decision_context,
                "diagram_next_steps": next_steps,
            }
            return error_reply, "", result_data

        clarification_questions = _diagram_clarification_questions(
            user_notes=user_notes,
            backend_error_message=backend_error_message,
        )
        if clarification_questions and not _is_diagram_system_error(backend_error_message) and not _is_diagram_invariant_error(backend_error_message):
            result_data = {
                "questions": clarification_questions,
                "backend_error_message": backend_error_message,
                "diagram_recovery_status": "needs_clarification",
                "assumptions_used": [],
                "recovery_attempt_count": 0,
                "diagram_final_disposition": "needs_clarification",
            }
            return _format_diagram_clarification_reply(clarification_questions), "", result_data

        error_reply, next_steps = _build_diagram_error_reply(
            backend_error_message=backend_error_message,
            attempted_recovery=False,
        )
        return error_reply, "", {
            "backend_error_message": backend_error_message,
            "diagram_recovery_status": "backend_error",
            "assumptions_used": [],
            "recovery_attempt_count": 0,
            "diagram_final_disposition": "backend_error",
            "diagram_next_steps": next_steps,
        }
    except Exception as exc:
        logger.warning("Diagram A2A call failed: %s", exc)
        return f"Diagram generation failed: {exc}", "", {}


_INJECTED_GUIDANCE_BLOCKS: tuple[tuple[str, str], ...] = (
    ("[Decision Context]", "[End Decision Context]"),
    ("[Skill Injection Contract]", "[End Skill Injection Contract]"),
    ("[Injected Skill Guidance]", "[End Skill Guidance]"),
)

_OCI_REGION_RE = re.compile(r"\b[a-z]{2}-[a-z]+-\d\b")
_DIAGRAM_COMPONENT_MARKERS = (
    "oke",
    "kubernetes",
    "container engine",
    "database",
    "db",
    "load balancer",
    "lb",
    "waf",
    "object storage",
    "bucket",
    "bastion",
    "web",
    "app tier",
    "data tier",
    "private subnet",
    "public subnet",
    "vcn",
    "subnet",
    "dr",
    "disaster recovery",
    "multi-region",
    "multi region",
)
_DIAGRAM_SYSTEM_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "connection refused",
    "connection reset",
    "service unavailable",
    "internal server error",
    "traceback",
    "unexpected exception",
    "dns",
    "socket",
    "503",
    "500",
)
_DIAGRAM_INVARIANT_ERROR_MARKERS = (
    "invariant",
    "unsupported",
    "invalid combination",
    "cannot combine",
    "must not",
    "conflict",
    "violates",
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


def _normalize_assumption_payload(assumption: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(assumption.get("id", "") or "").strip(),
        "statement": str(assumption.get("statement", "") or "").strip(),
        "reason": str(assumption.get("reason", "") or "").strip(),
        "risk": str(assumption.get("risk", "low") or "low").strip().lower(),
    }


def _merge_assumption_lists(
    existing: list[dict[str, Any]] | None,
    additions: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in [*(existing or []), *(additions or [])]:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_assumption_payload(raw)
        statement = normalized["statement"]
        if not statement:
            continue
        key = normalized["id"] or statement.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _merge_decision_context(
    base_context: dict[str, Any] | None,
    overlay_context: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(base_context or {})
    overlay = dict(overlay_context or {})
    if not base:
        base = {
            "goal": "",
            "constraints": {},
            "assumptions": [],
            "success_criteria": [],
            "missing_inputs": [],
            "requires_user_confirmation": False,
        }

    base["goal"] = str(overlay.get("goal", "") or base.get("goal", "") or "")
    merged_constraints = dict(base.get("constraints", {}) or {})
    for key, value in dict(overlay.get("constraints", {}) or {}).items():
        if value not in (None, "", [], {}):
            merged_constraints[key] = value
    base["constraints"] = merged_constraints
    base["assumptions"] = _merge_assumption_lists(
        list(base.get("assumptions", []) or []),
        list(overlay.get("assumptions", []) or []),
    )
    base["success_criteria"] = list(dict.fromkeys([
        *list(base.get("success_criteria", []) or []),
        *list(overlay.get("success_criteria", []) or []),
    ]))
    base["missing_inputs"] = list(dict.fromkeys([
        *list(base.get("missing_inputs", []) or []),
        *list(overlay.get("missing_inputs", []) or []),
    ]))
    base["requires_user_confirmation"] = bool(
        overlay.get("requires_user_confirmation", base.get("requires_user_confirmation", False))
    )
    return base


def _sanitize_diagram_backend_error_message(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(message or "")).strip()
    if not cleaned:
        return "Unknown backend failure."
    return cleaned[:320]


def _diagram_mentions_multi_region(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in (
        "multi-region",
        "multi region",
        "across two regions",
        "across 2 regions",
        "two regions",
        "2 regions",
        "cross-region",
        "cross region",
    ))


def _diagram_has_region_names(text: str) -> bool:
    return bool(_OCI_REGION_RE.search(str(text or "").lower()))


def _diagram_has_explicit_posture(text: str) -> bool:
    lowered = str(text or "").lower()
    return "active-active" in lowered or "active active" in lowered or "active-passive" in lowered or "active passive" in lowered


def _diagram_has_explicit_replication_technology(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in (
        "goldengate",
        "golden gate",
        "data guard",
        "dataguard",
        "mysql replication",
        "postgres replication",
        "physical standby",
        "logical replication",
        "object storage replication",
    ))


def _diagram_has_concrete_database_flavor(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in (
        "autonomous database",
        "adb",
        "postgres",
        "mysql",
        "oracle database",
        "exadata",
    ))


def _diagram_request_has_topology_intent(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _DIAGRAM_COMPONENT_MARKERS)


def _diagram_request_has_contradiction(text: str) -> bool:
    lowered = str(text or "").lower()
    single_region = "single-region" in lowered or "single region" in lowered
    multi_region = _diagram_mentions_multi_region(lowered)
    active_active = "active-active" in lowered or "active active" in lowered
    active_passive = "active-passive" in lowered or "active passive" in lowered
    return (single_region and multi_region) or (active_active and active_passive)


def _is_diagram_system_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(marker in lowered for marker in _DIAGRAM_SYSTEM_ERROR_MARKERS)


def _is_diagram_invariant_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(marker in lowered for marker in _DIAGRAM_INVARIANT_ERROR_MARKERS)


def _diagram_retry_assumptions(
    *,
    user_notes: str,
    decision_context: dict[str, Any] | None,
    backend_error_message: str,
) -> list[dict[str, str]]:
    lowered = str(user_notes or "").lower()
    backend_lowered = str(backend_error_message or "").lower()
    assumptions: list[dict[str, str]] = []

    if _diagram_mentions_multi_region(lowered) and not _diagram_has_explicit_posture(lowered):
        assumptions.append(
            {
                "id": "diagram_multi_region_posture_default",
                "statement": "Multi-region posture not specified; assume active-passive HA/DR across two OCI regions.",
                "reason": "The request asks for a multi-region diagram without an explicit active-active or active-passive posture.",
                "risk": "medium",
            }
        )

    if (
        _diagram_mentions_multi_region(lowered)
        and not _diagram_has_region_names(lowered)
        and (
            "region" in backend_lowered
            or "multi-region" in backend_lowered
            or "paired" in backend_lowered
            or "secondary" in backend_lowered
            or True
        )
    ):
        assumptions.append(
            {
                "id": "diagram_region_pair_default",
                "statement": "Exact OCI region names were not provided; assume the tenancy-preferred primary region plus a paired secondary region placeholder.",
                "reason": "The topology requires two regions but the request does not name them.",
                "risk": "medium",
            }
        )

    if (
        any(marker in lowered for marker in ("replication", "replica", "dr", "disaster recovery"))
        or "replication" in backend_lowered
    ) and not _diagram_has_explicit_replication_technology(lowered):
        assumptions.append(
            {
                "id": "diagram_replication_default",
                "statement": "Replication technology was not specified; assume inter-region database replication plus object replication.",
                "reason": "The request implies cross-region data protection without naming the replication mechanism.",
                "risk": "medium",
            }
        )

    if (
        "database" in lowered
        or "db" in lowered
        or "database" in backend_lowered
        or (_diagram_mentions_multi_region(lowered) and not _diagram_has_concrete_database_flavor(lowered))
    ) and not _diagram_has_concrete_database_flavor(lowered):
        assumptions.append(
            {
                "id": "diagram_database_flavor_default",
                "statement": "Database flavor was not specified; use a generic database node in the diagram.",
                "reason": "The request implies a data tier but does not pin a concrete managed database service.",
                "risk": "low",
            }
        )

    merged = _merge_assumption_lists(
        list((decision_context or {}).get("assumptions", []) or []),
        assumptions,
    )
    existing_ids = {
        str(item.get("id", "") or "")
        for item in list((decision_context or {}).get("assumptions", []) or [])
        if isinstance(item, dict)
    }
    return [item for item in merged if item.get("id") not in existing_ids]


def _diagram_clarification_questions(
    *,
    user_notes: str,
    backend_error_message: str,
) -> list[dict[str, Any]]:
    lowered = str(user_notes or "").lower()
    if _diagram_request_has_contradiction(lowered):
        return [
            {
                "id": "topology.scope",
                "question": "Should the diagram be single-region or multi-region? The current request asks for both.",
                "blocking": True,
            }
        ]

    questions: list[dict[str, Any]] = []
    if not _diagram_request_has_topology_intent(lowered):
        questions.append(
            {
                "id": "workload.components",
                "question": "What major OCI components need to appear in the diagram (for example OKE, load balancer, database, Object Storage, or WAF)?",
                "blocking": True,
            }
        )
    if "public" not in lowered and "private" not in lowered and "internet" not in lowered:
        questions.append(
            {
                "id": "network.exposure",
                "question": "Should ingress be public, private, or both?",
                "blocking": True,
            }
        )
    if not questions and "database" in str(backend_error_message or "").lower():
        questions.append(
            {
                "id": "data.tier",
                "question": "What data tier should appear in the diagram: a generic database node, Autonomous Database, PostgreSQL, or MySQL?",
                "blocking": True,
            }
        )
    return questions


def _build_diagram_recovery_context(assumptions: list[dict[str, Any]]) -> str:
    if not assumptions:
        return ""
    lines = [
        "Retry the diagram with these bounded architect assumptions. Do not ask follow-up questions unless the request is still contradictory.",
    ]
    lines.extend(
        f"- {item.get('statement', '').strip()}"
        for item in assumptions
        if str(item.get("statement", "")).strip()
    )
    return "\n".join(lines)


def _build_diagram_error_reply(
    *,
    backend_error_message: str,
    attempted_recovery: bool,
) -> tuple[str, list[str]]:
    cleaned = _sanitize_diagram_backend_error_message(backend_error_message)
    lines = []
    if _is_diagram_system_error(cleaned):
        lines.append("I could not complete the diagram because the drawing backend hit a system-side failure.")
        next_steps = ["Retry the diagram once the drawing backend is healthy."]
    elif _is_diagram_invariant_error(cleaned):
        lines.append("I could not complete the diagram because the requested topology still violates a backend layout invariant.")
        next_steps = ["Revise the conflicting topology requirement and retry generate_diagram."]
    else:
        lines.append("I could not complete the diagram because the drawing backend rejected the current topology inputs.")
        next_steps = ["Revise the blocking decision in the request and retry generate_diagram."]
    if attempted_recovery:
        lines.append("I retried once with bounded OCI defaults, but the backend still could not render the diagram.")
    lines.append(f"Backend failure: {cleaned}")
    return "\n".join(lines), next_steps


def _diagram_result_payload_from_outputs(
    outputs: dict[str, Any],
    *,
    backend_error_message: str = "",
    diagram_recovery_status: str = "none",
    assumptions_used: list[dict[str, Any]] | None = None,
    recovery_attempt_count: int = 0,
    final_disposition: str = "",
) -> dict[str, Any]:
    result_data: dict[str, Any] = {
        "backend_error_message": str(backend_error_message or ""),
        "diagram_recovery_status": str(diagram_recovery_status or "none"),
        "assumptions_used": _merge_assumption_lists([], list(assumptions_used or [])),
        "recovery_attempt_count": int(recovery_attempt_count or 0),
        "diagram_final_disposition": str(final_disposition or ""),
    }
    if isinstance(outputs.get("reference_architecture"), dict):
        result_data["reference_architecture"] = dict(outputs.get("reference_architecture", {}) or {})
        result_data["reference_family"] = str(result_data["reference_architecture"].get("reference_family", "") or "")
        result_data["reference_confidence"] = float(result_data["reference_architecture"].get("reference_confidence", 0) or 0)
        result_data["reference_mode"] = str(result_data["reference_architecture"].get("reference_mode", "") or "")
        result_data["standards_bundle_version"] = str(result_data["reference_architecture"].get("standards_bundle_version", "") or "")
    if isinstance(outputs.get("render_manifest"), dict):
        result_data["render_manifest"] = dict(outputs.get("render_manifest", {}) or {})
    if isinstance(outputs.get("node_to_resource_map"), dict):
        result_data["node_to_resource_map"] = dict(outputs.get("node_to_resource_map", {}) or {})
    if isinstance(outputs.get("draw_dict"), dict):
        result_data["draw_dict"] = dict(outputs.get("draw_dict", {}) or {})
    if isinstance(outputs.get("spec"), dict):
        result_data["spec"] = dict(outputs.get("spec", {}) or {})
    return result_data


async def _post_diagram_a2a_task(
    *,
    payload: dict[str, Any],
    a2a_base_url: str,
) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(f"{a2a_base_url}/api/a2a/task", json=payload)
    body = resp.json()
    return body if isinstance(body, dict) else {}


def _diagram_reply_assumptions(
    result_data: dict[str, Any] | None,
    fallback_decision_context: dict[str, Any] | None = None,
) -> list[str]:
    assumption_pool = _merge_assumption_lists(
        list((fallback_decision_context or {}).get("assumptions", []) or []),
        list((result_data or {}).get("assumptions_used", []) or []),
    )
    if isinstance((result_data or {}).get("decision_context"), dict):
        assumption_pool = _merge_assumption_lists(
            assumption_pool,
            list(((result_data or {}).get("decision_context") or {}).get("assumptions", []) or []),
        )
    rendered: list[str] = []
    for assumption in assumption_pool:
        statement = str(assumption.get("statement", "") or "").strip()
        if not statement:
            continue
        risk = str(assumption.get("risk", "") or "low").strip().lower()
        rendered.append(f"{statement} (risk: {risk or 'low'})")
    return rendered


def _build_single_diagram_reply(
    call: dict[str, Any],
    *,
    decision_context: dict[str, Any] | None = None,
) -> str:
    summary = str(call.get("result_summary", "") or "").strip() or "Diagram request completed."
    if "Assumptions applied:" in summary:
        return summary
    result_data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
    recovery_status = str(result_data.get("diagram_recovery_status", "none") or "none")
    if recovery_status in {"needs_clarification", "backend_error"}:
        return summary
    if recovery_status != "retried_with_assumptions" and not list(result_data.get("assumptions_used", []) or []):
        return summary
    assumptions = _diagram_reply_assumptions(result_data, decision_context)
    if not assumptions:
        return summary
    return "\n".join([summary, "", "Assumptions applied:", *[f"- {item}" for item in assumptions]])


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
    if len(tool_calls) == 1 and followup is None:
        call = tool_calls[0]
        if str(call.get("tool", "") or "") == "generate_diagram":
            return _build_single_diagram_reply(call, decision_context=decision_context)
        summary = str(call.get("result_summary", "") or "").strip()
        return summary or f"Completed `{call.get('tool', 'requested_tool')}`."

    lines = ["Completed the requested outputs:"]
    for call in tool_calls:
        tool_name = str(call.get("tool", "") or "requested_tool")
        label = _tool_goal_label(tool_name)
        summary = str(call.get("result_summary", "") or "").strip()
        if summary:
            lines.append(f"- {label}: {summary}")
        else:
            lines.append(f"- {label} completed.")
    merged_assumptions = _merge_assumption_lists(
        list((decision_context or {}).get("assumptions", []) or []),
        [],
    )
    for call in tool_calls:
        data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
        merged_assumptions = _merge_assumption_lists(
            merged_assumptions,
            list((data.get("decision_context", {}) or {}).get("assumptions", []) or []),
        )
        merged_assumptions = _merge_assumption_lists(
            merged_assumptions,
            list(data.get("assumptions_used", []) or []),
        )
    assumptions = [
        f"{str(item.get('statement', '') or '').strip()} (risk: {str(item.get('risk', '') or 'low').strip().lower() or 'low'})"
        for item in merged_assumptions
        if str(item.get("statement", "") or "").strip()
    ]
    missing_inputs = list(dict.fromkeys([
        *[str(item).strip() for item in (decision_context or {}).get("missing_inputs", []) or [] if str(item).strip()],
        *[
            str(item).strip()
            for call in tool_calls
            for item in ((call.get("result_data", {}) or {}).get("decision_context", {}) or {}).get("missing_inputs", []) or []
            if str(item).strip()
        ],
    ]))
    if assumptions and (len(tool_calls) > 1 or followup is not None):
        lines.append("")
        lines.append("Assumptions applied:")
        lines.extend(f"- {assumption}" for assumption in assumptions)
    if missing_inputs and followup is None:
        lines.append("")
        lines.append("Missing inputs to tighten the next pass:")
        lines.extend(f"- {item}" for item in missing_inputs)
    if followup:
        lines.append("")
        lines.append(str(followup.get("message", "")).strip())
    return "\n".join(lines)


def _bom_diagram_pair_plan_for_message(user_message: str) -> list[dict[str, str]]:
    requested = _requested_generation_tools(user_message)
    if not {"generate_bom", "generate_diagram"} <= requested:
        return []
    if _request_references_existing_bom(user_message):
        return []

    scenarios = _extract_numbered_scenarios(user_message)
    if not scenarios:
        scenarios = [{"label": "Scenario 1", "text": str(user_message or "").strip()}]
    return scenarios


def _extract_numbered_scenarios(user_message: str) -> list[dict[str, str]]:
    text = str(user_message or "").strip()
    if not text:
        return []
    matches = list(re.finditer(r"(?:^|[\n\r]|\s)(\d{1,2})[.)]\s+", text))
    if len(matches) < 2:
        return []

    scenarios: list[dict[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        scenario_text = text[start:end].strip(" \t\r\n.;")
        if not scenario_text:
            continue
        number = match.group(1)
        scenarios.append({"label": f"Scenario {number}", "text": scenario_text})
    return scenarios


def _build_scenario_bom_prompt(
    *,
    scenario_label: str,
    scenario_text: str,
    user_message: str,
) -> str:
    lines = [
        f"{scenario_label}: generate the OCI BOM for this architecture option.",
        f"Scenario: {scenario_text.strip()}",
        "Create the BOM first because the diagram will be generated from this BOM result.",
        "Original request:",
        str(user_message or "").strip(),
    ]
    return "\n".join(line for line in lines if line.strip()).strip()


def _bom_result_can_feed_diagram(result_summary: str, result_data: dict[str, Any] | None) -> bool:
    data = dict(result_data or {})
    if _extract_blocking_skill_decision(data):
        return False
    if isinstance(data.get("archie_question_bundle"), dict):
        return False
    if str(data.get("type", "") or "").strip().lower() == "question":
        return False
    if str(data.get("error_code", "") or "").strip():
        return False
    summary = str(result_summary or "").strip().lower()
    if not summary:
        return False
    return "clarification required" not in summary and "not ready" not in summary


def _compact_bom_payload_for_diagram(result_data: dict[str, Any] | None) -> str:
    data = dict(result_data or {})
    payload = data.get("bom_payload", {}) if isinstance(data.get("bom_payload"), dict) else {}
    if not payload:
        return ""
    lines = ["[Generated BOM Context]"]
    line_items = list(payload.get("line_items", []) or [])
    if line_items:
        lines.append("Line items:")
        for idx, item in enumerate(line_items[:20], start=1):
            if not isinstance(item, dict):
                continue
            sku = str(item.get("sku", "") or item.get("part_number", "") or "").strip()
            desc = str(item.get("description", "") or item.get("name", "") or item.get("service", "") or "").strip()
            qty = item.get("quantity", item.get("qty", ""))
            bits = [f"{idx}."]
            if sku:
                bits.append(sku)
            if desc:
                bits.append(desc)
            if qty not in ("", None):
                bits.append(f"qty={qty}")
            lines.append(" ".join(str(bit) for bit in bits if str(bit).strip()))
    totals = payload.get("totals", {}) if isinstance(payload.get("totals"), dict) else {}
    if totals:
        lines.append("Totals: " + json.dumps(totals, ensure_ascii=True, sort_keys=True))
    assumptions = [str(item).strip() for item in payload.get("assumptions", []) or [] if str(item).strip()]
    if assumptions:
        lines.append("Assumptions: " + "; ".join(assumptions[:8]))
    lines.append("[End Generated BOM Context]")
    return "\n".join(lines).strip()


def _build_diagram_bom_text_from_bom_result(
    *,
    scenario_label: str,
    scenario_text: str,
    user_message: str,
    bom_summary: str,
    bom_result_data: dict[str, Any] | None,
) -> str:
    lines = [
        f"{scenario_label}: generate the OCI architecture diagram for this architecture option.",
        f"Scenario: {scenario_text.strip()}",
        "Use the generated BOM below as the source of truth for diagram components.",
        "Represent the core OCI topology: VCN/subnets, connectivity, compute/app tier, data/storage tier, and security controls as supported by the BOM.",
        "Original request:",
        str(user_message or "").strip(),
        "",
        "[Generated BOM Summary]",
        str(bom_summary or "").strip(),
        "[End Generated BOM Summary]",
    ]
    payload_context = _compact_bom_payload_for_diagram(bom_result_data)
    if payload_context:
        lines.extend(["", payload_context])
    return "\n".join(line for line in lines if line is not None).strip()


def _build_paired_bom_diagram_reply(
    scenarios: list[dict[str, str]],
    tool_calls: list[dict[str, Any]],
    *,
    decision_context: dict[str, Any] | None = None,
) -> str:
    lines = ["I built the requested workflow in prerequisite order:"]
    calls_by_scenario: dict[str, list[dict[str, Any]]] = {}
    for call in tool_calls:
        calls_by_scenario.setdefault(str(call.get("scenario_label", "") or "Scenario"), []).append(call)

    for scenario in scenarios:
        label = str(scenario.get("label", "") or "Scenario").strip()
        text = str(scenario.get("text", "") or "").strip()
        lines.append("")
        lines.append(f"{label}: {text}")
        scenario_calls = calls_by_scenario.get(label, [])
        if not scenario_calls:
            lines.append("- No tools executed.")
            continue
        diagram_ran = False
        for call in scenario_calls:
            tool_name = str(call.get("tool", "") or "requested_tool")
            label = _tool_goal_label(tool_name)
            summary = str(call.get("result_summary", "") or "").strip()
            if tool_name == "generate_diagram":
                diagram_ran = True
            lines.append(f"- {label}: {summary or 'completed.'}")
        if not diagram_ran:
            lines.append("- Architecture diagram: skipped until the BOM clarification above is resolved.")

    merged_assumptions = _merge_assumption_lists(
        list((decision_context or {}).get("assumptions", []) or []),
        [],
    )
    for call in tool_calls:
        data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
        merged_assumptions = _merge_assumption_lists(
            merged_assumptions,
            list((data.get("decision_context", {}) or {}).get("assumptions", []) or []),
        )
        merged_assumptions = _merge_assumption_lists(
            merged_assumptions,
            list(data.get("assumptions_used", []) or []),
        )
    assumptions = [
        f"{str(item.get('statement', '') or '').strip()} (risk: {str(item.get('risk', '') or 'low').strip().lower() or 'low'})"
        for item in merged_assumptions
        if str(item.get("statement", "") or "").strip()
    ]
    if assumptions:
        lines.append("")
        lines.append("Assumptions applied:")
        lines.extend(f"- {assumption}" for assumption in assumptions)
    return "\n".join(lines).strip()


def _generation_workflow_plan_for_message(
    *,
    user_message: str,
    requested_tools: set[str],
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not requested_tools:
        return None

    pov_or_jep = requested_tools & {"generate_pov", "generate_jep"}
    if pov_or_jep and not _engagement_context_supports_documents(
        context=context,
        decision_context=decision_context,
        user_message=user_message,
    ):
        docs = " and ".join(_tool_goal_label(tool) for tool in _ordered_requested_tools(pov_or_jep))
        return {
            "status": "ask",
            "message": (
                f"I need engagement context before drafting the {docs}. "
                "Please paste or upload discovery notes, or give me the customer profile, business outcomes, "
                "workload scope, and target OCI architecture."
            ),
        }

    if requested_tools <= {"generate_pov", "generate_jep"}:
        return None

    has_architecture = _has_architecture_definition(context)
    diagram_requested = "generate_diagram" in requested_tools
    bom_requested = "generate_bom" in requested_tools
    diagram_will_be_built = diagram_requested

    terraform_args = {"_user_request_text": user_message, "prompt": user_message}
    terraform_scope_bounded = _terraform_scope_details_are_bounded(
        context=context,
        args=terraform_args,
        decision_context=decision_context,
        user_message=user_message,
    )
    if "generate_terraform" in requested_tools and (
        not terraform_scope_bounded or (not has_architecture and not diagram_will_be_built)
    ):
        lines = ["I need one more set of details before Terraform so the code has a safe boundary:"]
        if not has_architecture and not diagram_will_be_built:
            lines.append("- Architecture definition or diagram context to implement.")
        if not terraform_scope_bounded:
            lines.extend(f"- {item['question']}" for item in _terraform_targeted_questions())
        return {
            "status": "ask",
            "message": "\n".join(lines),
        }

    if "generate_waf" in requested_tools and not has_architecture and not diagram_will_be_built:
        return {
            "status": "ask",
            "message": (
                "I need an architecture diagram before I can run the Well-Architected review. "
                "Ask me to generate the diagram first, or provide the existing diagram context."
            ),
        }

    if "generate_terraform" in requested_tools and not has_architecture and not diagram_will_be_built:
        return {
            "status": "ask",
            "message": (
                "I need an architecture definition or diagram before Terraform. "
                "Provide the architecture context, or ask for the diagram and Terraform together with bounded module, state, and security scope."
            ),
        }

    if diagram_will_be_built and not bom_requested:
        diagram_args = {"_user_request_text": user_message, "bom_text": user_message}
        if not _diagram_has_sufficient_context(
            context=context,
            args=diagram_args,
            user_message=user_message,
        ):
            return {
                "status": "ask",
                "message": (
                    "I need topology context before building the diagram. "
                    "Please describe the major OCI components, network exposure, data tier, and region/DR posture."
                ),
            }

    sequence: list[str] = []
    if bom_requested:
        sequence.append("generate_bom")
    if diagram_will_be_built:
        sequence.append("generate_diagram")
    for tool_name in ("generate_waf", "generate_terraform", "generate_pov", "generate_jep"):
        if tool_name in requested_tools:
            sequence.append(tool_name)

    if not sequence:
        return None

    scenarios = _extract_numbered_scenarios(user_message) if bom_requested and diagram_will_be_built else []
    if not scenarios:
        scenarios = [{"label": "Scenario 1", "text": str(user_message or "").strip()}]

    return {
        "status": "sequence",
        "sequence": sequence,
        "scenarios": scenarios,
        "bom_feeds_diagram": bom_requested and diagram_will_be_built and not _request_references_existing_bom(user_message),
    }


def _ordered_requested_tools(tools: set[str]) -> list[str]:
    order = ["generate_bom", "generate_diagram", "generate_waf", "generate_terraform", "generate_pov", "generate_jep"]
    return [tool for tool in order if tool in tools]


def _engagement_context_supports_documents(
    *,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
    user_message: str,
) -> bool:
    args = {"_user_request_text": user_message, "feedback": user_message}
    return _pov_has_sufficient_context(
        context=context,
        decision_context=decision_context,
        args=args,
        user_message=user_message,
    )


def _workflow_call_is_blocked(call: dict[str, Any]) -> bool:
    result_data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
    if _extract_blocking_skill_decision(result_data):
        return True
    if isinstance(result_data.get("archie_question_bundle"), dict):
        return True
    summary = str(call.get("result_summary", "") or "").lower()
    return "clarification required" in summary or "please upload or paste" in summary


def _build_downstream_workflow_prompt(tool_name: str, scenario_text: str, user_message: str) -> str:
    scenario = str(scenario_text or "").strip()
    request = str(user_message or "").strip()
    if tool_name == "generate_waf":
        intent = "Review the latest generated architecture diagram for OCI Well-Architected risks."
    elif tool_name == "generate_terraform":
        intent = "Draft Terraform for the latest generated architecture diagram using the bounded module, state, and security scope in the request."
    elif tool_name == "generate_pov":
        intent = "Draft the customer POV from the current engagement context and requested workflow."
    elif tool_name == "generate_jep":
        intent = "Draft the JEP from the current engagement context and requested workflow."
    else:
        intent = "Continue the requested generation workflow."
    lines = [intent]
    if scenario:
        lines.append(f"Scenario: {scenario}")
    if request:
        lines.append(f"Original request: {request}")
    return "\n".join(lines).strip()


def _build_generation_workflow_reply(
    workflow_plan: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    *,
    decision_context: dict[str, Any] | None = None,
) -> str:
    sequence = list(workflow_plan.get("sequence", []) or [])
    followup = _workflow_followup_from_calls(tool_calls)
    if len(tool_calls) == 1:
        return _build_parallel_reply(tool_calls, decision_context=decision_context, followup=followup)

    if "generate_bom" in sequence and "generate_diagram" in sequence:
        reply = _build_paired_bom_diagram_reply(
            list(workflow_plan.get("scenarios", []) or []),
            tool_calls,
            decision_context=decision_context,
        )
        if followup:
            return f"{reply}\n\n{followup['message']}".strip()
        return reply

    return _build_parallel_reply(tool_calls, decision_context=decision_context, followup=followup)


def _workflow_followup_from_calls(tool_calls: list[dict[str, Any]]) -> dict[str, str] | None:
    pending_followup: dict[str, str] | None = None
    for call in tool_calls:
        result_data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
        decision = _extract_blocking_skill_decision(result_data)
        if decision:
            pending_followup = _prefer_followup(
                pending_followup,
                {"kind": "blocked", "message": _decision_pushback_text(decision)},
            )
            continue
        followup = _extract_governor_followup(result_data)
        if followup:
            pending_followup = _prefer_followup(pending_followup, followup)
    return pending_followup


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
    if "diagram" in msg or "drawio" in msg:
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


def _is_architecture_chat_only_request(user_message: str, decision_context: dict[str, Any] | None) -> bool:
    requested = _requested_generation_tools(user_message)
    if requested:
        return False
    if isinstance(decision_context, dict) and decision_context.get("conversational_architecture"):
        return True
    msg = str(user_message or "").lower()
    discussion_markers = (
        "architecture options",
        "tradeoffs",
        "trade-offs",
        "which approach",
        "should we",
        "talk through",
        "walk me through",
        "thinking through",
    )
    return any(marker in msg for marker in discussion_markers)


def _build_architecture_chat_reply(
    *,
    user_message: str,
    decision_context: dict[str, Any] | None,
) -> str:
    goal = str((decision_context or {}).get("goal", "") or user_message or "the OCI architecture").strip()
    missing_inputs = [str(item).strip() for item in (decision_context or {}).get("missing_inputs", []) or [] if str(item).strip()]
    assumptions = _render_assumptions(decision_context, limit=3)
    lines = [
        "I'm treating this as an architecture discussion first, not an artifact-generation request.",
        f"Current direction: {goal}",
    ]
    if assumptions:
        lines.append("")
        lines.append("Reasonable defaults to start from:")
        lines.extend(f"- {item}" for item in assumptions)
    if missing_inputs:
        lines.append("")
        lines.append("Decisions still worth confirming:")
        lines.extend(f"- {item}" for item in missing_inputs)
    lines.append("")
    lines.append("If you want, I can turn the agreed direction into a diagram, BOM, POV, or Terraform draft next.")
    return "\n".join(lines).strip()


def _is_change_update_intent(user_message: str) -> bool:
    msg = (user_message or "").lower()
    if _requested_generation_tools(user_message):
        return False
    has_change = any(token in msg for token in ("forgot", "missing", "add", "update", "change", "modify", "we learned", "learned that"))
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


def _is_recall_intent(user_message: str) -> bool:
    msg = (user_message or "").lower()
    if _requested_generation_tools(user_message):
        return False
    return any(
        marker in msg
        for marker in (
            "what did we have before",
            "what did we decide",
            "what do you remember",
            "recall",
            "summarize the current state",
            "what's the current state",
            "what did we learn",
        )
    )


def _build_recall_reply(context: dict[str, Any]) -> str:
    summary = context_store.build_context_summary(context).strip()
    if not summary:
        return "I don't have persisted Archie context for this customer yet."
    return "Here is the latest persisted Archie engagement state:\n\n" + summary


def _build_update_plan_from_context(context: dict[str, Any], *, change_request: str = "") -> list[str]:
    agents = context.get("agents", {}) if isinstance(context, dict) else {}
    available = set(agents.keys()) if isinstance(agents, dict) else set()
    msg = str(change_request or "").lower()

    impact_groups = {
        "architecture": {"diagram", "bom", "waf", "terraform", "pov", "jep"},
        "security": {"diagram", "waf", "terraform", "pov", "jep"},
        "delivery": {"pov", "jep"},
    }
    if any(token in msg for token in ("private", "public", "security", "waf", "iam", "compliance")):
        impacted = set(impact_groups["security"])
    elif any(token in msg for token in ("timeline", "milestone", "workshop", "poc", "objective")):
        impacted = set(impact_groups["delivery"])
    else:
        impacted = set(impact_groups["architecture"])

    tool_map = {
        "bom": "generate_bom",
        "diagram": "generate_diagram",
        "waf": "generate_waf",
        "pov": "generate_pov",
        "jep": "generate_jep",
        "terraform": "generate_terraform",
    }
    ordered_paths = ["bom", "diagram", "waf", "terraform", "pov", "jep"]
    return [tool_map[path] for path in ordered_paths if path in available and path in impacted]


def _infer_superseded_decision_ids(context: dict[str, Any], change_request: str) -> list[str]:
    archie = context_store.get_archie_state(context)
    resolved = archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []
    msg = str(change_request or "").lower()
    matched: list[str] = []
    for item in reversed(resolved):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id", "") or "").lower()
        if not question_id:
            continue
        if "private" in msg or "public" in msg:
            if question_id == "network.exposure":
                matched.append(str(item.get("id", "") or ""))
        if "region" in msg:
            if question_id in {"regions.count", "topology.scope"}:
                matched.append(str(item.get("id", "") or ""))
        if "database" in msg or "data tier" in msg:
            if question_id == "data.tier":
                matched.append(str(item.get("id", "") or ""))
    return [item for item in matched if item]


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
