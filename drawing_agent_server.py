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

import asyncio
import dataclasses
import functools
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import urllib.parse
import urllib.request as _urlreq
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
import yaml
from contextlib import asynccontextmanager

# Load .env if present (development / non-systemd deployments)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

try:
    from oci.addons.adk import Agent, AgentClient
    _OCI_ADK_AVAILABLE = True
except ImportError:
    _OCI_ADK_AVAILABLE = False
    Agent = AgentClient = None

try:
    from agent.llm_inference_client import run_inference as _run_inference
    _INFERENCE_AVAILABLE = True
except Exception:
    _INFERENCE_AVAILABLE = False
    _run_inference = None  # type: ignore

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
from agent.document_store import (
    save_note,
    list_notes,
    list_versions,
    get_latest_doc,
    save_approved_doc,
    get_approved_doc,
    get_jep_questions,
    save_jep_questions,
)
from agent.pov_agent import generate_pov
from agent.jep_agent import generate_jep, kickoff_jep
from agent.context_store import read_context, write_context, record_agent_run

try:
    import server.services.oci_object_storage as _oci_storage
    _OCI_STORAGE_AVAILABLE = True
except Exception:
    _oci_storage = None  # type: ignore
    _OCI_STORAGE_AVAILABLE = False

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    _startup(application)
    yield


app = FastAPI(title="OCI Drawing Agent", version="1.3.2", lifespan=_lifespan)

# ── Config ─────────────────────────────────────────────────────────────────────
_cfg_path = Path(__file__).parent / "config.yaml"
with open(_cfg_path) as _f:
    _cfg = yaml.safe_load(_f)

REGION            = _cfg.get("region", "us-phoenix-1")
AGENT_ENDPOINT_ID = _cfg.get("agent_endpoint_id", "")
COMPARTMENT_ID    = _cfg.get("compartment_id", "")
MAX_STEPS         = _cfg.get("max_steps", 5)
OUTPUT_DIR        = Path(_cfg.get("output_dir", "/tmp/diagrams"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Inference config ────────────────────────────────────────────────────────
_inf_cfg               = _cfg.get("inference", {})
INFERENCE_ENABLED      = _inf_cfg.get("enabled", False)
INFERENCE_ENDPOINT     = _inf_cfg.get("service_endpoint", "")
INFERENCE_MODEL_ID     = _inf_cfg.get("model_id", "")
INFERENCE_MAX_TOKENS    = int(_inf_cfg.get("max_tokens", 2000))
INFERENCE_TEMPERATURE   = float(_inf_cfg.get("temperature", 0.0))
INFERENCE_TOP_P         = float(_inf_cfg.get("top_p", 0.9))
INFERENCE_TOP_K         = int(_inf_cfg.get("top_k", 0))
INFERENCE_SYSTEM_MSG    = _inf_cfg.get("system_message", "")

# ── Persistence config ───────────────────────────────────────────────────────
_per_cfg              = _cfg.get("persistence", {})
PERSISTENCE_ENABLED   = _per_cfg.get("enabled", False)
PERSISTENCE_BACKEND   = _per_cfg.get("backend", "")
PERSISTENCE_REGION    = _per_cfg.get("region", REGION)
PERSISTENCE_NAMESPACE = _per_cfg.get("namespace", "")
PERSISTENCE_BUCKET    = _per_cfg.get("bucket_name", "")
PERSISTENCE_PREFIX    = _per_cfg.get("prefix", "diagrams")

# ── Git push config ──────────────────────────────────────────────────────────
_git_cfg              = _cfg.get("git_push", {})
GIT_PUSH_ENABLED      = _git_cfg.get("enabled", False)
GIT_PUSH_REPO         = _git_cfg.get("repo_path", str(Path(__file__).parent))
GIT_PUSH_SUBDIR       = _git_cfg.get("output_subdir", "tests/fixtures/outputs")
GIT_PUSH_BRANCH       = _git_cfg.get("branch", "main")

# ── Writing agents config ────────────────────────────────────────────────────
_writing_cfg           = _cfg.get("writing", {})
WRITING_MAX_TOKENS     = int(_writing_cfg.get("max_tokens", 4000))
WRITING_TEMPERATURE    = float(_writing_cfg.get("temperature", 0.7))
WRITING_TOP_P          = float(_writing_cfg.get("top_p", 0.9))
WRITING_TOP_K          = int(_writing_cfg.get("top_k", 0))

# ── Terraform agent config ────────────────────────────────────────────────────
_terraform_cfg          = _cfg.get("terraform", {})
TERRAFORM_MODEL_ID      = _terraform_cfg.get("model_id", "") or INFERENCE_MODEL_ID
TERRAFORM_MAX_TOKENS    = int(_terraform_cfg.get("max_tokens", 4000))
TERRAFORM_TEMPERATURE   = float(_terraform_cfg.get("temperature", 0.2))
TERRAFORM_TOP_P         = float(_terraform_cfg.get("top_p", 0.9))
TERRAFORM_TOP_K         = int(_terraform_cfg.get("top_k", 0))
TERRAFORM_EXAMPLE_REPOS = _terraform_cfg.get("example_repos", [])

# ── Auth / session config — all from environment, matching BOM agent pattern ──
# Set these in .env (dev) or as systemd EnvironmentFile / OCI Vault (prod).
# Auth is automatically enabled when the four required OIDC vars are present.
OIDC_CLIENT_ID              = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET          = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_AUTHORIZATION_ENDPOINT = os.environ.get("OIDC_AUTHORIZATION_ENDPOINT", "")
OIDC_TOKEN_ENDPOINT         = os.environ.get("OIDC_TOKEN_ENDPOINT", "")
OIDC_USERINFO_ENDPOINT      = os.environ.get("OIDC_USERINFO_ENDPOINT", "")
OIDC_REDIRECT_URI           = os.environ.get("OIDC_REDIRECT_URI", "")
OIDC_LOGOUT_ENDPOINT        = os.environ.get("OIDC_LOGOUT_ENDPOINT", "")
OIDC_REQUIRED_GROUP         = os.environ.get("OIDC_REQUIRED_GROUP", "")
OIDC_SCOPE                  = os.environ.get("OIDC_SCOPE", "openid profile email")
_SESSION_SECRET             = os.environ.get("SESSION_SECRET", "dev-secret-change-in-prod")

AUTH_ENABLED = all([OIDC_CLIENT_ID, OIDC_CLIENT_SECRET,
                    OIDC_AUTHORIZATION_ENDPOINT, OIDC_TOKEN_ENDPOINT])

# ── Fleet identity ───────────────────────────────────────────────────────────
AGENT_ID    = _cfg.get("agent_id", "agent3-oci-drawing")
FLEET_CFG   = _cfg.get("fleet", {})

AGENT_VERSION  = "1.3.2"
SCHEMA_VERSION = {"spec": "1.1", "draw_dict": "1.0"}

# ── Session middleware (must be added before first request) ───────────────────
app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET, https_only=False)

# ── Global mutable state ───────────────────────────────────────────────────────
_oci_agent: Optional[Any] = None          # real OCI Agent, set in startup
SESSION_STORE:     Dict[str, str]  = {}   # client_id → session_id (ADK path only; unused on inference path)
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
    # Stateless path: client echoes these back from the need_clarification response.
    # When present, /clarify uses them directly instead of looking up PENDING_CLARIFY.
    items_json:   Optional[str] = None
    prompt:       Optional[str] = None


class GenerateRequest(BaseModel):
    resources:          List[Dict[str, Any]]
    context:            Optional[str]  = ""
    questionnaire:      Optional[str]  = ""   # answers to pre-flight questionnaire
    notes:              Optional[str]  = ""   # meeting or free-form notes
    diagram_name:       Optional[str]  = "oci_architecture"
    client_id:          Optional[str]  = "default"
    customer_id:        Optional[str]  = None  # fleet context linkage
    customer_name:      Optional[str]  = ""
    deployment_hints:   Optional[dict] = {}


class PovRequest(BaseModel):
    customer_id:   str
    customer_name: str
    feedback:      Optional[str] = None


class JepRequest(BaseModel):
    customer_id:   str
    customer_name: str
    feedback:      Optional[str] = None
    diagram_key:   Optional[str] = None
    diagram_url:   Optional[str] = None


class ApproveDocRequest(BaseModel):
    customer_id:   str
    customer_name: str
    content:       str


class JepKickoffRequest(BaseModel):
    customer_id:   str
    customer_name: str


class JepAnswersRequest(BaseModel):
    customer_id: str
    answers:     dict


class A2AObjectRef(BaseModel):
    """OCI Object Storage reference — used in A2A task inputs."""
    namespace:  Optional[str] = None
    bucket:     str
    object:     str
    version_id: Optional[str] = None


class A2ATask(BaseModel):
    """
    Incoming task from an orchestrator or peer agent.

    skill values:
      "generate_diagram"  — generate from a resource list (inline or bucket ref)
      "upload_bom"        — parse a BOM Excel from a bucket ref and generate
      "clarify_diagram"   — submit clarification answers for a pending request
    """
    task_id:      str
    skill:        str
    inputs:       Dict[str, Any] = {}
    client_id:    str = "default"


class A2AResponse(BaseModel):
    task_id:       str
    agent_id:      str
    status:        str                    # "ok" | "need_clarification" | "error"
    outputs:       Dict[str, Any] = {}
    error_message: Optional[str]  = None


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
    """
    Strip markdown code fences from LLM output.
    Handles: ```json ... ```, ``` ... ```, or plain JSON.
    """
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        s = s.strip()
    return s


def _make_oci_runner(oci_agent) -> callable:
    """Wrap a real OCI Agent as the llm_runner callable."""
    def _run(prompt: str, client_id: str) -> dict:
        # The OCI ADK has two conflicting asyncio requirements:
        #   1. asyncio.get_event_loop() — needs a loop registered in the thread
        #   2. asyncio.run()            — needs NO running loop in the thread
        # Running directly in an async context satisfies (1) but breaks (2).
        # Running in a bare anyio thread satisfies (2) but breaks (1) on Python 3.12.
        # Fix: register a fresh, never-started loop as the thread-local loop so
        # that get_event_loop() returns it, while asyncio.run() is still free to
        # create and drive its own loop (it checks for a *running* loop, not a set one).
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            session_id = SESSION_STORE.get(client_id)
            response = oci_agent.run(prompt, session_id=session_id, max_steps=MAX_STEPS)
            SESSION_STORE[client_id] = response.session_id
            raw = extract_agent_text(response)
            logger.info("LLM raw (%d chars): %s", len(raw), raw[:400])
            cleaned = clean_json(raw)
            if not cleaned.startswith("{"):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"LLM response did not produce valid JSON. "
                        f"Cleaned output starts with: {cleaned[:200]!r}"
                    ),
                )
            return json.loads(cleaned)
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    return _run


async def call_llm(prompt: str, client_id: str) -> dict:
    """
    Call the LLM via app.state.llm_runner and return a parsed JSON dict.

    Injection seam: tests set app.state.llm_runner before startup so no real
    OCI call is made.

    Runtime path (inference.enabled=true):
      The runner is a sync callable that calls run_inference(), strips fences
      with clean_json(), and returns json.loads(text).  It is offloaded to an
      anyio worker thread so the async event loop stays unblocked.

    Runtime path (inference.enabled=false, legacy ADK):
      Same offload pattern; _make_oci_runner wraps the ADK Agent.
    """
    runner = getattr(app.state, "llm_runner", None)
    if runner is None:
        raise RuntimeError(
            "LLM runner is not initialised. "
            "Ensure the server started successfully with OCI auth, "
            "or inject app.state.llm_runner in tests."
        )
    if asyncio.iscoroutinefunction(runner):
        return await runner(prompt, client_id)
    return await anyio.to_thread.run_sync(functools.partial(runner, prompt, client_id))


def _clarify_response(
    client_id: str,
    diagram_name: str,
    request_id: str,
    input_hash: str,
    questions: list,
    items: Optional[list] = None,
    prompt: str = "",
) -> dict:
    resp: dict = {
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
    # Include serialised context so the browser can echo it back on /clarify,
    # making the conversation stateless (no PENDING_CLARIFY look-up needed).
    if items is not None:
        resp["_clarify_context"] = {
            "items_json": json.dumps([dataclasses.asdict(i) for i in items]),
            "prompt":     prompt,
        }
    return resp


async def run_pipeline(
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

    Async design:
    - call_llm is awaited directly so the OCI ADK sees a running event loop.
    - CPU-bound and file-I/O steps are offloaded to anyio worker threads.
    """
    if deployment_hints is None:
        deployment_hints = {}

    spec = await call_llm(prompt, client_id)

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
            items=items,
            prompt=prompt,
        )

    # ── Option 1: LayoutIntent path ───────────────────────────────────────────
    # Detect LayoutIntent (has "placements" key) vs legacy/hierarchical full spec.
    # Legacy FakeLLMRunner tests return a full hierarchical spec (no "placements"),
    # so the old path is preserved for backward compatibility.
    if "placements" in spec:
        try:
            from agent.layout_intent import validate_layout_intent, LayoutIntentError
            from agent.intent_compiler import compile_intent_to_flat_spec

            _spec_ref = spec  # capture for closure

            def _compile_intent():
                intent = validate_layout_intent(_spec_ref, items)
                return compile_intent_to_flat_spec(intent, items)

            spec = await anyio.to_thread.run_sync(_compile_intent)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"LayoutIntent validation/compile error: {exc}",
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
            items=items,
            prompt=prompt,
        )

    # ── Layout engine (CPU-bound) — run in thread ─────────────────────────────
    items_by_id = {i.id: i for i in items}
    draw_dict = await anyio.to_thread.run_sync(
        functools.partial(spec_to_draw_dict, spec, items_by_id)
    )

    # ── Multi-region post-processing (in-memory dict ops) ─────────────────────
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

    # ── Write draw.io file (file I/O) — run in thread ─────────────────────────
    drawio_path = OUTPUT_DIR / f"{diagram_name}.drawio"
    await anyio.to_thread.run_sync(
        functools.partial(generate_drawio, draw_dict, drawio_path)
    )
    drawio_xml = await anyio.to_thread.run_sync(drawio_path.read_text)

    # ── Persist artifacts (network I/O) — run in thread ───────────────────────
    object_store     = getattr(app.state, "object_store", None)
    persistence_cfg  = getattr(app.state, "persistence_config", None) or {}
    prefix           = persistence_cfg.get("prefix", "diagrams")

    persisted_version = 0
    if object_store is not None:
        artifacts = {
            "diagram.drawio":          drawio_xml.encode("utf-8"),
            "spec.json":               json.dumps(spec).encode("utf-8"),
            "draw_dict.json":          json.dumps(draw_dict).encode("utf-8"),
            "render_manifest.json":    json.dumps(render_manifest).encode("utf-8"),
            "node_to_resource_map.json": json.dumps(node_to_resource_map).encode("utf-8"),
        }
        latest = await anyio.to_thread.run_sync(
            functools.partial(
                persist_artifacts,
                object_store, prefix, client_id, diagram_name, artifacts,
            )
        )
        if latest:
            persisted_version = latest.get("version", 0)

    if GIT_PUSH_ENABLED:
        threading.Thread(
            target=_push_diagram_to_git,
            args=(drawio_xml, client_id, diagram_name, persisted_version),
            daemon=True,
        ).start()

    return {
        "status":                "ok",
        "agent_version":         AGENT_VERSION,
        "schema_version":        SCHEMA_VERSION,
        "client_id":             client_id,
        "diagram_name":          diagram_name,
        "request_id":            request_id,
        "input_hash":            input_hash,
        "output_path":           str(drawio_path),
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


# ── Git push helper ─────────────────────────────────────────────────────────────
def _push_diagram_to_git(drawio_xml: str, client_id: str, diagram_name: str, version: int) -> None:
    """
    Write the diagram XML to the git repo and push to the configured branch.
    Runs in a daemon thread — failures are logged but never surface to the caller.

    Output path: {GIT_PUSH_REPO}/{GIT_PUSH_SUBDIR}/{client_id}/{diagram_name}.drawio
    Always overwrites the file so the latest output is always at a fixed path;
    git history preserves every version.
    """
    import subprocess

    try:
        repo     = Path(GIT_PUSH_REPO)
        out_dir  = repo / GIT_PUSH_SUBDIR / client_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{diagram_name}.drawio"
        out_file.write_text(drawio_xml, encoding="utf-8")

        subprocess.run(
            ["git", "-C", str(repo), "add", str(out_file.relative_to(repo))],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m",
             f"diagram: {client_id}/{diagram_name} v{version}"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "push", "origin", GIT_PUSH_BRANCH],
            check=True, capture_output=True,
        )
        logger.info(
            "Git push ok: %s/%s v%d → %s", client_id, diagram_name, version, GIT_PUSH_BRANCH
        )
    except Exception as exc:
        logger.warning("Git push failed (non-fatal): %s", exc)


# ── Startup ─────────────────────────────────────────────────────────────────────
def _make_text_runner() -> callable:
    """
    Build a sync text_runner for writing agents (POV / JEP).

    Unlike the JSON llm_runner, this returns raw LLM text — no JSON parsing.
    System message is passed per-call so each writing agent can supply its own.
    Uses higher max_tokens and temperature suitable for long-form documents.
    """
    def _run(prompt: str, system_message: str = "") -> str:
        return _run_inference(
            prompt,
            endpoint=INFERENCE_ENDPOINT,
            model_id=INFERENCE_MODEL_ID,
            compartment_id=COMPARTMENT_ID,
            max_tokens=WRITING_MAX_TOKENS,
            temperature=WRITING_TEMPERATURE,
            top_p=WRITING_TOP_P,
            top_k=WRITING_TOP_K,
            system_message=system_message,
        )
    return _run


def _make_inference_runner() -> callable:
    """
    Build a sync llm_runner that calls run_inference() directly.

    Memory model: stateless — no session ID, no conversation history.
    Each call sends exactly one USER message; the system_message establishes
    behavioural rules (JSON-only output) before the user prompt.

    Clarification rounds work without session memory because run_pipeline()
    rebuilds the full enriched prompt from scratch before each call:
        enriched_prompt = original_prompt + "\\n\\nCLARIFICATION ANSWERS:..." + instruction

    clean_json() strips fences; json.loads() converts to dict.
    Raises HTTP 422 if the model output is not parseable JSON.
    """
    def _run(prompt: str, client_id: str) -> dict:
        # client_id is accepted for interface compatibility with the ADK runner
        # but is unused — inference is stateless.
        raw = _run_inference(
            prompt,
            endpoint=INFERENCE_ENDPOINT,
            model_id=INFERENCE_MODEL_ID,
            compartment_id=COMPARTMENT_ID,
            max_tokens=INFERENCE_MAX_TOKENS,
            temperature=INFERENCE_TEMPERATURE,
            top_p=INFERENCE_TOP_P,
            top_k=INFERENCE_TOP_K,
            system_message=INFERENCE_SYSTEM_MSG,
        )
        cleaned = clean_json(raw)
        if not cleaned.startswith("{"):
            raise HTTPException(
                status_code=422,
                detail=(
                    "LLM response did not produce valid JSON. "
                    f"Cleaned output starts with: {cleaned[:200]!r}"
                ),
            )
        return json.loads(cleaned)

    return _run


def _startup(app: FastAPI) -> None:
    global _oci_agent

    # Allow tests (or other callers) to pre-inject llm_runner before startup.
    # If already set, skip OCI initialisation entirely.
    if getattr(app.state, "llm_runner", None) is not None:
        logger.info("llm_runner already injected — skipping OCI init")
        _ensure_state_defaults()
        return

    # ── Path 1: Direct OCI Inference (preferred) ──────────────────────────────
    if INFERENCE_ENABLED and _INFERENCE_AVAILABLE:
        try:
            app.state.llm_runner = _make_inference_runner()
            logger.info(
                "Drawing Agent ready (OCI inference) model=%s", INFERENCE_MODEL_ID
            )
            _ensure_state_defaults()
            return
        except Exception as exc:
            logger.warning(
                "OCI inference runner init failed (%s) — trying ADK fallback", exc
            )

    # ── Path 2: Legacy ADK Agent Endpoint ────────────────────────────────────
    if not _OCI_ADK_AVAILABLE:
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
        logger.info("Drawing Agent ready (ADK)!")
    except Exception as exc:
        logger.warning("OCI ADK init failed (%s) — llm_runner will be None", exc)
        app.state.llm_runner = None

    _ensure_state_defaults()


def _init_object_store() -> None:
    """
    Initialise app.state.object_store from config.
    Only called during startup when tests have NOT pre-injected a store.
    Tests always pre-set app.state.object_store (even to None) so this is skipped.
    """
    if not PERSISTENCE_ENABLED:
        app.state.object_store = None
        app.state.persistence_config = {}
        return

    if PERSISTENCE_BACKEND == "oci_object_storage":
        try:
            from agent.object_store_oci import OciObjectStore
            app.state.object_store = OciObjectStore(
                region=PERSISTENCE_REGION,
                namespace=PERSISTENCE_NAMESPACE,
                bucket_name=PERSISTENCE_BUCKET,
            )
            app.state.persistence_config = {"prefix": PERSISTENCE_PREFIX}
            logger.info(
                "OCI object store ready: bucket=%s namespace=%s prefix=%s",
                PERSISTENCE_BUCKET,
                PERSISTENCE_NAMESPACE,
                PERSISTENCE_PREFIX,
            )
        except Exception as exc:
            logger.warning(
                "OCI object store init failed (%s) — persistence disabled", exc
            )
            app.state.object_store = None
            app.state.persistence_config = {}
    else:
        logger.warning(
            "Unknown persistence backend %r — persistence disabled", PERSISTENCE_BACKEND
        )
        app.state.object_store = None
        app.state.persistence_config = {}


def _ensure_state_defaults() -> None:
    # If tests (or earlier startup paths) have already set the object_store,
    # respect that choice; only fill in defaults for attributes not yet set.
    if not hasattr(app.state, "object_store"):
        _init_object_store()
    if getattr(app.state, "persistence_config", None) is None:
        app.state.persistence_config = {"prefix": PERSISTENCE_PREFIX}
    # Writing agent text_runner — separate from the JSON llm_runner
    if not hasattr(app.state, "text_runner"):
        if INFERENCE_ENABLED and _INFERENCE_AVAILABLE:
            try:
                app.state.text_runner = _make_text_runner()
                logger.info("Text runner ready (writing agents)")
            except Exception as exc:
                logger.warning("Text runner init failed (%s) — writing agents disabled", exc)
                app.state.text_runner = None
        else:
            app.state.text_runner = None


# ── OIDC helpers ─────────────────────────────────────────────────────────────
# OCI Identity Domain exposes explicit endpoints — no discovery document needed.
# Endpoints are read directly from environment variables at startup.

def _exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens (sync, run in thread)."""
    data = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  OIDC_REDIRECT_URI,
        "client_id":     OIDC_CLIENT_ID,
        "client_secret": OIDC_CLIENT_SECRET,
    }).encode()
    req = _urlreq.Request(OIDC_TOKEN_ENDPOINT, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with _urlreq.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _fetch_userinfo(access_token: str) -> dict:
    """Fetch user profile from the OIDC userinfo endpoint (sync, run in thread)."""
    req = _urlreq.Request(OIDC_USERINFO_ENDPOINT)
    req.add_header("Authorization", f"Bearer {access_token}")
    with _urlreq.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


async def require_user(request: Request) -> dict:
    """
    FastAPI dependency — returns the session user dict or raises HTTP 401.
    When auth is disabled (AUTH_ENABLED=False), returns a dummy local user
    so all endpoints work without modification.
    """
    if not AUTH_ENABLED:
        return {"email": "local", "name": "Local User"}
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required. Visit /login.")
    return user


# ── Auth routes ───────────────────────────────────────────────────────────────

_UI_DIST = Path(__file__).parent / "ui" / "dist"
_UI_INDEX = _UI_DIST / "index.html"
_LEGACY_INDEX = Path(__file__).parent / "index.html"

# Mount built React assets so /assets/... requests are served correctly.
_UI_ASSETS = _UI_DIST / "assets"
if _UI_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=_UI_ASSETS), name="ui_assets")


@app.get("/")
async def serve_ui(request: Request):
    """Serve the React SPA. Falls back to legacy index.html when dist not built."""
    if AUTH_ENABLED and not request.session.get("user"):
        return RedirectResponse("/login", status_code=302)
    if _UI_INDEX.exists():
        return FileResponse(str(_UI_INDEX))
    return FileResponse(str(_LEGACY_INDEX))


@app.get("/login")
async def login(request: Request):
    """Initiate OIDC authorization code flow."""
    if not AUTH_ENABLED:
        return RedirectResponse("/", status_code=302)
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id":     OIDC_CLIENT_ID,
        "redirect_uri":  OIDC_REDIRECT_URI,
        "scope":         OIDC_SCOPE,
        "state":         state,
    }
    if not OIDC_AUTHORIZATION_ENDPOINT:
        raise HTTPException(503, "OIDC_AUTHORIZATION_ENDPOINT is not set — check your .env")
    return RedirectResponse(f"{OIDC_AUTHORIZATION_ENDPOINT}?{urllib.parse.urlencode(params)}")


@app.get("/oauth2/callback")
async def oauth2_callback(
    request: Request,
    code: Optional[str]  = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """OIDC callback — exchange code for tokens, store user in session."""
    if error:
        return HTMLResponse(f"<h3>Auth error: {error}</h3><a href='/login'>Try again</a>", status_code=400)
    if not code or state != request.session.pop("oauth_state", None):
        return HTMLResponse("<h3>Invalid or expired state.</h3><a href='/login'>Try again</a>", status_code=400)

    try:
        tokens   = await anyio.to_thread.run_sync(functools.partial(_exchange_code, code))
        userinfo = await anyio.to_thread.run_sync(
            functools.partial(_fetch_userinfo, tokens.get("access_token", ""))
        )
    except Exception as exc:
        logger.error("OIDC token exchange failed: %s", exc)
        return HTMLResponse(f"<h3>Token exchange failed.</h3><pre>{exc}</pre><a href='/login'>Retry</a>", status_code=502)

    # Optional group membership check
    if OIDC_REQUIRED_GROUP:
        groups = userinfo.get("groups", [])
        if OIDC_REQUIRED_GROUP not in groups:
            return HTMLResponse(
                f"<h3>Access denied.</h3>"
                f"<p>You must be a member of group <code>{OIDC_REQUIRED_GROUP}</code>.</p>"
                f"<a href='/logout'>Sign out</a>",
                status_code=403,
            )

    request.session["user"] = {
        "email": userinfo.get("email", ""),
        "name":  userinfo.get("name") or userinfo.get("email", "unknown"),
    }
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    """Clear session. If OIDC_LOGOUT_ENDPOINT is set, redirect there (IdP single logout)."""
    request.session.clear()
    if AUTH_ENABLED and OIDC_LOGOUT_ENDPOINT:
        params = {"post_logout_redirect_uri": OIDC_REDIRECT_URI.rsplit("/oauth2/callback", 1)[0] + "/login"}
        return RedirectResponse(f"{OIDC_LOGOUT_ENDPOINT}?{urllib.parse.urlencode(params)}", status_code=302)
    return RedirectResponse("/login" if AUTH_ENABLED else "/", status_code=302)


# ── Endpoints ───────────────────────────────────────────────────────────────────

@app.post("/upload-to-bucket")
@app.post("/api/upload-to-bucket")
async def upload_to_bucket(
    file:        UploadFile = File(...),
    customer_id: str        = Form(...),
    bom_type:    str        = Form(default="main"),
):
    """
    Upload a file (BOM or context) to OCI Object Storage.

    bom_type controls the bucket prefix:
      main (default) → agent3/{customer_id}/{filename}
      poc            → agent3/{customer_id}/poc/{filename}

    Called by the browser UI drag-and-drop before triggering diagram generation.
    """
    object_store = getattr(app.state, "object_store", None)
    if object_store is None:
        raise HTTPException(503, "OCI Object Storage not available on this server")

    content  = await file.read()
    filename = file.filename or "upload.xlsx"
    cid = customer_id.strip()

    if bom_type == "poc":
        object_key = f"agent3/{cid}/poc/{filename}"
    else:
        object_key = f"agent3/{cid}/{filename}"

    content_type = file.content_type or "application/octet-stream"

    await anyio.to_thread.run_sync(
        functools.partial(object_store.put, object_key, content, content_type)
    )
    logger.info("upload-to-bucket: wrote %s (%d bytes)", object_key, len(content))
    return {"object_key": object_key, "filename": filename, "size": len(content), "bom_type": bom_type}


@app.post("/upload-bom")
@app.post("/api/upload-bom")
async def upload_bom(
    file:         UploadFile = File(...),
    context_file: UploadFile = File(None),
    context:      str        = Form(default=""),
    diagram_name: str        = Form(default="oci_architecture"),
    client_id:    str        = Form(default="default"),
    _user:        dict       = Depends(require_user),
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

        items, prompt = await anyio.to_thread.run_sync(
            functools.partial(bom_to_llm_input, bom_path, context=context_text)
        )
        await anyio.to_thread.run_sync(functools.partial(os.unlink, bom_path))
        logger.info("BOM parsed: %d services | context: %d chars", len(items), len(context_text))

        result = await run_pipeline(items, prompt, diagram_name, client_id,
                                    request_id, input_hash)

        if result["status"] == "ok":
            IDEMPOTENCY_CACHE[cache_key] = result

        return JSONResponse(status_code=200, content=result)

    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in /upload-bom: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/clarify")
@app.post("/api/clarify")
async def clarify(req: ClarifyRequest, _user: dict = Depends(require_user)):
    """
    Submit answers to clarification questions from /upload-bom or /generate.
    Re-runs the pipeline with answers appended to the original prompt.

    Stateless path (preferred for browser clients):
      The browser echoes back items_json + prompt from the _clarify_context
      field of the need_clarification response.  No server-side state needed.

    Stateful fallback (A2A / legacy):
      If items_json/prompt are absent, falls back to PENDING_CLARIFY lookup
      keyed by client_id.
    """
    request_id = str(uuid.uuid4())
    input_hash = compute_input_hash(req.answers or "")

    try:
        # ── Stateless path ────────────────────────────────────────────────────
        if req.items_json and req.prompt:
            from agent.bom_parser import ServiceItem
            raw_items = json.loads(req.items_json)
            items = [ServiceItem(**r) for r in raw_items]
            base_prompt = req.prompt
        else:
            # ── Stateful fallback ─────────────────────────────────────────────
            pending = PENDING_CLARIFY.get(req.client_id)
            if not pending:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"No pending clarification for client_id '{req.client_id}'. "
                        "Call /upload-bom or /generate first."
                    ),
                )
            items      = pending["items"]
            base_prompt = pending["prompt"]

        enriched_prompt = (
            base_prompt
            + f"\n\nCLARIFICATION ANSWERS:\n{req.answers.strip()}\n\n"
            + "Now produce the layout spec JSON using the answers above. "
            + "Output ONLY valid JSON."
        )

        result = await run_pipeline(
            items        = items,
            prompt       = enriched_prompt,
            diagram_name = req.diagram_name,
            client_id    = req.client_id,
            request_id   = request_id,
            input_hash   = input_hash,
        )

        if result["status"] == "ok":
            PENDING_CLARIFY.pop(req.client_id, None)

        return JSONResponse(status_code=200, content=result)

    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in /clarify: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/generate")
@app.post("/api/generate")
async def generate_from_resources(req: GenerateRequest, _user: dict = Depends(require_user)):
    """Generate diagram from a pre-parsed resource list (JSON body)."""
    request_id = str(uuid.uuid4())

    deployment_hints = req.deployment_hints or {}

    # Compose context_total deterministically: base context + questionnaire + notes
    context_total = req.context or ""
    if req.questionnaire and req.questionnaire.strip():
        context_total += f"\n\nQUESTIONNAIRE:\n{req.questionnaire}"
    if req.notes and req.notes.strip():
        context_total += f"\n\nNOTES:\n{req.notes}"

    input_hash = compute_input_hash(
        canonical_json(req.resources),
        "\n",
        context_total,
        "\n",
        canonical_json(deployment_hints),
    )

    # Validate and build ServiceItems before idempotency check so type errors surface fast
    from agent.bom_parser import build_layout_intent_prompt, ServiceItem
    items = []
    for r in req.resources:
        otype = r.get("oci_type") or r.get("type")
        if not otype:
            raise HTTPException(
                status_code=422,
                detail="resource missing oci_type/type",
            )
        items.append(ServiceItem(
            id=r.get("id", otype.replace(" ", "_")),
            oci_type=otype,
            label=r.get("label", otype),
            layer=r.get("layer", "compute"),
        ))

    # Idempotency check
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
        logger.error("Error in /generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat")
async def chat(req: ChatRequest, _user: dict = Depends(require_user)):
    """Free-form chat with the drawing agent."""
    try:
        result = call_llm(req.message, req.client_id)
        return {"response": str(result), "client_id": req.client_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/download/{filename}")
@app.get("/api/download/{filename}")
async def download_file(
    filename:     str,
    client_id:    Optional[str] = Query(default=None),
    diagram_name: Optional[str] = Query(default=None),
    _user:        dict          = Depends(require_user),
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


@app.get("/config")
@app.get("/api/config")
def get_config():
    """Return UI configuration (region, model info). No secrets exposed."""
    return {
        "region":           REGION,
        "agent_version":    AGENT_VERSION,
        "default_model_id": INFERENCE_MODEL_ID,
        "models": [
            {"id": INFERENCE_MODEL_ID, "name": "OCI GenAI (Inference)"},
        ] if INFERENCE_MODEL_ID else [],
    }


@app.post("/refresh-data")
def refresh_data(_user: dict = Depends(require_user)):
    """
    Reload the LLM runner and text runner in a background thread.
    Returns immediately; the reload happens asynchronously.
    Useful after updating config.yaml or cycling OCI credentials.
    """
    def _reload():
        logger.info("/refresh-data: reloading runners")
        app.state.llm_runner  = None
        app.state.text_runner = None
        _startup(app)
        logger.info("/refresh-data: reload complete")

    threading.Thread(target=_reload, daemon=True).start()
    return {"status": "refreshing", "agent_version": AGENT_VERSION}


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
                    "questionnaire":    {"type": "string", "description": "Answers to pre-flight questionnaire"},
                    "notes":            {"type": "string", "description": "Meeting notes or free-form context"},
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


# ── Agent card (A2A discovery) ───────────────────────────────────────────────

def _build_agent_card(host: str) -> dict:
    """
    Build the agent card dict.  Served at both the Google A2A well-known URL
    (/.well-known/agent.json) and the legacy alias (/.well-known/agent-card.json).

    Schema follows the Google A2A Agent Card specification (v0.1).
    Orchestrators should use /.well-known/agent.json.
    """
    _obj_ref_schema = {
        "type": "object",
        "required": ["bucket", "object"],
        "properties": {
            "namespace":  {"type": "string"},
            "bucket":     {"type": "string"},
            "object":     {"type": "string"},
            "version_id": {"type": "string"},
        },
    }
    return {
        "schema_version": "0.1",
        "agent_id":       AGENT_ID,
        "name":           "OCI Drawing Agent",
        "description": (
            "Generates OCI architecture draw.io diagrams from a Bill of Materials "
            "or resource list. Part of the OCI Agent Fleet (Agent 3 of 7)."
        ),
        "version": AGENT_VERSION,
        "url":     f"{host}/api/a2a/task",
        "fleet": {
            "fleet_id":     FLEET_CFG.get("fleet_id", "oci-agent-fleet"),
            "position":     FLEET_CFG.get("position", 3),
            "total_agents": FLEET_CFG.get("total_agents", 7),
            "upstream":     FLEET_CFG.get("upstream",   ["agent2-bom-sizing"]),
            "downstream":   FLEET_CFG.get("downstream", ["agent4-sizing-validation"]),
        },
        "capabilities": {
            "clarification_flow":     True,   # may return need_clarification; call clarify_diagram
            "streaming":              False,
            "push_notifications":     False,
            "object_storage_inputs":  True,   # accepts *_from_bucket ObjectRef inputs
        },
        "skills": [
            {
                "id":          "generate_diagram",
                "name":        "Generate Architecture Diagram",
                "description": (
                    "Generate a draw.io OCI architecture diagram from a resource list. "
                    "Accepts inline resources[] or an OCI bucket reference. "
                    "May return need_clarification — call clarify_diagram to continue."
                ),
                "input_schema": {
                    "type": "object",
                    "oneOf": [
                        {"required": ["resources"]},
                        {"required": ["resources_from_bucket"]},
                    ],
                    "properties": {
                        "resources":             {"type": "array",  "items": {"type": "object"}},
                        "resources_from_bucket": _obj_ref_schema,
                        "context":               {"type": "string"},
                        "context_from_bucket":   _obj_ref_schema,
                        "questionnaire":         {"type": "string"},
                        "notes":                 {"type": "string"},
                        "deployment_hints":      {"type": "object"},
                        "diagram_name":          {"type": "string", "default": "oci_architecture"},
                    },
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "status":          {"type": "string", "enum": ["ok", "need_clarification"]},
                        "request_id":      {"type": "string"},
                        "input_hash":      {"type": "string"},
                        "drawio_xml":      {"type": "string"},
                        "render_manifest": {"type": "object"},
                        "questions":       {"type": "array",  "description": "Present when status=need_clarification"},
                        "download":        {"type": "object"},
                    },
                },
            },
            {
                "id":          "upload_bom",
                "name":        "Upload BOM from Bucket",
                "description": (
                    "Parse an Excel BOM stored in OCI Object Storage and generate a diagram. "
                    "Agent 2 should PUT the BOM to the shared bucket and pass the reference here. "
                    "May return need_clarification."
                ),
                "input_schema": {
                    "type": "object",
                    "required": ["bom_from_bucket"],
                    "properties": {
                        "bom_from_bucket": _obj_ref_schema,
                        "context":         {"type": "string"},
                        "diagram_name":    {"type": "string", "default": "oci_architecture"},
                    },
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "status":     {"type": "string", "enum": ["ok", "need_clarification"]},
                        "request_id": {"type": "string"},
                        "drawio_xml": {"type": "string"},
                        "questions":  {"type": "array"},
                        "download":   {"type": "object"},
                    },
                },
            },
            {
                "id":          "clarify_diagram",
                "name":        "Submit Clarification Answers",
                "description": (
                    "Resume a pending diagram generation by providing answers to "
                    "clarification questions. Use the same client_id and diagram_name "
                    "as the original generate_diagram or upload_bom call."
                ),
                "input_schema": {
                    "type": "object",
                    "required": ["answers", "diagram_name"],
                    "properties": {
                        "answers":      {"type": "string", "description": "Free-text answers to the questions"},
                        "diagram_name": {"type": "string"},
                    },
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "status":     {"type": "string", "enum": ["ok", "need_clarification"]},
                        "request_id": {"type": "string"},
                        "drawio_xml": {"type": "string"},
                        "questions":  {"type": "array"},
                        "download":   {"type": "object"},
                    },
                },
            },
        ],
        "authentication": {
            "schemes": ["none"],
            "note": (
                "Internal OCI network only. "
                "Backend uses Instance Principal auth; no bearer token required from orchestrator."
            ),
        },
        "health_check_url": f"{host}/api/health",
    }


@app.get("/.well-known/agent.json")          # Google A2A spec primary URL
@app.get("/.well-known/agent-card.json")      # legacy alias — keep for backward compat
def agent_card():
    host = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8000")
    return JSONResponse(_build_agent_card(host))


# ── A2A task endpoint ────────────────────────────────────────────────────────

@app.post("/api/a2a/task", response_model=A2AResponse)
async def a2a_task(task: A2ATask) -> A2AResponse:
    """
    Receive a task from an orchestrator or peer agent and dispatch to the
    appropriate skill handler.

    Skill routing:
      generate_diagram  → _a2a_generate_diagram()
      upload_bom        → _a2a_upload_bom()
      clarify_diagram   → _a2a_clarify()

    All errors are returned as A2AResponse(status="error") — the orchestrator
    should inspect error_message; it never receives an HTTP 4xx/5xx for
    expected failure modes.
    """
    _SKILLS = {
        "generate_diagram": _a2a_generate_diagram,
        "upload_bom":       _a2a_upload_bom,
        "clarify_diagram":  _a2a_clarify,
    }
    handler = _SKILLS.get(task.skill)
    if handler is None:
        return A2AResponse(
            task_id=task.task_id,
            agent_id=AGENT_ID,
            status="error",
            error_message=(
                f"Unknown skill {task.skill!r}. "
                f"Available: {list(_SKILLS)}"
            ),
        )
    try:
        result = await handler(task)
        return A2AResponse(
            task_id=task.task_id,
            agent_id=AGENT_ID,
            status=result.get("status", "error"),
            outputs=result,
        )
    except HTTPException as exc:
        return A2AResponse(
            task_id=task.task_id,
            agent_id=AGENT_ID,
            status="error",
            error_message=str(exc.detail),
        )
    except Exception as exc:
        logger.error("A2A task %s skill=%s error: %s", task.task_id, task.skill, exc)
        return A2AResponse(
            task_id=task.task_id,
            agent_id=AGENT_ID,
            status="error",
            error_message=str(exc),
        )


# ── A2A skill handlers ───────────────────────────────────────────────────────

async def _a2a_generate_diagram(task: A2ATask) -> dict:
    """
    generate_diagram skill.
    Accepts inline resources[] or resources_from_bucket ObjectRef.
    Delegates to the existing /generate pipeline.
    """
    inp          = task.inputs
    diagram_name = inp.get("diagram_name", "oci_architecture")
    request_id   = str(uuid.uuid4())
    deployment_hints = inp.get("deployment_hints") or {}

    # ── Resolve resources ────────────────────────────────────────────────────
    if "resources_from_bucket" in inp and inp["resources_from_bucket"]:
        ref = A2AObjectRef(**inp["resources_from_bucket"])
        raw_resources = await _a2a_fetch_resources(ref)
    elif "resources" in inp:
        raw_resources = inp["resources"]
    else:
        raise HTTPException(422, "generate_diagram requires 'resources' or 'resources_from_bucket'")

    # ── Resolve optional text fields ─────────────────────────────────────────
    context = inp.get("context") or ""
    if "context_from_bucket" in inp and inp["context_from_bucket"]:
        ref = A2AObjectRef(**inp["context_from_bucket"])
        context = await _a2a_fetch_text(ref)

    questionnaire = inp.get("questionnaire") or ""
    notes         = inp.get("notes") or ""
    context_total = context
    if questionnaire.strip():
        context_total += f"\n\nQUESTIONNAIRE:\n{questionnaire}"
    if notes.strip():
        context_total += f"\n\nNOTES:\n{notes}"

    # ── Build ServiceItems ───────────────────────────────────────────────────
    from agent.bom_parser import build_layout_intent_prompt, ServiceItem
    items = []
    for r in raw_resources:
        otype = r.get("oci_type") or r.get("type")
        if not otype:
            raise HTTPException(422, f"resource missing oci_type/type: {r}")
        items.append(ServiceItem(
            id=r.get("id", otype.replace(" ", "_")),
            oci_type=otype,
            label=r.get("label", otype),
            layer=r.get("layer", "compute"),
        ))

    input_hash = compute_input_hash(
        canonical_json(raw_resources), "\n", context_total, "\n", canonical_json(deployment_hints)
    )
    cache_key = (task.client_id, diagram_name, input_hash)
    if cache_key in IDEMPOTENCY_CACHE:
        return IDEMPOTENCY_CACHE[cache_key]

    prompt = build_layout_intent_prompt(items, context=context_total)
    result = await run_pipeline(items, prompt, diagram_name, task.client_id,
                                request_id, input_hash, deployment_hints=deployment_hints)
    if result["status"] == "ok":
        IDEMPOTENCY_CACHE[cache_key] = result
    return result


async def _a2a_upload_bom(task: A2ATask) -> dict:
    """
    upload_bom skill.
    Agent 2 stores the BOM Excel in OCI Object Storage and passes the reference.
    Fetches the file server-side, parses it, runs the pipeline.
    """
    inp = task.inputs
    if "bom_from_bucket" not in inp or not inp["bom_from_bucket"]:
        raise HTTPException(422, "upload_bom requires 'bom_from_bucket' ObjectRef")
    if not _OCI_STORAGE_AVAILABLE:
        raise HTTPException(503, "OCI Object Storage client not available on this server")

    ref      = A2AObjectRef(**inp["bom_from_bucket"])
    context  = inp.get("context") or ""
    diagram_name = inp.get("diagram_name", "oci_architecture")
    request_id   = str(uuid.uuid4())

    # Fetch BOM bytes from OCI bucket
    bom_bytes: bytes = await anyio.to_thread.run_sync(
        functools.partial(
            _oci_storage.fetch_object,
            ref.bucket, ref.object, ref.namespace, ref.version_id,
        )
    )
    input_hash = compute_input_hash(hashlib.sha256(bom_bytes).hexdigest())

    cache_key = (task.client_id, diagram_name, input_hash)
    if cache_key in IDEMPOTENCY_CACHE:
        return IDEMPOTENCY_CACHE[cache_key]

    # Write to temp file and parse
    suffix = Path(ref.object).suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(bom_bytes)
        bom_path = tmp.name

    items, prompt = await anyio.to_thread.run_sync(
        functools.partial(bom_to_llm_input, bom_path, context=context)
    )
    await anyio.to_thread.run_sync(functools.partial(os.unlink, bom_path))

    result = await run_pipeline(items, prompt, diagram_name, task.client_id,
                                request_id, input_hash)
    if result["status"] == "ok":
        IDEMPOTENCY_CACHE[cache_key] = result
        # Mirror diagram back alongside the source BOM so it's easy to find.
        # BOM path:    agent3/maurtis/oci_bom_priced.xlsx
        # Output path: agent3/maurtis/diagram.drawio
        object_store = getattr(app.state, "object_store", None)
        if object_store is not None:
            bom_folder = str(Path(ref.object).parent)
            bom_output_key = f"{bom_folder}/diagram.drawio"
            try:
                await anyio.to_thread.run_sync(
                    functools.partial(
                        object_store.put,
                        bom_output_key,
                        result["drawio_xml"].encode("utf-8"),
                        "text/xml",
                    )
                )
                logger.info("upload_bom: mirrored diagram to %s", bom_output_key)
                result["bom_folder_output"] = bom_output_key
            except Exception as mirror_exc:
                logger.warning("upload_bom: mirror to %s failed: %s", bom_output_key, mirror_exc)
    return result


async def _a2a_clarify(task: A2ATask) -> dict:
    """
    clarify_diagram skill.
    Continues a pending clarification started by generate_diagram or upload_bom.
    The orchestrator must use the same client_id (from the A2ATask) that it used
    in the original request.
    """
    inp          = task.inputs
    answers      = inp.get("answers") or ""
    diagram_name = inp.get("diagram_name", "oci_architecture")
    request_id   = str(uuid.uuid4())
    input_hash   = compute_input_hash(answers)

    pending = PENDING_CLARIFY.get(task.client_id)
    if not pending:
        raise HTTPException(
            404,
            f"No pending clarification for client_id={task.client_id!r}. "
            "Call generate_diagram or upload_bom first.",
        )

    enriched = (
        pending["prompt"]
        + f"\n\nCLARIFICATION ANSWERS:\n{answers.strip()}\n\n"
        + "Now produce the layout spec JSON. Output ONLY valid JSON."
    )
    result = await run_pipeline(
        pending["items"], enriched, diagram_name,
        task.client_id, request_id, input_hash,
    )
    if result["status"] == "ok":
        PENDING_CLARIFY.pop(task.client_id, None)
    return result


async def _a2a_fetch_resources(ref: A2AObjectRef) -> List[Dict[str, Any]]:
    """Fetch a JSON resources array from OCI Object Storage."""
    if not _OCI_STORAGE_AVAILABLE:
        raise HTTPException(503, "OCI Object Storage client not available")
    data: bytes = await anyio.to_thread.run_sync(
        functools.partial(_oci_storage.fetch_object, ref.bucket, ref.object,
                          ref.namespace, ref.version_id)
    )
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(422, f"resources_from_bucket: invalid JSON: {exc}")
    if not isinstance(parsed, list):
        raise HTTPException(422, "resources_from_bucket: JSON root must be an array")
    return parsed


async def _a2a_fetch_text(ref: A2AObjectRef) -> str:
    """Fetch a UTF-8 text object from OCI Object Storage."""
    if not _OCI_STORAGE_AVAILABLE:
        raise HTTPException(503, "OCI Object Storage client not available")
    data: bytes = await anyio.to_thread.run_sync(
        functools.partial(_oci_storage.fetch_object, ref.bucket, ref.object,
                          ref.namespace, ref.version_id)
    )
    return data.decode("utf-8")


# ── Writing-agent helpers ────────────────────────────────────────────────────

async def call_text_llm(prompt: str, system_message: str = "") -> str:
    """
    Async wrapper for the text_runner (writing agents).
    Runs the sync runner in a worker thread so the event loop stays unblocked.
    """
    runner = getattr(app.state, "text_runner", None)
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Writing agent text runner is not initialised. "
                "Ensure inference.enabled=true in config.yaml and the server "
                "started with valid OCI credentials, or inject "
                "app.state.text_runner in tests."
            ),
        )
    if asyncio.iscoroutinefunction(runner):
        return await runner(prompt, system_message)
    return await anyio.to_thread.run_sync(
        functools.partial(runner, prompt, system_message)
    )


def _require_object_store():
    """Return the object store or raise 503 if not configured."""
    store = getattr(app.state, "object_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Object store is not initialised. "
                "Set persistence.enabled=true in config.yaml or inject "
                "app.state.object_store in tests."
            ),
        )
    return store


# ── Notes endpoints ──────────────────────────────────────────────────────────

@app.post("/api/notes/upload")
async def upload_note(
    customer_id: str        = Form(...),
    note_name:   str        = Form(default=""),
    file:        UploadFile = File(...),
):
    """
    Upload a meeting notes file for a customer.

    Stores to: notes/{customer_id}/{note_name}
    Updates:   notes/{customer_id}/MANIFEST.json
    """
    store = _require_object_store()
    name = note_name.strip() or (file.filename or "note.txt")
    content_bytes = await file.read()
    content_type  = file.content_type or "text/plain"
    try:
        key = await anyio.to_thread.run_sync(
            functools.partial(save_note, store, customer_id, name, content_bytes, content_type)
        )
        return {"status": "ok", "key": key, "customer_id": customer_id, "note_name": name}
    except Exception as exc:
        logger.error("Error in /notes/upload: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/notes/{customer_id}")
async def list_customer_notes(customer_id: str):
    """List all notes for a customer."""
    store = _require_object_store()
    try:
        notes = await anyio.to_thread.run_sync(
            functools.partial(list_notes, store, customer_id)
        )
        return {"status": "ok", "customer_id": customer_id, "notes": notes}
    except Exception as exc:
        logger.error("Error in /notes/%s: %s", customer_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── POV endpoints ────────────────────────────────────────────────────────────

@app.post("/api/pov/generate")
async def pov_generate(req: PovRequest):
    """
    Generate or update a Point of View document for a customer.

    Reads all notes from notes/{customer_id}/ and the previous POV version
    (if any), then uses the LLM to produce an updated draft.

    Saves to: pov/{customer_id}/v{n}.md + LATEST.md
    """
    store = _require_object_store()

    def _run_pov():
        def runner(prompt, system_message=""):
            from agent.llm_inference_client import run_inference as _ri
            return _ri(
                prompt,
                endpoint=INFERENCE_ENDPOINT,
                model_id=INFERENCE_MODEL_ID,
                compartment_id=COMPARTMENT_ID,
                max_tokens=WRITING_MAX_TOKENS,
                temperature=WRITING_TEMPERATURE,
                top_p=WRITING_TOP_P,
                top_k=WRITING_TOP_K,
                system_message=system_message,
            )
        text_runner = getattr(app.state, "text_runner", None) or runner
        return generate_pov(
            req.customer_id, req.customer_name, store, text_runner,
            feedback=req.feedback or "",
        )

    try:
        result = await anyio.to_thread.run_sync(_run_pov)
        return {
            "status":        "ok",
            "agent_version": AGENT_VERSION,
            "customer_id":   req.customer_id,
            "doc_type":      "pov",
            "version":       result["version"],
            "key":           result["key"],
            "latest_key":    result["latest_key"],
            "content":       result["content"],
            "errors":        [],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error in /pov/generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/pov/{customer_id}/latest")
async def pov_latest(customer_id: str):
    """Return the latest POV document for a customer."""
    store = _require_object_store()
    content = await anyio.to_thread.run_sync(
        functools.partial(get_latest_doc, store, "pov", customer_id)
    )
    if content is None:
        raise HTTPException(status_code=404, detail=f"No POV found for customer_id={customer_id!r}")
    return {"status": "ok", "customer_id": customer_id, "doc_type": "pov", "content": content}


@app.get("/api/pov/{customer_id}/versions")
async def pov_versions(customer_id: str):
    """List all POV versions for a customer."""
    store = _require_object_store()
    versions = await anyio.to_thread.run_sync(
        functools.partial(list_versions, store, "pov", customer_id)
    )
    return {"status": "ok", "customer_id": customer_id, "doc_type": "pov", "versions": versions}


# ── JEP endpoints ────────────────────────────────────────────────────────────

@app.post("/api/jep/generate")
async def jep_generate(req: JepRequest):
    """
    Generate or update a Joint Execution Plan for a customer.

    Reads all notes from notes/{customer_id}/, the previous JEP (if any),
    runs the stub BOM generator, references the latest diagram from the bucket,
    then uses the LLM to produce an updated JEP draft.

    Saves to: jep/{customer_id}/v{n}.md + LATEST.md
    """
    store = _require_object_store()
    persistence_cfg = getattr(app.state, "persistence_config", None) or {}
    prefix = persistence_cfg.get("prefix", "agent3")

    def _run_jep():
        text_runner = getattr(app.state, "text_runner", None)
        if text_runner is None:
            from agent.llm_inference_client import run_inference as _ri
            def text_runner(prompt, system_message=""):
                return _ri(
                    prompt,
                    endpoint=INFERENCE_ENDPOINT,
                    model_id=INFERENCE_MODEL_ID,
                    compartment_id=COMPARTMENT_ID,
                    max_tokens=WRITING_MAX_TOKENS,
                    temperature=WRITING_TEMPERATURE,
                    top_p=WRITING_TOP_P,
                    top_k=WRITING_TOP_K,
                    system_message=system_message,
                )
        return generate_jep(
            req.customer_id, req.customer_name, store, text_runner,
            feedback=req.feedback or "",
            diagram_key=req.diagram_key,
            diagram_url=req.diagram_url,
            persistence_prefix=prefix,
        )

    try:
        result = await anyio.to_thread.run_sync(_run_jep)
        return {
            "status":        "ok",
            "agent_version": AGENT_VERSION,
            "customer_id":   req.customer_id,
            "doc_type":      "jep",
            "version":       result["version"],
            "key":           result["key"],
            "latest_key":    result["latest_key"],
            "content":       result["content"],
            "bom":           result.get("bom"),
            "diagram_key":   result.get("diagram_key"),
            "errors":        [],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error in /jep/generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jep/{customer_id}/latest")
async def jep_latest(customer_id: str):
    """Return the latest JEP document for a customer."""
    store = _require_object_store()
    content = await anyio.to_thread.run_sync(
        functools.partial(get_latest_doc, store, "jep", customer_id)
    )
    if content is None:
        raise HTTPException(status_code=404, detail=f"No JEP found for customer_id={customer_id!r}")
    return {"status": "ok", "customer_id": customer_id, "doc_type": "jep", "content": content}


@app.get("/api/jep/{customer_id}/versions")
async def jep_versions(customer_id: str):
    """List all JEP versions for a customer."""
    store = _require_object_store()
    versions = await anyio.to_thread.run_sync(
        functools.partial(list_versions, store, "jep", customer_id)
    )
    return {"status": "ok", "customer_id": customer_id, "doc_type": "jep", "versions": versions}


# ── POV approve endpoints ─────────────────────────────────────────────────────

@app.post("/api/pov/approve")
async def pov_approve(req: ApproveDocRequest):
    """
    Save the SA-approved version of a POV document.

    Writes to: approved/{customer_id}/pov.md
    This becomes the base for the next LLM generation run.
    """
    store = _require_object_store()
    try:
        key = await anyio.to_thread.run_sync(
            functools.partial(save_approved_doc, store, "pov", req.customer_id, req.content)
        )
        from agent.notifications import notify
        notify("pov_approved", req.customer_id,
               f"Approved POV uploaded for {req.customer_name}")
        return {"status": "ok", "customer_id": req.customer_id, "doc_type": "pov", "key": key}
    except Exception as exc:
        logger.error("Error in /pov/approve: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/pov/{customer_id}/approved")
async def pov_get_approved(customer_id: str):
    """Return the SA-approved POV for a customer, if one exists."""
    store = _require_object_store()
    content = await anyio.to_thread.run_sync(
        functools.partial(get_approved_doc, store, "pov", customer_id)
    )
    if content is None:
        raise HTTPException(status_code=404, detail=f"No approved POV for customer_id={customer_id!r}")
    return {"status": "ok", "customer_id": customer_id, "doc_type": "pov", "content": content}


# ── JEP approve + kickoff endpoints ──────────────────────────────────────────

@app.post("/api/jep/approve")
async def jep_approve(req: ApproveDocRequest):
    """
    Save the SA-approved version of a JEP document.

    Writes to: approved/{customer_id}/jep.md
    This becomes the base for the next LLM generation run.
    """
    store = _require_object_store()
    try:
        key = await anyio.to_thread.run_sync(
            functools.partial(save_approved_doc, store, "jep", req.customer_id, req.content)
        )
        from agent.notifications import notify
        notify("jep_approved", req.customer_id,
               f"Approved JEP uploaded for {req.customer_name}")
        return {"status": "ok", "customer_id": req.customer_id, "doc_type": "jep", "key": key}
    except Exception as exc:
        logger.error("Error in /jep/approve: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jep/{customer_id}/approved")
async def jep_get_approved(customer_id: str):
    """Return the SA-approved JEP for a customer, if one exists."""
    store = _require_object_store()
    content = await anyio.to_thread.run_sync(
        functools.partial(get_approved_doc, store, "jep", customer_id)
    )
    if content is None:
        raise HTTPException(status_code=404, detail=f"No approved JEP for customer_id={customer_id!r}")
    return {"status": "ok", "customer_id": customer_id, "doc_type": "jep", "content": content}


@app.post("/api/jep/kickoff")
async def jep_kickoff(req: JepKickoffRequest):
    """
    Scan all meeting notes for POC signals and generate clarifying questions.

    Returns a list of questions for the SA to answer before generating the JEP.
    Saves results to jep/{customer_id}/poc_questions.json.
    """
    store = _require_object_store()
    persistence_cfg = getattr(app.state, "persistence_config", None) or {}

    def _run_kickoff():
        text_runner = getattr(app.state, "text_runner", None)
        if text_runner is None:
            from agent.llm_inference_client import run_inference as _ri
            def text_runner(prompt, system_message=""):
                return _ri(
                    prompt,
                    endpoint=INFERENCE_ENDPOINT,
                    model_id=INFERENCE_MODEL_ID,
                    compartment_id=COMPARTMENT_ID,
                    max_tokens=WRITING_MAX_TOKENS,
                    temperature=WRITING_TEMPERATURE,
                    top_p=WRITING_TOP_P,
                    top_k=WRITING_TOP_K,
                    system_message=system_message,
                )
        return kickoff_jep(req.customer_id, req.customer_name, store, text_runner)

    try:
        result = await anyio.to_thread.run_sync(_run_kickoff)
        return {
            "status":        "ok",
            "customer_id":   req.customer_id,
            "questions":     result["questions"],
            "extracted":     result["extracted"],
            "questions_key": result["questions_key"],
        }
    except Exception as exc:
        logger.error("Error in /jep/kickoff: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jep/answers")
async def jep_save_answers(req: JepAnswersRequest):
    """
    Save SA answers to the JEP kickoff questions.

    Merges the answers dict into jep/{customer_id}/poc_questions.json.
    """
    store = _require_object_store()
    try:
        existing = await anyio.to_thread.run_sync(
            functools.partial(get_jep_questions, store, req.customer_id)
        )
        questions = existing.get("questions", [])
        await anyio.to_thread.run_sync(
            functools.partial(save_jep_questions, store, req.customer_id, questions, req.answers)
        )
        return {"status": "ok", "customer_id": req.customer_id, "answers_saved": len(req.answers)}
    except Exception as exc:
        logger.error("Error in /jep/answers: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jep/{customer_id}/questions")
async def jep_get_questions(customer_id: str):
    """Return the stored kickoff Q&A for a customer."""
    store = _require_object_store()
    data = await anyio.to_thread.run_sync(
        functools.partial(get_jep_questions, store, customer_id)
    )
    return {"status": "ok", "customer_id": customer_id, **data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
