"""Native OCI tool-calling loop for Archie, selected only by agent_mode=native."""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from agent import archie_memory, archie_memory_retrieval, context_store, document_store, hat_engine
from agent.archie_wiring import (
    build_forge,
    get_registered_memory,
    get_registered_tool_specs,
)
from agent.llm_inference_client import run_inference_with_tools
from agent.persistence_objectstore import ObjectStoreBase
from agent.runtime_config import resolve_agent_llm_config
from skillforge.protocols import ToolSchema
from skillforge.types import ToolResult


SYSTEM_IDENTITY = (
    "You are Archie, a manager of expert OCI sub-agents and a sharp "
    "solutions-architect colleague. Converse and advise freely. When the user wants "
    "a deliverable, call the sub-agent; when they ask whether one exists or what it "
    "says, fetch and read it; otherwise just talk. Never fabricate a deliverable or a "
    "stored fact — call the tool, or say you don't have it."
)

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


async def run_turn(
    *,
    customer_id: str,
    customer_name: str,
    user_message: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    tool_runner: Callable | None = None,
    a2a_base_url: str = "http://localhost:8080",
    max_tool_iterations: int = 5,
) -> dict:
    context = await _maybe_await(
        context_store.read_context(store, customer_id, customer_name)
    )
    settings = archie_memory_retrieval.load_memory_settings()
    if archie_memory.record_native_turn_memory(context, user_message):
        context_store.write_context(store, customer_id, context)
    session_summary = archie_memory.compact_native_session(
        store=store,
        engagement_id=customer_id,
        session_id=customer_id,
        context=context,
        threshold=settings["session_summary_threshold"],
        retain_turns=settings["working_set_turns"],
        summary_char_budget=max(500, settings["working_set_char_budget"] // 4),
    )
    history = document_store.load_conversation_history(
        store, customer_id, settings["working_set_turns"]
    )
    forge = build_forge(
        store=store,
        customer_id=customer_id,
        customer_name=customer_name,
        text_runner=text_runner,
        tool_runner=tool_runner,
        a2a_base_url=a2a_base_url,
    )
    registered_specs = list(get_registered_tool_specs(forge))
    registered_specs.extend(
        archie_memory_retrieval.get_memory_tool_specs(
            store=store,
            engagement_id=customer_id,
            customer_name=customer_name,
        )
    )
    specs = {spec.name: spec for spec in registered_specs}
    memory = get_registered_memory(forge)
    schemas = [
        ToolSchema(name=spec.name, description=spec.description, args=spec.args)
        for spec in specs.values()
    ]
    hat_schemas = hat_engine.get_native_hat_tool_schemas()
    schemas.extend(hat_schemas)
    hat_names = {schema.name for schema in hat_schemas}

    working_set = archie_memory_retrieval.assemble_working_set(
        context=context,
        session_summary=session_summary,
        history=history,
        working_set_turns=settings["working_set_turns"],
        char_budget=settings["working_set_char_budget"],
    )
    prompt = _assemble_prompt(working_set, user_message)
    tool_calls: list[dict[str, Any]] = []
    artifacts: dict[str, str] = {}
    reply = ""

    for iteration in range(max_tool_iterations + 1):
        response = await _infer(prompt, schemas, tool_runner)
        if isinstance(response, str):
            reply = response
            break
        if iteration >= max_tool_iterations:
            reply = "I couldn't complete that request within the tool-call limit."
            break

        tool_name = str(response.get("tool") or "")
        args = response.get("args") or {}
        trace_id = str(uuid.uuid4())
        if tool_name in hat_names:
            result = await hat_engine.invoke_native_hat(tool_name)
        else:
            spec = specs.get(tool_name)
            if spec is None:
                result = ToolResult(
                    summary=f"Unknown tool: {tool_name}",
                    status="blocked",
                )
            else:
                tool_memory = (
                    memory.assemble(
                        session_id=customer_id,
                        context=context,
                        user_message=user_message,
                    )
                    if spec.memory_contract
                    else None
                )
                result = await spec.handler(
                    args,
                    memory=tool_memory,
                    context=context,
                    trace_id=trace_id,
                )
                if spec.safety_checker is not None and result.status == "ok":
                    passed, reason = spec.safety_checker(tool_name, result)
                    if not passed:
                        result = ToolResult(
                            summary=f"Safety check blocked: {reason}",
                            status="blocked",
                            data=result.data,
                        )
                if spec.memory_contract and result.status == "ok":
                    context = memory.update(
                        session_id=customer_id,
                        tool_name=tool_name,
                        result=result,
                        context=context,
                    )
                if result.status == "ok" and result.artifact_key:
                    archie_memory.refresh_engagement_digest(context)
                    context_store.write_context(store, customer_id, context)

        call = {
            "tool": tool_name,
            "args": dict(args),
            "result_summary": result.summary,
            "result_status": result.status,
            "result_data": dict(result.data or {}),
            "artifact_key": result.artifact_key or "",
        }
        tool_calls.append(call)
        if result.artifact_key:
            artifacts[tool_name] = result.artifact_key
        prompt += "\n\n" + _tool_result_message(tool_name, result)

    turns = [
        {
            "role": "user",
            "content": user_message,
            "timestamp": _now(),
            "customer_name": customer_name,
        },
        {
            "role": "assistant",
            "content": reply,
            "timestamp": _now(),
            **({"tool_calls": tool_calls} if tool_calls else {}),
            **({"artifacts": artifacts} if artifacts else {}),
        },
    ]
    document_store.save_conversation_turns(store, customer_id, turns)
    archie_memory.compact_native_session(
        store=store,
        engagement_id=customer_id,
        session_id=customer_id,
        context=context,
        threshold=settings["session_summary_threshold"],
        retain_turns=settings["working_set_turns"],
        summary_char_budget=max(500, settings["working_set_char_budget"] // 4),
    )
    return {
        "reply": reply,
        "tool_calls": tool_calls,
        "artifacts": artifacts,
        "history_length": len(history) + len(turns),
        "events": [],
    }


async def _infer(
    prompt: str,
    schemas: list[ToolSchema],
    tool_runner: Callable | None,
) -> dict | str:
    if tool_runner is not None:
        result = tool_runner(prompt, SYSTEM_IDENTITY, schemas, "orchestrator")
        return await _maybe_await(result)

    with open(_CONFIG_PATH, encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    llm_config = resolve_agent_llm_config(config, "orchestrator")
    return await _maybe_await(
        run_inference_with_tools(
            prompt=prompt,
            system_message=SYSTEM_IDENTITY,
            tools=[schema.to_api_dict() for schema in schemas],
            tool_choice="auto",
            model_id=str(llm_config.get("model_id") or ""),
            endpoint=str(llm_config.get("service_endpoint") or ""),
            compartment_id=str(config.get("compartment_id") or ""),
            max_tokens=int(llm_config.get("max_tokens", 4000)),
            temperature=float(llm_config.get("temperature", 0.0)),
            top_p=float(llm_config.get("top_p", 0.9)),
        )
    )


def _assemble_prompt(working_set: str, user_message: str) -> str:
    return "\n\n".join(
        (
            "[BOUNDED WORKING SET]\n" + working_set,
            "[USER MESSAGE]\n" + user_message,
        )
    )


def _tool_result_message(tool_name: str, result: ToolResult) -> str:
    payload = {
        "tool": tool_name,
        "status": result.status,
        "summary": result.summary,
        "artifact_key": result.artifact_key,
        "data": result.data,
        "clarification": result.clarification,
    }
    return "[TOOL RESULT]\n" + json.dumps(payload, ensure_ascii=False, default=str)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
