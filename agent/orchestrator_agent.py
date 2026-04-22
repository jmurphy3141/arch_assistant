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
from typing import Callable

from agent.persistence_objectstore import ObjectStoreBase
import agent.document_store as document_store
import agent.context_store as context_store
from agent import critic_agent
from agent.orchestrator_skill_engine import (
    OrchestratorSkillDecision,
    OrchestratorSkillEngine,
)
from agent.skill_loader import select_skills_for_call

logger = logging.getLogger(__name__)
MAX_CRITIC_RETRIES = 1

# ── System message ─────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM_MSG = """\
You are an Oracle OCI Solutions Architect Assistant helping an Oracle SA run a
customer engagement end-to-end.  You have access to tools that call specialist
agents.  Use them to fulfil the SA's requests.

When you need to take an action, output ONLY the following JSON on a single
line — no other text on that line, no markdown fences:
{"tool": "<name>", "args": {<key>: <value>}}

AVAILABLE TOOLS
  save_notes       {"text": "<notes text>"}
      Save SA-pasted meeting notes for this customer.

  get_summary      {}
      Return the current engagement state (what has been generated so far).

  generate_pov     {"feedback": "<optional correction text>"}
      Draft or update the Point of View document.  Call get_summary first
      to confirm notes exist.

  generate_diagram {"bom_text": "<optional inline BOM description>"}
      Generate an OCI architecture diagram.  If no BOM has been uploaded,
      ask the SA to upload or paste one before calling this tool.

  generate_waf     {}
      Run an OCI Well-Architected Framework review against the latest diagram.

  generate_jep     {"feedback": "<optional correction text>"}
      Draft or update the Joint Execution Plan.

  generate_terraform {"prompt": "<optional constraints or module goals>"}
      Generate Terraform via the specialist chain. If blocked, return
      clarification questions for the SA.

  get_document     {"type": "pov" | "jep" | "waf"}
      Retrieve the latest generated document content.

RULES
- When the SA pastes or describes notes, always call save_notes first.
- Before generating a POV or JEP, call get_summary to confirm notes exist.
- Before generating a diagram without an uploaded BOM, ask the SA to provide one.
- Never fabricate document content — always call the appropriate tool.
- Run path-specific expert skill validation before and after every path tool call.
- Enforce block outcomes from the skill layer with pushback and retry guidance.
- Respond in Markdown when not calling a tool.  Be concise.
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

    prompt = _build_prompt(history, summary, user_message)
    reply = ""
    forced_reply = ""

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

    new_turns.append({"role": "assistant", "content": reply, "timestamp": _now()})
    document_store.save_conversation_turns(store, customer_id, new_turns)

    return {
        "reply": reply,
        "tool_calls": tool_calls,
        "artifacts": artifacts,
        "history_length": len(history) + len(new_turns),
    }


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
) -> tuple[str, str, dict]:
    """
    Execute a tool call and return (result_summary, artifact_key).
    artifact_key is "" when no persistent artifact was produced.
    """
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
    result_data["skill_injected"] = bool(enriched_args.get("_skill_injected"))
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

        result = await asyncio.to_thread(
            waf_agent.generate_waf,
            customer_id,
            customer_name,
            store,
            text_runner,
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
        return f"JEP v{result.get('version')} saved. Key: {key}", key, {}

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

    block_parts: list[str] = []
    model_profile = ""
    for spec in selected:
        block_parts.append(
            "\n[Injected Skill Guidance]\n"
            f"Skill: {spec.name}\n"
            f"{spec.body}\n"
            "[End Skill Guidance]\n"
        )
        if not model_profile:
            model_profile = str(spec.metadata.get("model_profile", "")).strip()
    skill_block = "\n".join(block_parts)

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
) -> tuple[str, str, dict]:
    if tool_name not in {"generate_pov", "generate_jep", "generate_waf", "generate_terraform"}:
        return result_summary, artifact_key, result_data

    current_summary = result_summary
    current_key = artifact_key
    current_data = dict(result_data or {})
    for attempt in range(MAX_CRITIC_RETRIES):
        critic = await asyncio.to_thread(
            critic_agent.evaluate_tool_result,
            tool_name=tool_name,
            user_message=user_message,
            tool_args=args,
            result_summary=current_summary,
            result_data=current_data,
            text_runner=text_runner,
        )
        current_data["critic"] = critic
        if critic.get("overall_pass", True):
            break

        feedback = (critic.get("feedback") or "").strip()
        if not feedback:
            break
        retry_args = dict(args)
        if tool_name == "generate_terraform":
            retry_args["prompt"] = (
                f"{retry_args.get('prompt', '')}\n\n[Critic Feedback]\n{feedback}\n"
            ).strip()
        else:
            retry_args["feedback"] = (
                f"{retry_args.get('feedback', '')}\n\n[Critic Feedback]\n{feedback}\n"
            ).strip()
        retry_summary, retry_key, retry_data = await _execute_tool_core(
            tool_name,
            retry_args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
        )
        retry_data = dict(retry_data or {})
        retry_data["critic_retry"] = {
            "attempt": attempt + 1,
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
    return current_summary, current_key, current_data


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
