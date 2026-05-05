"""
agent/archie_loop.py
--------------------
Conversational orchestrator (Agent 0).

Accepts a natural-language SA message, decides which sub-agents to invoke
using a ReAct-style agentic loop, and returns a structured reply.

Conversation history is persisted per customer_id in OCI Object Storage at
  conversations/{customer_id}/history.json

Inter-agent calls:
  generate_diagram  → sub_agent_client.call_sub_agent("diagram", ...)
  generate_pov      → sub_agent_client.call_sub_agent("pov", ...)
  generate_waf      → sub_agent_client.call_sub_agent("waf", ...)
  generate_jep      → sub_agent_client.call_sub_agent("jep", ...)
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
import agent.hat_engine as hat_engine
import agent.jep_lifecycle as jep_lifecycle
import agent.safety_rules as safety_rules
from agent.drawio_inspector import inspect_drawio_xml
from agent.orchestrator_skill_engine import (
    OrchestratorSkillDecision,
    OrchestratorSkillEngine,
)
from agent.skill_loader import discover_skills, select_skills_for_call
from agent.reference_architecture import (
    build_reference_context_lines,
    select_reference_architecture,
    select_standards_bundle,
)
import agent.archie_memory as archie_memory
import agent.sub_agent_client as sub_agent_client

logger = logging.getLogger(__name__)
_PENDING_UPDATE_WORKFLOWS: dict[str, dict[str, Any]] = {}
CPU_SKU_TO_MEM_SKU = {
    "B93113": "B93114",
    "B97384": "B97385",
    "B111129": "B111130",
    "B94176": "B94177",
    "B93297": "B93298",
}

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


def _system_message_with_hat_tools(hat_tools: list[dict]) -> str:
    if not hat_tools:
        return ORCHESTRATOR_SYSTEM_MSG
    names: list[str] = []
    for tool in hat_tools:
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(function.get("name", "") or "").strip()
        if name:
            names.append(name)
    if not names:
        return ORCHESTRATOR_SYSTEM_MSG
    contracts = "\n".join(f"- {name} {{}}" for name in names)
    return (
        ORCHESTRATOR_SYSTEM_MSG.rstrip()
        + "\n\nHat tools:\n"
        + "- use_hat_X activates an expert hat before the next reasoning round.\n"
        + "- drop_hat_X deactivates an active expert hat.\n"
        + contracts
        + "\n"
    )

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


class _CriticCompat:
    def evaluate_tool_result(self, **_kwargs: Any) -> dict[str, Any]:
        return {"overall_status": "pass", "overall_pass": True}


critic_agent = _CriticCompat()


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

    _active_hats: list[str] = []
    loaded_hats = hat_engine.load_hats()
    hat_tools = hat_engine.get_hat_tool_definitions()
    orchestrator_system_msg = _system_message_with_hat_tools(hat_tools)

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
        archie_memory._record_saved_note_context(
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
        if archie_memory._message_supersedes_pending_specialist_questions(
            user_message=user_message,
            pending_checkpoint=pending_checkpoint,
        ):
            context_store.clear_pending_checkpoint(context)
            context_store.set_open_questions(context, [])
            await asyncio.to_thread(context_store.write_context, store, customer_id, context)
            pending_checkpoint = None
        else:
            specialist_reply, specialist_call, specialist_artifact = await archie_memory._handle_pending_specialist_questions(
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
    archie_memory._record_region_constraint_if_present(context, decision_context)
    archie_memory._record_infrastructure_profile_if_present(context, user_message)
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
            diagram_available_this_scenario = archie_memory._has_architecture_definition(context)

            for tool_name in sequence:
                if tool_name == "generate_bom":
                    if archie_memory._is_bom_revision_request(scenario_text, user_message, context) or (
                        archie_memory._mentions_bom_work_product(user_message) and archie_memory._latest_bom_fact_mismatches(context)
                    ):
                        tool_args = {"prompt": user_message}
                    elif archie_memory._bom_followup_should_hydrate_from_context(
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
                archie_memory._build_context_summary_for_skills, store, customer_id, customer_name
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
            prompt_for_llm = hat_engine.inject_hats(prompt, _active_hats)
            raw = await asyncio.to_thread(
                _call_text_runner,
                text_runner,
                prompt_for_llm,
                orchestrator_system_msg,
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
            if tool_name.startswith("use_hat_"):
                hat_name = tool_name[len("use_hat_"):]
                if hat_name in loaded_hats and hat_name not in _active_hats:
                    _active_hats.append(hat_name)
                result_summary = f"Hat '{hat_name}' activated."
                result_data = {"hat": hat_name, "action": "activated"}
                tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result_summary": result_summary,
                        "result_data": result_data,
                        "artifact_key": "",
                    }
                )
                new_turns.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(tool_call, separators=(",", ":")),
                        "timestamp": _now(),
                        "tool_call": tool_call,
                    }
                )
                new_turns.append(
                    {
                        "role": "tool",
                        "tool": tool_name,
                        "result_summary": result_summary,
                        "timestamp": _now(),
                    }
                )
                prompt = _append_tool_result(prompt, tool_name, result_summary)
                continue
            if tool_name.startswith("drop_hat_"):
                hat_name = tool_name[len("drop_hat_"):]
                if hat_name in _active_hats:
                    _active_hats.remove(hat_name)
                result_summary = f"Hat '{hat_name}' deactivated."
                result_data = {"hat": hat_name, "action": "deactivated"}
                tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result_summary": result_summary,
                        "result_data": result_data,
                        "artifact_key": "",
                    }
                )
                new_turns.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(tool_call, separators=(",", ":")),
                        "timestamp": _now(),
                        "tool_call": tool_call,
                    }
                )
                new_turns.append(
                    {
                        "role": "tool",
                        "tool": tool_name,
                        "result_summary": result_summary,
                        "timestamp": _now(),
                    }
                )
                prompt = _append_tool_result(prompt, tool_name, result_summary)
                continue
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
                prompt_for_llm = hat_engine.inject_hats(
                    prompt + "\n\nProvide a brief summary of what was accomplished.",
                    _active_hats,
                )
                raw = await asyncio.to_thread(
                    _call_text_runner,
                    text_runner,
                    prompt_for_llm,
                    orchestrator_system_msg,
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
        args = archie_memory._prepare_bom_tool_args(
            args=args,
            user_message=user_message,
            context=context,
            decision_context=decision_context,
        )
    if tool_name in {"save_notes", "generate_diagram", "generate_bom", "generate_pov", "generate_jep", "generate_waf", "generate_terraform"}:
        context = context or await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name=tool_name,
            args=args,
            context=context,
            decision_context=decision_context,
            user_message=user_message,
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name=tool_name,
            args=args,
            context=context,
        )
    if (
        tool_name == "generate_diagram"
        and context is not None
        and not archie_memory._diagram_has_sufficient_context(
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
        and not archie_memory._pov_has_sufficient_context(
            context=context,
            decision_context=decision_context,
            args=args,
            user_message=user_message,
        )
    ):
        return await archie_memory._mediate_specialist_questions(
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
            result_data={"questions": archie_memory._pov_targeted_questions(), "decision_context": dict(decision_context or {})},
            context=context,
        )
    if (
        tool_name == "generate_terraform"
        and context is not None
        and archie_memory._has_architecture_definition(context)
        and not archie_memory._terraform_scope_is_bounded(
            context=context,
            args=args,
            decision_context=decision_context,
            user_message=user_message,
        )
    ):
        return await archie_memory._mediate_specialist_questions(
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
            result_data={"questions": archie_memory._terraform_targeted_questions(), "decision_context": dict(decision_context or {})},
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
            archie_memory._build_context_summary_for_skills, store, customer_id, customer_name
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
    merged_decision_context = archie_memory._merge_decision_context(decision_context, result_data.get("decision_context"))
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
        archie_memory._record_saved_note_context(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            note_key=artifact_key,
            note_text=str(args.get("text", "") or ""),
            decision_context=merged_decision_context,
        )

    if tool_name.startswith("generate_") and not bool(enriched_args.get("_archie_question_retry")):
        context = context or await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        result_summary, artifact_key, result_data = await archie_memory._mediate_specialist_questions(
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

    safe, reason = safety_rules.check(tool_name, result_data)
    if not safe:
        result_summary = f"[Safety block] {reason}"
        artifact_key = ""
        result_data = {
            "safety_block": True,
            "reason": reason,
            "blocked_tool": tool_name,
            "original_result_data": result_data,
        }

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
        raw_feedback = str(args.get("feedback", "") or "")
        feedback = raw_feedback if "[Archie Canonical Memory]" in raw_feedback else str(args.get("_user_request_text", "") or raw_feedback or "")
        task = _compose_specialist_request_text(
            clean_request=feedback or "Generate a customer POV from current engagement context.",
            architect_brief=dict(args.get("_architect_brief", {}) or {}),
        )
        response = await sub_agent_client.call_sub_agent(
            "pov",
            task,
            {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "feedback": feedback,
                "architect_brief": dict(args.get("_architect_brief", {}) or {}),
            },
            str(uuid.uuid4()),
        )
        if str(response.get("status") or "").lower() == "needs_input":
            return str(response.get("result") or "POV needs more input."), "", response
        saved = await asyncio.to_thread(
            document_store.save_doc,
            store,
            "pov",
            customer_id,
            str(response.get("result") or ""),
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        key = str(saved.get("key", "") or "")
        return f"POV v{saved.get('version')} saved. Key: {key}", key, response

    if tool_name == "generate_diagram":
        s, k, d = await _call_generate_diagram(args, customer_id, a2a_base_url)
        return s, k, d

    if tool_name == "generate_waf":
        feedback = str(args.get("feedback", "") or "")
        task = _compose_specialist_request_text(
            clean_request=feedback or "Run an OCI Well-Architected Framework review for the current architecture.",
            architect_brief=dict(args.get("_architect_brief", {}) or {}),
        )
        response = await sub_agent_client.call_sub_agent(
            "waf",
            task,
            {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "feedback": feedback,
                "architect_brief": dict(args.get("_architect_brief", {}) or {}),
            },
            str(uuid.uuid4()),
        )
        if str(response.get("status") or "").lower() == "needs_input":
            return str(response.get("result") or "WAF review needs more input."), "", response
        saved = await asyncio.to_thread(
            document_store.save_doc,
            store,
            "waf",
            customer_id,
            str(response.get("result") or ""),
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        key = str(saved.get("key", "") or "")
        return f"WAF review saved. Key: {key}", key, response

    if tool_name == "generate_jep":
        feedback = str(args.get("feedback", "") or "")
        task = _compose_specialist_request_text(
            clean_request=feedback or "Generate a Joint Engagement Plan from current engagement context.",
            architect_brief=dict(args.get("_architect_brief", {}) or {}),
        )
        response = await sub_agent_client.call_sub_agent(
            "jep",
            task,
            {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "feedback": feedback,
                "architect_brief": dict(args.get("_architect_brief", {}) or {}),
            },
            str(uuid.uuid4()),
        )
        if str(response.get("status") or "").lower() == "needs_input":
            return str(response.get("result") or "JEP needs more input."), "", response
        saved = await asyncio.to_thread(
            document_store.save_doc,
            store,
            "jep",
            customer_id,
            str(response.get("result") or ""),
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        key = str(saved.get("key", "") or "")
        jep_state = await asyncio.to_thread(jep_lifecycle.mark_generated, store, customer_id)
        response.update({
            "jep_state": jep_state,
            "reason_codes": [],
            "required_next_step": jep_state.get("required_next_step", ""),
            "lock_outcome": "allowed",
        })
        return f"JEP v{saved.get('version')} saved. Key: {key}", key, response

    if tool_name == "generate_bom":
        response = await _execute_bom_tool_request(
            args=args,
            text_runner=text_runner,
            model_id="orchestrator-generate_bom",
        )
        summary = _summarize_bom_tool_response(response)
        return summary, "", response

    if tool_name == "generate_terraform":
        raw_prompt = str(args.get("prompt", "") or "")
        task = _compose_specialist_request_text(
            clean_request=raw_prompt or str(args.get("_user_request_text", "") or "Generate Terraform for the current architecture."),
            architect_brief=dict(args.get("_architect_brief", {}) or {}),
        )
        response = await sub_agent_client.call_sub_agent(
            "terraform",
            task,
            {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "architect_brief": dict(args.get("_architect_brief", {}) or {}),
            },
            str(uuid.uuid4()),
        )
        if str(response.get("status") or "").lower() == "needs_input":
            return str(response.get("result") or "Terraform needs more input."), "", response
        files = _parse_terraform_sub_agent_result(response.get("result"))
        saved = await asyncio.to_thread(
            document_store.save_terraform_bundle,
            store,
            customer_id,
            files,
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        key = str((saved.get("files") or {}).get("main.tf") or saved.get("latest_key") or "")
        response["terraform_files"] = files
        response["terraform_bundle"] = saved
        return f"Terraform bundle v{saved.get('version')} saved. Key: {key}", key, response

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
    trace_id = str(uuid.uuid4())
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
    structured_inputs = args.get("inputs") if isinstance(args.get("inputs"), dict) else {}
    use_structured_inputs = bool(structured_inputs)
    a2a_response = await sub_agent_client.call_sub_agent(
        "bom",
        prompt,
        {
            "structured_inputs": structured_inputs,
            "model_id": model_id,
            "bom_context_source": context_source,
        },
        trace_id,
    )
    status = str(a2a_response.get("status") or "").lower()
    raw_result = str(a2a_response.get("result") or "")
    trace = a2a_response.get("trace", {}) if isinstance(a2a_response.get("trace"), dict) else {}
    parsed_payload: Any = None
    if raw_result.strip():
        try:
            parsed_payload = json.loads(raw_result)
        except Exception:
            parsed_payload = None
    if status == "needs_input":
        response = {
            "type": "question",
            "reply": raw_result or "BOM clarification required.",
            "trace_id": trace_id,
            "trace": trace,
            "bom_context_source": context_source,
        }
    else:
        response = {
            "type": "final",
            "reply": "Final BOM prepared via BOM sub-agent.",
            "trace_id": trace_id,
            "trace": trace,
            "json_bom": raw_result,
            "bom_payload": parsed_payload if isinstance(parsed_payload, dict) else {},
            "bom_context_source": context_source,
        }
    trace.update(
        {
            "bom_cache_status_before_attempt": "sub_agent",
            "bom_cache_refresh_attempted": False,
            "bom_cache_refresh_status": "not_attempted",
            "bom_context_source": context_source,
            "bom_retry_count": 0,
            "bom_retry_succeeded": False,
            "bom_request_shape": "internal_a2a_generate_bom" if use_structured_inputs else "legacy_prompt",
            "bom_trace_stages": [
                "BOM hat selected",
                "structured inputs built" if use_structured_inputs else "legacy prompt prepared",
                "BOM sub-agent called",
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


def _parse_terraform_sub_agent_result(result: Any) -> dict[str, str]:
    raw = str(result or "").strip()
    data: dict[str, Any] = {}
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {"main_tf": raw}
    mapping = {
        "main_tf": "main.tf",
        "variables_tf": "variables.tf",
        "outputs_tf": "outputs.tf",
        "readme_md": "README.md",
    }
    files = {
        filename: str(data.get(source_key) or "")
        for source_key, filename in mapping.items()
    }
    if not any(content.strip() for content in files.values()):
        files["main.tf"] = raw
    return files




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
    decision_block = archie_memory._build_decision_context_block(decision_context)
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
    key = archie_memory._tool_primary_input_key(tool_name)
    if key and key in payload:
        payload[key] = archie_memory._strip_injected_guidance_blocks(str(payload.get(key, "") or ""))
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


def _diagram_artifact_view_from_result(
    *,
    store: ObjectStoreBase,
    artifact_key: str,
    result_data: dict[str, Any],
) -> dict[str, Any]:
    xml_text = str(result_data.get("drawio_xml", "") or "")
    source = "result_data.drawio_xml" if xml_text.strip() else ""
    if not xml_text.strip() and artifact_key:
        try:
            xml_text = store.get(artifact_key).decode("utf-8", errors="replace")
            source = artifact_key
        except Exception as exc:
            return {
                "readable": False,
                "labels": [],
                "cells": [],
                "search_text": "",
                "source": artifact_key,
                "error": f"drawio_fetch_error: {exc}",
            }
    if not xml_text.strip():
        return {
            "readable": False,
            "labels": [],
            "cells": [],
            "search_text": "",
            "source": source,
            "error": "drawio_xml_missing",
        }
    view = inspect_drawio_xml(xml_text)
    view["source"] = source
    # Keep trace payload compact; the reader returns every mxCell for callers that
    # need it, but Archie only needs labeled cells and searchable text.
    labeled_cells = [cell for cell in view.get("cells", []) if str(cell.get("value", "") or "").strip()]
    view["cells"] = labeled_cells[:200]
    view["labels"] = list(view.get("labels", []) or [])[:200]
    view["cell_count"] = len(labeled_cells)
    return view


def _diagram_review_source_text(
    *,
    sanitized_tool_input: dict[str, Any],
    user_message: str,
    context_summary: str,
    decision_context: dict[str, Any],
) -> str:
    parts = [
        str(user_message or ""),
        str(sanitized_tool_input.get("bom_text", "") or ""),
        str(sanitized_tool_input.get("_user_request_text", "") or ""),
        str(context_summary or ""),
        json.dumps(decision_context or {}, ensure_ascii=True, sort_keys=True),
    ]
    return "\n".join(part for part in parts if part.strip())


def _extract_requested_bm_count(text: str) -> int:
    lowered = str(text or "").lower()
    if not any(marker in lowered for marker in (" bm", "bm.", "bare metal", "bare-metal")):
        return 0
    count_words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
    }
    for word, count in count_words.items():
        if re.search(rf"\b{word}\s+(?:bm|bare[- ]metal)", lowered):
            return count
    match = re.search(r"\b(\d+)\s*(?:x\s*)?(?:bm|bare[- ]metal)", lowered)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:bm|bare[- ]metal)[^\n]{0,30}?\b(\d+)\b", lowered)
    if match:
        return int(match.group(1))
    return 1


def _diagram_review_requirements(
    *,
    sanitized_tool_input: dict[str, Any],
    user_message: str,
    context_summary: str,
    decision_context: dict[str, Any],
) -> dict[str, Any]:
    source = _diagram_review_source_text(
        sanitized_tool_input=sanitized_tool_input,
        user_message=user_message,
        context_summary=context_summary,
        decision_context=decision_context,
    )
    lowered = source.lower()
    bm_count = _extract_requested_bm_count(source)
    split_fd = any(
        marker in lowered
        for marker in (
            "split fd",
            "split fault",
            "fd1/fd2",
            "fd1 and fd2",
            "fault domain 1",
            "fault domain 2",
        )
    )
    vmware_context = any(
        marker in lowered
        for marker in (
            "ocvs",
            "oci dedicated vmware",
            "vxrail",
            "esxi",
            "vsphere",
            "sddc",
        )
    )
    return {
        "requested_bm_count": bm_count,
        "split_fault_domains": split_fd,
        "vmware_ocvs_context": vmware_context,
    }


def _diagram_actual_text(result_data: dict[str, Any]) -> str:
    view = result_data.get("diagram_artifact_view", {}) if isinstance(result_data.get("diagram_artifact_view"), dict) else {}
    parts = [
        str(view.get("search_text", "") or ""),
        json.dumps(result_data.get("node_to_resource_map", {}) or {}, ensure_ascii=True, sort_keys=True).lower(),
        json.dumps(result_data.get("draw_dict", {}) or {}, ensure_ascii=True, sort_keys=True).lower(),
        json.dumps(result_data.get("spec", {}) or {}, ensure_ascii=True, sort_keys=True).lower(),
    ]
    return "\n".join(part for part in parts if part.strip())


def _count_actual_bm_nodes(result_data: dict[str, Any]) -> int:
    seen: set[str] = set()
    node_map = result_data.get("node_to_resource_map", {}) if isinstance(result_data.get("node_to_resource_map"), dict) else {}
    for node_id, value in node_map.items():
        if not isinstance(value, dict):
            continue
        text = " ".join(str(value.get(field, "") or "").lower() for field in ("oci_type", "label", "layer"))
        if any(marker in text for marker in ("bare metal", "bare-metal", "bm.standard", "bm host", "bm server")):
            seen.add(str(node_id))
    draw_dict = result_data.get("draw_dict", {}) if isinstance(result_data.get("draw_dict"), dict) else {}
    for node in draw_dict.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        text = " ".join(str(node.get(field, "") or "").lower() for field in ("type", "label", "id"))
        if any(marker in text for marker in ("bare metal", "bare-metal", "bm.standard", "bm host", "bm server")):
            seen.add(str(node.get("id", "") or text))
    actual_text = _diagram_actual_text(result_data)
    label_hits = re.findall(r"\b(?:bm\.standard|bm\s*(?:host|server)|bare[- ]metal)\b", actual_text)
    return max(len(seen), len(label_hits))


def _actual_fault_domain_names(result_data: dict[str, Any]) -> set[str]:
    actual_text = _diagram_actual_text(result_data)
    names: set[str] = set()
    if re.search(r"\bfd\s*1\b|fault domain 1", actual_text):
        names.add("fd1")
    if re.search(r"\bfd\s*2\b|fault domain 2", actual_text):
        names.add("fd2")
    if re.search(r"\bfd\s*3\b|fault domain 3", actual_text):
        names.add("fd3")
    return names


def _bm_fault_domain_evidence(result_data: dict[str, Any]) -> set[str]:
    actual_text = _diagram_actual_text(result_data)
    evidence: set[str] = set()
    bm_marker = r"(?:bm\.standard|bm\s*(?:host|server)|bare[- ]metal)"
    fd1_marker = r"(?:fd\s*1|fault domain 1)"
    fd2_marker = r"(?:fd\s*2|fault domain 2)"
    if re.search(rf"{fd1_marker}.{{0,180}}{bm_marker}|{bm_marker}.{{0,180}}{fd1_marker}", actual_text, flags=re.DOTALL):
        evidence.add("fd1")
    if re.search(rf"{fd2_marker}.{{0,180}}{bm_marker}|{bm_marker}.{{0,180}}{fd2_marker}", actual_text, flags=re.DOTALL):
        evidence.add("fd2")

    draw_dict = result_data.get("draw_dict", {}) if isinstance(result_data.get("draw_dict"), dict) else {}
    boxes = [box for box in draw_dict.get("boxes", []) or [] if isinstance(box, dict) and str(box.get("box_type", "") or "") == "_fd_box"]
    nodes = [node for node in draw_dict.get("nodes", []) or [] if isinstance(node, dict)]
    for node in nodes:
        node_text = " ".join(str(node.get(field, "") or "").lower() for field in ("id", "type", "label"))
        if not any(marker in node_text for marker in ("bm.standard", "bm host", "bm server", "bare metal", "bare-metal")):
            continue
        try:
            cx = float(node.get("x", 0) or 0) + float(node.get("w", 0) or 0) / 2
            cy = float(node.get("y", 0) or 0) + float(node.get("h", 0) or 0) / 2
        except (TypeError, ValueError):
            continue
        for box in boxes:
            try:
                x = float(box.get("x", 0) or 0)
                y = float(box.get("y", 0) or 0)
                w = float(box.get("w", 0) or 0)
                h = float(box.get("h", 0) or 0)
            except (TypeError, ValueError):
                continue
            if x <= cx <= x + w and y <= cy <= y + h:
                label = str(box.get("label", "") or box.get("id", "") or "").lower()
                if "1" in label:
                    evidence.add("fd1")
                elif "2" in label:
                    evidence.add("fd2")
                elif "3" in label:
                    evidence.add("fd3")
    return evidence


def _review_diagram_artifact(
    *,
    sanitized_tool_input: dict[str, Any],
    user_message: str,
    artifact_key: str,
    result_data: dict[str, Any],
    context_summary: str,
    decision_context: dict[str, Any],
) -> dict[str, Any]:
    requirements = _diagram_review_requirements(
        sanitized_tool_input=sanitized_tool_input,
        user_message=user_message,
        context_summary=context_summary,
        decision_context=decision_context,
    )
    findings: list[str] = []
    actions: list[str] = []
    view = result_data.get("diagram_artifact_view", {}) if isinstance(result_data.get("diagram_artifact_view"), dict) else {}
    requires_artifact_read = any(
        (
            int(requirements.get("requested_bm_count", 0) or 0),
            bool(requirements.get("split_fault_domains")),
            bool(requirements.get("vmware_ocvs_context")),
        )
    )
    if artifact_key and requires_artifact_read and not view.get("readable", False):
        findings.append("Archie could not read the generated draw.io artifact for acceptance review.")
        if view.get("error"):
            findings.append(str(view.get("error")))
        actions.append("Return a readable .drawio XML artifact for review.")

    requested_bm = int(requirements.get("requested_bm_count", 0) or 0)
    produced_bm = _count_actual_bm_nodes(result_data)
    if requested_bm and produced_bm < requested_bm:
        findings.append(f"Diagram artifact shows {produced_bm} BM/bare metal server nodes; requested {requested_bm}.")
        actions.append(f"Render {requested_bm} visible BM/bare metal server nodes in the diagram.")

    fd_names = _actual_fault_domain_names(result_data)
    bm_fd_evidence = _bm_fault_domain_evidence(result_data)
    if requirements.get("split_fault_domains") and not {"fd1", "fd2"} <= (fd_names & bm_fd_evidence):
        findings.append("Diagram artifact does not show BM server placement split across FD1 and FD2.")
        actions.append("Show the BM servers split across Fault Domain 1 and Fault Domain 2.")

    actual_text = _diagram_actual_text(result_data)
    if requirements.get("vmware_ocvs_context") and not any(
        marker in actual_text
        for marker in ("ocvs", "sddc", "esxi", "vsphere", "vcenter", "nsx", "vmware")
    ):
        findings.append("Diagram artifact does not show OCVS/VMware-specific elements from the engagement context.")
        actions.append("Render OCVS/VMware elements such as SDDC, ESXi hosts, vSphere/vCenter, or NSX where appropriate.")

    return {
        "findings": findings,
        "required_actions": actions,
        "requirements": requirements,
        "produced": {
            "bm_count": produced_bm,
            "fault_domains": sorted(fd_names),
            "bm_fault_domain_evidence": sorted(bm_fd_evidence),
            "artifact_readable": bool(view.get("readable", False)),
        },
    }


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
    if tool_name == "generate_diagram":
        current_data["diagram_artifact_view"] = _diagram_artifact_view_from_result(
            store=store,
            artifact_key=current_key,
            result_data=current_data,
        )
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

    if (
        tool_name == "generate_diagram"
        and review.get("verdict") == "revise_required"
        and _diagram_review_retry_is_safe(current_data)
    ):
        feedback = _build_archie_diagram_review_feedback(review)
        if feedback:
            retry_args = dict(args)
            retry_args["_archie_expert_review_retry"] = True
            original_bom_text = str(retry_args.get("bom_text", "") or user_message or "").strip()
            retry_args["bom_text"] = (
                f"{original_bom_text}\n\n[Archie Diagram Artifact Review Feedback]\n{feedback}"
            ).strip()
            architect_brief = dict(retry_args.get("_architect_brief", {}) or {})
            architect_context = str(architect_brief.get("architect_context", "") or "").strip()
            architect_brief["architect_context"] = (
                f"{architect_context}\n\n[Archie Diagram Artifact Review Feedback]\n{feedback}"
            ).strip()
            architect_brief["user_notes"] = _append_diagram_review_feedback_to_notes(
                str(architect_brief.get("user_notes", "") or original_bom_text),
                feedback,
            )
            retry_args["_architect_brief"] = architect_brief
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
            retry_data["applied_skills"] = list(current_data.get("applied_skills", []) or args.get("_skill_injected", []) or [])
            retry_data["skill_versions"] = dict(current_data.get("skill_versions", {}) or args.get("_skill_versions", {}) or {})
            retry_data["skill_model_profile"] = str(current_data.get("skill_model_profile", "") or args.get("_skill_model_profile", "") or "")
            retry_data["diagram_artifact_view"] = _diagram_artifact_view_from_result(
                store=store,
                artifact_key=retry_key,
                result_data=retry_data,
            )
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
                        "Diagram artifact review retry did not satisfy the requested visual elements.",
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

    if tool_name == "generate_diagram":
        diagram_review = _review_diagram_artifact(
            sanitized_tool_input=sanitized_tool_input,
            user_message=user_message,
            artifact_key=artifact_key,
            result_data=result_data,
            context_summary=context_summary,
            decision_context=decision_context,
        )
        findings.extend(diagram_review["findings"])
        required_actions.extend(diagram_review["required_actions"])
        governor = result_data.get("governor", {}) if isinstance(result_data.get("governor"), dict) else {}
        governor_status = str(governor.get("overall_status", "pass") or "pass").lower()
        if governor_status == "revise":
            critique = str(governor.get("decision_summary", "") or governor.get("critique_summary", "") or "").strip()
            if critique:
                findings.append(critique)
            else:
                findings.append("Governor quality review requested diagram revision.")
        if findings:
            verdict = "revise_required" if _diagram_review_retry_is_safe(result_data) else "blocked"
        return {
            "verdict": verdict,
            "findings": findings,
            "required_actions": required_actions,
            "retry_allowed": verdict == "revise_required",
            "review_type": "diagram_artifact_acceptance",
            "requirements": diagram_review["requirements"],
            "produced": diagram_review["produced"],
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


def _diagram_review_retry_is_safe(result_data: dict[str, Any]) -> bool:
    if not isinstance(result_data, dict):
        return False
    if result_data.get("_archie_expert_review_retry") is True:
        return False
    if isinstance(result_data.get("archie_question_bundle"), dict):
        return False
    if str(result_data.get("diagram_recovery_status", "none") or "none") in {"needs_clarification", "backend_error"}:
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


def _build_archie_diagram_review_feedback(review: dict[str, Any]) -> str:
    findings = [str(item).strip() for item in review.get("findings", []) if str(item).strip()]
    actions = [str(item).strip() for item in review.get("required_actions", []) if str(item).strip()]
    requirements = review.get("requirements", {}) if isinstance(review.get("requirements"), dict) else {}
    lines = [
        "Revise the draw.io artifact so the visible diagram satisfies Archie architect review.",
    ]
    if requirements:
        lines.append("Required visual evidence:")
        if int(requirements.get("requested_bm_count", 0) or 0):
            lines.append(f"- {int(requirements.get('requested_bm_count', 0) or 0)} visible BM/bare metal server nodes.")
        if requirements.get("split_fault_domains"):
            lines.append("- BM/bare metal servers visibly split across FD1 and FD2.")
        if requirements.get("vmware_ocvs_context"):
            lines.append("- OCVS/VMware-specific elements such as SDDC, ESXi hosts, vSphere/vCenter, or NSX where appropriate.")
    lines.extend(f"- Finding: {item}" for item in findings)
    lines.extend(f"- Required action: {item}" for item in actions)
    return "\n".join(lines).strip()


def _append_diagram_review_feedback_to_notes(notes: str, feedback: str) -> str:
    clean_notes = archie_memory._strip_injected_guidance_blocks(str(notes or "")).strip()
    clean_feedback = archie_memory._strip_injected_guidance_blocks(str(feedback or "")).strip()
    if not clean_feedback:
        return clean_notes
    if clean_feedback in clean_notes:
        return clean_notes
    return (
        f"{clean_notes}\n\n"
        "Archie diagram acceptance corrections for this regeneration:\n"
        f"{clean_feedback}"
    ).strip()


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
        ocpu = archie_memory._coerce_positive_float(compute.get("ocpu"))
        ram_gb = archie_memory._coerce_positive_float(memory.get("gb"))
        block_tb = archie_memory._coerce_positive_float(storage.get("block_tb"))
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
        if tool_name not in refinable_tools or refinement_count >= max_refinements:
            if refinement_count >= max_refinements and governor.get("issues"):
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
            key = "prompt" if tool_name == "generate_terraform" else "feedback"
            retry_args[key] = f"{retry_args.get(key, '')}\n\n{guidance_block}".strip()

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
        retry_data["critic_retry"] = {"attempt": refinement_count, "feedback": feedback}
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




def _compose_specialist_request_text(
    *,
    clean_request: str,
    architect_brief: dict[str, Any] | None,
    include_missing_inputs: bool = True,
) -> str:
    brief_block = archie_memory._render_architect_brief_text(architect_brief)
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
            "assumptions_used": archie_memory._merge_assumption_lists(
                list((decision_context or {}).get("assumptions", []) or []),
                list(result_data.get("assumptions_used", []) or []),
            ),
            "decision_context_hash": archie_memory._decision_context_hash(decision_context),
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
            "decision_context_hash": archie_memory._decision_context_hash(decision_context),
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
        "decision_context_hash": archie_memory._decision_context_hash(decision_context),
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
        "decision_context_hash": archie_memory._decision_context_hash(decision_context),
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
            final_answer = archie_memory._standard_components_scope_answer()
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
    user_notes = str(architect_brief.get("user_notes", "") or "").strip() or archie_memory._strip_injected_guidance_blocks(bom_text).strip()
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
    architect_requirements = _diagram_review_requirements(
        sanitized_tool_input=args,
        user_message=user_notes,
        context_summary=architect_context,
        decision_context=decision_context if isinstance(decision_context, dict) else {},
    )
    acceptance_lines: list[str] = []
    if int(architect_requirements.get("requested_bm_count", 0) or 0):
        acceptance_lines.append(
            f"- Render {int(architect_requirements.get('requested_bm_count', 0) or 0)} visible BM/bare metal server or host nodes."
        )
    if architect_requirements.get("split_fault_domains"):
        acceptance_lines.append("- Show BM/bare metal servers visibly split across FD1 and FD2.")
    if architect_requirements.get("vmware_ocvs_context"):
        acceptance_lines.append("- Include visible OCVS/VMware-specific elements when applicable, such as SDDC, ESXi hosts, vSphere/vCenter, or NSX.")
    if acceptance_lines:
        context_parts.append("Archie diagram architect acceptance criteria:\n" + "\n".join(acceptance_lines))
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
            merged_decision_context = archie_memory._merge_decision_context(
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

    merged = archie_memory._merge_assumption_lists(
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
        "assumptions_used": archie_memory._merge_assumption_lists([], list(assumptions_used or [])),
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
    if outputs.get("drawio_xml"):
        result_data["drawio_xml"] = str(outputs.get("drawio_xml") or "")
    return result_data


async def _post_diagram_a2a_task(
    *,
    payload: dict[str, Any],
    a2a_base_url: str,
) -> dict[str, Any]:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    notes = str(inputs.get("notes") or payload.get("task") or "")
    context = str(inputs.get("context") or "")
    task = "\n\n".join(part for part in (notes.strip(), context.strip()) if part)
    response = await sub_agent_client.call_sub_agent(
        "diagram",
        task or "Generate a diagram for this engagement.",
        {
            "diagram_name": str(payload.get("task_id") or "diagram"),
            "customer_id": str(payload.get("client_id") or ""),
            "reference_architecture": inputs.get("reference_architecture") or {},
            "standards_bundle_version": str(inputs.get("standards_bundle_version") or ""),
        },
        str(payload.get("task_id") or ""),
    )
    status = str(response.get("status") or "error").lower()
    if status == "ok":
        return {
            "status": "ok",
            "task_id": str(payload.get("task_id") or ""),
            "outputs": {
                "drawio_xml": str(response.get("result") or ""),
                "trace": response.get("trace", {}),
            },
        }
    if status == "needs_input":
        questions: Any = []
        raw_result = str(response.get("result") or "")
        if raw_result:
            try:
                questions = json.loads(raw_result)
            except Exception:
                questions = [{"question": raw_result}]
        return {
            "status": "need_clarification",
            "task_id": str(payload.get("task_id") or ""),
            "outputs": {"questions": questions if isinstance(questions, list) else []},
        }
    return {
        "status": "error",
        "task_id": str(payload.get("task_id") or ""),
        "error_message": str(response.get("result") or "Diagram sub-agent failed."),
        "outputs": {"trace": response.get("trace", {})},
    }


def _diagram_reply_assumptions(
    result_data: dict[str, Any] | None,
    fallback_decision_context: dict[str, Any] | None = None,
) -> list[str]:
    assumption_pool = archie_memory._merge_assumption_lists(
        list((fallback_decision_context or {}).get("assumptions", []) or []),
        list((result_data or {}).get("assumptions_used", []) or []),
    )
    if isinstance((result_data or {}).get("decision_context"), dict):
        assumption_pool = archie_memory._merge_assumption_lists(
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
    if str(governor.get("overall_status", "pass") or "pass") in {"revise", "blocked", "checkpoint_required"}:
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
    assumptions = archie_memory._merge_assumption_lists(list((decision_context or {}).get("assumptions", []) or []), [])
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
        assumptions = archie_memory._merge_assumption_lists(
            assumptions,
            list((data.get("decision_context", {}) or {}).get("assumptions", []) or []),
        )
        assumptions = archie_memory._merge_assumption_lists(assumptions, list(data.get("assumptions_used", []) or []))

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

    deliverables = [archie_memory._tool_goal_label(str(call.get("tool", "") or "requested_tool")) for call in successful_calls]
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
    requested_deliverables = [archie_memory._tool_goal_label(tool) for tool in requested_tools]
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
            if archie_memory._bom_call_was_memory_revision(data) and "BOM revision was performed" not in summary:
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
        label = archie_memory._tool_goal_label(tool_name)
        summary = str(call.get("result_summary", "") or "").strip()
        if summary:
            lines.append(f"- {label}: {summary}")
        else:
            lines.append(f"- {label} completed.")
        if tool_name == "generate_bom":
            data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
            if archie_memory._bom_call_was_memory_revision(data) and "BOM revision was performed" not in lines[-1]:
                lines.append("  BOM revision was performed from updated memory.")
            lines.extend(_bom_resolved_inputs_reply_section(data))
    merged_assumptions = archie_memory._merge_assumption_lists(
        list((decision_context or {}).get("assumptions", []) or []),
        [],
    )
    for call in tool_calls:
        data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
        merged_assumptions = archie_memory._merge_assumption_lists(
            merged_assumptions,
            list((data.get("decision_context", {}) or {}).get("assumptions", []) or []),
        )
        merged_assumptions = archie_memory._merge_assumption_lists(
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
    architecture_option = str(payload.get("architecture_option", "") or "").strip()
    if architecture_option:
        lines.append(f"Architecture option: {architecture_option}")
        if "native" in architecture_option.lower():
            lines.append("Diagram directive: OCI Native Services, no OCVS/vCenter/NSX/ESXi boxes.")
    target_services = list((payload.get("structured_inputs", {}) or {}).get("target_services", []) or [])
    if target_services:
        lines.append("Native target services: " + ", ".join(str(item) for item in target_services if str(item).strip()))
    mapping = list((payload.get("structured_inputs", {}) or {}).get("workload_service_mapping", []) or [])
    if mapping:
        lines.append("Workload-to-service mapping:")
        for item in mapping[:10]:
            if isinstance(item, dict) and item.get("workload") and item.get("target_service"):
                lines.append(f"- {item.get('workload')} -> {item.get('target_service')}")
    resolved_inputs = list(payload.get("resolved_inputs", []) or [])
    if resolved_inputs:
        lines.append("Resolved BOM inputs:")
        for item in resolved_inputs[:12]:
            if isinstance(item, dict) and item.get("question_id") and item.get("answer"):
                lines.append(f"- {item.get('question_id')}: {item.get('answer')}")
    line_items = list(payload.get("line_items", []) or [])
    if line_items:
        lines.append("Line items:")
        for idx, item in enumerate(line_items[:20], start=1):
            if not isinstance(item, dict):
                continue
            sku = str(item.get("sku", "") or item.get("part_number", "") or "").strip()
            desc = str(item.get("description", "") or item.get("name", "") or item.get("service", "") or "").strip()
            qty = item.get("quantity", item.get("qty", ""))
            notes = str(item.get("notes", "") or "").strip()
            bits = [f"{idx}."]
            if sku:
                bits.append(sku)
            if desc:
                bits.append(desc)
            if qty not in ("", None):
                bits.append(f"qty={qty}")
            if notes:
                bits.append(f"notes={notes}")
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
            label = archie_memory._tool_goal_label(tool_name)
            summary = str(call.get("result_summary", "") or "").strip()
            if tool_name == "generate_diagram":
                diagram_ran = True
            lines.append(f"- {label}: {summary or 'completed.'}")
        if not diagram_ran:
            lines.append("- Architecture diagram: skipped until the BOM clarification above is resolved.")

    merged_assumptions = archie_memory._merge_assumption_lists(
        list((decision_context or {}).get("assumptions", []) or []),
        [],
    )
    for call in tool_calls:
        data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
        merged_assumptions = archie_memory._merge_assumption_lists(
            merged_assumptions,
            list((data.get("decision_context", {}) or {}).get("assumptions", []) or []),
        )
        merged_assumptions = archie_memory._merge_assumption_lists(
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
        docs = " and ".join(archie_memory._tool_goal_label(tool) for tool in _ordered_requested_tools(pov_or_jep))
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

    has_architecture = archie_memory._has_architecture_definition(context)
    diagram_requested = "generate_diagram" in requested_tools
    bom_requested = "generate_bom" in requested_tools
    diagram_will_be_built = diagram_requested

    terraform_args = {"_user_request_text": user_message, "prompt": user_message}
    terraform_scope_bounded = archie_memory._terraform_scope_details_are_bounded(
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
            lines.extend(f"- {item['question']}" for item in archie_memory._terraform_targeted_questions())
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
        if not archie_memory._diagram_has_sufficient_context(
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
    return archie_memory._pov_has_sufficient_context(
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

    if target_artifact == "bom" and archie_memory._is_bom_revision_request(text, text, context):
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
    if archie_memory._is_bom_revision_request(user_message, user_message, context):
        return True
    archie = context_store.get_archie_state(context)
    has_facts = bool(archie.get("client_facts") or archie.get("infrastructure_profile"))
    latest_downloads = [
        item for item in _artifact_downloads_from_context(context=context, customer_id=customer_id, store=store)
        if item.get("type") == "bom"
    ]
    if action_intent.get("production") and archie_memory._mentions_bom_work_product(user_message) and has_facts and not latest_downloads:
        return True
    return bool(archie_memory._mentions_bom_work_product(user_message) and archie_memory._latest_bom_fact_mismatches(context))


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
    if any(verb in msg for verb in generation_verbs) and archie_memory._mentions_bom_work_product(user_message):
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
    if _message_requests_diagram_generation(msg) or _message_requests_diagram_revision(msg):
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


def _message_requests_diagram_revision(msg: str) -> bool:
    revision_marker = any(
        marker in msg
        for marker in (
            "does not show",
            "doesn't show",
            "doesnt show",
            "not showing",
            "missing",
            "add ",
            "update ",
            "revise ",
        )
    )
    visual_target = any(
        marker in msg
        for marker in (
            " bm",
            "bm.",
            "bare metal",
            "fault domain",
            " fd",
            "server",
            "host",
            "ocvs",
            "sddc",
            "esxi",
            "vsphere",
        )
    )
    return revision_marker and visual_target


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
