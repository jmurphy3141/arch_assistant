"""Native OCI tool-calling loop for Archie, selected only by agent_mode=native."""

from __future__ import annotations

import inspect
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from agent import (
    archie_memory,
    archie_memory_retrieval,
    context_store,
    document_store,
    reference_tools,
)
from agent.archie_wiring import (
    NATIVE_SYSTEM_IDENTITY,
    build_forge,
    get_registered_memory,
    get_registered_tool_specs,
)
from agent.llm_inference_client import run_inference_with_tools
from agent.persistence_objectstore import ObjectStoreBase
from agent.runtime_config import resolve_agent_llm_config
from skillforge.protocols import ToolSchema
from skillforge.types import ToolResult


SYSTEM_IDENTITY = NATIVE_SYSTEM_IDENTITY

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
logger = logging.getLogger(__name__)


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
    registered_specs.extend(reference_tools.get_reference_tool_specs())
    specs = {spec.name: spec for spec in registered_specs}
    memory = get_registered_memory(forge)
    schemas = [
        ToolSchema(name=spec.name, description=spec.description, args=spec.args)
        for spec in specs.values()
    ]
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
        spec = specs.get(tool_name)
        result_artifact_key = ""
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
            if tool_name == "generate_bom" and result.status == "ok":
                result = await _persist_native_bom_artifact(
                    result, customer_id=customer_id, store=store
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
            result_artifact_key = _result_artifact_key(result)
            if result.status == "ok" and result_artifact_key:
                artifact_type = _artifact_type_for_tool(tool_name)
                if artifact_type:
                    context_store.register_artifact(
                        context,
                        artifact_type,
                        key=result_artifact_key,
                        summary=result.summary,
                        download=str((result.data or {}).get("download_url") or ""),
                    )
                archie_memory.refresh_engagement_digest(context)
                context_store.write_context(store, customer_id, context)

        call = {
            "tool": tool_name,
            "args": dict(args),
            "result_summary": result.summary,
            "result_status": result.status,
            "result_data": dict(result.data or {}),
            "artifact_key": result_artifact_key if result.status == "ok" else "",
        }
        tool_calls.append(call)
        if result.status == "ok" and result_artifact_key:
            artifacts[tool_name] = result_artifact_key
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
        "artifact_key": _result_artifact_key(result) if result.status == "ok" else "",
        "data": result.data,
        "clarification": result.clarification,
    }
    if result.status != "ok":
        return (
            "[TOOL RESULT - NO ARTIFACT PRODUCED]\n"
            f"Status: {result.status}. Required clarification: "
            f"{result.clarification or result.summary}\n"
            + json.dumps(payload, ensure_ascii=False, default=str)
        )
    return "[TOOL RESULT]\n" + json.dumps(payload, ensure_ascii=False, default=str)


def _artifact_type_for_tool(tool_name: str) -> str:
    if not tool_name.startswith("generate_"):
        return ""
    return {
        "generate_poc_plan": "poc_plan",
        "generate_technical_proposal": "technical_proposal",
        "generate_terraform": "terraform",
    }.get(tool_name, tool_name.removeprefix("generate_"))


def _result_artifact_key(result: ToolResult) -> str:
    if result.artifact_key:
        return str(result.artifact_key)
    data = result.data if isinstance(result.data, dict) else {}
    for field in (
        "xlsx_artifact_key",
        "drawio_key",
        "docx_key",
        "object_key",
        "artifact_key",
    ):
        if data.get(field):
            return str(data[field])
    return ""


async def _persist_native_bom_artifact(
    result: ToolResult, *, customer_id: str, store: ObjectStoreBase
) -> ToolResult:
    """Run the existing BOM export before native indexing; the HTTP route then no-ops."""
    if _result_artifact_key(result):
        return result
    data = dict(result.data or {})
    payload = data.get("bom_payload")
    if not isinstance(payload, dict) or not payload.get("line_items"):
        return result

    from agent.bom_service import get_shared_bom_service
    from server.services.bom_artifacts import persist_bom_xlsx_downloads

    wrapper = {
        "tool_calls": [
            {
                "tool": "generate_bom",
                "result_status": "ok",
                "result_data": data,
            }
        ]
    }
    await persist_bom_xlsx_downloads(
        customer_id,
        store,
        wrapper,
        bom_service_factory=get_shared_bom_service,
        logger=logger,
    )
    persisted = wrapper["tool_calls"][0]["result_data"]
    return ToolResult(
        summary=result.summary,
        status=result.status,
        data=persisted,
        artifact_key=str(persisted.get("xlsx_artifact_key") or ""),
        clarification=result.clarification,
    )


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
