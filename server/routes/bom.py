from __future__ import annotations

import functools
import io
import time

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from agent.bom_service import get_shared_bom_service, new_trace_id
from server.models import BomChatRequest, BomXlsxRequest
from server.services.bom_artifacts import (
    BOM_XLSX_CONTENT_TYPE,
    bom_xlsx_download_is_valid,
    bom_xlsx_key,
    validate_bom_xlsx_filename,
)


def create_bom_router(deps) -> APIRouter:
    router = APIRouter()

    def _service():
        return getattr(deps.app().state, "bom_service", None) or get_shared_bom_service()

    @router.get("/api/bom/config")
    async def bom_config(_user: dict = Depends(deps.require_user)):
        service = _service()
        default_model_id = deps.default_model_id() or "bom-default"
        return service.config(default_model_id=default_model_id)

    @router.get("/api/bom/health")
    async def bom_health(_user: dict = Depends(deps.require_user)):
        service = _service()
        payload = service.health()
        payload["trace_id"] = deps.current_trace_id()
        return payload

    @router.post("/api/bom/chat")
    async def bom_chat(req: BomChatRequest, _user: dict = Depends(deps.require_user)):
        service = _service()
        trace_id = deps.current_trace_id() or new_trace_id()
        model_id = req.model_id or deps.default_model_id() or "bom-default"
        started = time.perf_counter()
        result = await anyio.to_thread.run_sync(
            functools.partial(
                service.chat,
                message=req.message,
                conversation=[{"role": t.role, "content": t.content} for t in req.conversation],
                trace_id=trace_id,
                model_id=model_id,
                text_runner=getattr(deps.app().state, "text_runner", None),
            )
        )
        trace = result.get("trace", {}) if isinstance(result, dict) else {}
        deps.logger.info(
            "bom_chat trace_id=%s model_id=%s type=%s repair_attempts=%s cache_ready=%s cache_source=%s latency_ms=%d",
            trace_id,
            trace.get("model_id", model_id),
            result.get("type") if isinstance(result, dict) else "",
            trace.get("repair_attempts", 0),
            trace.get("cache_ready", False),
            trace.get("cache_source", "none"),
            int((time.perf_counter() - started) * 1000),
        )
        return result

    @router.post("/api/bom/generate-xlsx")
    async def bom_generate_xlsx(req: BomXlsxRequest, _user: dict = Depends(deps.require_user)):
        service = _service()
        workbook_bytes = await anyio.to_thread.run_sync(functools.partial(service.generate_xlsx, req.bom_payload))
        filename = f"oci-bom-{time.strftime('%Y%m%d-%H%M%S')}.xlsx"
        return StreamingResponse(
            io.BytesIO(workbook_bytes),
            media_type=BOM_XLSX_CONTENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/bom/{customer_id}/download/{filename}")
    async def bom_download_xlsx(
        customer_id: str,
        filename: str,
        _user: dict = Depends(deps.require_user),
    ):
        safe_filename = validate_bom_xlsx_filename(filename)
        object_store = getattr(deps.app().state, "object_store", None)
        if object_store is None:
            raise HTTPException(status_code=404, detail="File not found")
        key = bom_xlsx_key(customer_id, safe_filename)
        if not bom_xlsx_download_is_valid(object_store, key):
            raise HTTPException(status_code=404, detail="BOM XLSX file not found")
        try:
            data = object_store.get(key)
        except KeyError:
            raise HTTPException(status_code=404, detail="BOM XLSX file not found")
        return Response(
            content=data,
            media_type=BOM_XLSX_CONTENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )

    @router.post("/api/bom/refresh-data")
    async def bom_refresh_data(_user: dict = Depends(deps.require_admin_user)):
        service = _service()
        started = time.perf_counter()
        payload = await anyio.to_thread.run_sync(service.refresh_data)
        deps.logger.info(
            "bom_refresh_data trace_id=%s source=%s pricing_skus=%s latency_ms=%d",
            deps.current_trace_id(),
            payload.get("source", "unknown"),
            payload.get("pricing_sku_count", 0),
            int((time.perf_counter() - started) * 1000),
        )
        payload["trace_id"] = deps.current_trace_id()
        return payload

    return router
