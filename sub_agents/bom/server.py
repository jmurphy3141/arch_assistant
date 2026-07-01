from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import yaml
from fastapi import FastAPI

from agent.bom_service import get_shared_bom_service
from sub_agents.grounding import (
    grounding_trace,
    input_grounding_missing,
    needs_input_message,
    normalize_engagement_context,
    output_grounding_missing,
)
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


_bom_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_bom_llm = _bom_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_bom_llm.get("model_id"), _main_inference.get("model_id"), default=""))


card = AgentCard(
    name="bom",
    description="Produces a priced OCI Bill of Materials from workload inputs.",
    inputs={
        "required": ["task", "engagement_context"],
        "optional": ["region", "trace_id"],
    },
    output="Structured BOM JSON with line items and monthly cost total",
    llm_model_id=_model_id,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = get_shared_bom_service()
    if not service.health().get("ready"):
        await anyio.to_thread.run_sync(service.refresh_data)
    yield


async def handle(req: A2ARequest) -> A2AResponse:
    raw_context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    context = normalize_engagement_context(raw_context)
    grounding_missing = input_grounding_missing(raw_context)
    if grounding_missing:
        return A2AResponse(
            status="needs_input",
            result=needs_input_message(grounding_missing),
            trace={"agent": card.name, "trace_id": req.trace_id, **grounding_trace(context, grounding_missing)},
        )
    service = get_shared_bom_service()
    structured_inputs = (
        context.get("structured_inputs")
        if isinstance(context.get("structured_inputs"), dict)
        else None
    )
    if structured_inputs:
        response = await anyio.to_thread.run_sync(
            lambda: service.generate_from_inputs(
                inputs=structured_inputs,
                trace_id=req.trace_id,
                model_id="deterministic-structured-inputs",
            )
        )
        if str(response.get("type") or "").lower() != "final" or not isinstance(response.get("bom_payload"), dict):
            return A2AResponse(
                status="needs_input",
                result=str(response.get("reply") or "Structured BOM inputs could not be normalized."),
                trace={**dict(response.get("trace", {}) or {}), **grounding_trace(context, [])},
            )
        result = json.dumps({
            "bom_payload": response["bom_payload"],
            "prices_from": (response.get("trace") or {}).get("cache_source", "pricing_cache"),
        }, ensure_ascii=False)
        grounding_missing = output_grounding_missing(raw_context, result)
        if grounding_missing:
            return A2AResponse(
                status="needs_input",
                result=needs_input_message(grounding_missing),
                trace={**dict(response.get("trace", {}) or {}), **grounding_trace(context, grounding_missing)},
            )
        return A2AResponse(
            status="ok",
            result=result,
            trace={**dict(response.get("trace", {}) or {}), **grounding_trace(context, [])},
        )
    task_msg = req.task
    if raw_context:
        ctx_block = json.dumps(context, ensure_ascii=False, indent=2)
        task_msg = (
            f"[Archie Canonical Memory]\n{ctx_block}\n[End Archie Canonical Memory]\n\n{req.task}"
        )
    response = await anyio.to_thread.run_sync(
        lambda: service.chat(
            message=task_msg,
            conversation=[],
            trace_id=req.trace_id,
            model_id=_model_id,
        )
    )

    service_trace = response.get("trace") if isinstance(response.get("trace"), dict) else {}
    result_type = str(response.get("type") or "")

    if result_type != "final" or not response.get("json_bom"):
        error_detail = str(response.get("reply") or "BOM validation failed.")
        return A2AResponse(
            status="needs_input",
            result=error_detail,
            trace={**service_trace, **grounding_trace(context, [])},
        )

    bom_json = response.get("json_bom")
    if not isinstance(bom_json, str):
        bom_json = json.dumps(response.get("bom_payload") or {}, ensure_ascii=False)

    grounding_missing = output_grounding_missing(raw_context, bom_json)
    if grounding_missing:
        return A2AResponse(
            status="needs_input",
            result=needs_input_message(grounding_missing),
            trace={**service_trace, **grounding_trace(context, grounding_missing)},
        )
    return A2AResponse(
        status="ok",
        result=bom_json,
        trace={**service_trace, **grounding_trace(context, [])},
    )


app = FastAPI(title="bom A2A sub-agent", lifespan=lifespan)


@app.get("/a2a/card")
async def get_card() -> dict[str, Any]:
    return card.model_dump()


@app.post("/a2a", response_model=A2AResponse)
async def post_a2a(req: A2ARequest) -> A2AResponse:
    return await handle(req)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "agent": card.name}
