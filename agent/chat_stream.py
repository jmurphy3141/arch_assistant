"""
agent/chat_stream.py
--------------------
Streaming assembly helpers for Archie chat turns.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import AsyncGenerator

from agent.document_store import update_latest_assistant_turn
from server.services.bom_artifacts import attach_artifact_delivery_to_reply


logger = logging.getLogger(__name__)


async def _chat_event_dicts(
    *,
    customer_id: str,
    customer_name: str,
    message: str,
    store,
    text_runner,
    tool_runner=None,
    a2a_base_url: str = "",
    project_id: str | None = None,
    project_name: str | None = None,
) -> AsyncGenerator[dict, None]:
    import drawing_agent_server as server

    trace_id = server._current_trace_id()
    req = SimpleNamespace(
        customer_id=customer_id,
        customer_name=customer_name,
        message=message,
        project_id=project_id,
        project_name=project_name,
    )

    async def _run_orchestrator_with_stream_notifications(queue: asyncio.Queue[dict]):
        from agent.notifications import notification_sink

        loop = asyncio.get_running_loop()

        def _sink(event: str, customer_id: str, _detail: str = "") -> None:
            payload = server._tool_started_stream_event(
                event=event,
                customer_id=customer_id,
                trace_id=trace_id,
            )
            if payload is not None:
                loop.call_soon_threadsafe(queue.put_nowait, payload)

        def _thinking_sink(label: str, phase: str) -> None:
            if phase == "hat_activate":
                payload = {
                    "trace_id": trace_id,
                    "customer_id": customer_id,
                    "event_type": "hat_activate",
                    "hat": label,
                    "display_name": label,
                }
            else:
                payload = {
                    "trace_id": trace_id,
                    "customer_id": customer_id,
                    "event_type": "thinking",
                    "label": label,
                    "reasoning_type": phase,
                }
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        with notification_sink(_sink):
            result = await server._run_orchestrator_turn(
                req=req,
                store=store,
                text_runner=text_runner,
                tool_runner=tool_runner,
                orch_cfg=server._cfg.get("orchestrator", {}),
                reasoning_sink=_thinking_sink,
            )
        result = await server._persist_bom_xlsx_downloads(customer_id, store, result)
        project_membership = server._persist_chat_project_membership(store, req)
        return result, project_membership

    async def _drain_status_queue(queue: asyncio.Queue[dict], task: asyncio.Task):
        while not task.done():
            try:
                yield await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
        while not queue.empty():
            yield queue.get_nowait()

    yield {
        "trace_id": trace_id,
        "customer_id": customer_id,
        "event_type": "status",
        "status": "started",
    }
    try:
        status_queue: asyncio.Queue[dict] = asyncio.Queue()
        task = asyncio.create_task(_run_orchestrator_with_stream_notifications(status_queue))
        async for payload in _drain_status_queue(status_queue, task):
            yield payload
        result, project_membership = await task
        artifact_manifest = server._build_artifact_manifest(customer_id, result)
        result = attach_artifact_delivery_to_reply(result, artifact_manifest)
        await asyncio.to_thread(
            update_latest_assistant_turn,
            store,
            customer_id,
            {
                "content": result.get("reply", ""),
                "tool_calls": result.get("tool_calls", []),
                "artifacts": result.get("artifacts", {}),
                "artifact_manifest": artifact_manifest,
            },
        )
        for event in result.get("events", []):
            event_type = str(event.get("type") or "")
            event_data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
            if event_type == "hat_activate":
                hat = str(event_data.get("hat", "") or "")
                display = str(event_data.get("display_name", hat) or hat)
                yield {
                    "trace_id": trace_id,
                    "customer_id": customer_id,
                    "event_type": "hat_activate",
                    "hat": hat,
                    "display_name": display,
                }
            elif event_type == "hat_drop":
                hat = str(event_data.get("hat", "") or "")
                yield {
                    "trace_id": trace_id,
                    "customer_id": customer_id,
                    "event_type": "hat_drop",
                    "hat": hat,
                }
            elif event_type in (
                "step3_planning",
                "expert_pre_action",
                "expert_post_review",
                "hat_auto_activated",
                "pre_action_light",
            ):
                # Surface reasoning steps as a "thinking" event for UI visibility.
                label_map = {
                    "step3_planning": "Planning approach...",
                    "expert_pre_action": "Expert pre-action analysis...",
                    "expert_post_review": "Expert review...",
                    "hat_auto_activated": (
                        f"Activating {event_data.get('hat', 'expert')} lens..."
                    ),
                    "pre_action_light": (
                        f"Pre-action check for {event_data.get('tool', 'tool')}..."
                    ),
                }
                yield {
                    "trace_id": trace_id,
                    "customer_id": customer_id,
                    "event_type": "thinking",
                    "label": label_map.get(event_type, "Thinking..."),
                    "reasoning_type": event_type,
                }
        for tool_call in result.get("tool_calls", []):
            if (
                tool_call.get("tool") == "generate_terraform"
                and isinstance(tool_call.get("result_data"), dict)
            ):
                for stage in tool_call.get("result_data", {}).get("stages", []):
                    yield {
                        "trace_id": trace_id,
                        "customer_id": customer_id,
                        "event_type": "terraform_stage",
                        "stage": stage,
                    }
            yield {
                "trace_id": trace_id,
                "customer_id": customer_id,
                "event_type": "tool",
                "tool_call": tool_call,
            }
        for chunk in server._chunk_reply_text(result.get("reply", "")):
            yield {
                "trace_id": trace_id,
                "customer_id": customer_id,
                "event_type": "token",
                "delta": chunk,
            }
        yield {
            "trace_id": trace_id,
            "customer_id": customer_id,
            "project_id": project_membership["project_id"],
            "project_name": project_membership["project_name"],
            "engagement_id": customer_id,
            "event_type": "completion",
            "reply": result.get("reply", ""),
            "tool_calls": result.get("tool_calls", []),
            "artifacts": result.get("artifacts", {}),
            "artifact_manifest": artifact_manifest,
            "history_length": result.get("history_length", 0),
        }
    except Exception as exc:
        logger.error(
            "/api/chat/stream error customer=%s trace_id=%s: %s",
            customer_id,
            trace_id,
            exc,
        )
        yield {
            "trace_id": trace_id,
            "customer_id": customer_id,
            "event_type": "error",
            "error": str(exc),
        }


async def stream_chat_turn(
    *,
    customer_id: str,
    customer_name: str,
    message: str,
    store,
    text_runner,
    tool_runner=None,
    a2a_base_url: str = "",
    project_id: str | None = None,
    project_name: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields NDJSON-encoded chat event strings.

    Yields one JSON line per event.
    """
    async for event in _chat_event_dicts(
        customer_id=customer_id,
        customer_name=customer_name,
        message=message,
        store=store,
        text_runner=text_runner,
        tool_runner=tool_runner,
        a2a_base_url=a2a_base_url,
        project_id=project_id,
        project_name=project_name,
    ):
        yield json.dumps(event) + "\n"


async def stream_chat_turn_sse(
    *,
    customer_id: str,
    customer_name: str,
    message: str,
    store,
    text_runner,
    tool_runner=None,
    a2a_base_url: str = "",
    project_id: str | None = None,
    project_name: str | None = None,
) -> AsyncGenerator[str, None]:
    async for event in _chat_event_dicts(
        customer_id=customer_id,
        customer_name=customer_name,
        message=message,
        store=store,
        text_runner=text_runner,
        tool_runner=tool_runner,
        a2a_base_url=a2a_base_url,
        project_id=project_id,
        project_name=project_name,
    ):
        if str(event.get("event_type") or "") in {"hat_activate", "hat_drop"}:
            continue
        event_name = {
            "terraform_stage": "terraform_stage",
            "completion": "completion",
            "token": "token",
            "tool": "tool",
            "error": "error",
        }.get(str(event.get("event_type") or ""), "status")
        yield f"event: {event_name}\ndata: {json.dumps(event)}\n\n"
