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
from dataclasses import asdict, dataclass
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
from agent.bom_service import CPU_SKU_TO_MEM_SKU, get_shared_bom_service, new_trace_id
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
- Use accumulated client facts and work products unless the user supersedes them.
- Updated/new deliverables are revisions; compare current facts against the latest work product before deciding.
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
_MEMORY_CONTRACT_TOOLS = {
    "generate_diagram",
    "generate_bom",
    "generate_pov",
    "generate_jep",
    "generate_waf",
    "generate_terraform",
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


@dataclass(frozen=True)
class TurnIntent:
    classification: str
    target_artifact: str = ""
    operation: str = ""
    extracted_corrections: tuple[str, ...] = ()
    confidence: float = 0.0
    candidate_tool: str = ""


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

    def _save_context_note_only(note_text: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        note_key = document_store.save_note(
            store,
            customer_id,
            f"note_{ts}.md",
            note_text.encode("utf-8"),
        )
        _record_saved_note_context(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            note_key=note_key,
            note_text=note_text,
            decision_context=decision_context,
        )
        return note_key

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
        if _message_supersedes_pending_specialist_questions(
            user_message=user_message,
            pending_checkpoint=pending_checkpoint,
        ):
            context_store.clear_pending_checkpoint(context)
            context_store.set_open_questions(context, [])
            await asyncio.to_thread(context_store.write_context, store, customer_id, context)
            pending_checkpoint = None
        else:
            specialist_reply, specialist_call, specialist_artifact = await _handle_pending_specialist_questions(
                pending_checkpoint=pending_checkpoint,
                user_message=user_message,
                conversation_history=history,
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

    persisted_context_summary_before_turn = context_store.build_context_summary(context).strip()
    decision_context = decision_context_builder.build_decision_context(
        user_message=user_message,
        context=context,
    )
    _record_region_constraint_if_present(context, decision_context)
    _record_infrastructure_profile_if_present(context, user_message)
    context_store.set_latest_decision_context(context, decision_context)
    context_store.set_archie_decision_state(
        context,
        constraints=dict(decision_context.get("constraints", {}) or {}),
        assumptions=list(decision_context.get("assumptions", []) or []),
    )
    context_store.refresh_archie_memory(context)
    await asyncio.to_thread(context_store.write_context, store, customer_id, context)

    if _is_note_capture_only_request(user_message):
        note_key = await asyncio.to_thread(_save_context_note_only, user_message)
        return _finalize_turn(f"I saved those customer notes for later use. Key: {note_key}")

    turn_intent = _classify_turn_intent(
        user_message=user_message,
        requested_tools=requested_tools,
        context=context,
    )
    action_intent = _tool_backed_action_intent(user_message, turn_intent=turn_intent)
    if (
        action_intent
        and pending_checkpoint
        and str(pending_checkpoint.get("type", "") or "") in {"assumption_review", "cost_override"}
    ):
        return _finalize_turn(_checkpoint_blocks_artifact_action_reply(pending_checkpoint))

    action_reply = _tool_backed_action_reply(
        user_message=user_message,
        action_intent=action_intent,
        turn_intent=turn_intent,
        requested_tools=requested_tools,
        context=context,
        customer_id=customer_id,
        store=store,
    )
    if action_reply is not None:
        return _finalize_turn(action_reply)

    prompt = _build_prompt(
        history,
        summary,
        user_message,
        decision_context=decision_context,
        pending_checkpoint=context_store.get_pending_checkpoint(context),
    )

    if _is_recall_intent(user_message) and not requested_tools:
        if _is_migration_target_recall_intent(user_message) and not persisted_context_summary_before_turn:
            return _finalize_turn("I don't have a verified migration target recorded for this customer yet.")
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
                        "artifact_key": artifact_key,
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
            return _finalize_turn(_append_management_summary(
                "Confirmed. I executed the approved update sequence in order using the Archie dependency plan:\n"
                f"- Executed tools: {executed}\n"
                "- Review the tool outputs above and confirm if any additional updates are needed.",
                tool_calls,
                decision_context=workflow_decision_context,
            ))

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
                    if _is_bom_revision_request(scenario_text, user_message, context) or (
                        _mentions_bom_work_product(user_message) and _latest_bom_fact_mismatches(context)
                    ):
                        tool_args = {"prompt": user_message}
                    elif _bom_followup_should_hydrate_from_context(
                        prompt=scenario_text,
                        user_message=user_message,
                        context=context,
                        decision_context=decision_context,
                    ):
                        tool_args = {"prompt": user_message}
                    else:
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
                forced_tool = _single_requested_tool_to_force(requested_tools, tool_calls)
                if forced_tool:
                    tool_call = {
                        "tool": forced_tool,
                        "args": _default_generation_tool_args(forced_tool, user_message),
                    }
                    logger.info(
                        "Orchestrator forced requested specialist tool after non-tool reply tool=%s customer=%s raw_preview=%s",
                        forced_tool,
                        customer_id,
                        raw.strip()[:120],
                    )
                elif requested_tools:
                    reply = _deliverable_requires_specialist_reply(requested_tools)
                    logger.info(
                        "Orchestrator blocked requested deliverable after non-tool reply customer=%s raw_preview=%s",
                        customer_id,
                        raw.strip()[:120],
                    )
                    break
                elif action_intent:
                    reply = _tool_required_blocker_reply(user_message, action_intent)
                    logger.info(
                        "Orchestrator blocked tool-backed action after non-tool reply customer=%s raw_preview=%s",
                        customer_id,
                        raw.strip()[:120],
                    )
                    break
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
    from agent.notifications import notify

    if tool_name.startswith("generate_") or tool_name in {"save_notes", "get_summary", "get_document"}:
        notify(f"tool_started:{tool_name}", customer_id, "")

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
        args = _enforce_memory_contract_on_tool_args(
            tool_name=tool_name,
            args=args,
            context=context,
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
    sanitized_tool_input = _postflight_tool_args(tool_name, enriched_args)
    pre_execution_trace = _build_pre_execution_tool_trace(
        tool_name=tool_name,
        enriched_args=enriched_args,
        sanitized_tool_input=sanitized_tool_input,
        decision_context=decision_context,
        context_summary=context_summary,
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
    result_data["trace"] = {
        **pre_execution_trace,
        **(result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}),
    }
    merged_decision_context = _merge_decision_context(decision_context, result_data.get("decision_context"))
    merged_constraint_tags = decision_context_builder.derive_constraint_tags(merged_decision_context)
    applied_skills = list(enriched_args.get("_skill_injected", []) or [])
    result_data["skill_injected"] = bool(applied_skills)
    result_data["applied_skills"] = applied_skills
    result_data["skill_sections"] = dict(enriched_args.get("_skill_sections", {}) or {})
    result_data["skill_versions"] = dict(enriched_args.get("_skill_versions", {}) or {})
    result_data["skill_model_profile"] = str(enriched_args.get("_skill_model_profile", "") or "")
    if tool_name in _MEMORY_CONTRACT_TOOLS:
        result_data["memory_snapshot_hash"] = str(enriched_args.get("_memory_snapshot_hash", "") or "")
        result_data["memory_sections_injected"] = list(enriched_args.get("_memory_sections_injected", []) or [])
        result_data["memory_facts_used"] = list(enriched_args.get("_memory_facts_used", []) or [])
        result_data["memory_unresolved_facts"] = list(enriched_args.get("_memory_unresolved_facts", []) or [])
        result_data["memory_latest_baseline_used"] = dict(enriched_args.get("_memory_latest_baseline_used", {}) or {})
    result_data["decision_context"] = merged_decision_context
    result_data["constraint_tags"] = merged_constraint_tags or list(enriched_args.get("_constraint_tags", []) or [])
    result_data["expert_mode"] = dict(enriched_args.get("_expert_mode", {}) or {})
    result_data["standards_bundle_version"] = str(enriched_args.get("_standards_bundle_version", "") or "")
    result_data["reference_architecture"] = dict(enriched_args.get("_reference_architecture", {}) or {})
    result_data["reference_family"] = str(enriched_args.get("_reference_family", "") or result_data.get("reference_family", "") or "")
    result_data["reference_confidence"] = float(enriched_args.get("_reference_confidence", result_data.get("reference_confidence", 0)) or 0)
    result_data["reference_mode"] = str(enriched_args.get("_reference_mode", result_data.get("reference_mode", "")) or "")
    if tool_name == "generate_bom":
        result_data["bom_context_source"] = str(
            result_data.get("bom_context_source", "")
            or enriched_args.get("_bom_context_source", "")
            or "direct_request"
        )
        if enriched_args.get("_bom_grounding"):
            result_data["_bom_grounding"] = str(enriched_args.get("_bom_grounding", "") or "")
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
            tool_args=_postflight_tool_args(tool_name, enriched_args),
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

    result_summary, artifact_key, result_data = await _archie_expert_review_if_needed(
        tool_name=tool_name,
        args=enriched_args,
        sanitized_tool_input=sanitized_tool_input,
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

        raw_feedback = str(args.get("feedback", "") or "")
        feedback = raw_feedback if "[Archie Canonical Memory]" in raw_feedback else str(args.get("_user_request_text", "") or raw_feedback or "")
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

    raw_prompt = str(args.get("prompt", "") or "").strip()
    clean_request = raw_prompt if "[Archie Canonical Memory]" in raw_prompt else str(args.get("_user_request_text", "") or raw_prompt or "").strip()
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
    structured_inputs = args.get("inputs") if isinstance(args.get("inputs"), dict) else {}
    use_structured_inputs = bool(structured_inputs)
    if use_structured_inputs:
        response = await asyncio.to_thread(
            service.generate_from_inputs,
            inputs=structured_inputs,
            trace_id=trace_id,
            model_id=model_id,
        )
    else:
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
            if use_structured_inputs:
                response = await asyncio.to_thread(
                    service.generate_from_inputs,
                    inputs=structured_inputs,
                    trace_id=trace_id,
                    model_id=model_id,
                )
            else:
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
            "bom_request_shape": "internal_a2a_generate_bom" if use_structured_inputs else "legacy_prompt",
            "bom_trace_stages": [
                "BOM hat selected",
                "structured inputs built" if use_structured_inputs else "legacy prompt prepared",
                "BOM agent called",
                "payload reviewed" if response.get("bom_payload") else "payload pending clarification",
            ],
        }
    )
    response["trace"] = trace
    response["bom_context_source"] = context_source
    if use_structured_inputs:
        response["structured_inputs"] = structured_inputs
        response["structured_inputs_source"] = str(args.get("_bom_inputs_source", "") or "archie_memory_and_current_turn")
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
    "use that information",
    "use that info",
    "that information",
    "that info",
    "for that",
    "from that",
    "from the notes",
    "from saved notes",
    "from the conversation",
    "what it has",
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
    payload["prompt"] = _append_reusable_bom_inputs(payload["prompt"], context)

    if bool(payload.get("_bom_grounded_from_context")):
        return _attach_structured_bom_inputs(payload, context=context, user_message=user_message)

    if _is_bom_revision_request(prompt, user_message, context) or (
        _mentions_bom_work_product(user_message) and _latest_bom_fact_mismatches(context)
    ):
        payload["prompt"] = _build_bom_revision_prompt(
            prompt=prompt,
            user_message=user_message,
            context=context,
            decision_context=decision_context,
        )
        payload["_bom_context_source"] = "bom_revision"
        payload["_bom_grounded_from_context"] = True
        payload["_bom_grounding"] = "revision-grounded"
        return _attach_structured_bom_inputs(payload, context=context, user_message=user_message)

    if not _is_bom_deictic_followup(prompt, user_message):
        return _attach_structured_bom_inputs(payload, context=context, user_message=user_message)

    diagram_ctx = dict(((context or {}).get("agents", {}) or {}).get("diagram", {}) or {})
    if _diagram_context_supports_bom(diagram_ctx, decision_context):
        payload["prompt"] = _build_bom_followup_prompt(
            prompt=prompt,
            diagram_ctx=diagram_ctx,
            decision_context=decision_context,
        )
        payload["_bom_context_source"] = "latest_diagram"
        payload["_bom_grounded_from_context"] = True
        return _attach_structured_bom_inputs(payload, context=context, user_message=user_message)

    archie_context = _build_archie_specialist_context(context, decision_context=decision_context)
    if _text_has_bom_sizing(archie_context):
        payload["prompt"] = _build_bom_context_followup_prompt(
            prompt=prompt,
            archie_context=archie_context,
            decision_context=decision_context,
        )
        payload["_bom_context_source"] = "persisted_notes"
        payload["_bom_grounded_from_context"] = True
        return _attach_structured_bom_inputs(payload, context=context, user_message=user_message)

    payload["_bom_direct_reply"] = _format_bom_followup_clarification()
    payload["_bom_context_source"] = "unresolved_followup"
    payload["_bom_grounded_from_context"] = False
    return payload


def _attach_structured_bom_inputs(
    payload: dict[str, Any],
    *,
    context: dict[str, Any] | None,
    user_message: str,
) -> dict[str, Any]:
    structured_inputs = _build_structured_bom_inputs(
        context=context,
        user_message=user_message,
        prompt=str(payload.get("prompt", "") or ""),
    )
    if structured_inputs:
        payload["inputs"] = structured_inputs
        payload["_bom_request_shape"] = "internal_a2a_generate_bom"
        payload["_bom_inputs_source"] = "archie_memory_and_current_turn"
    else:
        payload.pop("inputs", None)
        payload.pop("_bom_request_shape", None)
        payload.pop("_bom_inputs_source", None)
    return payload


def _build_structured_bom_inputs(
    *,
    context: dict[str, Any] | None,
    user_message: str,
    prompt: str,
) -> dict[str, Any]:
    memory = context_store.get_archie_memory(context or {}) if isinstance(context, dict) else {}
    facts = memory.get("client_facts", {}) if isinstance(memory.get("client_facts"), dict) else {}
    sizing = facts.get("sizing", {}) if isinstance(facts.get("sizing"), dict) else {}
    current_profile = _extract_infrastructure_profile(" ".join([str(prompt or ""), str(user_message or "")]))
    profile = _merge_structured_bom_dicts(sizing, current_profile) if current_profile else dict(sizing)
    combined_text = " ".join(
        part
        for part in (
            str(user_message or ""),
            str(prompt or ""),
            json.dumps(facts, ensure_ascii=True, sort_keys=True) if facts else "",
        )
        if part
    )

    region = _structured_bom_region(facts, combined_text)
    architecture_option = _structured_bom_architecture_option(facts, profile, combined_text)
    ocpu = _structured_bom_ocpu(profile, combined_text)
    memory_gb = _structured_bom_memory_gb(profile, combined_text)
    block_tb = _structured_bom_block_tb(profile, combined_text)
    connectivity = _structured_bom_connectivity(facts, profile, combined_text)
    dr = _structured_bom_dr(facts, profile, combined_text)
    workloads = _structured_list(facts.get("workloads"))
    os_mix = _structured_list(facts.get("os_mix"))
    if not os_mix:
        lower = combined_text.lower()
        os_mix = [label for marker, label in (("linux", "Linux"), ("windows", "Windows")) if marker in lower]

    inputs: dict[str, Any] = {
        "region": region,
        "architecture_option": architecture_option,
        "compute": {"ocpu": ocpu, "gpu": _structured_bom_gpu_requested(facts, combined_text)},
        "memory": {"gb": memory_gb},
        "storage": {"block_tb": block_tb},
        "connectivity": connectivity,
        "dr": dr,
        "workloads": workloads,
        "os_mix": os_mix,
        "output_format": "xlsx" if re.search(r"\b(?:xlsx|excel|spreadsheet|workbook)\b", combined_text, re.I) else "json",
    }
    has_any_sizing = any(value not in (None, "", [], {}) for value in (ocpu, memory_gb, block_tb))
    has_complete_sizing = all(value not in (None, "", [], {}) for value in (ocpu, memory_gb, block_tb))
    return inputs if has_complete_sizing or has_any_sizing else {}


def _structured_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,;/]", value) if part.strip()]
    return []


def _structured_bom_region(facts: dict[str, Any], text: str) -> str:
    explicit = _extract_oci_region(text)
    if explicit:
        return explicit
    region = str(facts.get("region_geography", "") or facts.get("region", "") or "").strip()
    if region.lower() == "south africa":
        return "af-johannesburg-1"
    return region


def _structured_bom_architecture_option(
    facts: dict[str, Any],
    profile: dict[str, Any],
    text: str,
) -> str:
    explicit = str(facts.get("architecture_option", "") or "").strip()
    if explicit:
        return explicit
    haystack = " ".join(
        [
            str(facts.get("platform", "") or ""),
            str(profile.get("platform", "") or ""),
            text,
        ]
    ).lower()
    if any(marker in haystack for marker in ("ocvs", "dedicated vmware", "vmware", "vxrail", "esxi")):
        return "OCI Dedicated VMware Solution"
    return ""


def _structured_bom_ocpu(profile: dict[str, Any], text: str) -> float | None:
    cpu = profile.get("cpu", {}) if isinstance(profile.get("cpu"), dict) else {}
    for key in ("ocpu", "ocpu_equivalent", "target_ocpu", "logical_cores", "cores"):
        value = _coerce_positive_float(cpu.get(key))
        if value is not None:
            return value
    match = re.search(r"(?:equiv(?:alent)?|target|oci[-\s]?equiv(?:alent)?)[^\d]{0,24}(\d+(?:\.\d+)?)\s*o?cpu\b", text, re.I)
    if match:
        return float(match.group(1))
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*o?cpu\b", text, re.I)
    if match:
        return float(match.group(1))
    return None


def _structured_bom_memory_gb(profile: dict[str, Any], text: str) -> float | None:
    memory = profile.get("memory", {}) if isinstance(profile.get("memory"), dict) else {}
    for key in ("gb", "target_gb", "oci_equivalent_gb", "total_gb", "used_gb"):
        value = _coerce_positive_float(memory.get(key))
        if value is not None:
            return _normalize_ram_gb(value)
    match = re.search(r"(?:oci[-\s]?equiv(?:alent)?|target)[^\d]{0,32}(\d+(?:\.\d+)?)\s*(tb|gb)\b[^\n]{0,32}(?:ram|memory)", text, re.I)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb)\s*(?:of\s+)?(?:ram|memory)\b", text, re.I)
    return _ram_capacity_match_to_gb(match) if match else None


def _structured_bom_block_tb(profile: dict[str, Any], text: str) -> float | None:
    storage = profile.get("storage", {}) if isinstance(profile.get("storage"), dict) else {}
    for key in ("block_tb", "target_tb", "oci_equivalent_tb", "used_tb", "total_tb"):
        value = _coerce_positive_float(storage.get(key))
        if value is not None:
            return value
    for key in ("block_gb", "target_gb", "oci_equivalent_gb", "used_gb", "total_gb"):
        value = _coerce_positive_float(storage.get(key))
        if value is not None:
            return value / 1024.0
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(tb|gb)\s*(?:of\s+)?(?:block\s+storage|block\s+volume|storage|vsan|hci|capacity)\b",
        text,
        re.I,
    )
    if not match:
        return _extract_block_storage_tb_from_text(text)
    gb = _capacity_match_to_gb(match)
    direct_tb = gb / 1024.0 if gb is not None else None
    window_tb = _extract_block_storage_tb_from_text(text)
    values = [value for value in (direct_tb, window_tb) if value is not None and value > 0]
    return max(values) if values else None


def _structured_bom_connectivity(
    facts: dict[str, Any],
    profile: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    memory_conn = facts.get("connectivity", {}) if isinstance(facts.get("connectivity"), dict) else {}
    profile_conn = profile.get("connectivity", {}) if isinstance(profile.get("connectivity"), dict) else {}
    conn = _merge_structured_bom_dicts(memory_conn, profile_conn)
    internet = _coerce_positive_float(conn.get("internet_mbps") or conn.get("internet_bandwidth_mbps"))
    if internet is None:
        bandwidth = str(conn.get("internet_bandwidth", "") or "")
        internet = _coerce_positive_float(bandwidth)
    if internet is None:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*mbps\b", text, re.I)
        internet = float(match.group(1)) if match else None
    lower = text.lower()
    return {
        "internet_mbps": internet,
        "mpls": bool(conn.get("mpls")) or "mpls" in lower,
        "sd_wan": bool(conn.get("sd_wan")) or "sd-wan" in lower or "sd wan" in lower,
    }


def _structured_bom_dr(
    facts: dict[str, Any],
    profile: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    memory_dr = facts.get("dr", {}) if isinstance(facts.get("dr"), dict) else {}
    profile_dr = profile.get("dr", {}) if isinstance(profile.get("dr"), dict) else {}
    dr = _merge_structured_bom_dicts(memory_dr, profile_dr)
    rto = _coerce_positive_float(dr.get("rto_hours") or dr.get("sla_hours"))
    if rto is None:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b[^\n]{0,24}(?:dr|rto|restore|sla)", text, re.I)
        if not match:
            match = re.search(r"(?:dr|rto|restore|sla)[^\d]{0,24}(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", text, re.I)
        rto = float(match.group(1)) if match else None
    lower = text.lower()
    return {
        "rto_hours": rto,
        "cross_region_restore": bool(dr.get("cross_region_restore")) or "cross-region restore" in lower or "cross region restore" in lower,
    }


def _structured_bom_gpu_requested(facts: dict[str, Any], text: str) -> bool:
    exclusions = [str(item).lower() for item in facts.get("exclusions", []) or []]
    lower = text.lower()
    if "gpu" in exclusions or re.search(r"\b(?:no|exclude|excluding|out of scope|non[-\s])\s*gpu\b", lower):
        return False
    return bool(re.search(r"\bgpu\b", lower))


def _coerce_positive_float(value: Any) -> float | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    parsed = float(match.group(0))
    return parsed if parsed > 0 else None


def _capacity_match_to_gb(match: re.Match[str]) -> float | None:
    try:
        value = float(match.group(1))
        unit = str(match.group(2) or "gb").lower()
    except Exception:
        return None
    return value * 1024.0 if unit == "tb" else value


def _ram_capacity_match_to_gb(match: re.Match[str]) -> float | None:
    try:
        value = float(match.group(1))
        unit = str(match.group(2) or "gb").lower()
    except Exception:
        return None
    if unit == "tb":
        return value if value >= 128 else value * 1024.0
    return value


def _normalize_ram_gb(value: float) -> float:
    if value >= 128 * 1024 and (value / 1024.0) <= 4096:
        return value / 1024.0
    return value


def _extract_block_storage_tb_from_text(text: str) -> float | None:
    markers = ("block storage", "block volume", "storage", "vsan", "hci", "capacity")
    values: list[float] = []
    raw = str(text or "")
    lowered = raw.lower()
    for match in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(tb|gb)\b", raw, flags=re.IGNORECASE):
        start, end = match.span()
        window = lowered[max(0, start - 48):min(len(lowered), end + 48)]
        if not any(marker in window for marker in markers):
            continue
        if any(skip in window for skip in ("ram", "memory", "egress", "traffic")) and not any(
            marker in window for marker in ("block storage", "block volume", "vsan", "hci")
        ):
            continue
        gb = _capacity_match_to_gb(match)
        if gb is not None and gb > 0:
            values.append(gb / 1024.0)
    return max(values) if values else None


def _merge_structured_bom_dicts(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in dict(incoming or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_structured_bom_dicts(merged[key], value)
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _text_has_bom_sizing(text: str) -> bool:
    lowered = str(text or "").lower()
    has_compute = "ocpu" in lowered or re.search(r"\b\d+(?:\.\d+)?\s*(?:cpu|cores?)\b", lowered) is not None
    has_memory = "ram" in lowered or "memory" in lowered
    has_storage = "storage" in lowered or "block volume" in lowered or re.search(r"\b\d+(?:\.\d+)?\s*tb\b", lowered) is not None
    return has_compute and has_memory and has_storage


def _bom_followup_should_hydrate_from_context(
    *,
    prompt: str,
    user_message: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> bool:
    if not _is_bom_deictic_followup(prompt, user_message):
        return False
    archie_context = _build_archie_specialist_context(context, decision_context=decision_context)
    return _text_has_bom_sizing(archie_context)


def _build_bom_context_followup_prompt(
    *,
    prompt: str,
    archie_context: str,
    decision_context: dict[str, Any] | None,
) -> str:
    lines = [
        "Generate BOM from the persisted customer notes and conversation context.",
        "Use explicit sizing values from the context; do not fall back to default sizing when OCPU, RAM, or storage are present.",
    ]
    cleaned_prompt = _strip_injected_guidance_blocks(prompt).strip()
    if cleaned_prompt:
        lines.append(f"User follow-up: {cleaned_prompt}")
    lines.append("[Persisted Customer Context]")
    lines.append(archie_context)
    if _has_meaningful_decision_context(decision_context):
        lines.append(
            f"Current decision context: {decision_context_builder.summarize_decision_context(decision_context)}"
        )
    lines.append("[End Persisted Customer Context]")
    return "\n".join(lines).strip()


def _is_bom_revision_request(prompt: str, user_message: str, context: dict[str, Any] | None) -> bool:
    if not _mentions_bom_work_product(" ".join([str(prompt or ""), str(user_message or "")])):
        return False
    if _is_pure_download_or_link_request(user_message):
        return False
    msg = f" {str(user_message or prompt or '').lower()} "
    revision_markers = (
        " feedback",
        " pushback",
        " customer asked",
        " customer requested",
        " asked for",
        " only have",
        " you have",
        " missing",
        " too low",
        " too small",
        " should have",
        " should be",
        " need more",
        " needs more",
        " new bom",
        " new xlsx",
        " new workbook",
        " new version",
        " updated bom",
        " updated xlsx",
        " update bom",
        " update the bom",
        " update xlsx",
        " current bom",
        " current xlsx",
        " regenerate",
        " rebuild",
        " revise",
        " revision",
        " incorrect",
        " wrong",
        " not correct",
        " fix the bom",
        " replace the bom",
    )
    if any(marker in msg for marker in revision_markers):
        return True
    latest = context_store.latest_bom_work_product(context or {}) if isinstance(context, dict) else None
    return latest is not None and _latest_bom_fact_mismatches(context)


def _mentions_bom_work_product(text: str) -> bool:
    msg = str(text or "").lower()
    return any(
        marker in msg
        for marker in (
            "bom",
            "bill of materials",
            "xlsx",
            "xlxs",
            "xlsc",
            "excel",
            "spreadsheet",
            "workbook",
            "pricing",
            "priced",
            "sku",
        )
    )


def _build_bom_revision_prompt(
    *,
    prompt: str,
    user_message: str,
    context: dict[str, Any] | None,
    decision_context: dict[str, Any] | None,
) -> str:
    archie = context_store.get_archie_state(context or {}) if isinstance(context, dict) else {}
    facts = archie.get("client_facts", {}) if isinstance(archie.get("client_facts"), dict) else {}
    facts_summary = str(archie.get("facts_summary", "") or "").strip()
    latest = context_store.latest_bom_work_product(context or {}) if isinstance(context, dict) else None
    mismatches = _latest_bom_fact_mismatches(context, as_list=True)
    lines = [
        "Revise the current BOM/XLSX work product from accumulated client facts.",
        "Treat newer client facts as authoritative over the prior BOM baseline.",
        "Update missing or incorrect items and return a final structured bom_payload.",
    ]
    cleaned_prompt = _strip_injected_guidance_blocks(prompt or user_message).strip()
    if cleaned_prompt:
        lines.append(f"User revision request: {cleaned_prompt}")
    turn_corrections = _extract_turn_corrections(user_message or prompt)
    if turn_corrections:
        lines.append("[Corrected Facts From Current Turn]")
        lines.extend(f"- {item}" for item in turn_corrections)
        lines.append("[End Corrected Facts From Current Turn]")
    if facts_summary:
        lines.append(f"Facts summary: {facts_summary}")
    if facts:
        lines.append("[Accumulated Client Facts]")
        lines.append(json.dumps(facts, ensure_ascii=True, sort_keys=True, indent=2)[:4000])
        lines.append("[End Accumulated Client Facts]")
    archie_context = _build_archie_specialist_context(context, decision_context=decision_context)
    if archie_context:
        lines.append("[Current Archie Context]")
        lines.append(archie_context)
        lines.append("[End Current Archie Context]")
    if latest:
        baseline = latest.get("baseline", {}) if isinstance(latest.get("baseline"), dict) else {}
        lines.append("[Prior BOM Baseline]")
        lines.append(json.dumps(baseline, ensure_ascii=True, sort_keys=True, indent=2)[:4000])
        lines.append("[End Prior BOM Baseline]")
    if mismatches:
        lines.append("Explicit deltas/mismatches to correct:")
        lines.extend(f"- {item}" for item in mismatches)
    if _has_meaningful_decision_context(decision_context):
        lines.append(
            f"Current decision context: {decision_context_builder.summarize_decision_context(decision_context)}"
        )
    return "\n".join(lines).strip()


def _latest_bom_fact_mismatches(context: dict[str, Any] | None, *, as_list: bool = False) -> list[str] | bool:
    if not isinstance(context, dict):
        return [] if as_list else False
    archie = context_store.get_archie_state(context)
    facts = archie.get("client_facts", {}) if isinstance(archie.get("client_facts"), dict) else {}
    latest = context_store.latest_bom_work_product(context)
    if not facts or not latest:
        return [] if as_list else False
    baseline = latest.get("baseline", {}) if isinstance(latest.get("baseline"), dict) else {}
    searchable = json.dumps(baseline, ensure_ascii=True, sort_keys=True).lower()
    mismatches: list[str] = []

    fact_region = str(facts.get("region", "") or facts.get("geography", "") or "").strip()
    baseline_region = str(baseline.get("region", "") or "").strip()
    if fact_region and baseline_region and fact_region.lower() != baseline_region.lower():
        mismatches.append(f"region changed from {baseline_region} to {fact_region}")
    if fact_region and not baseline_region:
        mismatches.append(f"region/geography fact is {fact_region} but prior BOM has no region baseline")

    platform = str(facts.get("platform", "") or "").strip()
    if platform and all(token not in searchable for token in ("vmware", "vxrail", "esxi")):
        mismatches.append(f"platform is {platform}; prior BOM baseline does not reflect VMware/VxRail source context")

    security = facts.get("security", {}) if isinstance(facts.get("security"), dict) else {}
    if security.get("waf") and "waf" not in searchable and "web application firewall" not in searchable:
        mismatches.append("WAF is required in current facts but missing from prior BOM baseline")
    if security.get("bastion") and "bastion" not in searchable:
        mismatches.append("bastion is required in current facts but missing from prior BOM baseline")

    connectivity = facts.get("connectivity", {}) if isinstance(facts.get("connectivity"), dict) else {}
    for key, label in (("mpls", "MPLS"), ("sd_wan", "SD-WAN"), ("fastconnect", "FastConnect"), ("vpn", "VPN")):
        if connectivity.get(key) and label.lower() not in searchable.replace("_", "-"):
            mismatches.append(f"{label} connectivity is in current facts but not represented in prior BOM baseline")

    dr = facts.get("dr", {}) if isinstance(facts.get("dr"), dict) else {}
    if dr and all(token not in searchable for token in ("dr", "disaster", "restore", "backup")):
        mismatches.append("DR/restore requirements are in current facts but missing from prior BOM baseline")

    exclusions = facts.get("scope_exclusions") if isinstance(facts.get("scope_exclusions"), list) else []
    if exclusions:
        mismatches.append("scope exclusions to honor: " + ", ".join(str(item) for item in exclusions))
    return mismatches if as_list else bool(mismatches)


def _append_reusable_bom_inputs(prompt: str, context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return str(prompt or "").strip()
    archie = context_store.get_archie_state(context)
    lines: list[str] = []
    facts_summary = str(archie.get("facts_summary", "") or "").strip()
    if facts_summary:
        lines.append(f"- accumulated_client_facts: {facts_summary}")
    lines.extend(_infrastructure_profile_context_lines(context))
    constraints = dict(archie.get("latest_approved_constraints", {}) or {})
    region = str(constraints.get("region", "") or "").strip()
    if region:
        lines.append(f"- constraints.region: {region}")
    resolved = archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []
    seen: set[str] = set()
    for item in reversed(resolved):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id", "") or item.get("id", "") or "").strip()
        if question_id not in {"components.scope", "workload.components", "regions.mode", "region.mode", "topology.scope", "regions.count"}:
            continue
        canonical = "components.scope" if question_id in {"components.scope", "workload.components"} else "regions.mode"
        if canonical in seen:
            continue
        answer = _coerce_specialist_answer(canonical, str(item.get("final_answer", "") or item.get("suggested_answer", "") or ""))
        if answer:
            lines.append(f"- {canonical}: {answer}")
            seen.add(canonical)
    if not lines:
        return str(prompt or "").strip()
    block = "[Archie Reusable Approved Inputs]\n" + "\n".join(lines) + "\n[End Archie Reusable Approved Inputs]"
    cleaned = str(prompt or "").strip()
    if block in cleaned:
        return cleaned
    return f"{cleaned}\n\n{block}".strip()


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
    _record_infrastructure_profile_if_present(context, note_text)
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
    context_store.refresh_archie_memory(context)
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
    facts_summary = str(archie.get("facts_summary", "") or "").strip()
    if facts_summary:
        lines.append(f"Accumulated client facts: {facts_summary}")
    lines.extend(_infrastructure_profile_context_lines(context))
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


def _enforce_memory_contract_on_tool_args(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(args or {})
    if tool_name not in _MEMORY_CONTRACT_TOOLS:
        return payload

    memory = context_store.get_archie_memory(context or {})
    memory_block = context_store.render_archie_memory(memory)
    memory_hash = context_store.archie_memory_hash(memory)
    payload["_memory_snapshot"] = memory
    payload["_memory_snapshot_hash"] = memory_hash
    payload["_memory_sections_injected"] = [
        key for key in ("client_facts", "architecture_state", "work_products", "assumptions") if isinstance(memory.get(key), dict)
    ]
    payload["_memory_facts_used"] = _memory_facts_used(memory)
    payload["_memory_unresolved_facts"] = list(((memory.get("assumptions", {}) or {}).get("unresolved_gaps", []) or []))
    payload["_memory_latest_baseline_used"] = _memory_latest_baseline_used(memory, tool_name)

    primary_key = _tool_primary_input_key(tool_name)
    if primary_key:
        current = _strip_injected_guidance_blocks(str(payload.get(primary_key, "") or "")).strip()
        if "[Archie Canonical Memory]" not in current:
            current = f"{current}\n\n{memory_block}".strip() if current else memory_block
        payload[primary_key] = current
    return payload


def _memory_facts_used(memory: dict[str, Any]) -> list[str]:
    facts = memory.get("client_facts", {}) if isinstance(memory.get("client_facts"), dict) else {}
    used: list[str] = []
    for key in ("region_geography", "platform", "sizing", "workloads", "connectivity", "dr", "security", "exclusions"):
        value = facts.get(key)
        if value not in (None, "", [], {}):
            used.append(key)
    return used


def _memory_latest_baseline_used(memory: dict[str, Any], tool_name: str) -> dict[str, Any]:
    work_products = memory.get("work_products", {}) if isinstance(memory.get("work_products"), dict) else {}
    if tool_name == "generate_bom":
        return dict(work_products.get("latest_bom", {}) or {})
    if tool_name == "generate_diagram":
        return dict(work_products.get("latest_bom", {}) or work_products.get("latest_diagram", {}) or {})
    if tool_name == "generate_waf":
        return dict(work_products.get("latest_diagram", {}) or {})
    if tool_name == "generate_terraform":
        return dict(work_products.get("latest_diagram", {}) or work_products.get("latest_bom", {}) or {})
    if tool_name == "generate_pov":
        return dict(work_products.get("latest_diagram", {}) or work_products.get("latest_bom", {}) or {})
    if tool_name == "generate_jep":
        return dict(work_products.get("latest_pov", {}) or work_products.get("latest_bom", {}) or {})
    return {}


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
        raw_id = str(raw_question.get("id", "") or raw_question.get("question_id", "") or "").strip()
        return {
            "question_id": _stable_specialist_question_id(
                tool_name=tool_name,
                question=question,
                raw_id=raw_id,
                index=index,
            ),
            "question": question,
            "blocking": bool(raw_question.get("blocking", True)),
        }
    if isinstance(raw_question, str) and raw_question.strip():
        question = raw_question.strip()
        return {
            "question_id": _stable_specialist_question_id(
                tool_name=tool_name,
                question=question,
                raw_id="",
                index=index,
            ),
            "question": question,
            "blocking": True,
        }
    return None


def _stable_specialist_question_id(
    *,
    tool_name: str,
    question: str,
    raw_id: str,
    index: int,
) -> str:
    fallback = str(raw_id or f"{tool_name}.q{index}").strip()
    normalized = _normalize_specialist_question_id(fallback)
    if tool_name != "generate_bom":
        return fallback
    if normalized and not re.fullmatch(r"(generate\.bom\.)?q\d+", normalized):
        return fallback
    inferred = _infer_bom_question_id(question)
    return inferred or fallback


def _infer_bom_question_id(question: str) -> str:
    lowered = str(question or "").lower()
    if "region" in lowered:
        if any(token in lowered for token in ("single-region", "multi-region", "multi ad", "multi-ad", "topology")):
            return "regions.mode"
        return "constraints.region"
    if "gpu" in lowered or "non-gpu" in lowered or "accelerator" in lowered:
        return "bom.compute.gpu"
    if "ocpu" in lowered:
        return "bom.compute.ocpu"
    if "memory" in lowered or "ram" in lowered:
        return "bom.compute.memory"
    if "vpu" in lowered or "performance unit" in lowered:
        return "bom.storage.vpu"
    if "object storage" in lowered or "bucket" in lowered:
        return "bom.storage.object"
    if "load balancer" in lowered or re.search(r"\blb\b", lowered):
        return "bom.network.load_balancer"
    if "fastconnect" in lowered or "vpn" in lowered or "connectivity" in lowered or "on-prem" in lowered or "on prem" in lowered:
        return "bom.network.connectivity"
    if "budget" in lowered or "monthly" in lowered or "cost cap" in lowered or "spend" in lowered:
        return "bom.budget"
    if "storage" in lowered or "block volume" in lowered or "block" in lowered:
        return "bom.storage.block"
    if any(token in lowered for token in ("sizing", "quantity", "quantities")):
        return "workload.sizing"
    return ""


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
        if _should_ignore_specialist_question(tool_name, normalized):
            continue
        if normalized:
            bundle.append(normalized)
    return bundle


def _should_ignore_specialist_question(tool_name: str, question: dict[str, Any] | None) -> bool:
    if tool_name != "generate_bom" or not isinstance(question, dict):
        return False
    question_id = str(question.get("question_id", "") or "").strip()
    return bool(question_id) and question_id in _specialist_question_id_aliases("bom.budget")


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
            for alias in _specialist_question_id_aliases(question_id):
                latest[alias] = item
    return latest


def _resolved_answer_for_question(
    resolved: dict[str, dict[str, Any]],
    question_id: str,
) -> tuple[dict[str, Any] | None, str]:
    for alias in _specialist_question_id_aliases(question_id):
        prior = resolved.get(alias)
        if not isinstance(prior, dict):
            continue
        answer = str(prior.get("final_answer", "") or prior.get("suggested_answer", "") or "").strip()
        if answer:
            return prior, _coerce_specialist_answer(question_id, answer)
    return None, ""


def _standard_components_scope_answer() -> str:
    return (
        "all BOM-derived and standard reference architecture components: VCN, public/private subnets, "
        "load balancer, application compute or OKE, database, Object Storage, DRG/connectivity, "
        "WAF/security controls, Vault/KMS, logging, and monitoring"
    )


def _coerce_specialist_answer(question_id: str, answer: str) -> str:
    qid = _normalize_specialist_question_id(question_id)
    cleaned = str(answer or "").strip()
    if qid in {"components.scope", "workload.components"} and cleaned.lower() == "all":
        return _standard_components_scope_answer()
    topology_aliases = {"regions.mode", "region.mode", "topology.scope", "regions.count"}
    if qid in topology_aliases:
        lowered = cleaned.lower()
        if qid == "regions.count":
            if any(token in lowered for token in ("multi-region", "multi region", "two regions", "2 regions")):
                return "2"
            if any(token in lowered for token in ("single", "one region", "1 region", "single ad", "single-ad")):
                return "1"
        elif qid in {"regions.mode", "region.mode", "topology.scope"}:
            if lowered in {"1", "one", "one region"}:
                return "single-region"
            if lowered in {"2", "two", "two regions"}:
                return "multi-region"
    return cleaned


def _record_region_constraint_if_present(context: dict[str, Any], decision_context: dict[str, Any]) -> None:
    constraints = dict((decision_context or {}).get("constraints", {}) or {})
    region = str(constraints.get("region", "") or "").strip()
    if not region:
        return
    context_store.merge_archie_client_facts(context, {"region": region})
    prior, prior_answer = _resolved_answer_for_question(_latest_resolved_answer_map(context), "constraints.region")
    if isinstance(prior, dict) and prior_answer == region:
        return
    context_store.record_resolved_question(
        context,
        {
            "id": str(uuid.uuid4()),
            "question_id": "constraints.region",
            "question": "Preferred OCI region",
            "final_answer": region,
            "source": "archie_region_normalization",
            "confidence": "high",
            "timestamp": _now(),
        },
    )


def _record_infrastructure_profile_if_present(context: dict[str, Any], text: str) -> None:
    profile = _extract_infrastructure_profile(text)
    if profile:
        context_store.merge_archie_infrastructure_profile(context, profile)
    facts = _extract_client_facts(text, profile=profile)
    if facts:
        context_store.merge_archie_client_facts(context, facts)


def _extract_client_facts(text: str, *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = str(text or "")
    lower = raw.lower()
    facts: dict[str, Any] = {}
    infra = profile if isinstance(profile, dict) else {}
    if infra:
        facts["infrastructure"] = infra
        if infra.get("platform"):
            facts["platform"] = infra.get("platform")
        if isinstance(infra.get("connectivity"), dict):
            facts["connectivity"] = dict(infra.get("connectivity", {}) or {})
        if isinstance(infra.get("dr"), dict):
            facts["dr"] = dict(infra.get("dr", {}) or {})
        workloads = infra.get("workload_notes")
        if isinstance(workloads, list) and workloads:
            facts["workloads"] = list(workloads)

    region = _extract_region_or_geography(raw)
    if region:
        if region.startswith("oci:"):
            facts["region"] = region.removeprefix("oci:")
        else:
            facts["geography"] = region

    security: dict[str, Any] = {}
    if "waf" in lower or "web application firewall" in lower:
        security["waf"] = True
    if "bastion" in lower:
        security["bastion"] = True
    if "single ad" in lower or "single availability domain" in lower:
        security["identity_topology"] = "single AD"
    if "active directory" in lower or re.search(r"\bad\b", lower):
        security.setdefault("directory", "Active Directory")
    if security:
        facts["security"] = security

    os_mix = []
    if "linux" in lower:
        os_mix.append("Linux")
    if "windows" in lower:
        os_mix.append("Windows")
    if os_mix:
        facts["os_mix"] = os_mix

    databases = []
    if "sql" in lower or "sql server" in lower:
        databases.append("SQL Server")
    if "oracle db" in lower or "oracle database" in lower:
        databases.append("Oracle Database")
    if databases:
        facts["databases"] = databases

    exclusions = []
    exclusion_patterns = (
        (r"\b(?:exclude|excluding|out of scope|no)\s+gpu\b", "GPU"),
        (r"\b(?:exclude|excluding|out of scope|no)\s+database\b", "database"),
        (r"\b(?:exclude|excluding|out of scope|no)\s+dr\b", "DR"),
        (r"\b(?:exclude|excluding|out of scope|no)\s+waf\b", "WAF"),
    )
    for pattern, label in exclusion_patterns:
        if re.search(pattern, lower):
            exclusions.append(label)
    if exclusions:
        facts["scope_exclusions"] = exclusions
    return facts


def _extract_region_or_geography(text: str) -> str:
    raw = str(text or "")
    region = _extract_oci_region(raw)
    if region:
        return f"oci:{region}"
    lower = raw.lower()
    if "south africa" in lower or "za-" in lower:
        return "South Africa"
    if "united kingdom" in lower or " uk " in f" {lower} ":
        return "United Kingdom"
    if "europe" in lower or "emea" in lower:
        return "Europe/EMEA"
    return ""


def _extract_oci_region(text: str) -> str:
    match = re.search(r"\b([a-z]{2,}-[a-z]+-\d+)\b", str(text or ""), flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _extract_infrastructure_profile(text: str) -> dict[str, Any]:
    raw = str(text or "")
    lower = raw.lower()
    profile: dict[str, Any] = {}

    platforms: list[str] = []
    if "vxrail" in lower or "vx rail" in lower:
        platforms.append("VxRail")
    if "vmware esxi" in lower or re.search(r"\besxi\b", lower):
        platforms.append("VMware ESXi")
    elif "vmware" in lower:
        platforms.append("VMware")
    if platforms:
        profile["platform"] = " / ".join(dict.fromkeys(platforms))

    cpu: dict[str, Any] = {}
    _set_number(cpu, "logical_cores", _extract_number(raw, r"\b(\d+(?:[.,]\d+)?)\s*(?:logical\s+)?(?:cpu\s+)?cores?\b"))
    _set_number(cpu, "sockets", _extract_number(raw, r"\b(\d+(?:[.,]\d+)?)\s*sockets?\b"))
    _set_number(cpu, "cores_per_socket", _extract_number(raw, r"\b(\d+(?:[.,]\d+)?)\s*cores?\s*per\s*socket\b"))
    _set_number(cpu, "used_ghz", _extract_number(raw, r"\b(?:used|consumed|utili[sz]ed)\s*(?:cpu\s*)?(?:capacity\s*)?[:=]?\s*(\d+(?:[.,]\d+)?)\s*ghz\b"))
    _set_number(cpu, "total_ghz", _extract_number(raw, r"\b(?:total|installed|available)\s*(?:cpu\s*)?(?:capacity\s*)?[:=]?\s*(\d+(?:[.,]\d+)?)\s*ghz\b"))
    model = _extract_processor_model(raw)
    if model:
        cpu["processor_model"] = model
    if cpu:
        profile["cpu"] = cpu

    memory = _extract_used_total_capacity(raw, ("memory", "ram"), default_unit="gb")
    if memory:
        profile["memory"] = memory

    storage = _extract_used_total_capacity(raw, ("storage", "datastore", "disk", "capacity"), default_unit="tb")
    if storage:
        profile["storage"] = storage

    connectivity = _extract_connectivity_profile(raw)
    if connectivity:
        profile["connectivity"] = connectivity

    dr = _extract_dr_profile(raw)
    if dr:
        profile["dr"] = dr

    workload_notes = _extract_workload_notes(raw)
    if workload_notes:
        profile["workload_notes"] = workload_notes

    if profile:
        profile["source"] = "chat_discovery"
        profile["updated_at"] = _now()
    return profile


def _set_number(target: dict[str, Any], key: str, value: float | None) -> None:
    if value is None or value <= 0:
        return
    target[key] = int(value) if float(value).is_integer() else value


def _extract_number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(str(match.group(1)).replace(",", ""))
    except Exception:
        return None


def _extract_processor_model(text: str) -> str:
    patterns = (
        r"\b(?:processor|cpu)\s*model\s*[:=-]\s*([^\n;,]+)",
        r"\b((?:intel\s+)?xeon[^\n;,]{0,80})",
        r"\b((?:amd\s+)?epyc[^\n;,]{0,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" .")
    return ""


def _extract_used_total_capacity(text: str, markers: tuple[str, ...], *, default_unit: str) -> dict[str, Any]:
    marker_expr = "|".join(re.escape(marker) for marker in markers)
    unit_expr = r"(tb|tib|gb|gib)"
    patterns = (
        rf"\b(?:{marker_expr})\b[^\n]{{0,80}}?\b(?:used|consumed|utili[sz]ed)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}[^\n]{{0,80}}?\b(?:total|installed|available|capacity)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}",
        rf"\b(?:used|consumed|utili[sz]ed)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}[^\n]{{0,80}}?\b(?:total|installed|available|capacity)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}[^\n]{{0,40}}?\b(?:{marker_expr})\b",
        rf"\b(?:{marker_expr})\b[^\n]{{0,80}}?\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\s*(?:used|consumed|utili[sz]ed)[^\n]{{0,80}}?\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\s*(?:total|installed|available|capacity)",
    )
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
        if not match:
            continue
        used = _capacity_to_unit(float(match.group(1).replace(",", "")), match.group(2), default_unit)
        total = _capacity_to_unit(float(match.group(3).replace(",", "")), match.group(4), default_unit)
        suffix = default_unit.lower()
        return {f"used_{suffix}": used, f"total_{suffix}": total}

    total_match = re.search(
        rf"\b(?:{marker_expr})\b[^\n]{{0,80}}?\b(?:total|installed|available|capacity)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*{unit_expr}",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    if total_match:
        total = _capacity_to_unit(float(total_match.group(1).replace(",", "")), total_match.group(2), default_unit)
        return {f"total_{default_unit.lower()}": total}

    standalone_values: list[float] = []
    standalone_patterns = (
        rf"\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\s*(?:of\s+)?(?:{marker_expr})\b",
        rf"\b(?:{marker_expr})\b[^,\n.;]{{0,40}}?\b(\d+(?:[.,]\d+)?)\s*{unit_expr}\b",
    )
    for pattern in standalone_patterns:
        for match in re.finditer(pattern, str(text or ""), flags=re.IGNORECASE):
            matched_text = match.group(0).lower()
            trailing_text = str(text or "")[match.end():min(len(str(text or "")), match.end() + 32)].lower()
            if default_unit.lower() == "gb" and any(
                marker in f"{matched_text} {trailing_text}"
                for marker in ("block", "storage", "volume", "vsan", "hci", "capacity")
            ):
                continue
            value = _capacity_to_unit(float(match.group(1).replace(",", "")), match.group(2), default_unit)
            if value > 0:
                standalone_values.append(value)
    if standalone_values:
        return {f"total_{default_unit.lower()}": max(standalone_values)}
    return {}


def _capacity_to_unit(value: float, unit: str, target_unit: str) -> float:
    source = str(unit or "").lower()
    target = str(target_unit or "").lower()
    value_gb = value * 1024.0 if source in {"tb", "tib"} else value
    if target == "gb" and source in {"tb", "tib"} and value >= 128:
        value_gb = value
    converted = value_gb / 1024.0 if target == "tb" else value_gb
    return int(converted) if float(converted).is_integer() else round(converted, 2)


def _extract_connectivity_profile(text: str) -> dict[str, Any]:
    lower = str(text or "").lower()
    connectivity: dict[str, Any] = {}
    internet = re.search(r"\binternet(?:\s+bandwidth)?\s*[:=-]?\s*(\d+(?:[.,]\d+)?)\s*(gbps|mbps)\b", text, flags=re.IGNORECASE)
    if internet:
        unit = "Gbps" if internet.group(2).lower() == "gbps" else "Mbps"
        connectivity["internet_bandwidth"] = f"{internet.group(1).replace(',', '')} {unit}"
    if "mpls" in lower:
        connectivity["mpls"] = True
    if "sd-wan" in lower or "sd wan" in lower:
        connectivity["sd_wan"] = True
    if "fastconnect" in lower:
        connectivity["fastconnect"] = True
    if "vpn" in lower:
        connectivity["vpn"] = True
    return connectivity


def _extract_dr_profile(text: str) -> dict[str, Any]:
    lower = str(text or "").lower()
    dr: dict[str, Any] = {}
    if "cross-region" in lower or "cross region" in lower:
        dr["cross_region_restore"] = "restore" in lower or "dr" in lower or "disaster recovery" in lower
    sla = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:hour|hr|h)\s*(?:sla|rto|restore|recovery)\b", text, flags=re.IGNORECASE)
    if not sla:
        sla = re.search(r"\b(?:sla|rto|restore|recovery)\s*(?:of|within|<=|:)?\s*(\d+(?:[.,]\d+)?)\s*(?:hour|hr|h)s?\b", text, flags=re.IGNORECASE)
    if sla:
        hours = float(sla.group(1).replace(",", ""))
        dr["sla_hours"] = int(hours) if hours.is_integer() else hours
    elif "24h" in lower or "24 h" in lower or "24-hour" in lower:
        dr["sla_hours"] = 24
    return dr


def _extract_workload_notes(text: str) -> list[str]:
    lower = str(text or "").lower()
    markers = (
        ("dc", "domain controllers"),
        ("domain controller", "domain controllers"),
        ("sql", "SQL databases"),
        ("oracle db", "Oracle databases"),
        ("oracle database", "Oracle databases"),
        ("custom app", "custom applications"),
        ("patch repo", "patch repository"),
        ("file server", "file servers"),
    )
    notes: list[str] = []
    for token, label in markers:
        if token in lower and label not in notes:
            notes.append(label)
    return notes


def _infrastructure_profile_context_lines(context: dict[str, Any] | None) -> list[str]:
    if not isinstance(context, dict):
        return []
    archie = context_store.get_archie_state(context)
    profile = archie.get("infrastructure_profile", {}) if isinstance(archie.get("infrastructure_profile"), dict) else {}
    if not profile:
        return []
    lines = ["Infrastructure profile:"]
    platform = str(profile.get("platform", "") or "").strip()
    if platform:
        lines.append(f"- platform: {platform}")
    cpu = profile.get("cpu", {}) if isinstance(profile.get("cpu"), dict) else {}
    if cpu:
        bits = []
        for key, label in (
            ("logical_cores", "logical cores"),
            ("sockets", "sockets"),
            ("cores_per_socket", "cores/socket"),
            ("processor_model", "processor"),
            ("used_ghz", "used GHz"),
            ("total_ghz", "total GHz"),
        ):
            if cpu.get(key) not in (None, "", [], {}):
                bits.append(f"{label}={cpu.get(key)}")
        if bits:
            lines.append("- CPU: " + ", ".join(bits))
    memory = profile.get("memory", {}) if isinstance(profile.get("memory"), dict) else {}
    if memory:
        lines.append("- memory: " + ", ".join(f"{key}={value}" for key, value in memory.items() if value not in (None, "", [], {})))
    storage = profile.get("storage", {}) if isinstance(profile.get("storage"), dict) else {}
    if storage:
        lines.append("- storage: " + ", ".join(f"{key}={value}" for key, value in storage.items() if value not in (None, "", [], {})))
    connectivity = profile.get("connectivity", {}) if isinstance(profile.get("connectivity"), dict) else {}
    if connectivity:
        lines.append("- connectivity: " + ", ".join(f"{key}={value}" for key, value in connectivity.items() if value not in (None, "", [], {})))
    dr = profile.get("dr", {}) if isinstance(profile.get("dr"), dict) else {}
    if dr:
        lines.append("- DR: " + ", ".join(f"{key}={value}" for key, value in dr.items() if value not in (None, "", [], {})))
    workload_notes = [str(item).strip() for item in profile.get("workload_notes", []) or [] if str(item).strip()]
    if workload_notes:
        lines.append("- workload notes: " + ", ".join(workload_notes))
    return lines


def _first_profile_value(section: dict[str, Any], keys: tuple[str, ...]) -> Any:
    if not isinstance(section, dict):
        return None
    for key in keys:
        value = section.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _largest_profile_value(section: dict[str, Any], keys: tuple[str, ...]) -> Any:
    if not isinstance(section, dict):
        return None
    numeric = [section.get(key) for key in keys if isinstance(section.get(key), (int, float))]
    if numeric:
        return max(numeric)
    return _first_profile_value(section, keys)


def _infrastructure_profile_ocpu_answer(profile: dict[str, Any]) -> str:
    cpu = profile.get("cpu", {}) if isinstance(profile, dict) and isinstance(profile.get("cpu"), dict) else {}
    value = _first_profile_value(cpu, ("logical_cores", "cores", "ocpu"))
    if value in (None, "", [], {}):
        return ""
    return f"{value:g} OCPU equivalent" if isinstance(value, (int, float)) else f"{value} OCPU equivalent"


def _infrastructure_profile_memory_answer(profile: dict[str, Any]) -> str:
    memory = profile.get("memory", {}) if isinstance(profile, dict) and isinstance(profile.get("memory"), dict) else {}
    value = _largest_profile_value(memory, ("used_gb", "total_gb"))
    if value in (None, "", [], {}):
        return ""
    return f"{value:g} GB RAM" if isinstance(value, (int, float)) else f"{value} GB RAM"


def _infrastructure_profile_storage_answer(profile: dict[str, Any]) -> str:
    storage = profile.get("storage", {}) if isinstance(profile, dict) and isinstance(profile.get("storage"), dict) else {}
    value = _largest_profile_value(storage, ("used_tb", "total_tb"))
    if value not in (None, "", [], {}):
        return f"{value:g} TB block storage" if isinstance(value, (int, float)) else f"{value} TB block storage"
    gb_value = _largest_profile_value(storage, ("used_gb", "total_gb"))
    if gb_value not in (None, "", [], {}):
        return f"{gb_value:g} GB block storage" if isinstance(gb_value, (int, float)) else f"{gb_value} GB block storage"
    return ""


def _infrastructure_profile_connectivity_answer(profile: dict[str, Any]) -> str:
    connectivity = profile.get("connectivity", {}) if isinstance(profile, dict) and isinstance(profile.get("connectivity"), dict) else {}
    if not connectivity:
        return ""
    parts: list[str] = []
    bandwidth = str(connectivity.get("internet_bandwidth", "") or "").strip()
    if bandwidth:
        parts.append(f"internet bandwidth {bandwidth}")
    if connectivity.get("mpls"):
        parts.append("MPLS")
    if connectivity.get("sd_wan"):
        parts.append("SD-WAN")
    if connectivity.get("fastconnect"):
        parts.append("FastConnect connectivity")
    if connectivity.get("vpn"):
        parts.append("site-to-site VPN")
    return ", ".join(parts)


def _infrastructure_profile_sizing_answer(profile: dict[str, Any]) -> str:
    parts = [
        _infrastructure_profile_ocpu_answer(profile),
        _infrastructure_profile_memory_answer(profile),
        _infrastructure_profile_storage_answer(profile),
    ]
    rendered = [part for part in parts if part]
    return ", ".join(rendered) if len(rendered) >= 2 else ""


def _component_labels_from_text(text: str) -> list[str]:
    lowered = str(text or "").lower()
    labels: list[str] = []
    markers = (
        ("oke", "OKE"),
        ("kubernetes", "OKE"),
        ("load balancer", "Load Balancer"),
        ("lb", "Load Balancer"),
        ("database", "Database"),
        ("autonomous database", "Autonomous Database"),
        ("adb", "Autonomous Database"),
        ("postgres", "PostgreSQL"),
        ("mysql", "MySQL"),
        ("object storage", "Object Storage"),
        ("bucket", "Object Storage"),
        ("waf", "WAF"),
        ("vcn", "VCN"),
        ("subnet", "Subnets"),
        ("drg", "DRG"),
        ("fastconnect", "FastConnect"),
        ("vpn", "VPN"),
        ("vault", "Vault/KMS"),
        ("kms", "Vault/KMS"),
        ("monitoring", "Monitoring"),
        ("logging", "Logging"),
        ("compute", "Compute"),
        ("app server", "Compute"),
        ("web server", "Compute"),
    )
    for token, label in markers:
        if token in lowered and label not in labels:
            labels.append(label)
    return labels


def _infer_components_scope_from_context(context: dict[str, Any], text: str) -> tuple[str, str, str]:
    labels = _component_labels_from_text(text)
    agents = context.get("agents", {}) if isinstance(context, dict) else {}
    bom = dict((agents or {}).get("bom", {}) or {})
    diagram = dict((agents or {}).get("diagram", {}) or {})
    if int(bom.get("line_item_count", 0) or 0) > 0:
        return _standard_components_scope_answer(), "prior BOM line items are available for component scope", "high"
    if int(diagram.get("node_count", 0) or 0) > 0 or str(diagram.get("deployment_summary", "") or "").strip():
        return _standard_components_scope_answer(), "latest generated architecture state is available for component scope", "high"
    if labels:
        return ", ".join(labels), "current BOM, notes, or architecture context names these OCI components", "high"
    lowered = str(text or "").lower()
    if "small site" in lowered or "small-site" in lowered or "reference architecture" in lowered:
        return _standard_components_scope_answer(), "standard small-site reference architecture provides the component scope", "medium"
    return "", "", "needs_confirmation"


def _extract_first_number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(str(match.group(1)).replace(",", ""))
    except Exception:
        return None


def _format_quantity(value: float, unit: str) -> str:
    rendered = f"{value:g}"
    return f"{rendered} {unit}"


def _extract_ocpu_answer(text: str) -> str:
    value = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*ocpus?\b")
    return _format_quantity(value, "OCPU") if value is not None and value > 0 else ""


def _extract_memory_answer(text: str) -> str:
    capacities = _extract_used_total_capacity(text, ("memory", "ram"), default_unit="gb")
    capacity_values = [value for key, value in capacities.items() if key.endswith("_gb") and isinstance(value, (int, float))]
    if capacity_values:
        return _format_quantity(max(capacity_values), "GB RAM")
    value = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\s*(?:memory|ram)\b")
    if value is None:
        value = _extract_first_number(text, r"\b(?:memory|ram)\s*(?:of|:|=)?\s*(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\b")
    return _format_quantity(value, "GB RAM") if value is not None and value > 0 else ""


def _extract_block_storage_answer(text: str) -> str:
    capacities = _extract_used_total_capacity(text, ("storage", "datastore", "disk", "capacity"), default_unit="tb")
    tb_values = [value for key, value in capacities.items() if key.endswith("_tb") and isinstance(value, (int, float))]
    if tb_values:
        return _format_quantity(max(tb_values), "TB block storage")
    tb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:tb|tib)\s*(?:block|block volume|volume|storage)\b")
    if tb is not None and tb > 0:
        return _format_quantity(tb, "TB block storage")
    gb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\s*(?:block|block volume|volume|storage)\b")
    if gb is None:
        gb = _extract_first_number(text, r"\b(?:block|block volume|volume|storage)\s*(?:of|:|=)?\s*(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\b")
    return _format_quantity(gb, "GB block storage") if gb is not None and gb > 0 else ""


def _extract_object_storage_answer(text: str) -> str:
    tb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:tb|tib)\s*object storage\b")
    if tb is not None and tb > 0:
        return _format_quantity(tb, "TB Object Storage")
    gb = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*(?:gb|gib)\s*object storage\b")
    if gb is not None and gb > 0:
        return _format_quantity(gb, "GB Object Storage")
    lowered = str(text or "").lower()
    if "object storage" in lowered or "bucket" in lowered:
        return "include Object Storage"
    return ""


def _extract_vpu_answer(text: str) -> tuple[str, str]:
    value = _extract_first_number(text, r"\b(\d+(?:[.,]\d+)?)\s*vpus?\s*/?\s*(?:gb|gib)?\b")
    if value is not None and value > 0:
        return _format_quantity(value, "VPU/GB"), "current request provides Block Volume performance units"
    lowered = str(text or "").lower()
    if "balanced" in lowered and ("block" in lowered or "volume" in lowered or "storage" in lowered):
        return "Balanced Block Volume performance, 10 VPU/GB", "current request names Balanced Block Volume performance"
    return "", ""


def _combined_bom_sizing_answer(text: str) -> tuple[str, str, str]:
    parts: list[str] = []
    ocpu = _extract_ocpu_answer(text)
    memory = _extract_memory_answer(text)
    storage = _extract_block_storage_answer(text)
    if ocpu:
        parts.append(ocpu)
    if memory:
        parts.append(memory)
    if storage:
        parts.append(storage)
    if len(parts) >= 2:
        return ", ".join(parts), "current request/context provides multiple BOM sizing inputs", "high"
    if parts:
        return parts[0], "current request/context provides partial BOM sizing input", "medium"
    return "", "", "needs_confirmation"


def _suggest_answer_for_question(
    question: dict[str, Any],
    *,
    context: dict[str, Any],
    user_message: str,
) -> tuple[str, str, str]:
    question_id = str(question.get("question_id", "") or "").strip()
    prompt = str(question.get("question", "") or "").strip()
    resolved = _latest_resolved_answer_map(context)
    prior, answer = _resolved_answer_for_question(resolved, question_id)
    if isinstance(prior, dict) and answer:
        return answer, "prior Archie-approved decision", "high"

    archie = context_store.get_archie_state(context)
    infrastructure_profile = archie.get("infrastructure_profile", {}) if isinstance(archie.get("infrastructure_profile"), dict) else {}
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
    qid = _normalize_specialist_question_id(question_id)
    prompt_lc = prompt.lower()

    if qid in _specialist_question_id_aliases("constraints.region") or (
        "region" in prompt_lc and not any(token in prompt_lc for token in ("single-region", "multi-region", "multi ad", "multi-ad", "topology"))
    ):
        region = str(constraints.get("region", "") or "").strip()
        if region:
            return region, "latest decision context already has the target OCI region", "high"
        if "bom" in str(user_message or "").lower() or "pricing" in str(user_message or "").lower():
            return (
                "pricing-only estimate; treat OCI pricing as region-consistent for this draft",
                "BOM pricing-only flow does not require a pinned OCI deployment region",
                "medium",
            )

    if qid in _specialist_question_id_aliases("regions.mode") or any(
        token in prompt_lc for token in ("single-region", "multi-region", "multi ad", "multi-ad", "topology")
    ):
        if any(token in text for token in ("multi-region", "multi region", "two regions", "2 regions")):
            if question_id == "regions.count":
                return "2", "current Archie context mentions a multi-region topology", "high"
            return "multi-region", "current Archie context mentions a multi-region topology", "high"
        region = str(constraints.get("region", "") or "").strip()
        if region or "single region" in text or "single-region" in text:
            if question_id == "regions.count":
                return "1", "latest decision context has a single primary region", "medium"
            return "single-region", "latest decision context has a single primary region", "medium"

    if qid in _specialist_question_id_aliases("bom.compute.gpu"):
        if any(token in text for token in ("non-gpu", "non gpu", "no gpu", "without gpu", "cpu-only", "cpu only")):
            return "non-GPU compute", "current request/context explicitly excludes GPU compute", "high"
        if "gpu" in text or "accelerator" in text:
            return "GPU compute", "current request/context mentions GPU or accelerator compute", "high"
        if infrastructure_profile:
            return "non-GPU compute", "no GPU or accelerator requirement is present in the saved infrastructure profile", "medium"

    if qid in _specialist_question_id_aliases("bom.compute.ocpu"):
        answer = _extract_ocpu_answer(text)
        if answer:
            return answer, "current request/context provides OCPU sizing", "high"
        answer = _infrastructure_profile_ocpu_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides CPU sizing", "high"

    if qid in _specialist_question_id_aliases("bom.compute.memory"):
        answer = _extract_memory_answer(text)
        if answer:
            return answer, "current request/context provides memory sizing", "high"
        answer = _infrastructure_profile_memory_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides memory sizing", "high"

    if qid in _specialist_question_id_aliases("bom.storage.block"):
        answer = _extract_block_storage_answer(text)
        if answer:
            return answer, "current request/context provides block storage sizing", "high"
        answer = _infrastructure_profile_storage_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides storage sizing", "high"

    if qid in _specialist_question_id_aliases("bom.storage.vpu"):
        answer, basis = _extract_vpu_answer(text)
        if answer:
            return answer, basis, "high"
        if _extract_block_storage_answer(text):
            return "Balanced Block Volume performance, 10 VPU/GB", "current BOM service default for block storage performance", "medium"

    if qid in _specialist_question_id_aliases("bom.network.load_balancer"):
        if any(token in text for token in ("no load balancer", "without load balancer", "no lb", "without lb")):
            return "do not include a load balancer", "current request/context excludes a load balancer", "high"
        if any(token in text for token in ("load balancer", "flexible lb", " lb ", "ingress", "public web", "external users", "internet")):
            return "include one OCI Flexible Load Balancer", "current request/context indicates ingress or load balancing", "high"

    if qid in _specialist_question_id_aliases("bom.storage.object"):
        if any(token in text for token in ("no object storage", "without object storage", "no bucket")):
            return "do not include Object Storage", "current request/context excludes Object Storage", "high"
        answer = _extract_object_storage_answer(text)
        if answer:
            return answer, "current request/context includes Object Storage or bucket scope", "high"

    if qid in _specialist_question_id_aliases("bom.network.connectivity"):
        answer = _infrastructure_profile_connectivity_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides connectivity facts", "high"
        if "fastconnect" in text:
            return "FastConnect connectivity", "current request/context mentions FastConnect", "high"
        if "vpn" in text:
            return "site-to-site VPN connectivity", "current request/context mentions VPN", "high"
        if any(token in text for token in ("on-prem", "on prem", "onprem", "drg")):
            return "private connectivity through DRG", "current request/context mentions on-premises connectivity or DRG", "medium"

    if qid in _specialist_question_id_aliases("workload.sizing"):
        answer, basis, confidence = _combined_bom_sizing_answer(text)
        if answer:
            return answer, basis, confidence
        answer = _infrastructure_profile_sizing_answer(infrastructure_profile)
        if answer:
            return answer, "saved infrastructure profile provides CPU, memory, and storage footprint", "high"

    if qid in _specialist_question_id_aliases("network.exposure") or "public, private, or both" in prompt_lc:
        has_private = "private" in text
        has_public = "public" in text or "internet" in text
        if has_private and has_public:
            return "both", "notes mention both private and public exposure", "medium"
        if has_private:
            return "private", "notes emphasize private networking/exposure", "high"
        if has_public:
            return "public", "notes mention public or internet ingress", "high"

    if question_id in {"workload.components", "components.scope"} or "major oci components" in prompt_lc:
        components, basis, confidence = _infer_components_scope_from_context(context, text)
        if components:
            return components, basis, confidence

    if question_id == "data.tier" or "data tier" in prompt_lc:
        if "autonomous database" in text or "adb" in text:
            return "Autonomous Database", "notes mention Autonomous Database", "high"
        if "postgres" in text:
            return "PostgreSQL", "notes mention PostgreSQL", "high"
        if "mysql" in text:
            return "MySQL", "notes mention MySQL", "high"
        if "database" in text or "data tier" in text:
            return "generic database node", "notes imply a data tier without a pinned engine", "medium"

    if qid in _specialist_question_id_aliases("bom.budget") or "budget" in prompt_lc or "monthly" in prompt_lc:
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
            answer = _coerce_specialist_answer(question_id, answer)
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


def _resolved_inputs_from_answers(answers: list[dict[str, Any]]) -> list[dict[str, str]]:
    resolved_inputs: list[dict[str, str]] = []
    for item in answers:
        question_id = str(item.get("question_id", "") or item.get("id", "") or "question").strip()
        answer = str(item.get("final_answer", "") or item.get("suggested_answer", "") or "").strip()
        if not question_id or not answer:
            continue
        resolved_inputs.append(
            {
                "question_id": question_id,
                "question": str(item.get("question", "") or "").strip(),
                "answer": _coerce_specialist_answer(question_id, answer),
                "basis": str(item.get("basis", "") or "").strip(),
                "confidence": str(item.get("confidence", "") or "").strip(),
            }
        )
    return resolved_inputs


def _attach_bom_resolved_inputs(
    result_data: dict[str, Any],
    answers: list[dict[str, Any]],
) -> None:
    if not isinstance(result_data, dict):
        return
    payload = result_data.get("bom_payload")
    if not isinstance(payload, dict):
        return
    resolved_inputs = _resolved_inputs_from_answers(answers)
    if not resolved_inputs:
        return
    existing = payload.get("resolved_inputs") if isinstance(payload.get("resolved_inputs"), list) else []
    by_id: dict[str, dict[str, str]] = {
        str(item.get("question_id", "") or ""): dict(item)
        for item in existing
        if isinstance(item, dict) and str(item.get("question_id", "") or "").strip()
    }
    for item in resolved_inputs:
        by_id[item["question_id"]] = item
    payload["resolved_inputs"] = list(by_id.values())


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
    if isinstance(decision_context, dict) and decision_context:
        context_store.set_latest_decision_context(context, decision_context)

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
    if tool_name == "generate_bom":
        _attach_bom_resolved_inputs(rerun_data, auto_answered)
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

    overrides = _parse_explicit_specialist_answers(
        pending_checkpoint=pending_checkpoint,
        user_message=user_message,
    )

    answers = []
    for item in questions:
        question_id = str(item.get("question_id", "") or "").strip()
        final_answer = overrides.get(question_id, "")
        if not final_answer and len(questions) == 1 and str(user_message or "").strip() and ":" not in str(user_message or ""):
            final_answer = str(user_message or "").strip()
        if not final_answer:
            final_answer = str(item.get("suggested_answer", "") or "").strip()
        if final_answer:
            final_answer = _coerce_specialist_answer(question_id, final_answer)
            answers.append({**item, "final_answer": final_answer})
    return answers


def _specialist_question_id_map(questions: list[dict[str, Any]]) -> dict[str, str]:
    question_ids = [
        str(item.get("question_id", "") or "").strip()
        for item in questions
        if str(item.get("question_id", "") or "").strip()
    ]
    question_id_map: dict[str, str] = {}
    for question_id in question_ids:
        for alias in _specialist_question_id_aliases(question_id):
            question_id_map.setdefault(alias, question_id)
    return question_id_map


def _parse_explicit_specialist_answers(
    *,
    pending_checkpoint: dict[str, Any],
    user_message: str,
) -> dict[str, str]:
    questions = [dict(item) for item in list(pending_checkpoint.get("questions", []) or []) if isinstance(item, dict)]
    question_id_map = _specialist_question_id_map(questions)
    overrides: dict[str, str] = {}
    for line in str(user_message or "").splitlines():
        parsed = _parse_specialist_answer_line(line, question_id_map)
        if not parsed:
            continue
        qid, value = parsed
        overrides[qid] = value
    return overrides


def _normalize_specialist_question_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", str(value or "").strip().lower()).strip(".")


def _specialist_question_id_aliases(question_id: str) -> set[str]:
    normalized = _normalize_specialist_question_id(question_id)
    aliases = {normalized} if normalized else set()
    alias_groups = (
        {"components.scope", "workload.components"},
        {"regions.mode", "region.mode", "topology.scope", "regions.count"},
        {"constraints.region", "region", "preferred.region"},
        {"bom.compute.gpu", "compute.gpu", "gpu", "gpu.mode", "compute.type"},
        {"bom.compute.ocpu", "compute.ocpu", "ocpu", "workload.ocpu"},
        {"bom.compute.memory", "compute.memory", "memory", "ram", "workload.memory"},
        {"bom.storage.block", "storage.block", "block.storage", "block.volume", "storage"},
        {"bom.storage.vpu", "storage.vpu", "vpu", "block.vpu"},
        {"bom.network.load.balancer", "network.load.balancer", "load.balancer", "lb"},
        {"bom.storage.object", "storage.object", "object.storage", "bucket"},
        {"bom.network.connectivity", "network.connectivity", "connectivity", "on.prem.connectivity"},
        {"bom.budget", "budget", "monthly.budget", "cost.max.monthly"},
        {"workload.sizing", "bom.sizing", "sizing"},
    )
    for group in alias_groups:
        if normalized in group:
            aliases.update(group)
    parts = [part for part in normalized.split(".") if part]
    if len(parts) >= 2:
        first = parts[0]
        tail = ".".join(parts[1:])
        if first.endswith("s"):
            aliases.add(".".join([first[:-1], tail]))
        else:
            aliases.add(".".join([first + "s", tail]))
    return aliases


def _parse_specialist_answer_line(
    line: str,
    question_id_map: dict[str, str],
) -> tuple[str, str] | None:
    text = str(line or "").strip()
    if not text:
        return None
    for separator in (":", ","):
        if separator in text:
            raw_id, raw_answer = text.split(separator, 1)
            canonical = question_id_map.get(_normalize_specialist_question_id(raw_id))
            answer = raw_answer.strip()
            if canonical and answer:
                return canonical, answer

    for alias in sorted(question_id_map, key=len, reverse=True):
        display_alias = alias.replace(".", r"\s*\.\s*")
        match = re.match(rf"^\s*{display_alias}\s*\.\s+(.+?)\s*$", text, flags=re.IGNORECASE)
        if match:
            answer = match.group(1).strip()
            if answer:
                return question_id_map[alias], answer
    return None


def _message_supersedes_pending_specialist_questions(
    *,
    user_message: str,
    pending_checkpoint: dict[str, Any],
) -> bool:
    if not _requested_generation_tools(user_message):
        return False
    if _is_specialist_question_approve_message(user_message) or _is_checkpoint_reject_message(user_message):
        return False
    if _parse_explicit_specialist_answers(
        pending_checkpoint=pending_checkpoint,
        user_message=user_message,
    ):
        return False
    return True


def _is_specialist_question_retry_message(user_message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(user_message or "").lower()).strip()
    if not normalized:
        return False
    retry_messages = {
        "try again",
        "please try again",
        "try again please",
        "retry",
        "rerun",
        "run again",
        "continue",
        "go ahead",
        "proceed",
        "please continue",
        "please retry",
        "try it again",
        "run it again",
    }
    retry_phrases = ("try again", "retry", "rerun", "run again", "continue", "proceed")
    return normalized in retry_messages or any(phrase in normalized for phrase in retry_phrases)


def _recover_specialist_answers_from_history(
    *,
    pending_checkpoint: dict[str, Any],
    conversation_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    questions = [dict(item) for item in list(pending_checkpoint.get("questions", []) or []) if isinstance(item, dict)]
    required_ids = {
        str(item.get("question_id", "") or "").strip()
        for item in questions
        if str(item.get("question_id", "") or "").strip()
    }
    if not required_ids:
        return []

    prompt = str(pending_checkpoint.get("prompt", "") or "").strip()
    start_index = -1
    if prompt:
        for idx, turn in enumerate(conversation_history or []):
            if str(turn.get("role", "") or "") != "assistant":
                continue
            content = str(turn.get("content", "") or "")
            if content.strip() == prompt or prompt in content:
                start_index = idx

    candidate_turns = [
        turn
        for turn in list(conversation_history or [])[start_index + 1 :]
        if str(turn.get("role", "") or "") == "user"
    ]
    for turn in reversed(candidate_turns):
        overrides = _parse_explicit_specialist_answers(
            pending_checkpoint=pending_checkpoint,
            user_message=str(turn.get("content", "") or ""),
        )
        if required_ids <= set(overrides):
            return [
                {
                    **item,
                    "final_answer": _coerce_specialist_answer(
                        str(item.get("question_id", "") or "").strip(),
                        overrides[str(item.get("question_id", "") or "").strip()],
                    ),
                }
                for item in questions
                if str(item.get("question_id", "") or "").strip() in overrides
            ]
    return []


async def _handle_pending_specialist_questions(
    *,
    pending_checkpoint: dict[str, Any],
    user_message: str,
    conversation_history: list[dict[str, Any]],
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

    answers: list[dict[str, Any]] = []
    if (
        not _parse_explicit_specialist_answers(
            pending_checkpoint=pending_checkpoint,
            user_message=user_message,
        )
        and _is_specialist_question_retry_message(user_message)
    ):
        answers = _recover_specialist_answers_from_history(
            pending_checkpoint=pending_checkpoint,
            conversation_history=conversation_history,
        )
    if not answers:
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


def _postflight_tool_args(tool_name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(args or {})
    key = _tool_primary_input_key(tool_name)
    if key and key in payload:
        payload[key] = _strip_injected_guidance_blocks(str(payload.get(key, "") or ""))
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


def _build_pre_execution_tool_trace(
    *,
    tool_name: str,
    enriched_args: dict[str, Any],
    sanitized_tool_input: dict[str, Any],
    decision_context: dict[str, Any] | None,
    context_summary: str,
) -> dict[str, Any]:
    expert_mode = dict(enriched_args.get("_expert_mode", {}) or {})
    return {
        "archie_lens": _archie_lens_for_tool(tool_name, expert_mode),
        "selected_archie_lens": _archie_lens_for_tool(tool_name, expert_mode),
        "sent_to_specialist": sanitized_tool_input,
        "sanitized_tool_input": sanitized_tool_input,
        "skill_guidance_injected": bool(enriched_args.get("_skill_guidance_block")),
        "injected_skill_guidance": {
            "applied_skills": list(enriched_args.get("_skill_injected", []) or []),
            "skill_versions": dict(enriched_args.get("_skill_versions", {}) or {}),
            "model_profile": str(enriched_args.get("_skill_model_profile", "") or ""),
        },
        "memory_snapshot_hash": str(enriched_args.get("_memory_snapshot_hash", "") or ""),
        "memory_sections_injected": list(enriched_args.get("_memory_sections_injected", []) or []),
        "memory_facts_used": list(enriched_args.get("_memory_facts_used", []) or []),
        "memory_unresolved_facts": list(enriched_args.get("_memory_unresolved_facts", []) or []),
        "memory_latest_baseline_used": dict(enriched_args.get("_memory_latest_baseline_used", {}) or {}),
        "decision_context": dict(decision_context or {}),
        "context_source": _infer_tool_context_source(tool_name, enriched_args, context_summary),
        "review_verdict": "pending",
        "review_findings": [],
        "refinement_history": [],
    }


def _archie_lens_for_tool(tool_name: str, expert_mode: dict[str, Any] | None = None) -> str:
    labels = {
        "generate_diagram": "OCI architecture diagram reviewer",
        "generate_bom": "OCI BOM sizing and pricing reviewer",
        "generate_pov": "OCI customer POV reviewer",
        "generate_jep": "OCI joint execution plan reviewer",
        "generate_waf": "OCI Well-Architected reviewer",
        "generate_terraform": "OCI Terraform implementation reviewer",
        "get_summary": "Archie context truthfulness reviewer",
        "get_document": "Archie document existence reviewer",
    }
    label = labels.get(tool_name, "Archie tool result reviewer")
    family = str((expert_mode or {}).get("reference_family", "") or "").strip()
    if family:
        return f"{label} ({family})"
    return label


def _infer_tool_context_source(tool_name: str, args: dict[str, Any], context_summary: str) -> str:
    if tool_name == "generate_bom":
        return str(args.get("_bom_context_source", "") or "direct_request")
    if bool(args.get("_bom_grounded_from_context")):
        return "latest_diagram"
    if str(context_summary or "").strip():
        return "persisted_context"
    return "direct_request"


async def _archie_expert_review_if_needed(
    *,
    tool_name: str,
    args: dict[str, Any],
    sanitized_tool_input: dict[str, Any],
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str,
    user_message: str,
    result_summary: str,
    artifact_key: str,
    result_data: dict[str, Any],
    context_summary: str,
    decision_context: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    if not (tool_name.startswith("generate_") or tool_name in {"get_summary", "get_document"}):
        return result_summary, artifact_key, result_data

    current_summary = result_summary
    current_key = artifact_key
    current_data = dict(result_data or {})
    review = _archie_expert_review(
        tool_name=tool_name,
        sanitized_tool_input=sanitized_tool_input,
        user_message=user_message,
        result_summary=current_summary,
        artifact_key=current_key,
        result_data=current_data,
        context_summary=context_summary,
        decision_context=decision_context,
    )
    current_data["archie_expert_review"] = review
    _merge_archie_review_trace(current_data, review)

    if (
        tool_name == "generate_bom"
        and review.get("verdict") == "revise_required"
        and _bom_review_retry_is_safe(current_data)
    ):
        feedback = _build_archie_bom_review_feedback(review)
        if feedback:
            retry_args = dict(args)
            retry_args["prompt"] = f"{retry_args.get('prompt', '')}\n\n[Archie Deterministic Review Feedback]\n{feedback}".strip()
            retry_args["_archie_expert_review_retry"] = True
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
            retry_data = dict(retry_data or {})
            retry_data["decision_context"] = dict(decision_context or {})
            retry_data["constraint_tags"] = list(args.get("_constraint_tags", []) or [])
            retry_trace = {
                **(current_data.get("trace", {}) if isinstance(current_data.get("trace"), dict) else {}),
                **(retry_data.get("trace", {}) if isinstance(retry_data.get("trace"), dict) else {}),
            }
            retry_trace["refinement_history"] = [
                *list(retry_trace.get("refinement_history", []) or []),
                {"attempt": 1, "review_feedback": feedback},
            ]
            retry_data["trace"] = retry_trace
            retry_review = _archie_expert_review(
                tool_name=tool_name,
                sanitized_tool_input=_postflight_tool_args(tool_name, retry_args),
                user_message=user_message,
                result_summary=retry_summary,
                artifact_key=retry_key,
                result_data=retry_data,
                context_summary=context_summary,
                decision_context=decision_context,
            )
            if retry_review.get("verdict") == "revise_required":
                retry_review = {
                    **retry_review,
                    "verdict": "blocked",
                    "retry_allowed": False,
                    "findings": [
                        *list(retry_review.get("findings", []) or []),
                        "BOM deterministic review retry did not satisfy the requested sizing.",
                    ],
                }
            retry_data["archie_expert_review"] = retry_review
            _merge_archie_review_trace(retry_data, retry_review)
            current_summary, current_key, current_data = retry_summary, retry_key, retry_data
            review = retry_review

    if review.get("verdict") in {"revise_required", "blocked"}:
        findings = [str(item).strip() for item in review.get("findings", []) if str(item).strip()]
        if findings:
            current_summary = "Archie expert review blocked this tool result.\n\nFindings:\n" + "\n".join(
                f"- {finding}" for finding in findings
            )
        else:
            current_summary = "Archie expert review blocked this tool result."
        current_key = ""
        current_data["checkpoint_required"] = True
        current_data["best_effort"] = False

    return current_summary, current_key, current_data


def _merge_archie_review_trace(result_data: dict[str, Any], review: dict[str, Any]) -> None:
    trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
    trace["review_verdict"] = str(review.get("verdict", "") or "")
    trace["review_findings"] = list(review.get("findings", []) or [])
    trace["review_required_actions"] = list(review.get("required_actions", []) or [])
    trace["review_requirements"] = dict(review.get("requirements", {}) or {})
    trace["review_produced"] = dict(review.get("produced", {}) or {})
    trace["archie_expert_review"] = review
    result_data["trace"] = trace


def _archie_expert_review(
    *,
    tool_name: str,
    sanitized_tool_input: dict[str, Any],
    user_message: str,
    result_summary: str,
    artifact_key: str,
    result_data: dict[str, Any],
    context_summary: str,
    decision_context: dict[str, Any],
) -> dict[str, Any]:
    findings: list[str] = []
    required_actions: list[str] = []
    verdict = "pass"

    if tool_name in {"get_summary", "get_document"}:
        if tool_name == "get_document" and str(result_summary or "").lower().startswith("no "):
            findings.append("Requested document does not exist in persisted document storage.")
            verdict = "blocked"
        elif not str(result_summary or "").strip():
            findings.append("Read-only tool returned an empty result.")
            verdict = "blocked"
        return {
            "verdict": verdict,
            "findings": findings,
            "required_actions": required_actions,
            "retry_allowed": False,
            "review_type": "read_only_truthfulness",
        }

    if tool_name.startswith("generate_") and not (str(result_summary or "").strip() or result_data):
        findings.append("Generation tool returned no summary or structured result data.")
        verdict = "blocked"

    if tool_name.startswith("generate_") and artifact_key:
        # Generation artifacts must be backed by an actual key before Archie exposes them.
        # Some in-process document stores return logical keys that are not directly head-able,
        # so missing object metadata is a finding only for download-producing paths.
        if tool_name in {"generate_diagram", "generate_terraform"} and not artifact_key:
            findings.append("Generated artifact key is missing.")
            verdict = "blocked"

    if tool_name == "generate_bom":
        bom_review = _review_bom_sizing_consistency(
            sanitized_tool_input=sanitized_tool_input,
            user_message=user_message,
            result_data=result_data,
            context_summary=context_summary,
            decision_context=decision_context,
        )
        findings.extend(bom_review["findings"])
        required_actions.extend(bom_review["required_actions"])
        if bom_review["findings"]:
            verdict = "revise_required" if _bom_review_retry_is_safe(result_data) else "blocked"
        return {
            "verdict": verdict,
            "findings": findings,
            "required_actions": required_actions,
            "retry_allowed": verdict == "revise_required",
            "review_type": "generation_acceptance",
            "requirements": bom_review["requirements"],
            "produced": bom_review["produced"],
        }

    return {
        "verdict": verdict,
        "findings": findings,
        "required_actions": required_actions,
        "retry_allowed": False,
        "review_type": "generation_acceptance",
    }


def _bom_review_retry_is_safe(result_data: dict[str, Any]) -> bool:
    if not isinstance(result_data, dict):
        return False
    if result_data.get("_archie_expert_review_retry") is True:
        return False
    if isinstance(result_data.get("archie_question_bundle"), dict):
        return False
    if str(result_data.get("type", "") or "").lower() not in {"", "final"}:
        return False
    return True


def _build_archie_bom_review_feedback(review: dict[str, Any]) -> str:
    findings = [str(item).strip() for item in review.get("findings", []) if str(item).strip()]
    actions = [str(item).strip() for item in review.get("required_actions", []) if str(item).strip()]
    lines = [
        "Revise the BOM so produced line item quantities meet or exceed the explicit customer sizing requirements.",
    ]
    lines.extend(f"- {item}" for item in findings)
    lines.extend(f"- Required action: {item}" for item in actions)
    return "\n".join(lines).strip()


def _review_bom_sizing_consistency(
    *,
    sanitized_tool_input: dict[str, Any],
    user_message: str,
    result_data: dict[str, Any],
    context_summary: str,
    decision_context: dict[str, Any],
) -> dict[str, Any]:
    source_text = _bom_review_source_text(
        sanitized_tool_input=sanitized_tool_input,
        user_message=user_message,
        context_summary=context_summary,
        decision_context=decision_context,
    )
    requirements = _extract_bom_sizing_requirements(source_text)
    produced = _extract_bom_produced_sizing(result_data.get("bom_payload", {}))
    findings: list[str] = []
    actions: list[str] = []

    comparisons = (
        ("ocpu", "OCPU", "ocpu"),
        ("ram_gb", "RAM GB", "ram_gb"),
        ("storage_gb", "storage GB", "storage_gb"),
    )
    for key, label, produced_key in comparisons:
        required_value = requirements.get(key)
        if required_value is None:
            continue
        produced_value = float(produced.get(produced_key, 0.0) or 0.0)
        if produced_value + 0.0001 < float(required_value):
            findings.append(
                f"BOM sizing mismatch for {label}: requested {required_value:g}, produced {produced_value:g}."
            )
            actions.append(f"Increase BOM {label} quantity to at least {required_value:g}.")

    region = str(requirements.get("region", "") or "").strip()
    if region:
        payload_region = str((result_data.get("bom_payload", {}) or {}).get("region", "") or "").strip()
        if payload_region and payload_region.lower() != region.lower():
            findings.append(f"BOM region mismatch: requested {region}, produced {payload_region}.")
            actions.append(f"Use requested region {region} or state that regional pricing is unavailable.")

    return {
        "requirements": requirements,
        "produced": produced,
        "findings": findings,
        "required_actions": actions,
    }


def _bom_review_source_text(
    *,
    sanitized_tool_input: dict[str, Any],
    user_message: str,
    context_summary: str,
    decision_context: dict[str, Any],
) -> str:
    if isinstance(sanitized_tool_input.get("inputs"), dict) and sanitized_tool_input.get("inputs"):
        return json.dumps(sanitized_tool_input.get("inputs"), ensure_ascii=True, sort_keys=True)
    input_text = json.dumps(sanitized_tool_input, ensure_ascii=True, sort_keys=True)
    return "\n".join(
        part
        for part in (
            user_message,
            input_text,
            context_summary,
            decision_context_builder.summarize_decision_context(decision_context),
        )
        if str(part or "").strip()
    )


def _extract_bom_sizing_requirements(text: str) -> dict[str, Any]:
    cleaned = str(text or "")
    lower = cleaned.lower()
    requirements: dict[str, Any] = {}
    try:
        structured = json.loads(cleaned)
    except Exception:
        structured = None
    if isinstance(structured, dict):
        compute = structured.get("compute", {}) if isinstance(structured.get("compute"), dict) else {}
        memory = structured.get("memory", {}) if isinstance(structured.get("memory"), dict) else {}
        storage = structured.get("storage", {}) if isinstance(structured.get("storage"), dict) else {}
        ocpu = _coerce_positive_float(compute.get("ocpu"))
        ram_gb = _coerce_positive_float(memory.get("gb"))
        block_tb = _coerce_positive_float(storage.get("block_tb"))
        if ocpu is not None:
            requirements["ocpu"] = ocpu
        if ram_gb is not None:
            requirements["ram_gb"] = ram_gb
        if block_tb is not None:
            requirements["storage_gb"] = block_tb * 1024.0
        region = str(structured.get("region", "") or "").strip()
        if region:
            requirements["region"] = region
        if requirements:
            return requirements
    ocpu_values = [
        float(match.group(1) or match.group(2))
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*o\s*cpu|\b(\d+(?:\.\d+)?)\s*ocpu\b", lower)
    ]
    ocpu_values = [value for value in ocpu_values if value > 0]
    if ocpu_values:
        requirements["ocpu"] = max(ocpu_values)

    ram_values = [
        _capacity_to_gb(float(match.group(1)), match.group(2))
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(tb|gb)\s*(?:of\s+)?(?:ram|memory)\b", lower)
    ]
    if ram_values:
        requirements["ram_gb"] = max(ram_values)

    storage_values: list[float] = []
    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(tb|gb)\s*(?:of\s+)?(?:block\s+storage|object\s+storage|storage|block\s+volume|volume|vol)\b",
        lower,
    ):
        start = max(0, match.start() - 24)
        if "egress" in lower[start : match.end() + 16]:
            continue
        storage_values.append(_capacity_to_gb(float(match.group(1)), match.group(2)))
    if storage_values:
        requirements["storage_gb"] = max(storage_values)

    region_match = re.search(r"\b(?:region|oci region)\s*[:=]\s*([a-z]{2,}-[a-z]+-\d)\b", lower)
    if region_match:
        requirements["region"] = region_match.group(1)
    return requirements


def _capacity_to_gb(value: float, unit: str) -> float:
    return value * 1024.0 if str(unit).lower() == "tb" else value


def _extract_bom_produced_sizing(payload: Any) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {"ocpu": 0.0, "ram_gb": 0.0, "storage_gb": 0.0}
    cpu_skus = {sku.upper() for sku in CPU_SKU_TO_MEM_SKU.keys()}
    mem_skus = {sku.upper() for sku in CPU_SKU_TO_MEM_SKU.values()}
    produced = {"ocpu": 0.0, "ram_gb": 0.0, "storage_gb": 0.0, "block_storage_gb": 0.0, "object_storage_gb": 0.0}
    for row in payload.get("line_items", []) or []:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku", "") or "").strip().upper()
        desc = str(row.get("description", "") or "").lower()
        category = str(row.get("category", "") or "").lower()
        try:
            quantity = float(row.get("quantity") or 0.0)
        except Exception:
            quantity = 0.0
        if sku in cpu_skus or ("ocpu" in desc and category == "compute"):
            produced["ocpu"] += quantity
        elif sku in mem_skus or ("memory" in desc and category == "compute"):
            produced["ram_gb"] += quantity
        elif category == "storage" or "storage" in desc or "volume" in desc:
            produced["storage_gb"] += quantity
            if "object" in desc:
                produced["object_storage_gb"] += quantity
            else:
                produced["block_storage_gb"] += quantity
    return produced


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
            tool_args=_postflight_tool_args(tool_name, args),
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
        "archie_expert_review": dict(result_data.get("archie_expert_review", {}) or {}),
        "review_verdict": str(
            (
                result_data.get("archie_expert_review", {})
                if isinstance(result_data.get("archie_expert_review"), dict)
                else {}
            ).get("verdict", "")
            or ""
        ),
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
        context_store.record_bom_work_product(
            context,
            bom_payload=payload,
            context_source=record["context_source"],
            grounding=str(result_data.get("_bom_grounding", "") or ""),
        )


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
    context_store.refresh_archie_memory(context)
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
    has_budget_checkpoint_signal = any(value is not None for value in (
        constraints.get("cost_max_monthly"),
        cost.get("budget_target"),
        cost.get("variance"),
    ))
    if has_budget_checkpoint_signal and str(cost.get("status", "pass") or "pass") == "checkpoint_required":
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
    has_cost_checkpoint_signal = any(value is not None for value in (budget, variance))
    if has_cost_checkpoint_signal:
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
        "tool_name": tool_name,
        "prompt": prompt,
        "recommended_action": "approve or revise input",
        "options": ["approve checkpoint", "revise input"],
        "decision_context_hash": _decision_context_hash(decision_context),
        "decision_context": dict(decision_context or {}),
        "constraints": dict((decision_context or {}).get("constraints", {}) or {}),
        "assumptions": list((decision_context or {}).get("assumptions", []) or []),
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
    if resolution == "approved":
        _record_approved_checkpoint_inputs(context, pending)
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


def _record_approved_checkpoint_inputs(context: dict[str, Any], checkpoint: dict[str, Any]) -> None:
    decision_context = checkpoint.get("decision_context", {}) if isinstance(checkpoint.get("decision_context"), dict) else {}
    constraints = dict(checkpoint.get("constraints", {}) or {})
    if not constraints and isinstance(decision_context, dict):
        constraints = dict(decision_context.get("constraints", {}) or {})
    assumptions = list(checkpoint.get("assumptions", []) or [])
    if not assumptions and isinstance(decision_context, dict):
        assumptions = list(decision_context.get("assumptions", []) or [])

    if constraints or assumptions:
        context_store.set_archie_decision_state(context, constraints=constraints, assumptions=assumptions)

    region = str(constraints.get("region", "") or "").strip()
    if region:
        context_store.record_resolved_question(
            context,
            {
                "id": str(uuid.uuid4()),
                "question_id": "constraints.region",
                "question": "Approved checkpoint region",
                "final_answer": region,
                "source": "approved_checkpoint",
                "confidence": "high",
                "timestamp": _now(),
            },
        )

    for item in assumptions:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "") or "").strip()
        if not statement:
            continue
        lowered = statement.lower()
        question_id = ""
        final_answer = ""
        if "region" in lowered and region:
            question_id = "constraints.region"
            final_answer = region
        elif "single-region" in lowered or "single region" in lowered:
            question_id = "regions.mode"
            final_answer = "single-region"
        elif "multi-region" in lowered or "multi region" in lowered:
            question_id = "regions.mode"
            final_answer = "multi-region"
        elif any(token in lowered for token in ("component", "workload", "bom", "architecture")):
            question_id = "components.scope"
            final_answer = _standard_components_scope_answer()
        if question_id and final_answer:
            context_store.record_resolved_question(
                context,
                {
                    "id": str(uuid.uuid4()),
                    "question_id": question_id,
                    "question": statement,
                    "final_answer": final_answer,
                    "source": "approved_checkpoint",
                    "confidence": "medium",
                    "timestamp": _now(),
                },
            )


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
    ("[Archie Canonical Memory]", "[End Archie Canonical Memory]"),
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


def _bom_resolved_inputs_reply_section(result_data: dict[str, Any]) -> list[str]:
    payload = result_data.get("bom_payload", {}) if isinstance(result_data.get("bom_payload"), dict) else {}
    resolved_inputs = payload.get("resolved_inputs") if isinstance(payload.get("resolved_inputs"), list) else []
    memory_facts = [str(item).strip() for item in result_data.get("memory_facts_used", []) or [] if str(item).strip()]
    baseline = result_data.get("memory_latest_baseline_used", {}) if isinstance(result_data.get("memory_latest_baseline_used"), dict) else {}
    if not resolved_inputs and not memory_facts and not baseline:
        return []
    lines = []
    if memory_facts or baseline:
        lines.extend(["", "Facts Used from Memory:"])
        if memory_facts:
            lines.append("- " + ", ".join(memory_facts))
        if baseline:
            version = str(baseline.get("version", "") or "").strip()
            grounding = str(baseline.get("grounding", "") or baseline.get("context_source", "") or "").strip()
            lines.append(
                "- latest BOM baseline: "
                + (f"v{version}" if version else "available")
                + (f" ({grounding})" if grounding else "")
            )
    if not resolved_inputs:
        return lines
    lines.extend(["", "Archie used these answers:"])
    for item in resolved_inputs[:8]:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id", "") or "").strip()
        answer = str(item.get("answer", "") or item.get("final_answer", "") or "").strip()
        if question_id and answer:
            lines.append(f"- {question_id}: {answer}")
    return lines if len(lines) > 2 else []


def _call_result_is_successful_generation(call: dict[str, Any]) -> bool:
    tool_name = str(call.get("tool", "") or "")
    if not tool_name.startswith("generate_"):
        return False
    result_data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
    if _extract_blocking_skill_decision(result_data):
        return False
    if isinstance(result_data.get("archie_question_bundle"), dict):
        return False
    governor = result_data.get("governor", {}) if isinstance(result_data.get("governor"), dict) else {}
    if str(governor.get("overall_status", "pass") or "pass") in {"blocked", "checkpoint_required"}:
        return False
    summary = str(call.get("result_summary", "") or "").strip().lower()
    blocked_markers = (
        "clarification required",
        "please upload or paste",
        "i need ",
        "cannot ",
        "not yet enabled",
        "unknown tool",
        "did not meet completion",
    )
    if any(marker in summary for marker in blocked_markers):
        return False
    if str(call.get("artifact_key", "") or "").strip():
        return True
    if summary.startswith("final bom prepared"):
        return True
    return any(marker in summary for marker in ("saved. key:", "generated. key:", "review "))


def _fallback_applied_skills(tool_name: str) -> list[str]:
    return [name for name in _MANDATORY_SKILL_FALLBACKS.get(tool_name, ()) if name]


def _governor_critic_summary(data: dict[str, Any]) -> str:
    if (
        str(data.get("type", "") or "").lower() == "final"
        and isinstance(data.get("bom_payload"), dict)
        and str((data.get("archie_expert_review", {}) or {}).get("verdict", "") or "") == "pass"
    ):
        return "Archie deterministic review passed for the generated BOM payload."
    governor = data.get("governor", {}) if isinstance(data.get("governor"), dict) else {}
    quality = governor.get("quality", {}) if isinstance(governor.get("quality"), dict) else {}
    last_critique = data.get("last_critique", {}) if isinstance(data.get("last_critique"), dict) else {}
    for candidate in (
        governor.get("decision_summary"),
        quality.get("summary"),
        last_critique.get("critique_summary"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return "No critic feedback available"


def _synthesize_management_metadata(
    tool_calls: list[dict[str, Any]],
    *,
    decision_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    successful_calls = [call for call in tool_calls if _call_result_is_successful_generation(call)]
    applied_skills: list[str] = []
    artifact_refs: list[str] = []
    governor_summaries: list[str] = []
    refinement_count = 0
    checkpoint_statuses: list[str] = []
    assumptions = _merge_assumption_lists(list((decision_context or {}).get("assumptions", []) or []), [])
    tradeoffs: list[str] = []

    for call in successful_calls:
        tool_name = str(call.get("tool", "") or "")
        data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
        skills = [str(item).strip() for item in data.get("applied_skills", []) or [] if str(item).strip()]
        if not skills:
            skills = _fallback_applied_skills(tool_name)
        for skill in skills:
            if skill not in applied_skills:
                applied_skills.append(skill)

        artifact_key = str(call.get("artifact_key", "") or "").strip()
        if artifact_key and artifact_key not in artifact_refs:
            artifact_refs.append(artifact_key)
        decision_log = data.get("decision_log", {}) if isinstance(data.get("decision_log"), dict) else {}
        artifact_candidates = list(decision_log.get("artifact_refs", []) or []) + list(data.get("artifact_refs", []) or [])
        for artifact_ref in artifact_candidates:
            artifact_text = str(artifact_ref or "").strip()
            if artifact_text and artifact_text not in artifact_refs:
                artifact_refs.append(artifact_text)

        refinement_count += int(data.get("refinement_count", 0) or 0)
        assumptions = _merge_assumption_lists(
            assumptions,
            list((data.get("decision_context", {}) or {}).get("assumptions", []) or []),
        )
        assumptions = _merge_assumption_lists(assumptions, list(data.get("assumptions_used", []) or []))

        governor = data.get("governor", {}) if isinstance(data.get("governor"), dict) else {}
        for section in ("security", "cost", "quality"):
            section_data = governor.get(section, {}) if isinstance(governor.get(section), dict) else {}
            for key in ("findings", "issues", "suggestions"):
                tradeoffs.extend(str(item).strip() for item in section_data.get(key, []) or [] if str(item).strip())

        governor_summary = _governor_critic_summary(data)
        if governor_summary not in governor_summaries:
            governor_summaries.append(governor_summary)

        checkpoint = data.get("checkpoint")
        if isinstance(checkpoint, dict):
            checkpoint_statuses.append(str(checkpoint.get("status", "pending") or "pending"))

    deliverables = [_tool_goal_label(str(call.get("tool", "") or "requested_tool")) for call in successful_calls]
    rendered_assumptions = [
        f"{str(item.get('statement', '') or '').strip()} (risk: {str(item.get('risk', '') or 'low').strip().lower() or 'low'})"
        for item in assumptions
        if isinstance(item, dict) and str(item.get("statement", "") or "").strip()
    ]
    return {
        "successful_call_count": len(successful_calls),
        "applied_skills": applied_skills,
        "refinement_count": refinement_count,
        "governor_critic_summary": "; ".join(governor_summaries) if governor_summaries else "No critic feedback available",
        "key_decisions": (
            "Generated " + ", ".join(deliverables) + " in requested prerequisite order."
            if deliverables
            else ""
        ),
        "assumptions": rendered_assumptions,
        "key_tradeoffs": list(dict.fromkeys(tradeoffs)),
        "artifact_refs": artifact_refs,
        "checkpoint_status": ", ".join(dict.fromkeys(checkpoint_statuses)) if checkpoint_statuses else "none",
    }


def _render_management_summary(
    tool_calls: list[dict[str, Any]],
    *,
    decision_context: dict[str, Any] | None = None,
) -> str:
    metadata = _synthesize_management_metadata(tool_calls, decision_context=decision_context)
    if not metadata["successful_call_count"]:
        return ""

    assumption_line = "; ".join(metadata["assumptions"][:3]) if metadata["assumptions"] else "None beyond the supplied request/context."
    tradeoff_line = "; ".join(metadata["key_tradeoffs"][:3]) if metadata["key_tradeoffs"] else "No blocking tradeoffs reported."
    skills_line = ", ".join(metadata["applied_skills"]) if metadata["applied_skills"] else "not reported"
    artifact_line = ", ".join(metadata["artifact_refs"][:3]) if metadata["artifact_refs"] else "none"

    return "\n".join(
        [
            "Management Summary",
            f"- Applied skills: {skills_line}",
            f"- Refinement count: {metadata['refinement_count']}",
            f"- Governor/critic summary: {metadata['governor_critic_summary']}",
            f"- Key decisions: {metadata['key_decisions']}",
            f"- Assumptions/tradeoffs: {assumption_line} Tradeoffs: {tradeoff_line}",
            f"- Artifact refs: {artifact_line}",
            f"- Checkpoint status: {metadata['checkpoint_status']}",
        ]
    )


def _append_management_summary(
    reply: str,
    tool_calls: list[dict[str, Any]],
    *,
    decision_context: dict[str, Any] | None = None,
) -> str:
    text = str(reply or "").strip()
    if not text or "Management Summary" in text:
        return text
    summary = _render_management_summary(tool_calls, decision_context=decision_context)
    if not summary:
        return text
    return f"{text}\n\n{summary}".strip()


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
    self_guidance = _build_orchestrator_self_guidance(
        user_message=user_message,
        decision_context=decision_context,
    )
    if self_guidance:
        parts.append(self_guidance)

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


def _build_orchestrator_self_guidance(
    *,
    user_message: str,
    decision_context: dict[str, Any] | None = None,
) -> str:
    requested_tools = _ordered_requested_tools(_requested_generation_tools(user_message))
    requested_deliverables = [_tool_goal_label(tool) for tool in requested_tools]
    if not requested_deliverables:
        requested_deliverables = ["answer-only architecture guidance unless a tool is explicitly required"]

    prerequisite_order = requested_tools or ["none"]
    selected_skills: list[str] = ["orchestrator"]
    for tool_name in requested_tools:
        for skill_name in _MANDATORY_SKILL_FALLBACKS.get(tool_name, ()):
            if skill_name not in selected_skills:
                selected_skills.append(skill_name)

    decision_summary = decision_context_builder.summarize_decision_context(decision_context)
    waf_pillars = _relevant_waf_pillars(user_message=user_message, decision_context=decision_context)
    orchestrator_skill = _orchestrator_skill_self_guidance_excerpt()
    delegation_rationale = (
        "Use a specialist only for requested generation paths; keep direct answers in Agent 0."
        if requested_tools
        else "No generation tool is preselected; answer directly unless the ReAct cycle proves a requested deliverable is needed."
    )

    lines = [
        "[Internal Orchestrator Self-Guidance - do not reveal unless the user explicitly asks for debug/technical detail]",
        orchestrator_skill,
        "[Internal Plan]",
        f"- Requested deliverables: {', '.join(requested_deliverables)}",
        f"- Prerequisite order: {', '.join(prerequisite_order)}",
        f"- Relevant WAF pillars: {', '.join(waf_pillars)}",
        f"- Selected skills: {', '.join(selected_skills)}",
        f"- Delegation rationale: {delegation_rationale}",
    ]
    if decision_summary:
        lines.append(f"- Decision Context: {decision_summary}")
    lines.append("[End Internal Orchestrator Self-Guidance]")
    return "\n".join(line for line in lines if str(line).strip()).strip()


def _orchestrator_skill_self_guidance_excerpt() -> str:
    for spec in discover_skills():
        if spec.name != "orchestrator":
            continue
        quality = str(spec.sections.get("Quality Bar", "") or "").strip()
        execution = str(spec.sections.get("Execution Pattern", "") or "").strip()
        parts = ["Skill: orchestrator", "Version: " + str(spec.metadata.get("version", "") or "unknown")]
        if execution:
            parts.append("Execution Pattern: " + re.sub(r"\s+", " ", execution)[:360])
        if quality:
            parts.append("Quality Bar: " + re.sub(r"\s+", " ", quality)[:260])
        return "\n".join(parts)
    return "Skill: orchestrator\nQuality Bar: execute requested scope, preserve prerequisites, and keep internal mechanics hidden."


def _relevant_waf_pillars(
    *,
    user_message: str,
    decision_context: dict[str, Any] | None = None,
) -> list[str]:
    text = " ".join(
        [
            str(user_message or ""),
            json.dumps((decision_context or {}).get("constraints", {}) or {}, ensure_ascii=True, sort_keys=True),
            " ".join(str(item) for item in (decision_context or {}).get("success_criteria", []) or []),
        ]
    ).lower()
    pillars: list[str] = []
    checks = (
        ("Security", ("security", "waf", "private", "public", "iam", "kms", "vault", "compliance", "nsg")),
        ("Reliability", ("ha", "dr", "availability", "multi-ad", "multi region", "resilience", "failover")),
        ("Performance Efficiency", ("latency", "performance", "throughput", "scale", "sizing", "ocpu")),
        ("Cost Optimization", ("cost", "budget", "bom", "pricing", "spend", "under")),
        ("Operational Excellence", ("operations", "monitoring", "logging", "runbook", "terraform", "automation")),
    )
    for pillar, markers in checks:
        if any(marker in text for marker in markers):
            pillars.append(pillar)
    return pillars or ["Security", "Reliability", "Cost Optimization"]


def _append_tool_result(prompt: str, tool_name: str, result_summary: str) -> str:
    base = prompt.rstrip()
    if base.endswith("ASSISTANT:"):
        base = base[: -len("ASSISTANT:")].rstrip()
    return base + (
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
            return _append_management_summary(
                _build_single_diagram_reply(call, decision_context=decision_context),
                tool_calls,
                decision_context=decision_context,
            )
        summary = str(call.get("result_summary", "") or "").strip()
        if str(call.get("tool", "") or "") == "generate_bom":
            data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
            if _bom_call_was_memory_revision(data) and "BOM revision was performed" not in summary:
                summary = f"BOM revision was performed from updated memory.\n\n{summary}".strip()
            section = _bom_resolved_inputs_reply_section(data)
            if section:
                summary = "\n".join([summary or "Final BOM prepared.", *section]).strip()
        return _append_management_summary(
            summary or f"Completed `{call.get('tool', 'requested_tool')}`.",
            tool_calls,
            decision_context=decision_context,
        )

    lines = ["Completed the requested outputs:"]
    for call in tool_calls:
        tool_name = str(call.get("tool", "") or "requested_tool")
        label = _tool_goal_label(tool_name)
        summary = str(call.get("result_summary", "") or "").strip()
        if summary:
            lines.append(f"- {label}: {summary}")
        else:
            lines.append(f"- {label} completed.")
        if tool_name == "generate_bom":
            data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
            if _bom_call_was_memory_revision(data) and "BOM revision was performed" not in lines[-1]:
                lines.append("  BOM revision was performed from updated memory.")
            lines.extend(_bom_resolved_inputs_reply_section(data))
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
    return _append_management_summary(
        "\n".join(lines),
        tool_calls,
        decision_context=decision_context,
    )


def _bom_call_was_memory_revision(result_data: dict[str, Any]) -> bool:
    trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
    return str(trace.get("bom_context_source", result_data.get("bom_context_source", "")) or "") == "bom_revision"


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
    missing_inputs = list(dict.fromkeys([
        *[str(item).strip() for item in (decision_context or {}).get("missing_inputs", []) or [] if str(item).strip()],
        *[
            str(item).strip()
            for call in tool_calls
            for item in ((call.get("result_data", {}) or {}).get("decision_context", {}) or {}).get("missing_inputs", []) or []
            if str(item).strip()
        ],
    ]))
    if assumptions:
        lines.append("")
        lines.append("Assumptions applied:")
        lines.extend(f"- {assumption}" for assumption in assumptions)
    if missing_inputs:
        lines.append("")
        lines.append("Missing inputs to tighten the next pass:")
        lines.extend(f"- {item}" for item in missing_inputs)
    return _append_management_summary(
        "\n".join(lines).strip(),
        tool_calls,
        decision_context=decision_context,
    )


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
        if len(requested_tools) == 1:
            tool_name = next(iter(requested_tools))
            return {
                "status": "sequence",
                "sequence": [tool_name],
                "scenarios": [{"label": "Scenario 1", "text": str(user_message or "").strip()}],
                "bom_feeds_diagram": False,
            }
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


_ACTION_PRODUCTION_MARKERS = (
    "export",
    "xlsx",
    "xlxs",
    "xlsc",
    "excel",
    "spreadsheet",
    "workbook",
    "download",
    "generate file",
    "save file",
    "pricing export",
    "price export",
)
_ACTION_ACCESS_MARKERS = (
    "share link",
    "download link",
    " link ",
    " url",
    "url ",
    "presigned",
    "pre-signed",
)
_ACTION_VERIFY_MARKERS = (
    "in the bucket",
    "in object storage",
    "uploaded",
    "verify",
    "verify file",
    "verify files",
    "check file",
    "check files",
    "check whether",
    "check if",
    "exists",
    " exist ",
    "list files",
    "list the files",
)


def _classify_turn_intent(
    *,
    user_message: str,
    requested_tools: set[str],
    context: dict[str, Any] | None,
) -> TurnIntent:
    text = str(user_message or "")
    msg = f" {text.lower()} "
    target_artifact = _infer_turn_target_artifact(text, requested_tools)
    corrections = tuple(_extract_turn_corrections(text))
    candidate_tool = _target_artifact_to_tool(target_artifact)

    if target_artifact == "bom" and _is_bom_revision_request(text, text, context):
        classification = "artifact_feedback" if any(
            marker in msg for marker in (" feedback", " customer asked", " customer requested", " only have", " you have")
        ) else "artifact_revision"
        return TurnIntent(
            classification=classification,
            target_artifact=target_artifact,
            operation="revise",
            extracted_corrections=corrections,
            confidence=0.92 if corrections else 0.82,
            candidate_tool="generate_bom",
        )

    if _is_explicit_artifact_download_request(text, target_artifact, requested_tools):
        return TurnIntent(
            classification="artifact_download",
            target_artifact=target_artifact,
            operation="download",
            confidence=0.9,
            candidate_tool=candidate_tool,
        )

    if _is_explicit_artifact_verification_request(text, target_artifact):
        return TurnIntent(
            classification="artifact_verification",
            target_artifact=target_artifact,
            operation="verify",
            confidence=0.9,
            candidate_tool=candidate_tool,
        )

    if requested_tools:
        selected_tool = _ordered_requested_tools(requested_tools)[0]
        return TurnIntent(
            classification="new_generation",
            target_artifact=_tool_to_target_artifact(selected_tool),
            operation="generate",
            confidence=0.78,
            candidate_tool=selected_tool,
        )

    return TurnIntent(classification="conversation_only", operation="answer", confidence=0.5)


def _infer_turn_target_artifact(user_message: str, requested_tools: set[str]) -> str:
    msg = str(user_message or "").lower()
    if _mentions_operating_model(user_message):
        return "operating_model"
    if any(term in msg for term in ("bom", "bill of materials", "xlsx", "xlxs", "xlsc", "excel", "spreadsheet", "workbook", "pricing", "sku")):
        return "bom"
    if any(term in msg for term in ("diagram", "drawio", "draw.io", "topology file")):
        return "diagram"
    if "terraform" in msg or "iac" in msg:
        return "terraform"
    if "pov" in msg or "point of view" in msg:
        return "pov"
    if "jep" in msg or "joint execution plan" in msg:
        return "jep"
    if "waf" in msg or "well-architected" in msg or "well architected" in msg:
        return "waf"
    if len(requested_tools) == 1:
        return _tool_to_target_artifact(next(iter(requested_tools)))
    return ""


def _tool_to_target_artifact(tool_name: str) -> str:
    return {
        "generate_bom": "bom",
        "generate_diagram": "diagram",
        "generate_terraform": "terraform",
        "generate_pov": "pov",
        "generate_jep": "jep",
        "generate_waf": "waf",
    }.get(str(tool_name or ""), "")


def _target_artifact_to_tool(target_artifact: str) -> str:
    return {
        "bom": "generate_bom",
        "diagram": "generate_diagram",
        "terraform": "generate_terraform",
        "pov": "generate_pov",
        "jep": "generate_jep",
        "waf": "generate_waf",
    }.get(str(target_artifact or ""), "")


def _is_explicit_artifact_download_request(
    user_message: str,
    target_artifact: str,
    requested_tools: set[str],
) -> bool:
    msg = f" {str(user_message or '').lower()} "
    if _is_pure_download_or_link_request(user_message) and (target_artifact or requested_tools):
        return True
    if any(marker in msg for marker in _ACTION_ACCESS_MARKERS) and (target_artifact or requested_tools):
        return True
    return bool(_is_export_only_request(user_message) and target_artifact == "bom")


def _is_explicit_artifact_verification_request(user_message: str, target_artifact: str) -> bool:
    msg = f" {str(user_message or '').lower()} "
    explicit_verify = any(marker in msg for marker in (" verify", " check ", " exists", " exist ", " list "))
    explicit_location = any(marker in msg for marker in (" in the bucket", " in object storage", " object-store", " persisted"))
    file_terms = any(marker in msg for marker in (" file", " files", " artifact", " artifacts", " xlsx", " workbook", " bom", " diagram", " terraform"))
    uploaded_state = any(marker in msg for marker in (" uploaded", " upload complete", " present"))
    return bool((explicit_verify and (file_terms or target_artifact or explicit_location)) or (uploaded_state and (file_terms or target_artifact)))


def _extract_turn_corrections(user_message: str) -> list[str]:
    text = str(user_message or "").strip()
    if not text:
        return []
    corrections: list[str] = []
    for pattern, label in (
        (r"\b\d+(?:[.,]\d+)?\s*(?:tb|tib)\s+(?:of\s+)?storage\b", "storage"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:gb|gib)\s+(?:of\s+)?(?:object\s+)?storage\b", "storage"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:tb|tib)\s+(?:of\s+)?memory\b", "memory"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:gb|gib)\s+(?:of\s+)?(?:ram|memory)\b", "memory"),
        (r"\b\d+(?:[.,]\d+)?\s*(?:ocpu|ocpus|cpu|cpus|cores?)\b", "compute"),
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            item = f"{label}: {value}"
            if item not in corrections:
                corrections.append(item)
    return corrections[:12]


def _tool_backed_action_intent(user_message: str, *, turn_intent: TurnIntent | None = None) -> dict[str, bool]:
    msg = f" {str(user_message or '').lower()} "
    production = any(marker in msg for marker in _ACTION_PRODUCTION_MARKERS)
    access = any(marker in msg for marker in _ACTION_ACCESS_MARKERS)
    verification = bool(
        (turn_intent and turn_intent.classification == "artifact_verification")
        or any(marker in msg for marker in _ACTION_VERIFY_MARKERS)
    )
    if turn_intent and turn_intent.classification == "artifact_download":
        access = True
    if turn_intent and turn_intent.classification in {"artifact_feedback", "artifact_revision"}:
        production = True
    if not any((production, access, verification)):
        return {}
    return {
        "production": production,
        "access": access,
        "verification": verification,
        "operating_model": _mentions_operating_model(user_message),
    }


def _mentions_operating_model(user_message: str) -> bool:
    msg = f" {str(user_message or '').lower()} "
    return " operating model " in msg or re.search(r"\bom\b", msg) is not None


def _is_export_only_request(user_message: str) -> bool:
    msg = f" {str(user_message or '').lower()} "
    if not any(marker in msg for marker in _ACTION_PRODUCTION_MARKERS):
        return False
    if _is_workbook_only_request(user_message):
        return True
    if _has_generation_request_for_supported_artifact(user_message):
        return False
    generation_markers = (
        "build a bom",
        "build the bom",
        "create a bom",
        "create the bom",
        "generate a bom",
        "generate the bom",
        "draft a bom",
        "price this",
        "size this",
    )
    return not any(marker in msg for marker in generation_markers)


def _has_generation_request_for_supported_artifact(user_message: str) -> bool:
    msg = str(user_message or "").lower()
    generation_verbs = ("build", "create", "generate", "draft", "make")
    artifact_terms = (
        "bom",
        "bill of materials",
        "diagram",
        "drawio",
        "draw.io",
        "terraform",
        "pov",
        "point of view",
        "jep",
        "joint execution plan",
        "waf",
        "well-architected",
        "well architected",
    )
    return any(verb in msg for verb in generation_verbs) and any(term in msg for term in artifact_terms)


def _is_workbook_only_request(user_message: str) -> bool:
    msg = str(user_message or "").lower()
    workbook_terms = ("xlsx", "xlxs", "xlsc", "excel", "spreadsheet", "workbook")
    if not any(term in msg for term in workbook_terms):
        return False
    substantive_bom_terms = ("bom", "bill of materials", "pricing", "priced", "sku", "skus")
    sizing_terms = ("ocpu", "cpu", "ram", "memory", "storage", "block volume", "tb", "gb")
    return not any(term in msg for term in substantive_bom_terms) and not any(term in msg for term in sizing_terms)


def _tool_backed_action_reply(
    *,
    user_message: str,
    action_intent: dict[str, bool],
    turn_intent: TurnIntent | None = None,
    requested_tools: set[str],
    context: dict[str, Any],
    customer_id: str,
    store: ObjectStoreBase,
) -> str | None:
    if not action_intent:
        return None

    if turn_intent and turn_intent.classification in {"artifact_feedback", "artifact_revision"}:
        return None

    if action_intent.get("operating_model") and any(
        action_intent.get(key) for key in ("production", "access", "verification")
    ):
        return (
            "I don't have a generated artifact/link for that yet.\n"
            "Operating Model export is not a supported Archie artifact path yet. "
            "I can discuss the operating model in prose, or generate a supported BOM, diagram, POV, JEP, WAF, or Terraform artifact."
        )

    if "generate_bom" in requested_tools and _bom_action_should_regenerate(
        user_message=user_message,
        action_intent=action_intent,
        context=context,
        store=store,
        customer_id=customer_id,
    ):
        return None

    if (
        (turn_intent and turn_intent.classification == "artifact_verification")
        or action_intent.get("verification")
    ) and not (
        requested_tools and _has_generation_request_for_supported_artifact(user_message)
    ):
        return _build_artifact_verification_reply(context=context, customer_id=customer_id, store=store)

    if (
        (turn_intent and turn_intent.classification == "artifact_download")
        or action_intent.get("access")
        or _is_existing_artifact_access_request(user_message, requested_tools)
    ):
        return _build_artifact_link_reply(context=context, customer_id=customer_id, store=store)

    if action_intent.get("production") and _is_export_only_request(user_message):
        return _build_artifact_link_reply(context=context, customer_id=customer_id, store=store)

    return None


def _bom_action_should_regenerate(
    *,
    user_message: str,
    action_intent: dict[str, bool],
    context: dict[str, Any],
    store: ObjectStoreBase,
    customer_id: str,
) -> bool:
    if _is_pure_download_or_link_request(user_message):
        return False
    if _is_bom_revision_request(user_message, user_message, context):
        return True
    archie = context_store.get_archie_state(context)
    has_facts = bool(archie.get("client_facts") or archie.get("infrastructure_profile"))
    latest_downloads = [
        item for item in _artifact_downloads_from_context(context=context, customer_id=customer_id, store=store)
        if item.get("type") == "bom"
    ]
    if action_intent.get("production") and _mentions_bom_work_product(user_message) and has_facts and not latest_downloads:
        return True
    return bool(_mentions_bom_work_product(user_message) and _latest_bom_fact_mismatches(context))


def _is_pure_download_or_link_request(user_message: str) -> bool:
    msg = f" {str(user_message or '').lower()} "
    if not any(marker in msg for marker in ("download", "share", "link", "url", "presigned", "pre-signed")):
        return False
    revision_markers = (
        " new ",
        " updated ",
        " update ",
        " regenerate",
        " rebuild",
        " revise",
        " revision",
        " incorrect",
        " wrong",
        " not correct",
        " fix ",
        " replace ",
        " current bom",
        " current xlsx",
        " current workbook",
    )
    if any(marker in msg for marker in revision_markers):
        return False
    generation_verbs = ("build", "create", "generate", "draft", "make")
    if any(verb in msg for verb in generation_verbs) and _mentions_bom_work_product(user_message):
        return False
    return True


def _is_existing_artifact_access_request(user_message: str, requested_tools: set[str]) -> bool:
    if requested_tools and _has_generation_request_for_supported_artifact(user_message):
        return False
    if not requested_tools:
        return True
    if requested_tools == {"generate_bom"} and _is_export_only_request(user_message):
        return True
    msg = str(user_message or "").lower()
    if any(marker in msg for marker in ("share", "link", "download", "presigned", "pre-signed", "url")):
        return True
    return False


def _checkpoint_blocks_artifact_action_reply(pending_checkpoint: dict[str, Any]) -> str:
    prompt = str(pending_checkpoint.get("prompt", "") or "A checkpoint is pending.").strip()
    return (
        "I can't export, link, or verify artifacts while this checkpoint is pending.\n\n"
        f"{prompt}\n\n"
        "Reply `approve checkpoint` to proceed with the approved assumptions, or revise the request and rerun."
    ).strip()


def _tool_required_blocker_reply(user_message: str, action_intent: dict[str, bool]) -> str:
    _ = user_message
    if action_intent.get("verification"):
        return "I can't verify bucket contents from conversation text. I need a persisted artifact manifest or object-store metadata."
    return (
        "I don't have a generated artifact/link for that yet. "
        "Run the relevant specialist generation step first, then ask me for the download or verification."
    )


_BOM_XLSX_METADATA_SUFFIX = ".metadata.json"


def _bom_xlsx_metadata_key(xlsx_key: str) -> str:
    return f"{xlsx_key}{_BOM_XLSX_METADATA_SUFFIX}"


def _valid_bom_xlsx_metadata(store: ObjectStoreBase, xlsx_key: str) -> bool:
    meta_key = _bom_xlsx_metadata_key(xlsx_key)
    if not xlsx_key.lower().endswith(".xlsx") or not store.head(xlsx_key) or not store.head(meta_key):
        return False
    try:
        metadata = json.loads(store.get(meta_key).decode("utf-8"))
    except Exception:
        return False
    if not isinstance(metadata, dict):
        return False
    if metadata.get("tool") != "generate_bom":
        return False
    if str(metadata.get("status", "") or "").lower() not in {"approved", "final"}:
        return False
    if metadata.get("checkpoint_required") is True:
        return False
    if str(metadata.get("archie_review_verdict", "pass") or "pass").lower() != "pass":
        return False
    return True


def _artifact_downloads_from_context(
    *,
    context: dict[str, Any],
    customer_id: str,
    store: ObjectStoreBase,
) -> list[dict[str, str]]:
    downloads: list[dict[str, str]] = []
    agents = context.get("agents", {}) if isinstance(context, dict) else {}

    diagram = dict((agents or {}).get("diagram", {}) or {})
    diagram_key = str(diagram.get("diagram_key", "") or diagram.get("artifact_ref", "") or "").strip()
    if diagram_key and store.head(diagram_key):
        filename = diagram_key.split("/")[-1] or "diagram.drawio"
        diagram_name = str(diagram.get("diagram_name", "") or _infer_diagram_name_from_key(diagram_key) or "oci_architecture")
        downloads.append(
            {
                "type": "diagram",
                "key": diagram_key,
                "download_url": f"/api/download/{filename}?client_id={customer_id}&diagram_name={diagram_name}",
            }
        )

    for key in sorted(store.list(f"customers/{customer_id}/bom/xlsx/"), reverse=True):
        if not _valid_bom_xlsx_metadata(store, key):
            continue
        filename = key.split("/")[-1]
        downloads.append(
            {
                "type": "bom",
                "key": key,
                "download_url": f"/api/bom/{customer_id}/download/{filename}",
            }
        )
    bom = dict((agents or {}).get("bom", {}) or {})
    bom_xlsx = bom.get("bom_xlsx") if isinstance(bom.get("bom_xlsx"), dict) else {}
    context_bom_key = str(bom.get("xlsx_artifact_key") or bom_xlsx.get("key") or "").strip()
    if context_bom_key and _valid_bom_xlsx_metadata(store, context_bom_key):
        if all(item.get("key") != context_bom_key for item in downloads):
            filename = str(bom.get("xlsx_filename") or bom_xlsx.get("filename") or context_bom_key.split("/")[-1]).strip()
            downloads.append(
                {
                    "type": "bom",
                    "key": context_bom_key,
                    "download_url": f"/api/bom/{customer_id}/download/{filename}",
                }
            )

    terraform = document_store.get_latest_terraform_bundle(store, customer_id)
    if isinstance(terraform, dict):
        files = terraform.get("files", {}) if isinstance(terraform.get("files"), dict) else {}
        for filename, key in sorted(files.items()):
            key_text = str(key or "").strip()
            if key_text and store.head(key_text):
                downloads.append(
                    {
                        "type": "terraform",
                        "key": key_text,
                        "download_url": f"/api/terraform/{customer_id}/download/{filename}",
                    }
                )
    return downloads


def _build_artifact_link_reply(
    *,
    context: dict[str, Any],
    customer_id: str,
    store: ObjectStoreBase,
) -> str:
    downloads = _artifact_downloads_from_context(context=context, customer_id=customer_id, store=store)
    if not downloads:
        return (
            "I don't have a generated artifact/link for that yet. "
            "Generate the relevant BOM, diagram, or Terraform artifact first, then ask for the download link."
        )
    lines = ["Available generated artifact links:"]
    for item in downloads:
        lines.append(f"- {item['type']}: {item['download_url']} (key: {item['key']})")
    return "\n".join(lines)


def _candidate_artifact_refs(context: dict[str, Any], customer_id: str, store: ObjectStoreBase) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    agents = context.get("agents", {}) if isinstance(context, dict) else {}
    diagram = dict((agents or {}).get("diagram", {}) or {})
    diagram_key = str(diagram.get("diagram_key", "") or diagram.get("artifact_ref", "") or "").strip()
    if diagram_key:
        refs.append({"type": "diagram", "key": diagram_key})
    for key in sorted(store.list(f"customers/{customer_id}/bom/xlsx/"), reverse=True):
        if _valid_bom_xlsx_metadata(store, key):
            refs.append({"type": "bom", "key": key})
    bom = dict((agents or {}).get("bom", {}) or {})
    bom_xlsx = bom.get("bom_xlsx") if isinstance(bom.get("bom_xlsx"), dict) else {}
    context_bom_key = str(bom.get("xlsx_artifact_key") or bom_xlsx.get("key") or "").strip()
    if context_bom_key and _valid_bom_xlsx_metadata(store, context_bom_key) and all(item.get("key") != context_bom_key for item in refs):
        refs.append({"type": "bom", "key": context_bom_key})
    terraform = document_store.get_latest_terraform_bundle(store, customer_id)
    if isinstance(terraform, dict):
        files = terraform.get("files", {}) if isinstance(terraform.get("files"), dict) else {}
        for _filename, key in sorted(files.items()):
            if str(key or "").strip():
                refs.append({"type": "terraform", "key": str(key).strip()})
    return refs


def _build_artifact_verification_reply(
    *,
    context: dict[str, Any],
    customer_id: str,
    store: ObjectStoreBase,
) -> str:
    refs = _candidate_artifact_refs(context, customer_id, store)
    if not refs:
        return (
            "I don't have a persisted artifact manifest to verify yet. "
            "Generate the relevant artifact first, then ask me to verify it."
        )
    lines = ["Artifact verification from persisted object-store state:"]
    missing = False
    for item in refs:
        exists = store.head(item["key"])
        missing = missing or not exists
        status = "present" if exists else "not found"
        lines.append(f"- {item['type']}: {status} ({item['key']})")
    if missing:
        lines.append("I did not infer missing files from chat history; the status above comes from persisted keys only.")
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
    generation_or_export = any(token in msg for token in ("build", "create", "generate", "draft", "make", "export", "download"))
    bom_artifact_terms = (
        "bom",
        "bill of materials",
        "xlsx",
        "xlxs",
        "xlsc",
        "excel",
        "spreadsheet",
        "workbook",
    )
    bom_pricing_terms = ("pricing", "priced", "sku", "skus")
    if any(term in msg for term in bom_artifact_terms) or (
        generation_or_export and any(term in msg for term in bom_pricing_terms)
    ):
        requested.add("generate_bom")
    if _message_requests_diagram_generation(msg):
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


def _message_requests_diagram_generation(msg: str) -> bool:
    if "drawio" in msg or "draw.io" in msg or "topology file" in msg:
        return True
    if "diagram" not in msg:
        return False
    if any(marker in msg for marker in ("generate diagram", "generate a diagram", "build diagram", "build a diagram", "create diagram", "create a diagram", "architecture diagram")):
        return True
    if "terraform" in msg and any(marker in msg for marker in ("latest diagram", "existing diagram", "current diagram", "approved diagram")):
        return False
    return True


def _single_requested_tool_to_force(requested_tools: set[str], tool_calls: list[dict[str, Any]]) -> str:
    if len(requested_tools) != 1:
        return ""
    tool_name = next(iter(requested_tools))
    if any(call.get("tool") == tool_name for call in tool_calls):
        return ""
    return tool_name


def _default_generation_tool_args(tool_name: str, user_message: str) -> dict[str, Any]:
    text = str(user_message or "").strip()
    if tool_name == "generate_diagram":
        return {"bom_text": text}
    if tool_name == "generate_bom":
        return {"prompt": text}
    if tool_name == "generate_terraform":
        return {"prompt": text}
    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        return {"feedback": text}
    return {}


def _deliverable_requires_specialist_reply(requested_tools: set[str]) -> str:
    label = ", ".join(_ordered_requested_tools(requested_tools)) or "requested deliverable"
    return f"I can't generate that from Agent 0. This requires the `{label}` specialist path."


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


def _is_note_capture_only_request(user_message: str) -> bool:
    msg = f" {str(user_message or '').lower()} "
    capture_markers = (
        "remember",
        "save these notes",
        "save this note",
        "customer notes",
        "record this",
        "capture this",
    )
    defer_markers = (
        "do not build",
        "don't build",
        "do not generate",
        "don't generate",
        "not build",
        "not generate",
        "later use",
        "for later",
        "just remember",
    )
    return any(marker in msg for marker in capture_markers) and any(marker in msg for marker in defer_markers)


def _is_recall_intent(user_message: str) -> bool:
    msg = (user_message or "").lower()
    if _requested_generation_tools(user_message):
        return False
    return any(
        marker in msg
        for marker in (
            "what did we have before",
            "what did we decide",
            "what did the customer ask for",
            "what has the customer asked for",
            "what did customer ask for",
            "what do you remember",
            "recall",
            "summarize the current state",
            "what's the current state",
            "what did we learn",
            "what system are we migrating",
            "what are we migrating",
            "migration target",
            "target system",
        )
    )


def _is_migration_target_recall_intent(user_message: str) -> bool:
    msg = (user_message or "").lower()
    return any(
        marker in msg
        for marker in (
            "what system are we migrating",
            "what are we migrating",
            "migration target",
            "target system",
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
