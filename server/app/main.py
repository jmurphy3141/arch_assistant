#!/usr/bin/env python3
"""
server/app/main.py
------------------
OCI Drawing Agent — FastAPI Server (v1.3.2)

Serves under /api/* prefix to match OCI LB path routing.
Legacy /* aliases included for backwards compatibility.

Run (from repo root):
    uvicorn server.app.main:app --host 0.0.0.0 --port 8000 \
        --proxy-headers --forwarded-allow-ips='*'
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
import yaml
from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

try:
    from oci.addons.adk import Agent, AgentClient  # type: ignore

    _OCI_AVAILABLE = True
except ImportError:
    _OCI_AVAILABLE = False
    Agent = AgentClient = None  # type: ignore

from agent.bom_parser import (
    ServiceItem,
    bom_to_llm_input,
    build_layout_intent_prompt,
)
from agent.drawio_generator import generate_drawio
from agent.layout_engine import spec_to_draw_dict
from agent.oci_standards import get_catalogue_summary
from agent.persistence_objectstore import (
    ARTIFACT_ALLOWLIST,
    InMemoryObjectStore,
    ObjectStoreBase,
    persist_artifacts,
)
import server.services.oci_object_storage as _oci_storage

logger = logging.getLogger(__name__)

AGENT_VERSION = "1.3.2"
SCHEMA_VERSION = {"spec": "1.1", "draw_dict": "1.0"}

# ---------------------------------------------------------------------------
# Config (config.yaml lives at repo root — two levels up from this file)
# ---------------------------------------------------------------------------
_cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
try:
    with open(_cfg_path) as _f:
        _cfg = yaml.safe_load(_f) or {}
except FileNotFoundError:
    _cfg = {}

REGION = _cfg.get("region", "us-phoenix-1")
AGENT_ENDPOINT_ID = _cfg.get("agent_endpoint_id", "")
COMPARTMENT_ID = _cfg.get("compartment_id", "")
MAX_STEPS = int(_cfg.get("max_steps", 5))
OUTPUT_DIR = Path(_cfg.get("output_dir", "/tmp/diagrams"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Global mutable state
# ---------------------------------------------------------------------------
_oci_agent: Optional[Any] = None
SESSION_STORE: Dict[str, str] = {}
PENDING_CLARIFY: Dict[str, dict] = {}
IDEMPOTENCY_CACHE: Dict[tuple, dict] = {}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="OCI Drawing Agent", version=AGENT_VERSION)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ObjectRef(BaseModel):
    namespace: Optional[str] = None
    bucket: str
    object: str  # object name in OCI (shadows builtin but is valid Python)
    version_id: Optional[str] = None


class ClarifyRequest(BaseModel):
    answers: str
    client_id: Optional[str] = "default"
    diagram_name: Optional[str] = "oci_architecture"


class GenerateRequest(BaseModel):
    # Exactly one of resources or resources_from_bucket must be supplied
    resources: Optional[List[Dict[str, Any]]] = None
    resources_from_bucket: Optional[ObjectRef] = None

    context: Optional[str] = ""
    context_from_bucket: Optional[ObjectRef] = None

    questionnaire: Optional[str] = ""
    questionnaire_from_bucket: Optional[ObjectRef] = None

    notes: Optional[str] = ""
    notes_from_bucket: Optional[ObjectRef] = None

    deployment_hints: Optional[dict] = None
    deployment_hints_from_bucket: Optional[ObjectRef] = None

    diagram_name: Optional[str] = "oci_architecture"
    client_id: Optional[str] = "default"


class ResolveRequest(BaseModel):
    resources_from_bucket: Optional[ObjectRef] = None
    context_from_bucket: Optional[ObjectRef] = None
    questionnaire_from_bucket: Optional[ObjectRef] = None
    notes_from_bucket: Optional[ObjectRef] = None
    deployment_hints_from_bucket: Optional[ObjectRef] = None


class ChatRequest(BaseModel):
    message: str
    client_id: Optional[str] = "default"


class ListObjectsRequest(BaseModel):
    bucket: str
    prefix: Optional[str] = ""
    limit: Optional[int] = 100
    start: Optional[str] = None


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_input_hash(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def extract_agent_text(response: Any) -> str:
    if not hasattr(response, "data"):
        return str(response)
    data = response.data
    if "message" in data:
        msg = data["message"]
        if isinstance(msg, dict):
            text = msg.get("content", {}).get("text")
            if text is not None:
                return text
        if isinstance(msg, str):
            return msg
    for msg in data.get("messages", []):
        if msg.get("role") == "AGENT":
            return msg.get("content", {}).get("text") or ""
    return ""


def clean_json(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        s = s.strip()
    return s


# ---------------------------------------------------------------------------
# Allowlist & size-limit enforcement
# ---------------------------------------------------------------------------


def _check_bucket_allowed(bucket: str, object_name: str) -> None:
    """Raise 403 if bucket/prefix not in env-var allowlist."""
    allowed_buckets = set(
        b.strip() for b in os.environ.get("ALLOWED_BUCKETS", "").split(",") if b.strip()
    )
    if allowed_buckets and bucket not in allowed_buckets:
        raise HTTPException(
            status_code=403,
            detail=f"Bucket '{bucket}' not in allowlist. Set ALLOWED_BUCKETS env var.",
        )
    allowed_prefixes = [
        p.strip()
        for p in os.environ.get("ALLOWED_PREFIXES", "").split(",")
        if p.strip()
    ]
    if allowed_prefixes and not any(object_name.startswith(p) for p in allowed_prefixes):
        raise HTTPException(
            status_code=403,
            detail=f"Object '{object_name}' prefix not in allowlist.",
        )


async def _fetch_bucket_bytes(ref: ObjectRef, max_bytes: int, field: str) -> bytes:
    """Fetch raw bytes from OCI bucket, enforce size limit and allowlist."""
    _check_bucket_allowed(ref.bucket, ref.object)
    try:
        data: bytes = await anyio.to_thread.run_sync(
            functools.partial(
                _oci_storage.fetch_object,
                ref.bucket,
                ref.object,
                ref.namespace,
                ref.version_id,
            )
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"{field}: object not found: {ref.bucket}/{ref.object}",
        )
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail=f"{field}: access forbidden: {ref.bucket}/{ref.object}",
        )
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{field}: object too large ({len(data)} bytes > {max_bytes} limit)",
        )
    return data


async def _resolve_bucket_resources(ref: ObjectRef) -> List[Dict[str, Any]]:
    max_bytes = int(
        os.environ.get("MAX_OBJECT_BYTES_RESOURCES", str(10 * 1024 * 1024))
    )
    data = await _fetch_bucket_bytes(ref, max_bytes, "resources_from_bucket")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"resources_from_bucket: invalid JSON: {exc}",
        )
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=422,
            detail="resources_from_bucket: JSON root must be an array",
        )
    return parsed


async def _resolve_bucket_text(ref: ObjectRef, field: str) -> str:
    max_bytes = int(os.environ.get("MAX_OBJECT_BYTES_TEXT", str(1 * 1024 * 1024)))
    data = await _fetch_bucket_bytes(ref, max_bytes, field)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field}: UTF-8 decode error: {exc}",
        )


async def _resolve_bucket_json(ref: ObjectRef, field: str) -> dict:
    text = await _resolve_bucket_text(ref, field)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field}: invalid JSON: {exc}",
        )


# ---------------------------------------------------------------------------
# OCI runner / LLM bridge
# ---------------------------------------------------------------------------


def _make_oci_runner(oci_agent: Any) -> Any:
    def _run(prompt: str, client_id: str) -> dict:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            session_id = SESSION_STORE.get(client_id)
            response = oci_agent.run(prompt, session_id=session_id, max_steps=MAX_STEPS)
            SESSION_STORE[client_id] = response.session_id
            raw = extract_agent_text(response)
            cleaned = clean_json(raw)
            if not cleaned.startswith("{"):
                raise HTTPException(
                    status_code=422,
                    detail=f"LLM response is not JSON: {cleaned[:200]!r}",
                )
            return json.loads(cleaned)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    return _run


async def call_llm(prompt: str, client_id: str) -> dict:
    runner = getattr(app.state, "llm_runner", None)
    if runner is None:
        raise RuntimeError("LLM runner not initialised.")
    if asyncio.iscoroutinefunction(runner):
        return await runner(prompt, client_id)
    return await anyio.to_thread.run_sync(functools.partial(runner, prompt, client_id))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _clarify_response(
    client_id: str,
    diagram_name: str,
    request_id: str,
    input_hash: str,
    questions: list,
) -> dict:
    return {
        "status": "need_clarification",
        "agent_version": AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "client_id": client_id,
        "diagram_name": diagram_name,
        "request_id": request_id,
        "input_hash": input_hash,
        "questions": questions,
        "errors": [],
    }


async def run_pipeline(
    items: list,
    prompt: str,
    diagram_name: str,
    client_id: str,
    request_id: str,
    input_hash: str,
    deployment_hints: Optional[dict] = None,
) -> dict:
    if deployment_hints is None:
        deployment_hints = {}

    spec = await call_llm(prompt, client_id)

    if spec.get("status") == "need_clarification":
        PENDING_CLARIFY[client_id] = {
            "items": items,
            "prompt": prompt,
            "diagram_name": diagram_name,
        }
        return _clarify_response(
            client_id, diagram_name, request_id, input_hash,
            spec.get("questions", []),
        )

    # LayoutIntent path (spec has "placements" key)
    if "placements" in spec:
        try:
            from agent.layout_intent import LayoutIntentError, validate_layout_intent
            from agent.intent_compiler import compile_intent_to_flat_spec

            _spec_ref = spec

            def _compile():
                intent = validate_layout_intent(_spec_ref, items)
                return compile_intent_to_flat_spec(intent, items)

            spec = await anyio.to_thread.run_sync(_compile)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"LayoutIntent validation/compile error: {exc}",
            )

    # Multi-region hints check
    mr_mode = deployment_hints.get("multi_region_mode")
    is_multi_region = spec.get("deployment_type") == "multi_region" or len(
        deployment_hints.get("regions", [])
    ) >= 2
    if is_multi_region and not mr_mode:
        PENDING_CLARIFY[client_id] = {
            "items": items,
            "prompt": prompt,
            "diagram_name": diagram_name,
        }
        return _clarify_response(
            client_id, diagram_name, request_id, input_hash,
            [
                {
                    "id": "regions.mode",
                    "question": "Is the second region a DR/HA duplicate or split workloads?",
                    "blocking": True,
                }
            ],
        )

    items_by_id = {i.id: i for i in items}
    draw_dict = await anyio.to_thread.run_sync(
        functools.partial(spec_to_draw_dict, spec, items_by_id)
    )

    page_w = spec.get("page", {}).get("width", 1654)
    page_h = spec.get("page", {}).get("height", 1169)

    if mr_mode == "duplicate_drha":
        primary_box = next(
            (b for b in draw_dict["boxes"] if b.get("box_type") == "_region_box"), None
        )
        stub_x = (primary_box["x"] + primary_box["w"] + 40) if primary_box else 900
        stub_y = primary_box["y"] if primary_box else 120
        draw_dict["boxes"].append(
            {
                "id": "region_secondary_stub",
                "label": "Duplicate DR/HA Region",
                "box_type": "_region_stub",
                "tier": "",
                "x": stub_x,
                "y": stub_y,
                "w": 260,
                "h": 90,
            }
        )
    elif mr_mode == "split_workloads":
        page_w = 3308

    render_manifest = {
        "page": {"width": page_w, "height": page_h},
        "deployment_type": spec.get("deployment_type", "single_ad"),
        "node_count": len(draw_dict.get("nodes", [])),
        "edge_count": len(draw_dict.get("edges", [])),
        "multi_region_mode": mr_mode,
    }

    node_to_resource_map: dict = {
        n["id"]: {"oci_type": n.get("type", ""), "label": n.get("label", "")}
        for n in draw_dict.get("nodes", [])
    }
    for item in items:
        if item.id in node_to_resource_map:
            node_to_resource_map[item.id]["layer"] = item.layer
        else:
            node_to_resource_map[item.id] = {
                "oci_type": item.oci_type,
                "label": item.label,
                "layer": item.layer,
            }

    drawio_path = OUTPUT_DIR / f"{diagram_name}.drawio"
    await anyio.to_thread.run_sync(
        functools.partial(generate_drawio, draw_dict, drawio_path)
    )
    drawio_xml: str = await anyio.to_thread.run_sync(drawio_path.read_text)

    object_store = getattr(app.state, "object_store", None)
    persistence_cfg = getattr(app.state, "persistence_config", None) or {}
    prefix = persistence_cfg.get("prefix", "diagrams")

    if object_store is not None:
        artifacts = {
            "diagram.drawio": drawio_xml.encode("utf-8"),
            "spec.json": json.dumps(spec).encode("utf-8"),
            "draw_dict.json": json.dumps(draw_dict).encode("utf-8"),
            "render_manifest.json": json.dumps(render_manifest).encode("utf-8"),
            "node_to_resource_map.json": json.dumps(node_to_resource_map).encode("utf-8"),
        }
        await anyio.to_thread.run_sync(
            functools.partial(
                persist_artifacts,
                object_store,
                prefix,
                client_id,
                diagram_name,
                request_id,
                artifacts,
            )
        )

    return {
        "status": "ok",
        "agent_version": AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "client_id": client_id,
        "diagram_name": diagram_name,
        "request_id": request_id,
        "input_hash": input_hash,
        "output_path": str(drawio_path),
        "drawio_xml": drawio_xml,
        "spec": spec,
        "draw_dict": draw_dict,
        "render_manifest": render_manifest,
        "node_to_resource_map": node_to_resource_map,
        "download": {
            "url": (
                f"/api/download/diagram.drawio"
                f"?client_id={client_id}&diagram_name={diagram_name}"
            ),
            "object_storage_latest": f"{prefix}/{client_id}/{diagram_name}/LATEST.json",
        },
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
def startup() -> None:
    global _oci_agent
    if getattr(app.state, "llm_runner", None) is not None:
        _ensure_state_defaults()
        return
    if not _OCI_AVAILABLE:
        logger.warning("oci[adk] not importable — llm_runner will be None")
        app.state.llm_runner = None
        _ensure_state_defaults()
        return
    try:
        client = AgentClient(auth_type="instance_principal", region=REGION)
        _oci_agent = Agent(
            client=client,
            agent_endpoint_id=AGENT_ENDPOINT_ID,
            instructions=(
                "You are an OCI solutions architect and layout compiler. "
                "Output ONLY valid JSON — no markdown, no explanation."
            ),
            tools=[],
        )
        _oci_agent.setup()
        app.state.llm_runner = _make_oci_runner(_oci_agent)
        logger.info("Drawing Agent ready (OCI Instance Principal)")
    except Exception as exc:
        logger.warning("OCI init failed (%s) — llm_runner=None", exc)
        app.state.llm_runner = None
    _ensure_state_defaults()


def _ensure_state_defaults() -> None:
    if getattr(app.state, "object_store", None) is None:
        app.state.object_store = None
    if getattr(app.state, "persistence_config", None) is None:
        app.state.persistence_config = {}


# ---------------------------------------------------------------------------
# Routes (defined once, mounted at /api AND / for backwards compat)
# ---------------------------------------------------------------------------
router = APIRouter()


@router.post("/upload-bom")
async def upload_bom(
    file: UploadFile = File(...),
    context_file: UploadFile = File(None),
    context: str = Form(default=""),
    diagram_name: str = Form(default="oci_architecture"),
    client_id: str = Form(default="default"),
) -> JSONResponse:
    max_bom = int(os.environ.get("MAX_UPLOAD_BYTES_BOM", str(25 * 1024 * 1024)))
    request_id = str(uuid.uuid4())
    try:
        file_bytes = await file.read()
        if len(file_bytes) > max_bom:
            raise HTTPException(
                status_code=413,
                detail=f"BOM file too large ({len(file_bytes)} > {max_bom} bytes)",
            )

        input_hash = compute_input_hash(hashlib.sha256(file_bytes).hexdigest())
        cache_key = (client_id, diagram_name, input_hash)
        if cache_key in IDEMPOTENCY_CACHE:
            return JSONResponse(status_code=200, content=IDEMPOTENCY_CACHE[cache_key])

        suffix = Path(file.filename or "bom.xlsx").suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            bom_path = tmp.name

        context_text = context
        if context_file and context_file.filename:
            raw_ctx = await context_file.read()
            try:
                context_text = raw_ctx.decode("utf-8")
            except UnicodeDecodeError:
                context_text = raw_ctx.decode("latin-1", errors="replace")

        items, prompt = await anyio.to_thread.run_sync(
            functools.partial(bom_to_llm_input, bom_path, context=context_text)
        )
        await anyio.to_thread.run_sync(functools.partial(os.unlink, bom_path))

        result = await run_pipeline(
            items, prompt, diagram_name, client_id, request_id, input_hash
        )
        if result["status"] == "ok":
            IDEMPOTENCY_CACHE[cache_key] = result
        return JSONResponse(status_code=200, content=result)

    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in upload-bom: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/clarify")
async def clarify(req: ClarifyRequest) -> JSONResponse:
    request_id = str(uuid.uuid4())
    input_hash = compute_input_hash(req.answers or "")

    pending = PENDING_CLARIFY.get(req.client_id)
    if not pending:
        raise HTTPException(
            status_code=404,
            detail=f"No pending clarification for client_id '{req.client_id}'.",
        )
    try:
        enriched = (
            pending["prompt"]
            + f"\n\nCLARIFICATION ANSWERS:\n{req.answers.strip()}\n\n"
            + "Now produce the layout spec JSON. Output ONLY valid JSON."
        )
        result = await run_pipeline(
            pending["items"],
            enriched,
            req.diagram_name,
            req.client_id,
            request_id,
            input_hash,
        )
        if result["status"] == "ok":
            PENDING_CLARIFY.pop(req.client_id, None)
        return JSONResponse(status_code=200, content=result)

    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in clarify: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate")
async def generate_from_resources(req: GenerateRequest) -> JSONResponse:
    request_id = str(uuid.uuid4())
    deployment_hints = req.deployment_hints or {}

    # Validate mutual exclusivity
    if req.resources is not None and req.resources_from_bucket is not None:
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of: resources or resources_from_bucket",
        )
    if req.resources is None and req.resources_from_bucket is None:
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of: resources or resources_from_bucket",
        )

    # Resolve resources
    if req.resources_from_bucket is not None:
        raw_resources = await _resolve_bucket_resources(req.resources_from_bucket)
    else:
        raw_resources = req.resources  # type: ignore[assignment]

    # Resolve text fields
    context = req.context or ""
    if req.context_from_bucket:
        context = await _resolve_bucket_text(
            req.context_from_bucket, "context_from_bucket"
        )

    questionnaire = req.questionnaire or ""
    if req.questionnaire_from_bucket:
        questionnaire = await _resolve_bucket_text(
            req.questionnaire_from_bucket, "questionnaire_from_bucket"
        )

    notes = req.notes or ""
    if req.notes_from_bucket:
        notes = await _resolve_bucket_text(req.notes_from_bucket, "notes_from_bucket")

    if req.deployment_hints_from_bucket:
        deployment_hints = await _resolve_bucket_json(
            req.deployment_hints_from_bucket, "deployment_hints_from_bucket"
        )

    context_total = context
    if questionnaire and questionnaire.strip():
        context_total += f"\n\nQUESTIONNAIRE:\n{questionnaire}"
    if notes and notes.strip():
        context_total += f"\n\nNOTES:\n{notes}"

    # Build ServiceItems
    items: List[ServiceItem] = []
    for r in raw_resources:
        otype = r.get("oci_type") or r.get("type")
        if not otype:
            raise HTTPException(
                status_code=422, detail="resource missing oci_type/type"
            )
        items.append(
            ServiceItem(
                id=r.get("id", otype.replace(" ", "_")),
                oci_type=otype,
                label=r.get("label", otype),
                layer=r.get("layer", "compute"),
            )
        )

    input_hash = compute_input_hash(
        canonical_json(raw_resources),
        "\n",
        context_total,
        "\n",
        canonical_json(deployment_hints),
    )

    cache_key = (req.client_id, req.diagram_name, input_hash)
    if cache_key in IDEMPOTENCY_CACHE:
        return JSONResponse(status_code=200, content=IDEMPOTENCY_CACHE[cache_key])

    prompt = build_layout_intent_prompt(items, context=context_total)

    try:
        result = await run_pipeline(
            items,
            prompt,
            req.diagram_name,
            req.client_id,
            request_id,
            input_hash,
            deployment_hints=deployment_hints,
        )
        if result["status"] == "ok":
            IDEMPOTENCY_CACHE[cache_key] = result
        return JSONResponse(status_code=200, content=result)

    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/inputs/resolve")
async def resolve_inputs(req: ResolveRequest) -> JSONResponse:
    """Validate bucket refs and return a preview without generating a diagram."""
    resolved: dict = {}
    errors: dict = {}

    async def _try(key: str, coro: Any) -> None:
        try:
            resolved[key] = await coro
        except HTTPException as exc:
            errors[key] = {"ok": False, "status": exc.status_code, "detail": exc.detail}

    if req.resources_from_bucket:
        try:
            items = await _resolve_bucket_resources(req.resources_from_bucket)
            resolved["resources"] = {"ok": True, "count": len(items)}
        except HTTPException as exc:
            errors["resources"] = {
                "ok": False,
                "status": exc.status_code,
                "detail": exc.detail,
            }

    if req.context_from_bucket:
        try:
            ctx = await _resolve_bucket_text(
                req.context_from_bucket, "context_from_bucket"
            )
            resolved["context"] = {"ok": True, "length": len(ctx)}
        except HTTPException as exc:
            errors["context"] = {
                "ok": False,
                "status": exc.status_code,
                "detail": exc.detail,
            }

    if req.questionnaire_from_bucket:
        try:
            q = await _resolve_bucket_text(
                req.questionnaire_from_bucket, "questionnaire_from_bucket"
            )
            resolved["questionnaire"] = {"ok": True, "length": len(q)}
        except HTTPException as exc:
            errors["questionnaire"] = {
                "ok": False,
                "status": exc.status_code,
                "detail": exc.detail,
            }

    if req.notes_from_bucket:
        try:
            n = await _resolve_bucket_text(req.notes_from_bucket, "notes_from_bucket")
            resolved["notes"] = {"ok": True, "length": len(n)}
        except HTTPException as exc:
            errors["notes"] = {
                "ok": False,
                "status": exc.status_code,
                "detail": exc.detail,
            }

    if req.deployment_hints_from_bucket:
        try:
            dh = await _resolve_bucket_json(
                req.deployment_hints_from_bucket, "deployment_hints_from_bucket"
            )
            resolved["deployment_hints"] = {"ok": True, "keys": list(dh.keys())}
        except HTTPException as exc:
            errors["deployment_hints"] = {
                "ok": False,
                "status": exc.status_code,
                "detail": exc.detail,
            }

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok" if not errors else "partial",
            "resolved": resolved,
            "errors": errors,
        },
    )


@router.post("/oci/list-objects")
async def list_oci_objects(req: ListObjectsRequest) -> JSONResponse:
    """List objects in a bucket with prefix filter (allowlist enforced)."""
    _check_bucket_allowed(req.bucket, req.prefix or "")
    try:
        result = await anyio.to_thread.run_sync(
            functools.partial(
                _oci_storage.list_objects,
                req.bucket,
                req.prefix,
                None,
                req.limit or 100,
                req.start,
            )
        )
        return JSONResponse(
            status_code=200,
            content={"bucket": req.bucket, "prefix": req.prefix, **result},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCI list-objects error: {exc}")


@router.get("/download/{filename}")
def download_file(
    filename: str,
    client_id: Optional[str] = Query(default=None),
    diagram_name: Optional[str] = Query(default=None),
) -> Any:
    if not client_id or not diagram_name:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "MISSING_DOWNLOAD_SCOPE",
                "message": "Query params client_id and diagram_name are required.",
            },
        )

    candidates = [OUTPUT_DIR / filename]
    if filename == "diagram.drawio":
        candidates.append(OUTPUT_DIR / f"{diagram_name}.drawio")
    for path in candidates:
        if path.exists():
            return FileResponse(str(path), filename=filename)

    object_store = getattr(app.state, "object_store", None)
    if object_store is None:
        raise HTTPException(status_code=404, detail="File not found")

    artifact_name = filename
    if filename == f"{diagram_name}.drawio":
        artifact_name = "diagram.drawio"
    if artifact_name not in ARTIFACT_ALLOWLIST:
        raise HTTPException(
            status_code=403,
            detail=f"Filename '{artifact_name}' not in download allowlist.",
        )

    persistence_cfg = getattr(app.state, "persistence_config", None) or {}
    prefix = persistence_cfg.get("prefix", "diagrams")
    latest_key = f"{prefix}/{client_id}/{diagram_name}/LATEST.json"

    try:
        latest_raw = object_store.get(latest_key)
        latest = json.loads(latest_raw.decode("utf-8"))
        artifact_key = latest.get("artifacts", {}).get(artifact_name)
        if not artifact_key:
            raise HTTPException(
                status_code=404,
                detail=f"Artifact '{artifact_name}' not in LATEST.json",
            )
        data = object_store.get(artifact_key)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="File not found (no LATEST.json for this scope)",
        )

    content_type = (
        "text/xml" if artifact_name.endswith(".drawio") else "application/json"
    )
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "agent_version": AGENT_VERSION,
        "agent": "oci-drawing-agent",
        "pending_clarifications": list(PENDING_CLARIFY.keys()),
        "idempotency_cache_size": len(IDEMPOTENCY_CACHE),
    }


@router.get("/mcp/tools")
def mcp_tools() -> dict:
    return {
        "tools": [
            {
                "name": "upload_bom",
                "description": "Upload an Excel BOM to generate an OCI architecture diagram.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "diagram_name": {"type": "string"},
                        "client_id": {"type": "string"},
                    },
                    "required": ["file"],
                },
            },
            {
                "name": "generate_diagram",
                "description": "Generate a diagram from a resource list.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "resources": {"type": "array"},
                        "diagram_name": {"type": "string"},
                        "client_id": {"type": "string"},
                    },
                    "required": ["resources"],
                },
            },
            {
                "name": "clarify",
                "description": "Submit answers to clarification questions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "answers": {"type": "string"},
                        "client_id": {"type": "string"},
                        "diagram_name": {"type": "string"},
                    },
                    "required": ["answers", "client_id"],
                },
            },
            {
                "name": "get_oci_catalogue",
                "description": "List all known OCI resource types.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
    }


@router.get("/mcp/tools/get_oci_catalogue")
def get_catalogue() -> dict:
    return {"catalogue": get_catalogue_summary()}


@router.get("/.well-known/agent-card.json")
def agent_card() -> JSONResponse:
    host = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8000")
    return JSONResponse(
        {
            "schema_version": "1.0",
            "agent_version": AGENT_VERSION,
            "name": "OCI Drawing Agent",
            "description": (
                "Generates OCI architecture draw.io diagrams from a BOM Excel file."
            ),
            "vendor": "Oracle",
            "capabilities": [
                "diagram-generation",
                "bom-parsing",
                "clarification-flow",
                "multi-region",
                "object-storage-bucket-mode",
            ],
            "endpoints": {
                "upload_bom": {"path": "/api/upload-bom", "method": "POST"},
                "clarify": {"path": "/api/clarify", "method": "POST"},
                "generate": {"path": "/api/generate", "method": "POST"},
                "health": {"path": "/api/health", "method": "GET"},
                "inputs_resolve": {
                    "path": "/api/inputs/resolve",
                    "method": "POST",
                },
            },
        }
    )


# Mount with /api prefix (primary) AND without for backwards compatibility
app.include_router(router, prefix="/api")
app.include_router(router, prefix="")  # legacy aliases

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
