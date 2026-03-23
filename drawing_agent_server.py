#!/usr/bin/env python3
"""
OCI Drawing Agent - FastAPI Server  (v1.3.2)
Pipeline: BOM.xlsx + optional context file
  → bom_parser.py   (rule-based service extraction + LLM prompt)
  → OCI GenAI       (layout compiler → layout spec JSON or clarification questions)
  → layout_engine.py (spec → absolute x,y positions)
  → drawio_generator.py (positions → flat draw.io XML)

Endpoints:
  POST /upload-bom        — upload BOM + optional context file
  POST /clarify           — submit answers to clarification questions
  POST /generate          — JSON body (pre-parsed resources)
  POST /chat              — free-form chat
  GET  /download/{file}   — download generated file (requires client_id + diagram_name)
  GET  /health
  GET  /.well-known/agent-card.json
  GET  /mcp/tools

v1.3.2 additions:
  - request_id (UUIDv4) and input_hash (sha256) on all responses
  - app.state.llm_runner injection seam (tests override; startup sets real OCI runner)
  - app.state.object_store injection seam (default None = no persistence)
  - deployment_hints.multi_region_mode for hints-only multi-region rendering
  - /download requires client_id + diagram_name scope query params
  - In-process IDEMPOTENCY_CACHE keyed by (client_id, diagram_name, input_hash)
  - OCI Object Storage persistence with atomic LATEST.json pointer
"""

import hashlib
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel

try:
    from oci.addons.adk import Agent, AgentClient
    _OCI_AVAILABLE = True
except ImportError:
    _OCI_AVAILABLE = False
    Agent = AgentClient = None

from agent.bom_parser import bom_to_llm_input, parse_bom
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio
from agent.oci_standards import get_catalogue_summary
from agent.persistence_objectstore import (
    ObjectStoreBase,
    InMemoryObjectStore,
    persist_artifacts,
    ARTIFACT_ALLOWLIST,
)

logger = logging.getLogger(__name__)
app = FastAPI(title="OCI Drawing Agent", version="1.3.2")

# ── Config ─────────────────────────────────────────────────────────────────────
_cfg_path = Path(__file__).parent / "config.yaml"
with open(_cfg_path) as _f:
    _cfg = yaml.safe_load(_f)

REGION            = _cfg.get("region", "us-phoenix-1")
AGENT_ENDPOINT_ID = _cfg["agent_endpoint_id"]
COMPARTMENT_ID    = _cfg["compartment_id"]
MAX_STEPS         = _cfg.get("max_steps", 5)
OUTPUT_DIR        = Path(_cfg.get("output_dir", "/tmp/diagrams"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

AGENT_VERSION  = "1.3.2"
SCHEMA_VERSION = {"spec": "1.1", "draw_dict": "1.0"}

# ── Global mutable state ───────────────────────────────────────────────────────
_oci_agent: Optional[Any] = None          # real OCI Agent, set in startup
SESSION_STORE:     Dict[str, str]  = {}   # client_id  → session_id
PENDING_CLARIFY:   Dict[str, dict] = {}   # client_id  → {items, prompt, diagram_name}
IDEMPOTENCY_CACHE: Dict[tuple, dict] = {} # (client_id, diagram_name, input_hash) → result


# ── Pydantic models ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:   str
    client_id: Optional[str] = "default"


class ClarifyRequest(BaseModel):
    answers:      str
    client_id:    Optional[str] = "default"
    diagram_name: Optional[str] = "oci_architecture"


class GenerateRequest(BaseModel):
    resources:        List[Dict[str, Any]]
    context:          Optional[str]  = ""
    diagram_name:     Optional[str]  = "oci_architecture"
    client_id:        Optional[str]  = "default"
    deployment_hints: Optional[dict] = {}


# ── Helpers ─────────────────────────────────────────────────────────────────────

def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialisation for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_input_hash(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def extract_agent_text(response) -> str:
    if not hasattr(response, "data"):
        return str(response)
    data = response.data
    logger.debug("response.data: %s", str(data)[:300])

    if "message" in data:
        msg = data["message"]
        if isinstance(msg, dict):
            text = msg.get("content", {}).get("text")
            if text is not None:
                return text
        if isinstance(msg, str):
            return msg

    messages = data.get("messages", [])
    for msg in messages:
        if msg.get("role") == "AGENT":
            return msg.get("content", {}).get("text") or ""

    return ""


def clean_json(raw: str) -> str:
    """Strip markdown fences from LLM output."""
    return raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()


def _make_oci_runner(oci_agent) -> callable:
    """Wrap a real OCI Agent as the llm_runner callable."""
    def _run(prompt: str, client_id: str) -> dict:
        session_id = SESSION_STORE.get(client_id)
        response = oci_agent.run(prompt, session_id=session_id, max_steps=MAX_STEPS)
        SESSION_STORE[client_id] = response.session_id
        raw = extract_agent_text(response)
        logger.info("LLM raw (%d chars): %s", len(raw), raw[:400])
        return json.loads(clean_json(raw))
    return _run


def call_llm(prompt: str, client_id: str) -> dict:
    """
    Call the LLM via app.state.llm_runner.
    Returns parsed JSON dict.
    Tests inject a fake runner via app.state.llm_runner before startup.
    """
    runner = getattr(app.state, "llm_runner", None)
    if runner is None:
        raise RuntimeError(
            "LLM runner is not initialised. "
            "Ensure the server started successfully with OCI auth, "
            "or inject app.state.llm_runner in tests."
        )
    return runner(prompt, client_id)


def _clarify_response(
    client_id: str,
    diagram_name: str,
    request_id: str,
    input_hash: str,
    questions: list,
) -> dict:
    return {
        "status":         "need_clarification",
        "agent_version":  AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "client_id":      client_id,
        "diagram_name":   diagram_name,
        "request_id":     request_id,
        "input_hash":     input_hash,
        "questions":      questions,
        "errors":         [],
    }


def run_pipeline(
    items: list,
    prompt: str,
    diagram_name: str,
    client_id: str,
    request_id: str,
    input_hash: str,
    deployment_hints: Optional[dict] = None,
) -> dict:
    """
    Call LLM → layout engine → drawio generator.
    Returns a full v1.3.2 result dict (status ok or need_clarification).
    Persists artifacts if app.state.object_store is set.
    """
    if deployment_hints is None:
        deployment_hints = {}

    spec = call_llm(prompt, client_id)

    # ── Clarification requested by LLM ───────────────────────────────────────
    if spec.get("status") == "need_clarification":
        PENDING_CLARIFY[client_id] = {
            "items":        items,
            "prompt":       prompt,
            "diagram_name": diagram_name,
        }
        return _clarify_response(
            client_id, diagram_name, request_id, input_hash,
            spec.get("questions", []),
        )

    # ── Multi-region hints check ──────────────────────────────────────────────
    mr_mode = deployment_hints.get("multi_region_mode")
    is_multi_region = (
        spec.get("deployment_type") == "multi_region"
        or len(deployment_hints.get("regions", [])) >= 2
    )
    if is_multi_region and not mr_mode:
        PENDING_CLARIFY[client_id] = {
            "items":        items,
            "prompt":       prompt,
            "diagram_name": diagram_name,
        }
        return _clarify_response(
            client_id, diagram_name, request_id, input_hash,
            [
                {
                    "id":       "regions.mode",
                    "question": (
                        "Is the second region a duplicate DR/HA region or does it run "
                        "different workloads (split deployments)?"
                    ),
                    "blocking": True,
                }
            ],
        )

    # ── Layout engine ─────────────────────────────────────────────────────────
    items_by_id = {i.id: i for i in items}
    draw_dict   = spec_to_draw_dict(spec, items_by_id)

    # ── Multi-region post-processing ──────────────────────────────────────────
    page_w = spec.get("page", {}).get("width", 1654)
    page_h = spec.get("page", {}).get("height", 1169)

    if mr_mode == "duplicate_drha":
        # Keep only the primary region; add a lightweight stub box for the secondary
        regions = spec.get("regions", [])
        secondary_label = "Duplicate DR/HA Region"
        if len(regions) >= 2:
            secondary_label = (
                f"Duplicate DR/HA Region — {regions[1].get('label', '')}"
            )
        primary_box = next(
            (b for b in draw_dict["boxes"] if b.get("box_type") == "_region_box"),
            None,
        )
        stub_x = (primary_box["x"] + primary_box["w"] + 40) if primary_box else 900
        stub_y = primary_box["y"] if primary_box else 120

        draw_dict["boxes"].append({
            "id":       "region_secondary_stub",
            "label":    secondary_label,
            "box_type": "_region_stub",
            "tier":     "",
            "x":        stub_x,
            "y":        stub_y,
            "w":        260,
            "h":        90,
        })

    elif mr_mode == "split_workloads":
        page_w = 3308

    # ── Render manifest ───────────────────────────────────────────────────────
    render_manifest = {
        "page": {"width": page_w, "height": page_h},
        "deployment_type":   spec.get("deployment_type", "single_ad"),
        "node_count":        len(draw_dict.get("nodes", [])),
        "edge_count":        len(draw_dict.get("edges", [])),
        "multi_region_mode": mr_mode,
    }

    # ── Node-to-resource map ──────────────────────────────────────────────────
    node_to_resource_map: dict = {
        n["id"]: {"oci_type": n.get("type", ""), "label": n.get("label", "")}
        for n in draw_dict.get("nodes", [])
    }
    # Enrich with ServiceItem metadata where available
    for item in items:
        if item.id in node_to_resource_map:
            node_to_resource_map[item.id]["layer"] = item.layer
        else:
            node_to_resource_map[item.id] = {
                "oci_type": item.oci_type,
                "label":    item.label,
                "layer":    item.layer,
            }

    # ── Write draw.io file ────────────────────────────────────────────────────
    drawio_path = OUTPUT_DIR / f"{diagram_name}.drawio"
    generate_drawio(draw_dict, drawio_path)
    drawio_xml = drawio_path.read_text()

    # ── Persist artifacts ─────────────────────────────────────────────────────
    object_store     = getattr(app.state, "object_store", None)
    persistence_cfg  = getattr(app.state, "persistence_config", None) or {}
    prefix           = persistence_cfg.get("prefix", "diagrams")

    if object_store is not None:
        artifacts = {
            "diagram.drawio":          drawio_xml.encode("utf-8"),
            "spec.json":               json.dumps(spec).encode("utf-8"),
            "draw_dict.json":          json.dumps(draw_dict).encode("utf-8"),
            "render_manifest.json":    json.dumps(render_manifest).encode("utf-8"),
            "node_to_resource_map.json": json.dumps(node_to_resource_map).encode("utf-8"),
        }
        persist_artifacts(object_store, prefix, client_id, diagram_name, request_id, artifacts)

    return {
        "status":                "ok",
        "agent_version":         AGENT_VERSION,
        "schema_version":        SCHEMA_VERSION,
        "client_id":             client_id,
        "diagram_name":          diagram_name,
        "request_id":            request_id,
        "input_hash":            input_hash,
        "drawio_xml":            drawio_xml,
        "spec":                  spec,
        "draw_dict":             draw_dict,
        "render_manifest":       render_manifest,
        "node_to_resource_map":  node_to_resource_map,
        "download": {
            "url": (
                f"/download/diagram.drawio"
                f"?client_id={client_id}&diagram_name={diagram_name}"
            ),
            "object_storage_latest": (
                f"{prefix}/{client_id}/{diagram_name}/LATEST.json"
            ),
        },
        "errors": [],
    }


# ── Startup ─────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    global _oci_agent

    # Allow tests (or other callers) to pre-inject llm_runner before startup.
    # If already set, skip OCI initialisation entirely.
    if getattr(app.state, "llm_runner", None) is not None:
        logger.info("llm_runner already injected — skipping OCI init")
        _ensure_state_defaults()
        return

    if not _OCI_AVAILABLE:
        logger.warning("oci[adk] not importable — llm_runner will be None")
        app.state.llm_runner = None
        _ensure_state_defaults()
        return

    try:
        client = AgentClient(auth_type="instance_principal", region=REGION)
        logger.info("AgentClient ready — runtime: %s", client.runtime_endpoint)

        _oci_agent = Agent(
            client=client,
            agent_endpoint_id=AGENT_ENDPOINT_ID,
            instructions=(
                "You are an OCI solutions architect and layout compiler. "
                "When given a Bill of Materials, output ONLY valid JSON — "
                "either a layout specification or a clarification request. "
                "No markdown, no explanation, no preamble."
            ),
            tools=[],
        )
        _oci_agent.setup()
        app.state.llm_runner = _make_oci_runner(_oci_agent)
        logger.info("Drawing Agent ready!")
    except Exception as exc:
        logger.warning("OCI init failed (%s) — llm_runner will be None", exc)
        app.state.llm_runner = None

    _ensure_state_defaults()


def _ensure_state_defaults():
    if getattr(app.state, "object_store", None) is None:
        app.state.object_store = None
    if getattr(app.state, "persistence_config", None) is None:
        app.state.persistence_config = {}


# ── Endpoints ───────────────────────────────────────────────────────────────────

@app.post("/upload-bom")
async def upload_bom(
    file:         UploadFile = File(...),
    context_file: UploadFile = File(None),
    context:      str        = Form(default=""),
    diagram_name: str        = Form(default="oci_architecture"),
    client_id:    str        = Form(default="default"),
):
    """
    Upload an Excel BOM + optional context file.
    Returns a draw.io diagram or clarification questions.
    """
    request_id = str(uuid.uuid4())

    try:
        file_bytes = await file.read()
        input_hash = compute_input_hash(
            hashlib.sha256(file_bytes).hexdigest()
        )

        # Idempotency check
        cache_key = (client_id, diagram_name, input_hash)
        if cache_key in IDEMPOTENCY_CACHE:
            return IDEMPOTENCY_CACHE[cache_key]

        # Save BOM to temp file
        suffix = Path(file.filename).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            bom_path = tmp.name

        # Read optional context file
        context_text = context
        if context_file and context_file.filename:
            raw_ctx = await context_file.read()
            try:
                context_text = raw_ctx.decode("utf-8")
            except UnicodeDecodeError:
                context_text = raw_ctx.decode("latin-1", errors="replace")
            logger.info("Context file: %s (%d chars)", context_file.filename, len(context_text))

        items, prompt = bom_to_llm_input(bom_path, context=context_text)
        os.unlink(bom_path)
        logger.info("BOM parsed: %d services | context: %d chars", len(items), len(context_text))

        result = run_pipeline(items, prompt, diagram_name, client_id,
                              request_id, input_hash)

        if result["status"] == "ok":
            IDEMPOTENCY_CACHE[cache_key] = result

        return JSONResponse(status_code=200, content=result)

    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in /upload-bom: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/clarify")
async def clarify(req: ClarifyRequest):
    """
    Submit answers to clarification questions from /upload-bom or /generate.
    Re-runs the pipeline with answers appended to the original prompt.
    """
    request_id = str(uuid.uuid4())
    input_hash = compute_input_hash(req.answers or "")

    pending = PENDING_CLARIFY.get(req.client_id)
    if not pending:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No pending clarification for client_id '{req.client_id}'. "
                "Call /upload-bom or /generate first."
            ),
        )

    try:
        enriched_prompt = (
            pending["prompt"]
            + f"\n\nCLARIFICATION ANSWERS:\n{req.answers.strip()}\n\n"
            + "Now produce the layout spec JSON using the answers above. "
            + "Output ONLY valid JSON."
        )

        result = run_pipeline(
            items        = pending["items"],
            prompt       = enriched_prompt,
            diagram_name = req.diagram_name,
            client_id    = req.client_id,
            request_id   = request_id,
            input_hash   = input_hash,
        )

        if result["status"] == "ok":
            PENDING_CLARIFY.pop(req.client_id, None)

        return JSONResponse(status_code=200, content=result)

    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in /clarify: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/generate")
async def generate_from_resources(req: GenerateRequest):
    """Generate diagram from a pre-parsed resource list (JSON body)."""
    request_id = str(uuid.uuid4())

    deployment_hints = req.deployment_hints or {}
    input_hash = compute_input_hash(
        canonical_json(req.resources),
        "\n",
        req.context or "",
        "\n",
        canonical_json(deployment_hints),
    )

    # Idempotency check
    cache_key = (req.client_id, req.diagram_name, input_hash)
    if cache_key in IDEMPOTENCY_CACHE:
        return JSONResponse(status_code=200, content=IDEMPOTENCY_CACHE[cache_key])

    try:
        from agent.bom_parser import build_llm_prompt, ServiceItem
        items = [
            ServiceItem(
                id=r.get("id", r.get("oci_type", r.get("type", "svc")).replace(" ", "_")),
                oci_type=r.get("oci_type", r.get("type", "")),
                label=r.get("label", r.get("oci_type", r.get("type", ""))),
                layer=r.get("layer", "compute"),
            )
            for r in req.resources
        ]
        prompt = build_llm_prompt(items, context=req.context or "")

        result = run_pipeline(
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

    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in /generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat")
def chat(req: ChatRequest):
    """Free-form chat with the drawing agent."""
    try:
        result = call_llm(req.message, req.client_id)
        return {"response": str(result), "client_id": req.client_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/download/{filename}")
def download_file(
    filename:     str,
    client_id:    Optional[str] = Query(default=None),
    diagram_name: Optional[str] = Query(default=None),
):
    """
    Download a generated artifact.

    Requires query params: client_id, diagram_name
    Lookup order:
      1. Local OUTPUT_DIR
      2. Object store via LATEST.json (if app.state.object_store is set)
    Only filenames in ARTIFACT_ALLOWLIST are served from object store.
    """
    if not client_id or not diagram_name:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "MISSING_DOWNLOAD_SCOPE",
                "message":    "Query params client_id and diagram_name are required.",
            },
        )

    # ── Local lookup ───────────────────────────────────────────────────────────
    # Check exact filename first, then {diagram_name}.drawio as alias for diagram.drawio
    candidates = [OUTPUT_DIR / filename]
    if filename == "diagram.drawio":
        candidates.append(OUTPUT_DIR / f"{diagram_name}.drawio")

    for path in candidates:
        if path.exists():
            return FileResponse(str(path), filename=filename)

    # ── Object store fallback ──────────────────────────────────────────────────
    object_store = getattr(app.state, "object_store", None)
    if object_store is None:
        raise HTTPException(status_code=404, detail="File not found")

    # Map {diagram_name}.drawio → diagram.drawio for allowlist check
    artifact_name = filename
    if filename == f"{diagram_name}.drawio":
        artifact_name = "diagram.drawio"

    if artifact_name not in ARTIFACT_ALLOWLIST:
        raise HTTPException(
            status_code=403,
            detail=f"Filename '{artifact_name}' not in download allowlist.",
        )

    persistence_cfg = getattr(app.state, "persistence_config", None) or {}
    prefix          = persistence_cfg.get("prefix", "diagrams")
    latest_key      = f"{prefix}/{client_id}/{diagram_name}/LATEST.json"

    try:
        latest_raw  = object_store.get(latest_key)
        latest      = json.loads(latest_raw.decode("utf-8"))
        artifact_key = latest.get("artifacts", {}).get(artifact_name)
        if not artifact_key:
            raise HTTPException(status_code=404, detail=f"Artifact '{artifact_name}' not in LATEST.json")
        data         = object_store.get(artifact_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found (no LATEST.json for this scope)")

    content_type = "text/xml" if artifact_name.endswith(".drawio") else "application/json"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
def health():
    return {
        "status":                  "ok",
        "agent_version":           AGENT_VERSION,
        "agent":                   "oci-drawing-agent",
        "pending_clarifications":  list(PENDING_CLARIFY.keys()),
        "idempotency_cache_size":  len(IDEMPOTENCY_CACHE),
    }


@app.get("/mcp/tools")
def mcp_tools():
    return {"tools": [
        {
            "name": "upload_bom",
            "description": "Upload an Excel BOM and optional context file to generate an OCI architecture diagram.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file":             {"type": "string", "format": "binary"},
                    "context_file":     {"type": "string", "format": "binary"},
                    "context":          {"type": "string"},
                    "diagram_name":     {"type": "string"},
                    "client_id":        {"type": "string"},
                },
                "required": ["file"],
            },
        },
        {
            "name": "generate_diagram",
            "description": "Generate an OCI architecture diagram from a pre-parsed resource list.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resources":        {"type": "array"},
                    "context":          {"type": "string"},
                    "diagram_name":     {"type": "string"},
                    "client_id":        {"type": "string"},
                    "deployment_hints": {"type": "object"},
                },
                "required": ["resources"],
            },
        },
        {
            "name": "clarify",
            "description": "Submit answers to clarification questions returned by upload_bom.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "answers":      {"type": "string"},
                    "client_id":    {"type": "string"},
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
    ]}


@app.get("/mcp/tools/get_oci_catalogue")
def get_catalogue():
    return {"catalogue": get_catalogue_summary()}


@app.get("/.well-known/agent-card.json")
def agent_card():
    host = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8080")
    return JSONResponse({
        "schema_version": "1.0",
        "agent_version":  AGENT_VERSION,
        "name":           "OCI Drawing Agent",
        "description":    "Generates OCI architecture draw.io diagrams from a BOM Excel file.",
        "vendor":         "Oracle",
        "capabilities":   ["diagram-generation", "bom-parsing", "clarification-flow",
                           "multi-region", "object-storage-persistence"],
        "endpoints": {
            "upload_bom": {"path": "/upload-bom", "method": "POST"},
            "clarify":    {"path": "/clarify",    "method": "POST"},
            "generate":   {"path": "/generate",   "method": "POST"},
            "chat":       {"path": "/chat",        "method": "POST"},
            "tools":      {"path": "/mcp/tools",   "method": "GET"},
        },
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
