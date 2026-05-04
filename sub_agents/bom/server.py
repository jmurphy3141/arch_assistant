from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import yaml
from fastapi import FastAPI

from agent.bom_service import get_shared_bom_service
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
        "required": ["task"],
        "optional": ["region", "engagement_context", "trace_id"],
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
    service = get_shared_bom_service()
    response = await anyio.to_thread.run_sync(
        lambda: service.chat(
            message=req.task,
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
            trace=service_trace,
        )

    bom_json = response.get("json_bom")
    if not isinstance(bom_json, str):
        bom_json = json.dumps(response.get("bom_payload") or {}, ensure_ascii=False)

    return A2AResponse(
        status="ok",
        result=bom_json,
        trace=service_trace,
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
