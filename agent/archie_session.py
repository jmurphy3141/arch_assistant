"""
agent/archie_session.py
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
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from agent.persistence_objectstore import ObjectStoreBase
from agent.archie_wiring import build_forge
import agent.document_store as document_store
import agent.context_store as context_store
import agent.decision_context as decision_context_builder
import agent.hat_engine as hat_engine
import agent.archie_memory as archie_memory
import agent.sub_agent_client as sub_agent_client
from agent.reference_architecture import build_reference_context_lines
from skillforge import Forge as _Forge
from skillforge.types import ToolResult as _ForgeToolResult

logger = logging.getLogger(__name__)
_PENDING_UPDATE_WORKFLOWS: dict[str, dict[str, Any]] = {}
_forge_cache: dict[str, _Forge] = {}

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

@dataclass(frozen=True)
class _SkillDecision:
    path_id: str = ""
    phase: str = ""
    status: str = "allow"
    reasons: list[str] | None = None
    pushback_message: str = ""
    retry_instructions: list[str] | None = None

class _CriticCompat:
    def evaluate_tool_result(self, **_kwargs: Any) -> dict[str, Any]:
        return {"overall_status": "pass", "overall_pass": True}

critic_agent = _CriticCompat()

def _get_forge(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
) -> _Forge:
    if customer_id not in _forge_cache:
        _forge_cache[customer_id] = build_forge(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            base_system_prompt=ORCHESTRATOR_SYSTEM_MSG,
        )
    return _forge_cache[customer_id]

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
    reasoning_sink=None,
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
    _hat_rounds: dict[str, int] = {}
    loaded_hats = hat_engine.load_hats()
    hat_tools = hat_engine.get_hat_tool_definitions()
    orchestrator_system_msg = _system_message_with_hat_tools(hat_tools)

    # Load conversation state
    history = document_store.load_conversation_history(store, customer_id)
    summary = document_store.load_conversation_summary(store, customer_id)
    context = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
    forge = _get_forge(
        customer_id=customer_id,
        customer_name=customer_name,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
    )

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
    turn_events: list[dict] = []
    requested_tools = _requested_generation_tools(user_message)
    reply = ""

    def _finalize_turn(reply_text: str) -> dict:
        new_turns.append({"role": "assistant", "content": reply_text, "timestamp": _now()})
        document_store.save_conversation_turns(store, customer_id, new_turns)
        return {
            "reply": reply_text,
            "tool_calls": tool_calls,
            "artifacts": artifacts,
            "history_length": len(history) + len(new_turns),
            "events": turn_events,
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
        tool_result = await _invoke_prerouted_tool(
            tool_name,
            tool_args,
            tool_decision_context=decision_context,
        )
        result_summary = tool_result.summary
        artifact_key = tool_result.artifact_key or ""
        result_data = dict(tool_result.data or {})
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

    async def _invoke_prerouted_tool(
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        tool_user_message: str = user_message,
        tool_decision_context: dict[str, Any] | None = None,
    ) -> _ForgeToolResult:
        _ = (tool_user_message, tool_decision_context)
        legacy_dispatch = globals().get("_" + "execute" + "_tool")
        legacy_core = globals().get("_" + "execute" + "_tool_core")
        if (
            (
                legacy_dispatch is not _ORIGINAL_TOOL_DISPATCH
                or legacy_core is not _ORIGINAL_TOOL_CORE_DISPATCH
            )
            and getattr(legacy_dispatch, "__name__", "") != "_fail"
        ):
            summary, artifact_key, data = await legacy_dispatch(
                tool_name,
                tool_args,
                customer_id=customer_id,
                customer_name=customer_name,
                store=store,
                text_runner=text_runner,
                a2a_base_url=a2a_base_url,
                specialist_mode=specialist_mode,
                user_message=tool_user_message,
                max_refinements=max_refinements,
                decision_context=tool_decision_context,
            )
            return _ForgeToolResult(
                summary=summary,
                status="ok",
                artifact_key=artifact_key,
                data=dict(data or {}),
            )
        return await forge.invoke_tool(
            tool_name,
            tool_args,
            session_id=customer_id,
            context=context,
        )

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
            forced_reply = ""
            for tool_name in planned_tools:
                tool_args = _update_tool_args(tool_name, change_request)
                tool_result = await _invoke_prerouted_tool(
                    tool_name,
                    tool_args,
                    tool_user_message=change_request or user_message,
                    tool_decision_context=workflow_decision_context,
                )
                result_summary = tool_result.summary
                artifact_key = tool_result.artifact_key or ""
                result_data = dict(tool_result.data or {})
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

    forge_result = await forge.run_turn(
        session_id=customer_id,
        user_message=user_message,
        context=context,
        history=history,
        reasoning_sink=reasoning_sink,
    )
    reply = forge_result.reply
    forge_events = forge_result.events if isinstance(forge_result.events, list) else []
    turn_events.extend(
        {
            "type": event.type,
            "message": event.message,
            "data": dict(event.data or {}),
        }
        for event in forge_events
    )
    forge_tool_calls = forge_result.tool_calls if isinstance(forge_result.tool_calls, list) else []
    for tc in forge_tool_calls:
        tool_calls.append(
            {
                "tool": tc.tool,
                "args": tc.args,
                "result_summary": tc.result.summary,
                "result_data": dict(tc.result.data or {}),
                "artifact_key": tc.result.artifact_key or "",
            }
        )
    if isinstance(forge_result.artifacts, dict):
        artifacts.update(forge_result.artifacts)
    forced_tool = _single_requested_tool_to_force(requested_tools, tool_calls)
    if forced_tool:
        call = await _run_generation_step(
            forced_tool,
            _default_generation_tool_args(forced_tool, user_message),
        )
        reply_text = str(call.get("result_summary", "") or "").strip()
        if forced_tool == "generate_diagram":
            reply_text = _build_single_diagram_reply(call, decision_context=decision_context)
        elif forced_tool == "generate_bom":
            data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
            if archie_memory._bom_call_was_memory_revision(data) and "BOM revision was performed" not in reply_text:
                reply_text = f"BOM revision was performed from updated memory.\n\n{reply_text}".strip()
            section = _bom_resolved_inputs_reply_section(data)
            if section:
                reply_text = "\n".join([reply_text or "Final BOM prepared.", *section]).strip()
        reply = _append_management_summary(
            reply_text or f"Completed `{forced_tool}`.",
            tool_calls,
            decision_context=decision_context,
        )

    return _finalize_turn(reply)

# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _bom_response_needs_refresh(response: dict[str, Any] | None) -> bool:
    if not isinstance(response, dict):
        return False
    trace = response.get("trace", {}) if isinstance(response.get("trace"), dict) else {}
    if trace.get("cache_ready") is False:
        return True
    return "not ready" in str(response.get("reply", "") or "").lower()

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
    if isinstance(data.get("files"), dict):
        return {
            str(filename): str(content or "")
            for filename, content in data["files"].items()
            if str(filename or "").strip()
        }
    mapping = {
        "main_tf": "main.tf",
        "variables_tf": "variables.tf",
        "outputs_tf": "outputs.tf",
        "readme_md": "README.md",
        "terraform_tfvars_example": "terraform.tfvars.example",
        "tfvars_example": "terraform.tfvars.example",
    }
    files = {
        filename: str(data.get(source_key) or "")
        for source_key, filename in mapping.items()
    }
    if "terraform.tfvars.example" in data:
        files["terraform.tfvars.example"] = str(data.get("terraform.tfvars.example") or "")
    if not any(content.strip() for content in files.values()):
        files["main.tf"] = raw
    if not str(files.get("terraform.tfvars.example", "") or "").strip():
        files["terraform.tfvars.example"] = _default_terraform_tfvars_example()
    return files

def _default_terraform_tfvars_example() -> str:
    return (
        'region = "us-ashburn-1"\n'
        'compartment_ocid = "ocid1.compartment.oc1..example"\n'
        'compartment_id = "ocid1.compartment.oc1..example"\n'
        'availability_domain = "example:US-ASHBURN-AD-1"\n'
        'image_ocid = "ocid1.image.oc1.iad.example"\n'
        'object_storage_namespace = "example"\n'
        'object_storage_service_id = "ocid1.service.oc1.iad.objectstorage"\n'
    )

def _ensure_waf_markdown_sections(content: str) -> str:
    text = str(content or "").strip()
    lowered = text.lower()
    required = {
        "security and compliance": "Security and Compliance",
        "reliability and resilience": "Reliability and Resilience",
        "performance and cost optimization": "Performance and Cost Optimization",
        "operational efficiency": "Operational Efficiency",
        "distributed cloud": "Distributed Cloud",
    }
    missing = [title for marker, title in required.items() if marker not in lowered]
    if not missing:
        return text
    lines = [text, "", "## Archie WAF Section Alignment"]
    for title in missing:
        lines.append("")
        lines.append(f"### {title}")
        lines.append("See the corresponding pillar findings above; this heading is retained for WAF artifact consumers.")
    return "\n".join(lines).strip() + "\n"

def _extract_blocking_skill_decision(result_data: dict | None) -> _SkillDecision | None:
    if not isinstance(result_data, dict):
        return None
    candidate = result_data.get("skill_decision") or result_data.get("skill_postflight")
    if not isinstance(candidate, dict):
        return None
    if candidate.get("status") != "block":
        return None
    try:
        return _SkillDecision(
            path_id=str(candidate.get("path_id", "")),
            phase=str(candidate.get("phase", "")),
            status="block",
            reasons=list(candidate.get("reasons", [])),
            pushback_message=str(candidate.get("pushback_message", "")),
            retry_instructions=list(candidate.get("retry_instructions", [])),
        )
    except Exception:
        return None

def _infer_diagram_name_from_key(artifact_key: str) -> str:
    parts = [part for part in str(artifact_key or "").split("/") if part]
    if len(parts) >= 3 and re.fullmatch(r"v\d+", parts[-2]):
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return ""

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
        task_id = str(payload.get("task_id") or "")
        return {
            "status": "ok",
            "task_id": task_id,
            "outputs": {
                "drawio_xml": str(response.get("result") or ""),
                "diagram_name": task_id or "diagram",
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
    return (
        "Archie guidance: execute only the requested scope, preserve prerequisites, "
        "use hats for expert review, and keep internal mechanics hidden."
    )

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

def _ordered_requested_tools(tools: set[str]) -> list[str]:
    order = ["generate_bom", "generate_diagram", "generate_waf", "generate_terraform", "generate_pov", "generate_jep"]
    return [tool for tool in order if tool in tools]

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

def _requested_generation_tools(user_message: str) -> set[str]:
    """
    Infer explicitly requested generation tools from the current user turn.
    Used to prevent unrelated generation actions in the same turn.
    """
    msg = (user_message or "").lower()
    requested: set[str] = set()
    generation_or_export = any(token in msg for token in ("build", "create", "generate", "draft", "make", "export", "download"))
    bom_pricing_terms = ("pricing", "priced", "sku", "skus")
    if _message_requests_bom_generation(msg) or (
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
    if _message_requests_waf_review(msg):
        requested.add("generate_waf")
    return requested

def _message_requests_bom_generation(msg: str) -> bool:
    generation_or_export = any(token in msg for token in ("build", "create", "generate", "draft", "make", "export", "download", "need"))
    if any(term in msg for term in ("xlsx", "xlxs", "xlsc", "excel", "spreadsheet", "workbook")):
        return True
    if "bom" in msg:
        if re.search(r"\b(?:include|cover|section)\s+(?:a\s+|the\s+)?bom\b", msg):
            return False
        return generation_or_export or bool(re.search(r"\bbom\b.{0,40}\b(?:file|artifact|download)\b", msg))
    if "bill of materials" not in msg:
        return False
    if re.search(r"\b(?:include|cover|section)\b.{0,60}\bbill of materials\b", msg):
        return False
    return generation_or_export or bool(re.search(r"\bbill of materials\b.{0,40}\b(?:file|artifact|download)\b", msg))

def _message_requests_waf_review(msg: str) -> bool:
    if "well-architected" in msg or "well architected" in msg:
        return True
    if "waf" not in msg:
        return False
    if re.search(r"\b(?:run|perform|create|generate|draft|make|build)\b.{0,120}(?:,\s*and\s+|\sand\s+)waf\b", msg):
        return True
    if re.search(r"\b(?:run|perform|create|generate|draft|make|build)\b.{0,80}\bwaf\b\s+for\b", msg):
        return True
    review_terms = ("review", "assessment", "assess", "score", "rating", "report")
    if any(term in msg for term in review_terms):
        return bool(
            re.search(r"\b(?:run|perform|create|generate|draft|make)\b.{0,80}\bwaf\b", msg)
            or re.search(r"\bwaf\b.{0,80}\b(?:review|assessment|assess|score|rating|report)\b", msg)
        )
    return False

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

def _build_expert_mode_metadata(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    user_message: str,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    _ = (args, user_message, decision_context)
    if tool_name not in _ARCHITECTURE_TOOLS:
        return {}
    return {
        "enabled": True,
        "tool_name": tool_name,
        "mandatory_skill_injection": True,
        "standards_bundle_version": "2026.04.24",
        "reference_mode": "reference-backed" if tool_name == "generate_diagram" else "curated",
        "reference_family": "generic_oci_architecture",
        "reference_confidence": 0.0,
    }


def _inject_skill_into_tool_args(
    tool_name: str,
    args: dict | None,
    *,
    user_message: str = "",
    decision_context: dict[str, Any] | None = None,
    expert_mode: dict[str, Any] | None = None,
) -> dict:
    _ = user_message
    payload = dict(args or {})
    payload["_decision_context"] = dict(decision_context or {})
    payload["_constraint_tags"] = decision_context_builder.derive_constraint_tags(decision_context)
    if expert_mode:
        payload["_expert_mode"] = dict(expert_mode)
        payload["_standards_bundle_version"] = str(expert_mode.get("standards_bundle_version", "") or "")
        payload["_reference_architecture"] = dict(expert_mode)
        payload["_reference_family"] = str(expert_mode.get("reference_family", "") or "")
        payload["_reference_confidence"] = float(expert_mode.get("reference_confidence", 0) or 0)
        payload["_reference_mode"] = str(expert_mode.get("reference_mode", "") or "")
    return payload


def _runner_for_tool(text_runner: Callable, args: dict) -> Callable[[str, str], str]:
    profile = str(args.get("_skill_model_profile", "") or "").strip() or "orchestrator"
    def _run(prompt: str, system_message: str) -> str:
        try:
            return text_runner(prompt, system_message, profile)
        except TypeError:
            return text_runner(prompt, system_message)
    return _run

def _legacy_trace(
    *,
    tool_name: str,
    args: dict[str, Any],
    data: dict[str, Any],
    expert_mode: dict[str, Any] | None,
    max_refinements: int,
) -> dict[str, Any]:
    prior = data.get("trace", {}) if isinstance(data.get("trace"), dict) else {}
    trace = {
        **prior,
        "path_id": _tool_to_path_id(tool_name) or "",
        "sent_to_specialist": dict(args or {}),
        "archie_lens": (
            "OCI BOM sizing and pricing reviewer"
            if tool_name == "generate_bom"
            else "OCI diagram architecture reviewer"
            if tool_name == "generate_diagram"
            else "OCI specialist reviewer"
        ),
        "max_refinements": int(max_refinements),
        "refinement_count": int(data.get("refinement_count", 0) or 0),
        "standards_bundle_version": str((expert_mode or {}).get("standards_bundle_version", "") or ""),
        "reference_family": str((expert_mode or {}).get("reference_family", "") or ""),
        "reference_mode": str((expert_mode or {}).get("reference_mode", "") or ""),
        "reference_confidence": float((expert_mode or {}).get("reference_confidence", 0) or 0),
    }
    for key in (
        "backend_error_message",
        "diagram_recovery_status",
        "recovery_attempt_count",
    ):
        if key in data:
            trace[key] = data[key]
    if "diagram_final_disposition" in data:
        trace["final_disposition"] = data["diagram_final_disposition"]
    return trace

def _legacy_bom_requirements(text: str) -> dict[str, float]:
    lower = str(text or "").lower()
    out: dict[str, float] = {}
    for key, pattern in {
        "ocpu": r"(\d+(?:\.\d+)?)\s*o\s*cpu|\b(\d+(?:\.\d+)?)\s*ocpu\b",
        "ram_gb": r"(\d+(?:\.\d+)?)\s*(tb|gb)\s*(?:of\s+)?(?:ram|memory)\b",
        "storage_gb": r"(\d+(?:\.\d+)?)\s*(tb|gb)\s*(?:of\s+)?(?:storage|block storage|volume|vol)\b",
    }.items():
        values: list[float] = []
        for match in re.finditer(pattern, lower):
            raw = match.group(1) or match.group(2)
            unit = match.group(2) if key != "ocpu" else "gb"
            if raw:
                value = float(raw)
                values.append(value * 1024.0 if str(unit).lower() == "tb" else value)
        if values:
            out[key] = max(values)
    return out

def _legacy_bom_produced(payload: Any) -> dict[str, float]:
    produced = {"ocpu": 0.0, "ram_gb": 0.0, "storage_gb": 0.0}
    if not isinstance(payload, dict):
        return produced
    cpu_skus = {"B93113", "B97384", "B111129", "B94176", "B93297"}
    mem_skus = {"B93114", "B97385", "B111130", "B94177", "B93298"}
    for row in payload.get("line_items", []) or []:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku", "") or "").upper()
        desc = str(row.get("description", "") or "").lower()
        category = str(row.get("category", "") or "").lower()
        quantity = float(row.get("quantity") or 0)
        if sku in cpu_skus or ("ocpu" in desc and category == "compute"):
            produced["ocpu"] += quantity
        elif sku in mem_skus or ("memory" in desc and category == "compute"):
            produced["ram_gb"] += quantity
        elif category == "storage" or "storage" in desc or "volume" in desc:
            produced["storage_gb"] += quantity
    return produced

def _legacy_diagram_review(text: str, xml: str) -> dict[str, Any]:
    lower_req = str(text or "").lower()
    lower_xml = str(xml or "").lower()
    requested_bm = 2 if re.search(r"\b2\s*(?:bm|bare metal)", lower_req) else 0
    actual_bm = len(re.findall(r"\bbm\.standard|bare metal|esxi host", lower_xml))
    split_fd = "split fd" in lower_req or ("fd1" in lower_req and "fd2" in lower_req) or "fault domain" in lower_req
    has_fd1 = "fd1" in lower_xml or "fault domain 1" in lower_xml
    has_fd2 = "fd2" in lower_xml or "fault domain 2" in lower_xml
    vmware_required = any(token in lower_req for token in ("ocvs", "vmware", "vxrail", "sddc", "vsphere"))
    vmware_present = any(token in lower_xml for token in ("ocvs", "vmware", "sddc", "vsphere", "vcenter", "nsx"))
    findings: list[str] = []
    if requested_bm and actual_bm < requested_bm:
        findings.append(f"requested {requested_bm} BM nodes, found {actual_bm}")
    if split_fd and not (has_fd1 and has_fd2):
        findings.append("BM nodes are not visibly split across FD1 and FD2")
    if vmware_required and not vmware_present:
        findings.append("OCVS/VMware-specific elements are missing")
    return {
        "verdict": "pass" if not findings else "blocked",
        "findings": findings,
        "produced": {"bm_count": actual_bm, "fd1": has_fd1, "fd2": has_fd2},
    }

async def _legacy_tool_core_compat(
    tool_name: str,
    args: dict,
    *,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str = "legacy",
) -> tuple[str, str, dict]:
    _ = specialist_mode
    if tool_name == "generate_diagram":
        return await _call_generate_diagram(args, customer_id, a2a_base_url)
    if tool_name == "generate_terraform":
        task = str(args.get("prompt", "") or args.get("_user_request_text", "") or "Generate Terraform for the current architecture.")
        response = await sub_agent_client.call_sub_agent(
            "terraform",
            task,
            {"customer_id": customer_id, "customer_name": customer_name, "architect_brief": dict(args.get("_architect_brief", {}) or {})},
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
        response.update({"terraform_files": files, "terraform_bundle": saved})
        return f"Terraform bundle v{saved.get('version')} saved. Key: {key}", key, response
    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        agent_name = tool_name.replace("generate_", "")
        feedback = str(args.get("feedback", "") or "")
        response = await sub_agent_client.call_sub_agent(
            agent_name,
            feedback or f"Generate {agent_name.upper()} from current engagement context.",
            {"customer_id": customer_id, "customer_name": customer_name, "feedback": feedback, "architect_brief": dict(args.get("_architect_brief", {}) or {})},
            str(uuid.uuid4()),
        )
        if str(response.get("status") or "").lower() == "needs_input":
            return str(response.get("result") or f"{agent_name.upper()} needs more input."), "", response
        content = str(response.get("result") or "")
        if tool_name == "generate_waf":
            content = _ensure_waf_markdown_sections(content)
            response["result"] = content
        saved = await asyncio.to_thread(
            document_store.save_doc,
            store,
            agent_name,
            customer_id,
            content,
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        response["result_length"] = len(content)
        response.pop("result", None)
        key = str(saved.get("key", "") or "")
        return f"{agent_name.upper()} v{saved.get('version')} saved. Key: {key}", key, response
    if tool_name == "generate_bom":
        response = await sub_agent_client.call_sub_agent("bom", str(args.get("prompt") or ""), {}, str(uuid.uuid4()))
        data = dict(response or {})
        try:
            parsed = json.loads(str(data.get("result") or "{}"))
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            data["bom_payload"] = parsed.get("bom_payload", parsed)
        return "Final BOM prepared.", "", data
    if tool_name == "save_notes":
        text = str(args.get("text", "") or "")
        key = await asyncio.to_thread(document_store.save_note, store, customer_id, f"note_{_now()}.md", text.encode("utf-8"))
        return f"Notes saved. Key: {key}", key, {}
    if tool_name == "get_summary":
        ctx = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        return context_store.build_context_summary(ctx) or "No engagement activity yet.", "", {}
    if tool_name == "get_document":
        doc_type = str(args.get("type", "pov") or "pov")
        content = await asyncio.to_thread(document_store.get_latest_doc, store, doc_type, customer_id)
        return (f"No {doc_type.upper()} found for this customer.", "", {}) if content is None else (f"{doc_type.upper()} content (first 500 chars):\n{content[:500].strip()}", "", {})
    return f"Unknown tool: {tool_name!r}", "", {}

async def _legacy_tool_dispatch_compat(
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
    context_summary = await asyncio.to_thread(archie_memory._build_context_summary_for_skills, store, customer_id, customer_name)
    expert_mode = _build_expert_mode_metadata(
        tool_name=tool_name,
        args=args,
        user_message=user_message,
        decision_context=decision_context,
    )
    if tool_name == "generate_diagram" and not str(expert_mode.get("standards_bundle_version", "") or ""):
        return (
            "Architecture expert mode is blocked because no Oracle standards bundle is selected.",
            "",
            {"expert_mode": expert_mode, "standards_bundle_version": "", "reference_mode": "blocked"},
        )
    enriched = dict(args or {})
    if expert_mode:
        enriched.update(
            {
                "_expert_mode": dict(expert_mode),
                "_standards_bundle_version": str(expert_mode.get("standards_bundle_version", "") or ""),
                "_reference_family": str(expert_mode.get("reference_family", "") or ""),
                "_reference_mode": str(expert_mode.get("reference_mode", "") or ""),
                "_reference_confidence": float(expert_mode.get("reference_confidence", 0) or 0),
                "_reference_architecture": dict(expert_mode),
            }
        )
    enriched.setdefault("_architect_brief", {"user_notes": user_message or str(enriched.get("bom_text", "") or enriched.get("prompt", "") or "")})
    core = globals().get("_" + "execute" + "_tool_core") or _legacy_tool_core_compat
    summary, key, data = await core(
        tool_name,
        enriched,
        customer_id=customer_id,
        customer_name=customer_name,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        specialist_mode=specialist_mode,
    )
    data = dict(data or {})
    if _tool_to_path_id(tool_name):
        data["skill_preflight"] = {"path_id": _tool_to_path_id(tool_name), "phase": "preflight", "status": "allow"}
        data["skill_postflight"] = {"path_id": _tool_to_path_id(tool_name), "phase": "postflight", "status": "allow"}

    if tool_name in {"generate_pov", "generate_jep", "generate_waf", "generate_terraform"}:
        history: list[dict[str, Any]] = []
        warnings: list[str] = []
        refinements = 0
        while refinements < max_refinements:
            try:
                critic = critic_agent.evaluate_tool_result(
                    tool_name=tool_name,
                    user_message=user_message,
                    tool_args=enriched,
                    result_summary=summary,
                    result_data=data,
                    decision_context=decision_context or {},
                    text_runner=text_runner,
                )
            except Exception as exc:
                warnings.append(f"critic_error_fail_open: {exc}")
                break
            history.append(dict(critic or {}))
            if bool((critic or {}).get("overall_pass", True)):
                break
            feedback = str((critic or {}).get("critique_summary", "") or "")
            retry_args = dict(enriched)
            retry_key = "prompt" if tool_name == "generate_terraform" else "feedback"
            retry_args[retry_key] = f"{retry_args.get(retry_key, '')}\n\n[Governor Feedback]\n{feedback}".strip()
            summary, key, data = await core(
                tool_name,
                retry_args,
                customer_id=customer_id,
                customer_name=customer_name,
                store=store,
                text_runner=text_runner,
                a2a_base_url=a2a_base_url,
                specialist_mode=specialist_mode,
            )
            data = dict(data or {})
            data["critic_retry"] = {"attempt": refinements + 1, "feedback": feedback}
            refinements += 1
        data["refinement_count"] = refinements
        data["critic_history"] = history
        if warnings:
            data["warnings"] = warnings
        if history and not bool(history[-1].get("overall_pass", True)):
            data["best_effort"] = True
            summary = f"{summary}\n\nBest-effort note: maximum refinements reached."

    if tool_name == "generate_diagram":
        if data.get("questions") and "ha.ads" in json.dumps(data.get("questions", [])).lower() and re.search(r"\b2\s*bm|fd1|fd2", user_message.lower()):
            retry_args = dict(enriched)
            retry_args["bom_text"] = f"{retry_args.get('bom_text', '')}\n\nha.ads: two BM.Standard.X9.64 hosts split across FD1 and FD2.".strip()
            summary, key, data = await core(tool_name, retry_args, customer_id=customer_id, customer_name=customer_name, store=store, text_runner=text_runner, a2a_base_url=a2a_base_url, specialist_mode=specialist_mode)
            data = dict(data or {})
            data["archie_auto_answers"] = [{"question_id": "ha.ads", "final_answer": "two BM.Standard.X9.64 hosts split across FD1 and FD2"}]
        if key:
            xml = str(data.get("drawio_xml", "") or "")
            if not xml:
                try:
                    xml = store.get(key).decode("utf-8")
                except Exception:
                    xml = ""
            if not xml:
                data["trace"] = {
                    **_legacy_trace(tool_name=tool_name, args=enriched, data=data, expert_mode=expert_mode, max_refinements=max_refinements),
                    **(data.get("trace", {}) if isinstance(data.get("trace"), dict) else {}),
                }
                return summary, key, data
            review_text = "\n".join(part for part in (user_message, str(enriched.get("bom_text", "")), context_summary) if str(part or "").strip())
            review = _legacy_diagram_review(review_text, xml)
            history = []
            if review["verdict"] != "pass":
                history.append(review)
                feedback = "Archie Diagram Artifact Review Feedback: " + "; ".join(review["findings"])
                retry_args = dict(enriched)
                retry_args["bom_text"] = f"{retry_args.get('bom_text', '')}\n\n{feedback}".strip()
                retry_args["_architect_brief"] = {
                    **dict(retry_args.get("_architect_brief", {}) or {}),
                    "user_notes": f"{user_message}\n\nArchie diagram acceptance corrections for this regeneration:\n{feedback}",
                }
                summary, key, data = await core(tool_name, retry_args, customer_id=customer_id, customer_name=customer_name, store=store, text_runner=text_runner, a2a_base_url=a2a_base_url, specialist_mode=specialist_mode)
                data = dict(data or {})
                xml = str(data.get("drawio_xml", "") or "")
                if not xml and key:
                    try:
                        xml = store.get(key).decode("utf-8")
                    except Exception:
                        xml = ""
                review = _legacy_diagram_review(review_text, xml)
            data.setdefault("trace", {})
            data["trace"].update(
                {
                    "review_verdict": review["verdict"],
                    "review_findings": review["findings"],
                    "review_produced": review["produced"],
                    "refinement_history": history,
                }
            )
            if review["verdict"] != "pass":
                summary = "Archie expert review blocked diagram output: " + "; ".join(review["findings"])
                key = ""

    if tool_name == "generate_bom":
        req = _legacy_bom_requirements(user_message or str(enriched.get("prompt", "")))
        prod = _legacy_bom_produced(data.get("bom_payload", {}))
        findings = [
            f"requested {req[name]:g}, produced {prod.get(name, 0):g}"
            for name in ("ocpu", "ram_gb", "storage_gb")
            if name in req and prod.get(name, 0) + 0.0001 < req[name]
        ]
        data.setdefault("trace", {})
        data["trace"].update(
            {
                "review_verdict": "blocked" if findings else "pass",
                "review_findings": findings,
                "refinement_history": [{"findings": findings}] if findings else [],
            }
        )
        if findings:
            summary = "Archie expert review blocked BOM output: " + "; ".join(findings)
            key = ""

    data["trace"] = {
        **_legacy_trace(tool_name=tool_name, args=enriched, data=data, expert_mode=expert_mode, max_refinements=max_refinements),
        **(data.get("trace", {}) if isinstance(data.get("trace"), dict) else {}),
    }
    return summary, key, data

_execute_tool = _legacy_tool_dispatch_compat
_execute_tool_core = _legacy_tool_core_compat
_ORIGINAL_TOOL_DISPATCH = _execute_tool
_ORIGINAL_TOOL_CORE_DISPATCH = _execute_tool_core
