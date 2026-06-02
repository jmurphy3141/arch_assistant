from __future__ import annotations

import asyncio
import functools
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import anyio
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from agent.chat_stream import stream_chat_turn, stream_chat_turn_sse
from agent.document_store import (
    clear_conversation_history,
    clear_conversation_summary,
    clear_notes_manifest,
    list_conversation_summaries,
    list_notes,
    list_project_summaries,
    load_conversation_history,
    normalize_project_id,
    save_conversation_turns,
    save_project_engagement,
)
from agent.context_store import read_context, reset_context
from agent.persistence_objectstore import InMemoryObjectStore
from server.models import OrchestratorChatRequest


def create_chat_router(deps) -> APIRouter:
    router = APIRouter()

    def _persist_chat_project_membership(store, req: OrchestratorChatRequest) -> dict:
        project_name = (req.project_name or "").strip() or (req.customer_name or "").strip() or req.customer_id
        project_id = (req.project_id or "").strip() or normalize_project_id(project_name, req.customer_id)
        return save_project_engagement(
            store,
            customer_id=req.customer_id,
            customer_name=req.customer_name,
            project_id=project_id,
            project_name=project_name,
        )

    @router.post("/api/chat")
    async def api_chat(req: OrchestratorChatRequest):
        store = deps.require_object_store()
        text_runner = deps.make_orchestrator_text_runner()
        tool_runner = None if isinstance(store, InMemoryObjectStore) else deps.make_orchestrator_tool_runner()
        orch_cfg = deps.config().get("orchestrator", {})

        try:
            result = await deps.run_orchestrator_turn()(
                req=req,
                store=store,
                text_runner=text_runner,
                tool_runner=tool_runner,
                orch_cfg=orch_cfg,
            )
            result = await deps.persist_bom_xlsx_downloads(req.customer_id, store, result)
            artifact_manifest = deps.build_artifact_manifest(req.customer_id, result)
            project_membership = _persist_chat_project_membership(store, req)
            return {
                "status": "ok",
                "trace_id": deps.current_trace_id(),
                "project_id": project_membership["project_id"],
                "project_name": project_membership["project_name"],
                "engagement_id": req.customer_id,
                "reply": result["reply"],
                "tool_calls": result["tool_calls"],
                "artifacts": result["artifacts"],
                "artifact_manifest": artifact_manifest,
                "history_length": result["history_length"],
            }
        except Exception as exc:
            deps.logger.error(
                "/api/chat error customer=%s trace_id=%s: %s",
                req.customer_id,
                deps.current_trace_id(),
                exc,
            )
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/chat/background", status_code=202)
    async def api_chat_background(req: OrchestratorChatRequest):
        job_id = deps.new_job()
        store = deps.require_object_store()
        text_runner = deps.make_orchestrator_text_runner()
        tool_runner = None if isinstance(store, InMemoryObjectStore) else deps.make_orchestrator_tool_runner()
        a2a_base_url = os.environ.get("A2A_BASE_URL", "http://localhost:8080")

        from agent import archie_session

        history = await anyio.to_thread.run_sync(
            functools.partial(load_conversation_history, store, req.customer_id)
        )
        context = await anyio.to_thread.run_sync(
            functools.partial(read_context, store, req.customer_id, req.customer_name)
        )
        forge = archie_session._get_forge(
            customer_id=req.customer_id,
            customer_name=req.customer_name,
            store=store,
            text_runner=text_runner,
            tool_runner=tool_runner,
            a2a_base_url=a2a_base_url,
        )
        session = SimpleNamespace(forge=forge, history=history, context=context)
        new_turns = [
            {
                "role": "user",
                "content": req.message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "customer_name": req.customer_name,
            }
        ]

        def _turn_result_to_dict(result) -> dict:
            tool_calls = []
            forge_tool_calls = result.tool_calls if isinstance(result.tool_calls, list) else []
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
            forge_events = result.events if isinstance(result.events, list) else []
            return {
                "reply": result.reply,
                "tool_calls": tool_calls,
                "artifacts": dict(result.artifacts or {}),
                "history_length": len(session.history) + 2,
                "events": [
                    {"type": event.type, "message": event.message, "data": dict(event.data or {})}
                    for event in forge_events
                ],
            }

        async def on_complete(result) -> None:
            result_dict = _turn_result_to_dict(result)
            new_turns.append(
                {
                    "role": "assistant",
                    "content": result_dict["reply"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            await anyio.to_thread.run_sync(
                functools.partial(save_conversation_turns, store, req.customer_id, new_turns)
            )
            result_dict = await deps.persist_bom_xlsx_downloads(req.customer_id, store, result_dict)
            artifact_manifest = deps.build_artifact_manifest(req.customer_id, result_dict)
            project_membership = _persist_chat_project_membership(store, req)
            payload = {
                "status": "ok",
                "trace_id": deps.current_trace_id(),
                "project_id": project_membership["project_id"],
                "project_name": project_membership["project_name"],
                "engagement_id": req.customer_id,
                "reply": result_dict["reply"],
                "tool_calls": result_dict["tool_calls"],
                "artifacts": result_dict["artifacts"],
                "artifact_manifest": artifact_manifest,
                "history_length": result_dict["history_length"],
            }
            deps.complete_job(job_id, payload)
            try:
                from agent.notifications import notify

                notify("poc_step_complete", req.customer_id, detail=str(result_dict.get("reply", ""))[:200])
            except Exception:
                deps.logger.exception("Background chat notification failed job_id=%s", job_id)

        async def on_error(exc: Exception) -> None:
            deps.logger.error(
                "/api/chat/background error customer=%s job_id=%s trace_id=%s: %s",
                req.customer_id,
                job_id,
                deps.current_trace_id(),
                exc,
            )
            deps.fail_job(job_id, str(exc))

        asyncio.create_task(
            session.forge.run_turn_background(
                message=req.message,
                history=session.history,
                context=session.context,
                on_complete=on_complete,
                on_error=on_error,
                session_id=req.customer_id,
            )
        )
        return {"job_id": job_id, "status": "pending"}

    @router.post("/api/chat/stream")
    async def api_chat_stream(
        req: OrchestratorChatRequest,
        mode: str = Query(default="sse", pattern="^(sse|chunked)$"),
    ):
        store = deps.require_object_store()
        tool_runner = None if isinstance(store, InMemoryObjectStore) else deps.make_orchestrator_tool_runner()
        stream = stream_chat_turn if mode == "chunked" else stream_chat_turn_sse
        return StreamingResponse(
            stream(
                customer_id=req.customer_id,
                customer_name=req.customer_name,
                message=req.message,
                store=store,
                text_runner=deps.make_orchestrator_text_runner(),
                tool_runner=tool_runner,
                a2a_base_url=getattr(deps.app().state, "a2a_base_url", ""),
                project_id=req.project_id,
                project_name=req.project_name,
            ),
            media_type="application/x-ndjson" if mode == "chunked" else "text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/api/chat/{customer_id}/history")
    async def api_chat_history(customer_id: str, max_turns: int = Query(default=30, ge=1, le=200)):
        store = deps.require_object_store()
        history = await anyio.to_thread.run_sync(
            functools.partial(load_conversation_history, store, customer_id, max_turns)
        )
        return {
            "status": "ok",
            "trace_id": deps.current_trace_id(),
            "customer_id": customer_id,
            "history": history,
        }

    @router.get("/api/chat/history")
    async def api_chat_history_index(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default=""),
    ):
        store = deps.require_object_store()
        result = await anyio.to_thread.run_sync(
            functools.partial(list_conversation_summaries, store, page=page, page_size=page_size, search=search)
        )
        return {
            "status": "ok",
            "trace_id": deps.current_trace_id(),
            "items": result["items"],
            "pagination": result["pagination"],
        }

    @router.get("/api/chat/projects")
    async def api_chat_project_index(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
        search: str = Query(default=""),
    ):
        store = deps.require_object_store()
        result = await anyio.to_thread.run_sync(
            functools.partial(list_project_summaries, store, page=page, page_size=page_size, search=search)
        )
        return {
            "status": "ok",
            "trace_id": deps.current_trace_id(),
            "items": result["items"],
            "pagination": result["pagination"],
        }

    @router.delete("/api/chat/{customer_id}/history")
    async def api_clear_chat_history(customer_id: str):
        store = deps.require_object_store()
        await anyio.to_thread.run_sync(functools.partial(clear_conversation_history, store, customer_id))
        return {
            "status": "ok",
            "trace_id": deps.current_trace_id(),
            "customer_id": customer_id,
            "message": "History cleared.",
        }

    @router.post("/api/chat/{customer_id}/reset-context")
    async def api_reset_chat_context(customer_id: str):
        store = deps.require_object_store()

        def _reset() -> dict:
            reset_context(store, customer_id)
            clear_conversation_history(store, customer_id)
            clear_conversation_summary(store, customer_id)
            clear_notes_manifest(store, customer_id)
            return {
                "notes_manifest_count": len(list_notes(store, customer_id)),
                "history_count": len(load_conversation_history(store, customer_id, max_turns=0)),
            }

        counts = await anyio.to_thread.run_sync(_reset)
        return {
            "status": "ok",
            "trace_id": deps.current_trace_id(),
            "customer_id": customer_id,
            "message": "Context reset.",
            **counts,
        }

    # Expose this helper for legacy compatibility aliases in the composition root.
    router.persist_chat_project_membership = _persist_chat_project_membership  # type: ignore[attr-defined]
    return router
