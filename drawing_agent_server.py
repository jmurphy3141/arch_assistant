#!/usr/bin/env python3
"""
OCI Drawing Agent - FastAPI Server  (v1.9.1)
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

v1.5.0 additions:
  - request_id (UUIDv4) and input_hash (sha256) on all responses
  - app.state.llm_runner injection seam (tests override; startup sets real OCI runner)
  - app.state.object_store injection seam (default None = no persistence)
  - deployment_hints.multi_region_mode for hints-only multi-region rendering
  - /download requires client_id + diagram_name scope query params
  - In-process IDEMPOTENCY_CACHE keyed by (client_id, diagram_name, input_hash)
  - OCI Object Storage persistence with atomic LATEST.json pointer
"""

import asyncio
import copy
import contextvars
import dataclasses
import functools
import hashlib
import io
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.parse
import urllib.request as _urlreq
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from server.models import (
    A2AObjectRef,
    A2AResponse,
    A2ATask,
    A2Av1JsonRpcRequest,
    A2Av1Message,
    A2Av1Part,
    ApproveDocRequest,
    BomChatRequest,
    BomConversationTurn,
    BomXlsxRequest,
    ChatRequest,
    ClarifyRequest,
    GenerateRequest,
    JepAnswersRequest,
    JepKickoffRequest,
    JepRequest,
    JepRevisionRequest,
    OrchestratorChatRequest,
    PovRequest,
    RefineRequest,
    TerraformGenerateRequest,
    WafRequest,
)
from server.routes.a2a import create_a2a_router
from server.routes.bom import create_bom_router
from server.routes.briefing import create_briefing_router
from server.routes.chat import create_chat_router
from server.routes.documents import create_documents_router
from server.services import jobs as _jobs
from server.services.bom_artifacts import (
    BOM_XLSX_CONTENT_TYPE as _BOM_XLSX_CONTENT_TYPE,
    bom_payload_sizing as _bom_payload_sizing,
    bom_result_is_exportable as _bom_result_is_exportable,
    bom_xlsx_download_is_valid as _bom_xlsx_download_is_valid,
    bom_xlsx_key as _bom_xlsx_key,
    bom_xlsx_metadata as _bom_xlsx_metadata,
    bom_xlsx_metadata_key as _bom_xlsx_metadata_key,
    build_artifact_manifest as _build_artifact_manifest,
    persist_bom_xlsx_downloads as _server_persist_bom_xlsx_downloads,
    positive_float as _positive_float,
    result_has_bom_xlsx_metadata as _result_has_bom_xlsx_metadata,
    structured_bom_result_uses_default_sizing as _structured_bom_result_uses_default_sizing,
    validate_bom_xlsx_filename as _validate_bom_xlsx_filename,
)

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

from agent.bom_parser import (
    bom_to_llm_input,
    freeform_arch_text_to_llm_input,
    inline_bom_text_to_llm_input,
    parse_bom,
)
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio
from agent.oci_standards import get_catalogue_summary
from agent.reference_architecture import (
    render_reference_architecture,
    select_reference_architecture,
)
from agent.persistence_objectstore import (
    ObjectStoreBase,
    InMemoryObjectStore,
    persist_artifacts,
    ARTIFACT_ALLOWLIST,
)
from agent.document_store import (
    save_note,
    list_notes,
    clear_notes_manifest,
    list_versions,
    get_latest_doc,
    save_approved_doc,
    get_approved_doc,
    get_jep_questions,
    save_jep_questions,
    load_conversation_history,
    save_conversation_turns,
    clear_conversation_history,
    clear_conversation_summary,
    list_conversation_summaries,
    list_project_summaries,
    normalize_project_id,
    save_project_engagement,
    get_latest_terraform_bundle,
    list_terraform_versions,
    get_terraform_file,
)
from agent.jep_lifecycle import (
    generate_policy_block_payload as jep_generate_policy_block_payload,
    mark_approved as mark_jep_approved,
    mark_generated as mark_jep_generated,
    request_revision as request_jep_revision,
    sync_jep_state,
)
from agent.pov_agent import generate_pov
from agent.jep_agent import generate_jep, kickoff_jep
from agent.waf_agent import generate_waf
from agent.diagram_waf_orchestrator import run_diagram_waf_loop
from agent.context_store import (
    read_context,
    write_context,
    reset_context,
    record_agent_run,
    attach_bom_xlsx_to_latest,
)
from agent.runtime_config import resolve_agent_llm_config
from agent.bom_service import get_shared_bom_service, new_trace_id
from agent.chat_stream import stream_chat_turn, stream_chat_turn_sse
from agent.tools.specialists import build_inference_runner
from agent.tools.terraform import generate_terraform_bundle

try:
    import server.services.oci_object_storage as _oci_storage
    _OCI_STORAGE_AVAILABLE = True
except Exception:
    _oci_storage = None  # type: ignore
    _OCI_STORAGE_AVAILABLE = False

logger = logging.getLogger(__name__)
_TRACE_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


@asynccontextmanager
async def _lifespan(application: FastAPI):
    _startup(application)
    yield


AGENT_VERSION = "1.9.1"

app = FastAPI(title="OCI Drawing Agent", version=AGENT_VERSION, lifespan=_lifespan)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
    token = _TRACE_ID_CTX.set(trace_id)
    request.state.trace_id = trace_id
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        _TRACE_ID_CTX.reset(token)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    response.headers["x-trace-id"] = trace_id
    logger.info(
        "http_request method=%s path=%s status=%s trace_id=%s duration_ms=%d",
        request.method,
        request.url.path,
        response.status_code,
        trace_id,
        elapsed_ms,
    )
    return response

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
_WRITING_INFERENCE_CONFIG = {
    "endpoint": INFERENCE_ENDPOINT,
    "model_id": INFERENCE_MODEL_ID,
    "compartment_id": COMPARTMENT_ID,
    "max_tokens": WRITING_MAX_TOKENS,
    "temperature": WRITING_TEMPERATURE,
    "top_p": WRITING_TOP_P,
    "top_k": WRITING_TOP_K,
}

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
# OCI Identity Domain endpoints may be set explicitly, or derived from
# OIDC_ISSUER / OCI_IDENTITY_DOMAIN_URL, for example:
# https://idcs-<domain>.identity.oraclecloud.com

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _join_oidc_url(base: str, path: str) -> str:
    if not base:
        return ""
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


OIDC_CLIENT_ID              = _env("OIDC_CLIENT_ID")
OIDC_CLIENT_SECRET          = _env("OIDC_CLIENT_SECRET")
OIDC_REDIRECT_URI           = _env("OIDC_REDIRECT_URI")
OIDC_ISSUER                 = _env("OIDC_ISSUER") or _env("OCI_IDENTITY_DOMAIN_URL")
OIDC_AUTHORIZATION_ENDPOINT = _env("OIDC_AUTHORIZATION_ENDPOINT") or _join_oidc_url(OIDC_ISSUER, "/oauth2/v1/authorize")
OIDC_TOKEN_ENDPOINT         = _env("OIDC_TOKEN_ENDPOINT") or _join_oidc_url(OIDC_ISSUER, "/oauth2/v1/token")
OIDC_USERINFO_ENDPOINT      = _env("OIDC_USERINFO_ENDPOINT") or _join_oidc_url(OIDC_ISSUER, "/oauth2/v1/userinfo")
OIDC_LOGOUT_ENDPOINT        = _env("OIDC_LOGOUT_ENDPOINT") or _join_oidc_url(OIDC_ISSUER, "/oauth2/v1/userlogout")
OIDC_REQUIRED_GROUP         = _env("OIDC_REQUIRED_GROUP")
OIDC_SCOPE                  = _env("OIDC_SCOPE", "openid profile email")
_SESSION_SECRET             = _env("SESSION_SECRET", "dev-secret-change-in-prod")
SESSION_COOKIE_SECURE       = _env("SESSION_COOKIE_SECURE", "auto").lower()

AUTH_ENABLED = all([
    OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET,
    OIDC_REDIRECT_URI,
    OIDC_AUTHORIZATION_ENDPOINT,
    OIDC_TOKEN_ENDPOINT,
    OIDC_USERINFO_ENDPOINT,
])

# ── Fleet identity ───────────────────────────────────────────────────────────
AGENT_ID    = _cfg.get("agent_id", "agent3-oci-drawing")
FLEET_CFG   = _cfg.get("fleet", {})

SCHEMA_VERSION = {"spec": "1.1", "draw_dict": "1.0"}

# ── Diagram editor system message ─────────────────────────────────────────────
# Used by /api/refine when prev_spec is available — bypasses run_pipeline so
# the LLM is NEVER allowed to ask clarification questions on a refinement.
DIAGRAM_EDIT_SYSTEM = (
    "You are an OCI architecture diagram editor. "
    "You receive a current LayoutIntent JSON document and a change request. "
    "Modify the LayoutIntent JSON to apply ONLY the requested change. "
    "Keep everything else identical to the input. "
    "NEVER return need_clarification. NEVER ask questions. "
    "If a service needs to be added, choose the most appropriate oci_type and layer. "
    "Output ONLY the complete, valid, modified LayoutIntent JSON. No fences. No commentary."
)

# ── Session middleware (must be added before first request) ───────────────────
_session_https_only = (
    OIDC_REDIRECT_URI.startswith("https://")
    if SESSION_COOKIE_SECURE == "auto"
    else SESSION_COOKIE_SECURE in {"1", "true", "yes", "on"}
)
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    https_only=_session_https_only,
    same_site="lax",
)

# ── Global mutable state ───────────────────────────────────────────────────────
_oci_agent: Optional[Any] = None          # real OCI Agent, set in startup
SESSION_STORE:     Dict[str, str]  = {}   # client_id → session_id (ADK path only; unused on inference path)


def _current_trace_id() -> str:
    return _TRACE_ID_CTX.get() or ""
PENDING_CLARIFY:   Dict[str, dict] = {}   # client_id  → {items, prompt, diagram_name}
IDEMPOTENCY_CACHE: Dict[tuple, dict] = {} # (client_id, diagram_name, input_hash) → result

# ── Async job store ────────────────────────────────────────────────────────────
_JOB_STORE = _jobs.JOB_STORE
_JOB_TTL = _jobs.JOB_TTL
_new_job = _jobs.new_job
_complete_job = _jobs.complete_job
_fail_job = _jobs.fail_job


# ── Pydantic models live in server.models and are re-exported above ───────────

# In-memory task store for A2A v1.0 tasks (keyed by task_id)
A2A_TASKS: Dict[str, dict] = {}


def _ensure_waf_test_pillars(content: str) -> str:
    """Ensure legacy live-test pillar labels are always present in the response text."""
    required = [
        "Operational Excellence",
        "Security",
        "Reliability",
        "Performance Efficiency",
        "Cost Optimization",
        "Sustainability",
    ]
    missing = [pillar for pillar in required if pillar not in content]
    if not missing:
        return content
    aliases = "\n".join(f"- {pillar}" for pillar in required)
    return f"{content}\n\n## Pillar Mapping\n{aliases}\n"


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
    deployment_hints: Optional[dict] = None,
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
            "items_json":            json.dumps([dataclasses.asdict(i) for i in items]),
            "prompt":                prompt,
            "deployment_hints_json": json.dumps(deployment_hints or {}),
        }
    return resp


_FREEFORM_CLARIFY_PREFIX = "FREEFORM_NOTES_JSON:"


def _encode_freeform_clarify_prompt(*, notes: str, context: str, questionnaire: str) -> str:
    payload = {
        "notes": notes,
        "context": context,
        "questionnaire": questionnaire,
    }
    return _FREEFORM_CLARIFY_PREFIX + json.dumps(payload, separators=(",", ":"))


def _decode_freeform_clarify_prompt(prompt: str) -> dict[str, str] | None:
    raw = str(prompt or "")
    if not raw.startswith(_FREEFORM_CLARIFY_PREFIX):
        return None
    try:
        payload = json.loads(raw[len(_FREEFORM_CLARIFY_PREFIX):])
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "notes": str(payload.get("notes", "") or ""),
        "context": str(payload.get("context", "") or ""),
        "questionnaire": str(payload.get("questionnaire", "") or ""),
    }


def _freeform_diagram_questions() -> list[dict[str, Any]]:
    return [
        {
            "id": "components.scope",
            "question": (
                "What major OCI components should be shown in the diagram "
                "(for example load balancer, app servers, database, object storage, OKE, DRG)?"
            ),
            "blocking": True,
        },
        {
            "id": "regions.mode",
            "question": (
                "Should I assume a single-region deployment, multi-AD HA in one region, "
                "or multi-region DR?"
            ),
            "blocking": True,
        },
    ]


def _freeform_clarify_response(
    *,
    client_id: str,
    diagram_name: str,
    request_id: str,
    input_hash: str,
    notes: str,
    context: str,
    questionnaire: str,
    deployment_hints: dict | None = None,
) -> dict:
    PENDING_CLARIFY[client_id] = {
        "items": [],
        "prompt": _encode_freeform_clarify_prompt(
            notes=notes,
            context=context,
            questionnaire=questionnaire,
        ),
        "diagram_name": diagram_name,
        "deployment_hints": deployment_hints or {},
        "freeform_notes": notes,
        "freeform_context": context,
        "freeform_questionnaire": questionnaire,
    }
    return _clarify_response(
        client_id,
        diagram_name,
        request_id,
        input_hash,
        _freeform_diagram_questions(),
        items=[],
        prompt=_encode_freeform_clarify_prompt(
            notes=notes,
            context=context,
            questionnaire=questionnaire,
        ),
        deployment_hints=deployment_hints,
    )


_BM_X9_SHAPE_RE = re.compile(r"\bbm\.standard\.x9\.64\b", re.IGNORECASE)
_TWO_BM_X9_RE = re.compile(
    r"\b(?:two|2)\s+(?:distinct\s+)?(?:bm\.standard\.x9\.64|bm|bare[- ]metal)",
    re.IGNORECASE,
)
_VMWARE_OCVS_RE = re.compile(
    r"\b(?:ocvs|oci\s+dedicated\s+vmware\s+solution|vxrail|vmware|esxi|vsphere|sddc|vcenter|nsx)\b",
    re.IGNORECASE,
)


def _ocvs_bm_overlay_requested(text: str) -> bool:
    """Return True when explicit user context asks for two BM X9 ESXi/OCVS hosts."""
    source = str(text or "")
    if not source.strip():
        return False
    bm_shape = bool(_BM_X9_SHAPE_RE.search(source))
    two_hosts = bool(_TWO_BM_X9_RE.search(source)) or bool(
        bm_shape
        and re.search(r"\b(?:two|2)\b", source, re.IGNORECASE)
        and re.search(r"\bfd\s*1\b", source, re.IGNORECASE)
        and re.search(r"\bfd\s*2\b", source, re.IGNORECASE)
    )
    return bm_shape and two_hosts


def _ocvs_vmware_context_present(text: str) -> bool:
    return bool(_VMWARE_OCVS_RE.search(str(text or "")))


def _region_label_from_context(text: str, existing: str = "") -> str:
    match = re.search(r"\b[a-z]{2}-[a-z]+-\d+\b", str(text or ""), re.IGNORECASE)
    if match:
        return f"OCI Region - {match.group(0).lower()}"
    return existing or "OCI Region"


def _apply_ocvs_bm_overlay(spec: dict, *, source_text: str) -> tuple[dict, bool]:
    """
    Force explicit OCVS/BM.Standard.X9.64 ESXi placement before layout compilation.

    This handles regeneration requests where current user text is more specific
    than a prior BOM baseline that only parsed the SKU family as generic compute.
    """
    bm_overlay = _ocvs_bm_overlay_requested(source_text)
    vmware_overlay = _ocvs_vmware_context_present(source_text)
    if not bm_overlay and not vmware_overlay:
        return spec, False

    updated = copy.deepcopy(spec)
    regions = updated.get("regions")
    if not isinstance(regions, list) or not regions:
        regions = [{"id": "region_box", "label": "OCI Region"}]
        updated["regions"] = regions
    region = regions[0]
    if not isinstance(region, dict):
        region = {"id": "region_box", "label": "OCI Region"}
        regions[0] = region

    region.setdefault("id", "region_box")
    region["label"] = _region_label_from_context(source_text, str(region.get("label", "") or "OCI Region"))
    region.setdefault("regional_subnets", [])
    region.setdefault("gateways", [])

    services = [svc for svc in region.get("oci_services", []) or [] if isinstance(svc, dict)]
    service_ids = {str(svc.get("id", "") or "") for svc in services}
    required_services = [
        {"id": "ocvs_sddc", "type": "ocvs", "label": "OCI Dedicated VMware Solution SDDC"},
        {"id": "vcenter", "type": "vmware", "label": "vCenter"},
        {"id": "nsx", "type": "vmware", "label": "NSX"},
    ]
    for svc in required_services:
        if svc["id"] not in service_ids:
            services.append(svc)
    region["oci_services"] = services

    if not bm_overlay:
        return updated, True

    updated["deployment_type"] = "single_ad"

    region["availability_domains"] = [
        {
            "id": "ad1_box",
            "label": "Availability Domain 1",
            "fault_domains": [
                {
                    "id": "fd1_box",
                    "label": "FD1",
                    "subnets": [
                        {
                            "id": "fd1_ocvs_mgmt_subnet",
                            "label": "FD1 OCVS Management Subnet",
                            "tier": "app",
                            "nodes": [],
                        },
                        {
                            "id": "fd1_esxi_host_subnet",
                            "label": "FD1 ESXi Host Subnet",
                            "tier": "app",
                            "nodes": [
                                {
                                    "id": "bm_standard_x9_64_esxi_fd1",
                                    "type": "bare metal",
                                    "label": "BM.Standard.X9.64 ESXi Host - FD1",
                                }
                            ],
                        },
                    ],
                },
                {
                    "id": "fd2_box",
                    "label": "FD2",
                    "subnets": [
                        {
                            "id": "fd2_ocvs_mgmt_subnet",
                            "label": "FD2 OCVS Management Subnet",
                            "tier": "app",
                            "nodes": [],
                        },
                        {
                            "id": "fd2_esxi_host_subnet",
                            "label": "FD2 ESXi Host Subnet",
                            "tier": "app",
                            "nodes": [
                                {
                                    "id": "bm_standard_x9_64_esxi_fd2",
                                    "type": "bare metal",
                                    "label": "BM.Standard.X9.64 ESXi Host - FD2",
                                }
                            ],
                        },
                    ],
                },
                {"id": "fd3_box", "label": "FD3", "subnets": []},
            ],
            "subnets": [],
        }
    ]

    node_ids = {
        node["id"]
        for fd in region["availability_domains"][0]["fault_domains"]
        for subnet in fd.get("subnets", [])
        for node in subnet.get("nodes", [])
    }
    service_ids = {svc["id"] for svc in services}
    gateway_ids = {str(gw.get("id", "") or "") for gw in region.get("gateways", []) or [] if isinstance(gw, dict)}
    external_ids = {
        str(ext.get("id", "") or "")
        for ext in updated.get("external", []) or []
        if isinstance(ext, dict)
    }
    valid_edge_ids = node_ids | service_ids | gateway_ids | external_ids
    updated["edges"] = [
        edge
        for edge in updated.get("edges", []) or []
        if isinstance(edge, dict)
        and str(edge.get("source", "") or "") in valid_edge_ids
        and str(edge.get("target", "") or "") in valid_edge_ids
    ]
    return updated, True


async def run_pipeline(
    items: list,
    prompt: str,
    diagram_name: str,
    client_id: str,
    request_id: str,
    input_hash: str,
    deployment_hints: Optional[dict] = None,
    reference_context_text: str = "",
    reference_selection_hint: Optional[dict] = None,
) -> dict:
    """
    Call LLM → layout engine → drawio generator.
    Returns a full v1.5.0 result dict (status ok or need_clarification).
    Persists artifacts if app.state.object_store is set.

    Async design:
    - call_llm is awaited directly so the OCI ADK sees a running event loop.
    - CPU-bound and file-I/O steps are offloaded to anyio worker threads.
    """
    if deployment_hints is None:
        deployment_hints = {}
    reference_selection = select_reference_architecture(
        text=reference_context_text,
        items=items,
        deployment_hints=deployment_hints,
        orchestrator_hint=reference_selection_hint,
    )
    reference_metadata = reference_selection.as_dict()
    render_mode = str(reference_metadata.get("reference_mode", "best-effort-generic") or "best-effort-generic")

    if render_mode == "reference-backed":
        if reference_metadata.get("multi_region_mode") and not deployment_hints.get("multi_region_mode"):
            deployment_hints = dict(deployment_hints)
            deployment_hints["multi_region_mode"] = reference_metadata["multi_region_mode"]
        spec, reference_metadata = await anyio.to_thread.run_sync(
            functools.partial(
                render_reference_architecture,
                selection=reference_metadata,
                items=items,
                deployment_hints=deployment_hints,
            )
        )
    else:
        spec = await call_llm(prompt, client_id)

    # ── Clarification requested by LLM ───────────────────────────────────────
    if spec.get("status") == "need_clarification":
        PENDING_CLARIFY[client_id] = {
            "items":            items,
            "prompt":           prompt,
            "diagram_name":     diagram_name,
            "deployment_hints": deployment_hints,
        }
        return _clarify_response(
            client_id, diagram_name, request_id, input_hash,
            spec.get("questions", []),
            items=items,
            prompt=prompt,
            deployment_hints=deployment_hints,
        )

    # ── Option 1: LayoutIntent path ───────────────────────────────────────────
    # Detect LayoutIntent (has "placements" key) vs legacy/hierarchical full spec.
    # Legacy FakeLLMRunner tests return a full hierarchical spec (no "placements"),
    # so the old path is preserved for backward compatibility.
    layout_intent_spec: Optional[dict] = None  # captured for _refine_context
    if "placements" in spec:
        try:
            from agent.layout_intent import validate_layout_intent, LayoutIntentError
            from agent.intent_compiler import compile_intent_to_flat_spec

            _spec_ref = spec  # capture for closure
            layout_intent_spec = spec  # save LayoutIntent before compile overwrites spec

            def _compile_intent():
                intent = validate_layout_intent(_spec_ref, items)
                return compile_intent_to_flat_spec(intent, items)

            spec = await anyio.to_thread.run_sync(_compile_intent)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"LayoutIntent validation/compile error: {exc}",
            )

    overlay_source_text = "\n\n".join(
        part
        for part in (reference_context_text, prompt)
        if str(part or "").strip()
    )
    spec, ocvs_bm_overlay_applied = _apply_ocvs_bm_overlay(spec, source_text=overlay_source_text)

    # ── Multi-region hints check ──────────────────────────────────────────────
    # multi_compartment = multiple environments inside ONE region (IAM boundaries).
    # These are NOT separate geographic regions — skip the DR/HA clarification entirely.
    mr_mode = deployment_hints.get("multi_region_mode")
    if spec.get("deployment_type") == "multi_compartment":
        mr_mode = None   # compartments never trigger DR/HA post-processing
    is_multi_region = (
        spec.get("deployment_type") == "multi_region"
        or len(deployment_hints.get("regions", [])) >= 2
    )
    if is_multi_region and not mr_mode:
        PENDING_CLARIFY[client_id] = {
            "items":            items,
            "prompt":           prompt,
            "diagram_name":     diagram_name,
            "deployment_hints": deployment_hints,
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
            deployment_hints=deployment_hints,
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
        # Keep only the primary region; add a lightweight stub box for the secondary.
        # The stub is placed BELOW the primary region (not to the right — the primary
        # region fills the full canvas width, so placing to the right goes off-screen).
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
        stub_x = primary_box["x"]                            if primary_box else 144
        stub_y = (primary_box["y"] + primary_box["h"] + 40) if primary_box else 300
        stub_w = primary_box["w"]                            if primary_box else 600

        draw_dict["boxes"].append({
            "id":       "region_secondary_stub",
            "label":    secondary_label,
            "box_type": "_region_stub",
            "tier":     "",
            "x":        stub_x,
            "y":        stub_y,
            "w":        stub_w,
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
        "standards_bundle_version": str(reference_metadata.get("standards_bundle_version", "") or ""),
        "reference_family": str(reference_metadata.get("reference_family", "") or ""),
        "reference_confidence": float(reference_metadata.get("reference_confidence", 0) or 0),
        "reference_mode": str(reference_metadata.get("reference_mode", render_mode) or render_mode),
        "family_fit_score": float(reference_metadata.get("family_fit_score", 0) or 0),
        "ocvs_bm_overlay_applied": bool(ocvs_bm_overlay_applied),
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
            drawio_key = str(latest.get("artifacts", {}).get("diagram.drawio", "") or "")
            if drawio_key:
                resp_drawio_key = drawio_key
            else:
                resp_drawio_key = ""
        else:
            resp_drawio_key = ""
    else:
        resp_drawio_key = ""

    if GIT_PUSH_ENABLED:
        threading.Thread(
            target=_push_diagram_to_git,
            args=(drawio_xml, client_id, diagram_name, persisted_version),
            daemon=True,
        ).start()

    resp: dict = {
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
        "reference_architecture": reference_metadata,
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
    if resp_drawio_key:
        resp["drawio_key"] = resp_drawio_key
        resp["object_key"] = resp_drawio_key
    # Attach refine context so the UI can request diagram changes without
    # re-uploading the BOM.  Mirrors _clarify_context but for the "ok" path.
    if items is not None:
        refine_ctx: dict = {
            "items_json": json.dumps([dataclasses.asdict(i) for i in items]),
            "prompt":     prompt,
        }
        if layout_intent_spec is not None:
            refine_ctx["prev_spec"] = json.dumps(layout_intent_spec)
        refine_ctx["deployment_hints_json"] = json.dumps(deployment_hints or {})
        resp["_refine_context"] = refine_ctx
    return resp


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


def _make_editor_runner() -> callable:
    """
    Build a sync editor_runner for diagram editing (/api/refine).

    Same endpoint as inference_runner but:
      - temperature=0 for deterministic JSON output
      - max_tokens = max(INFERENCE_MAX_TOKENS, 4096) so a full LayoutIntent
        is never truncated mid-response
      - per-call system_message (like text_runner) so the editor persona can
        be supplied at call time
    """
    _max = max(INFERENCE_MAX_TOKENS, 4096)

    def _run(prompt: str, system_message: str = "") -> str:
        return _run_inference(
            prompt,
            endpoint       = INFERENCE_ENDPOINT,
            model_id       = INFERENCE_MODEL_ID,
            compartment_id = COMPARTMENT_ID,
            max_tokens     = _max,
            temperature    = 0.0,
            top_p          = INFERENCE_TOP_P,
            top_k          = INFERENCE_TOP_K,
            system_message = system_message,
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
        _ensure_state_defaults(app)
        return

    # ── Path 1: Direct OCI Inference (preferred) ──────────────────────────────
    if INFERENCE_ENABLED and _INFERENCE_AVAILABLE:
        try:
            app.state.llm_runner = _make_inference_runner()
            logger.info(
                "Drawing Agent ready (OCI inference) model=%s", INFERENCE_MODEL_ID
            )
            _ensure_state_defaults(app)
            return
        except Exception as exc:
            logger.warning(
                "OCI inference runner init failed (%s) — trying ADK fallback", exc
            )

    # ── Path 2: Legacy ADK Agent Endpoint ────────────────────────────────────
    if not _OCI_ADK_AVAILABLE:
        logger.warning("oci[adk] not importable — llm_runner will be None")
        app.state.llm_runner = None
        _ensure_state_defaults(app)
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

    _ensure_state_defaults(app)


def _init_object_store(target_app: FastAPI | None = None) -> None:
    """
    Initialise app.state.object_store from config.
    Only called during startup when tests have NOT pre-injected a store.
    Tests always pre-set app.state.object_store (even to None) so this is skipped.
    """
    app_obj = target_app or app
    if not PERSISTENCE_ENABLED:
        app_obj.state.object_store = None
        app_obj.state.persistence_config = {}
        return

    if PERSISTENCE_BACKEND == "oci_object_storage":
        try:
            from agent.object_store_oci import OciObjectStore
            app_obj.state.object_store = OciObjectStore(
                region=PERSISTENCE_REGION,
                namespace=PERSISTENCE_NAMESPACE,
                bucket_name=PERSISTENCE_BUCKET,
            )
            app_obj.state.persistence_config = {"prefix": PERSISTENCE_PREFIX}
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
            app_obj.state.object_store = None
            app_obj.state.persistence_config = {}
    else:
        logger.warning(
            "Unknown persistence backend %r — persistence disabled", PERSISTENCE_BACKEND
        )
        app_obj.state.object_store = None
        app_obj.state.persistence_config = {}


def _ensure_state_defaults(target_app: FastAPI | None = None) -> None:
    app_obj = target_app or app
    # If tests (or earlier startup paths) have already set the object_store,
    # respect that choice; only fill in defaults for attributes not yet set.
    if not hasattr(app_obj.state, "object_store"):
        _init_object_store(app_obj)
    if getattr(app_obj.state, "persistence_config", None) is None:
        app_obj.state.persistence_config = {"prefix": PERSISTENCE_PREFIX}
    # Writing agent text_runner — separate from the JSON llm_runner
    if not hasattr(app_obj.state, "text_runner"):
        if INFERENCE_ENABLED and _INFERENCE_AVAILABLE:
            try:
                app_obj.state.text_runner = _make_text_runner()
                logger.info("Text runner ready (writing agents)")
            except Exception as exc:
                logger.warning("Text runner init failed (%s) — writing agents disabled", exc)
                app_obj.state.text_runner = None
        else:
            app_obj.state.text_runner = None
    # Diagram editor runner — temperature=0 for deterministic JSON editing
    if not hasattr(app_obj.state, "editor_runner"):
        if INFERENCE_ENABLED and _INFERENCE_AVAILABLE:
            try:
                app_obj.state.editor_runner = _make_editor_runner()
                logger.info("Editor runner ready (diagram refine)")
            except Exception as exc:
                logger.warning("Editor runner init failed (%s) — refine will use text_runner", exc)
                app_obj.state.editor_runner = None
        else:
            app_obj.state.editor_runner = None
    if not hasattr(app_obj.state, "bom_service"):
        app_obj.state.bom_service = get_shared_bom_service()


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


async def require_admin_user(user: dict = Depends(require_user)) -> dict:
    """
    Admin/authz helper for mutation endpoints that should follow global OIDC group policy.
    """
    if not AUTH_ENABLED:
        return user
    if not OIDC_REQUIRED_GROUP:
        return user
    groups = user.get("groups", [])
    if isinstance(groups, str):
        groups = [groups]
    if OIDC_REQUIRED_GROUP not in groups:
        raise HTTPException(status_code=403, detail="Admin access required for this endpoint.")
    return user


# ── Auth routes ───────────────────────────────────────────────────────────────

_UI_DIST = Path(__file__).parent / "ui" / "dist"
_UI_INDEX = _UI_DIST / "index.html"
_UI_FAVICON = _UI_DIST / "favicon.jpg"
_LEGACY_INDEX = Path(__file__).parent / "index.html"

# Mount built React assets so /assets/... requests are served correctly.
_UI_ASSETS = _UI_DIST / "assets"
if _UI_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=_UI_ASSETS), name="ui_assets")


@app.get("/favicon.jpg")
async def serve_favicon():
    if _UI_FAVICON.exists():
        return FileResponse(str(_UI_FAVICON), media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="favicon not found")


@app.get("/")
async def serve_ui(request: Request):
    """Serve the React SPA. Returns 503 with instructions if dist is not built."""
    if AUTH_ENABLED and not request.session.get("user"):
        return RedirectResponse("/login", status_code=302)
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-App-Version": AGENT_VERSION,
    }
    if _UI_INDEX.exists():
        return FileResponse(str(_UI_INDEX), headers=headers)
    return HTMLResponse(
        "<html><body style='font-family:monospace;padding:2rem'>"
        "<h2>UI not built</h2>"
        "<p>The React UI dist is missing. Run on the server:</p>"
        "<pre>cd ~/drawing-agent/ui && npm install && npm run build</pre>"
        "</body></html>",
        status_code=503,
        headers=headers,
    )


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

    groups = userinfo.get("groups", [])
    if not isinstance(groups, list):
        groups = []
    request.session["user"] = {
        "email": userinfo.get("email", ""),
        "name":  userinfo.get("name") or userinfo.get("email", "unknown"),
        "groups": [str(g) for g in groups if str(g).strip()],
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


@app.post("/api/upload-bom")
async def upload_bom(
    file:          UploadFile = File(...),
    context_file:  UploadFile = File(None),
    context:       str        = Form(default=""),
    diagram_name:  str        = Form(default="oci_architecture"),
    client_id:     str        = Form(default="default"),
    customer_id:   str        = Form(default=""),
    customer_name: str        = Form(default=""),
    auto_waf:      bool       = Form(default=False),
    _user:         dict       = Depends(require_user),
):
    """
    Upload an Excel BOM + optional context file.
    Returns {"status":"pending","job_id":"..."} immediately.
    Poll GET /api/job/{job_id} for the result.
    """
    # Read file bytes NOW — UploadFile is not usable inside a background task
    file_bytes = await file.read()
    file_name  = file.filename or "bom.xlsx"
    ctx_bytes: bytes = b""
    ctx_name:  str   = ""
    if context_file and context_file.filename:
        ctx_bytes = await context_file.read()
        ctx_name  = context_file.filename

    # Idempotency check before spawning a job (skip for auto_waf)
    input_hash = compute_input_hash(hashlib.sha256(file_bytes).hexdigest())
    cache_key  = (client_id, diagram_name, input_hash)
    if not auto_waf and cache_key in IDEMPOTENCY_CACHE:
        return JSONResponse(status_code=200, content=IDEMPOTENCY_CACHE[cache_key])

    job_id = _new_job()

    async def _run() -> None:
        request_id = str(uuid.uuid4())
        try:
            # Save BOM to temp file
            suffix = Path(file_name).suffix or ".xlsx"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_bytes)
                bom_path = tmp.name

            # Decode context
            context_text = context
            if ctx_bytes:
                try:
                    context_text = ctx_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    context_text = ctx_bytes.decode("latin-1", errors="replace")
                logger.info("Context file: %s (%d chars)", ctx_name, len(context_text))

            items, prompt = await anyio.to_thread.run_sync(
                functools.partial(bom_to_llm_input, bom_path, context=context_text)
            )
            await anyio.to_thread.run_sync(functools.partial(os.unlink, bom_path))
            logger.info("BOM parsed: %d services | context: %d chars", len(items), len(context_text))

            result = await run_pipeline(
                items,
                prompt,
                diagram_name,
                client_id,
                request_id,
                input_hash,
                reference_context_text=context_text,
            )

            if result["status"] == "ok" and not auto_waf:
                IDEMPOTENCY_CACHE[cache_key] = result

            # ── Auto WAF orchestration loop ───────────────────────────────────
            if auto_waf and result["status"] == "ok":
                store       = getattr(app.state, "object_store", None)
                text_runner = getattr(app.state, "text_runner",  None)
                if store and text_runner:
                    eff_customer_id      = customer_id or client_id
                    eff_deployment_hints: dict = {}
                    refine_ctx = result.get("_refine_context") or {}
                    if refine_ctx.get("deployment_hints_json"):
                        try:
                            eff_deployment_hints = json.loads(refine_ctx["deployment_hints_json"])
                        except (json.JSONDecodeError, ValueError):
                            pass
                    loop_result = await run_diagram_waf_loop(
                        items            = items,
                        base_prompt      = prompt,
                        deployment_hints = eff_deployment_hints,
                        draw_result      = result,
                        customer_id      = eff_customer_id,
                        customer_name    = customer_name,
                        diagram_name     = diagram_name,
                        client_id        = client_id,
                        object_store     = store,
                        text_runner      = text_runner,
                        run_pipeline_fn  = run_pipeline,
                    )
                    waf_r = loop_result["waf_result"]
                    _complete_job(job_id, {
                        "status":        "orchestration_complete",
                        "agent_version": AGENT_VERSION,
                        "client_id":     client_id,
                        "customer_id":   eff_customer_id,
                        "diagram_name":  diagram_name,
                        "request_id":    request_id,
                        "draw_result":   loop_result["draw_result"],
                        "waf_result": {
                            "version":        waf_r.get("version"),
                            "key":            waf_r.get("key"),
                            "content":        waf_r.get("content", ""),
                            "overall_rating": waf_r.get("overall_rating", "⚠️"),
                        },
                        "loop_summary": {
                            "iterations": loop_result["iterations"],
                            "history":    loop_result["loop_history"],
                        },
                        "errors": [],
                    })
                    return
                else:
                    logger.warning("auto_waf=True but store/text_runner not configured — diagram only")

            # ── Need clarification — store auto_waf metadata for /clarify ─────
            if auto_waf and result["status"] == "need_clarification":
                if client_id in PENDING_CLARIFY:
                    PENDING_CLARIFY[client_id]["auto_waf"]      = True
                    PENDING_CLARIFY[client_id]["customer_id"]   = customer_id or client_id
                    PENDING_CLARIFY[client_id]["customer_name"] = customer_name

            _complete_job(job_id, result)

        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            _fail_job(job_id, detail)
        except json.JSONDecodeError as exc:
            _fail_job(job_id, f"LLM returned invalid JSON: {exc}")
        except Exception as exc:
            logger.error("upload-bom job %s failed: %s", job_id, exc)
            _fail_job(job_id, str(exc))

    asyncio.create_task(_run())
    return JSONResponse(status_code=202, content={"status": "pending", "job_id": job_id})


@app.post("/api/clarify")
async def clarify(req: ClarifyRequest, _user: dict = Depends(require_user)):
    """
    Submit answers to clarification questions.
    Returns {"status":"pending","job_id":"..."} immediately.
    Poll GET /api/job/{job_id} for the result.
    """
    job_id = _new_job()

    # Capture everything needed by the background task before returning
    req_snapshot = req.model_copy()

    async def _run() -> None:
        request_id = str(uuid.uuid4())
        input_hash = compute_input_hash(req_snapshot.answers or "")
        try:
            # ── Stateless path ────────────────────────────────────────────────
            freeform_payload: dict[str, str] | None = None
            if req_snapshot.items_json and req_snapshot.prompt:
                raw_items = json.loads(req_snapshot.items_json)
                freeform_payload = _decode_freeform_clarify_prompt(req_snapshot.prompt)
                if freeform_payload and not raw_items:
                    items = []
                    base_prompt = req_snapshot.prompt
                else:
                    from agent.bom_parser import ServiceItem
                    items = [ServiceItem(**r) for r in raw_items]
                    base_prompt = req_snapshot.prompt
                deployment_hints: dict = {}
                if req_snapshot.deployment_hints_json:
                    try:
                        deployment_hints = json.loads(req_snapshot.deployment_hints_json)
                    except (json.JSONDecodeError, ValueError):
                        deployment_hints = {}
            else:
                # ── Stateful fallback ─────────────────────────────────────────
                pending = PENDING_CLARIFY.get(req_snapshot.client_id)
                if not pending:
                    _fail_job(job_id,
                        f"No pending clarification for client_id '{req_snapshot.client_id}'. "
                        "Call /upload-bom or /generate first.")
                    return
                items            = pending["items"]
                base_prompt      = pending["prompt"]
                deployment_hints = dict(pending.get("deployment_hints") or {})
                if pending.get("freeform_notes"):
                    freeform_payload = {
                        "notes": str(pending.get("freeform_notes", "") or ""),
                        "context": str(pending.get("freeform_context", "") or ""),
                        "questionnaire": str(pending.get("freeform_questionnaire", "") or ""),
                    }

            # ── Resolve auto_waf metadata ──────────────────────────────────────
            pending_meta      = PENDING_CLARIFY.get(req_snapshot.client_id) or {}
            eff_auto_waf      = req_snapshot.auto_waf      or pending_meta.get("auto_waf", False)
            eff_customer_id   = req_snapshot.customer_id   or pending_meta.get("customer_id", "") or req_snapshot.client_id
            eff_customer_name = req_snapshot.customer_name or pending_meta.get("customer_name", "")

            # ── Map regions.mode answer to multi_region_mode hint ──────────────
            if "multi_region_mode" not in deployment_hints:
                ans_lower = req_snapshot.answers.lower()
                if any(w in ans_lower for w in [
                    "dr", "disaster", " ha ", "standby", "failover",
                    "duplicate", "active-passive", "passive", "replica",
                ]):
                    deployment_hints["multi_region_mode"] = "duplicate_drha"
                elif any(w in ans_lower for w in ["split", "different workload", "active-active"]):
                    deployment_hints["multi_region_mode"] = "split"

            if freeform_payload:
                combined_notes = (
                    freeform_payload["notes"].strip()
                    + f"\n\nCLARIFICATION ANSWERS:\n{req_snapshot.answers.strip()}\n"
                ).strip()
                result = await _run_freeform_diagram_pipeline(
                    notes=combined_notes,
                    context=freeform_payload["context"],
                    questionnaire=freeform_payload["questionnaire"],
                    diagram_name=req_snapshot.diagram_name,
                    client_id=req_snapshot.client_id,
                    request_id=request_id,
                    input_hash=input_hash,
                    deployment_hints=deployment_hints,
                )
            else:
                enriched_prompt = (
                    base_prompt
                    + f"\n\nCLARIFICATION ANSWERS:\n{req_snapshot.answers.strip()}\n\n"
                    + "Now produce the layout spec JSON using the answers above. "
                    + "Output ONLY valid JSON."
                )

                result = await run_pipeline(
                    items            = items,
                    prompt           = enriched_prompt,
                    diagram_name     = req_snapshot.diagram_name,
                    client_id        = req_snapshot.client_id,
                    request_id       = request_id,
                    input_hash       = input_hash,
                    deployment_hints = deployment_hints,
                    reference_context_text=req_snapshot.answers,
                )

            if result["status"] == "ok":
                PENDING_CLARIFY.pop(req_snapshot.client_id, None)

                # ── Auto WAF orchestration loop ────────────────────────────────
                if eff_auto_waf:
                    store       = getattr(app.state, "object_store", None)
                    text_runner = getattr(app.state, "text_runner",  None)
                    if store and text_runner:
                        loop_result = await run_diagram_waf_loop(
                            items            = items,
                            base_prompt      = base_prompt,
                            deployment_hints = deployment_hints,
                            draw_result      = result,
                            customer_id      = eff_customer_id,
                            customer_name    = eff_customer_name,
                            diagram_name     = req_snapshot.diagram_name,
                            client_id        = req_snapshot.client_id,
                            object_store     = store,
                            text_runner      = text_runner,
                            run_pipeline_fn  = run_pipeline,
                        )
                        waf_r = loop_result["waf_result"]
                        _complete_job(job_id, {
                            "status":        "orchestration_complete",
                            "agent_version": AGENT_VERSION,
                            "client_id":     req_snapshot.client_id,
                            "customer_id":   eff_customer_id,
                            "diagram_name":  req_snapshot.diagram_name,
                            "request_id":    request_id,
                            "draw_result":   loop_result["draw_result"],
                            "waf_result": {
                                "version":        waf_r.get("version"),
                                "key":            waf_r.get("key"),
                                "content":        waf_r.get("content", ""),
                                "overall_rating": waf_r.get("overall_rating", "⚠️"),
                            },
                            "loop_summary": {
                                "iterations": loop_result["iterations"],
                                "history":    loop_result["loop_history"],
                            },
                            "errors": [],
                        })
                        return

            _complete_job(job_id, result)

        except json.JSONDecodeError as exc:
            _fail_job(job_id, f"LLM returned invalid JSON: {exc}")
        except Exception as exc:
            logger.error("clarify job %s failed: %s", job_id, exc)
            _fail_job(job_id, str(exc))

    asyncio.create_task(_run())
    return JSONResponse(status_code=202, content={"status": "pending", "job_id": job_id})


@app.post("/api/refine")
async def refine_diagram(req: RefineRequest, _user: dict = Depends(require_user)):
    """
    Refine an already-generated diagram based on free-text feedback.

    When prev_spec is available (the normal path after any successful generation):
      - Uses call_text_llm with DIAGRAM_EDIT_SYSTEM — an "editor, never ask questions"
        persona — so the LLM receives ONLY the current LayoutIntent + the change
        request and is forbidden from returning need_clarification.
      - Parses the response as LayoutIntent JSON, validates, compiles, and
        regenerates the draw.io XML entirely server-side.

    When prev_spec is absent (legacy / test path):
      - Falls back to run_pipeline with the BOM prompt + appended feedback.
    """
    request_id = str(uuid.uuid4())
    input_hash = compute_input_hash(req.feedback or "")

    try:
        # ── Reconstruct items, base prompt, and deployment hints ───────────────
        if req.items_json and req.prompt:
            from agent.bom_parser import ServiceItem
            raw   = json.loads(req.items_json)
            items = [ServiceItem(**r) for r in raw]
            base_prompt = req.prompt
        else:
            raise HTTPException(
                status_code=400,
                detail="items_json and prompt are required for /refine (echo from _refine_context).",
            )

        deployment_hints: dict = {}
        if req.deployment_hints_json:
            try:
                deployment_hints = json.loads(req.deployment_hints_json)
            except (json.JSONDecodeError, ValueError):
                deployment_hints = {}

        if req.prev_spec:
            # ── Direct editor path — bypass run_pipeline entirely ─────────────
            # call_text_llm uses DIAGRAM_EDIT_SYSTEM (never returns need_clarification).
            # The LLM receives: current LayoutIntent JSON + the single change request.
            # Available service IDs are listed so the LLM can reference existing nodes.
            available_ids = ", ".join(f"{i.id} ({i.oci_type})" for i in items)
            edit_prompt = (
                "CURRENT DIAGRAM (LayoutIntent JSON — modify this):\n"
                + req.prev_spec
                + "\n\nAVAILABLE SERVICE IDs (from BOM — use these exact IDs for existing nodes):\n"
                + available_ids
                + "\n\nREQUESTED CHANGE:\n"
                + req.feedback.strip()
                + "\n\nOutput the COMPLETE updated LayoutIntent JSON."
            )

            # call_diagram_editor_llm uses editor_runner (temperature=0, sufficient
            # max_tokens) when available; falls back to text_runner for tests.
            raw_text = await call_diagram_editor_llm(edit_prompt, DIAGRAM_EDIT_SYSTEM)

            try:
                intent_data = json.loads(clean_json(raw_text))
            except (json.JSONDecodeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Diagram editor returned invalid JSON: {exc}. Raw: {raw_text[:400]!r}",
                )

            # Guard: LLM ignored the system message and returned need_clarification.
            # Treat the existing spec as the fallback — no-op edit is better than
            # throwing an error for a user-facing refinement request.
            if intent_data.get("status") == "need_clarification":
                logger.warning(
                    "/refine: editor LLM returned need_clarification despite system message — "
                    "falling back to prev_spec unchanged"
                )
                intent_data = json.loads(req.prev_spec)

            # ── Validate + compile LayoutIntent → flat spec ───────────────────
            from agent.layout_intent import validate_layout_intent, LayoutIntentError
            from agent.intent_compiler import compile_intent_to_flat_spec

            layout_intent_spec = intent_data   # preserve for _refine_context

            if "placements" in intent_data:
                try:
                    def _compile():
                        intent = validate_layout_intent(intent_data, items)
                        return compile_intent_to_flat_spec(intent, items)
                    spec = await anyio.to_thread.run_sync(_compile)
                except LayoutIntentError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Edited LayoutIntent is invalid: {exc}",
                    )
            else:
                # No placements key → treat as a compiled flat spec directly
                spec = intent_data

            # ── Multi-compartment guard — same as run_pipeline ────────────────
            mr_mode = deployment_hints.get("multi_region_mode")
            if spec.get("deployment_type") == "multi_compartment":
                mr_mode = None

            # ── Layout engine (CPU-bound) ─────────────────────────────────────
            items_by_id = {i.id: i for i in items}
            draw_dict = await anyio.to_thread.run_sync(
                functools.partial(spec_to_draw_dict, spec, items_by_id)
            )

            # ── Multi-region post-processing (duplicate DR/HA stub) ───────────
            page_w = spec.get("page", {}).get("width", 1654)
            page_h = spec.get("page", {}).get("height", 1169)
            if mr_mode == "duplicate_drha":
                regions = spec.get("regions", [])
                secondary_label = "Duplicate DR/HA Region"
                if len(regions) >= 2:
                    secondary_label = f"Duplicate DR/HA Region — {regions[1].get('label', '')}"
                primary_box = next(
                    (b for b in draw_dict["boxes"] if b.get("box_type") == "_region_box"), None
                )
                stub_x = primary_box["x"]                            if primary_box else 144
                stub_y = (primary_box["y"] + primary_box["h"] + 40) if primary_box else 300
                stub_w = primary_box["w"]                            if primary_box else 600
                draw_dict["boxes"].append({
                    "id": "region_secondary_stub", "label": secondary_label,
                    "box_type": "_region_stub", "tier": "",
                    "x": stub_x, "y": stub_y, "w": stub_w, "h": 90,
                })
            elif mr_mode == "split_workloads":
                page_w = 3308

            # ── Render manifest ───────────────────────────────────────────────
            render_manifest = {
                "page": {"width": page_w, "height": page_h},
                "deployment_type":   spec.get("deployment_type", "single_ad"),
                "node_count":        len(draw_dict.get("nodes", [])),
                "edge_count":        len(draw_dict.get("edges", [])),
                "multi_region_mode": mr_mode,
            }

            # ── Node-to-resource map ──────────────────────────────────────────
            node_to_resource_map: dict = {
                n["id"]: {"oci_type": n.get("type", ""), "label": n.get("label", "")}
                for n in draw_dict.get("nodes", [])
            }
            for item in items:
                if item.id in node_to_resource_map:
                    node_to_resource_map[item.id]["layer"] = item.layer
                else:
                    node_to_resource_map[item.id] = {
                        "oci_type": item.oci_type, "label": item.label, "layer": item.layer,
                    }

            # ── Write draw.io file ────────────────────────────────────────────
            drawio_path = OUTPUT_DIR / f"{req.diagram_name}.drawio"
            await anyio.to_thread.run_sync(
                functools.partial(generate_drawio, draw_dict, drawio_path)
            )
            drawio_xml = await anyio.to_thread.run_sync(drawio_path.read_text)

            # ── Persist artifacts ─────────────────────────────────────────────
            object_store    = getattr(app.state, "object_store", None)
            persistence_cfg = getattr(app.state, "persistence_config", None) or {}
            prefix          = persistence_cfg.get("prefix", "diagrams")
            persisted_version = 0
            if object_store is not None:
                artifacts = {
                    "diagram.drawio":            drawio_xml.encode("utf-8"),
                    "spec.json":                 json.dumps(spec).encode("utf-8"),
                    "draw_dict.json":            json.dumps(draw_dict).encode("utf-8"),
                    "render_manifest.json":      json.dumps(render_manifest).encode("utf-8"),
                    "node_to_resource_map.json": json.dumps(node_to_resource_map).encode("utf-8"),
                }
                latest = await anyio.to_thread.run_sync(
                    functools.partial(
                        persist_artifacts,
                        object_store, prefix, req.client_id, req.diagram_name, artifacts,
                    )
                )
                if latest:
                    persisted_version = latest.get("version", 0)
                    drawio_key = str(latest.get("artifacts", {}).get("diagram.drawio", "") or "")
                else:
                    drawio_key = ""
            else:
                drawio_key = ""

            if GIT_PUSH_ENABLED:
                threading.Thread(
                    target=_push_diagram_to_git,
                    args=(drawio_xml, req.client_id, req.diagram_name, persisted_version),
                    daemon=True,
                ).start()

            # ── Build response ────────────────────────────────────────────────
            result: dict = {
                "status":               "ok",
                "agent_version":        AGENT_VERSION,
                "schema_version":       SCHEMA_VERSION,
                "client_id":            req.client_id,
                "diagram_name":         req.diagram_name,
                "request_id":           request_id,
                "input_hash":           input_hash,
                "output_path":          str(drawio_path),
                "drawio_xml":           drawio_xml,
                "spec":                 spec,
                "draw_dict":            draw_dict,
                "render_manifest":      render_manifest,
                "node_to_resource_map": node_to_resource_map,
                "download": {
                    "url": (
                        f"/download/diagram.drawio"
                        f"?client_id={req.client_id}&diagram_name={req.diagram_name}"
                    ),
                    "object_storage_latest": (
                        f"{prefix}/{req.client_id}/{req.diagram_name}/LATEST.json"
                    ),
                },
                "errors": [],
                "_refine_context": {
                    "items_json":            req.items_json,
                    "prompt":                req.prompt,   # preserve original BOM prompt
                    "prev_spec":             json.dumps(layout_intent_spec),
                    "deployment_hints_json": json.dumps(deployment_hints),
                },
            }
            if drawio_key:
                result["drawio_key"] = drawio_key
                result["object_key"] = drawio_key
            return JSONResponse(status_code=200, content=result)

        else:
            # ── Fallback: no prev_spec — use run_pipeline with appended feedback ──
            enriched_prompt = (
                base_prompt
                + "\n\n═══════════════════════════════════════════════════════\n"
                + "DIAGRAM REFINEMENT REQUEST:\n"
                + req.feedback.strip()
                + "\n\nApply the requested changes. "
                + "Return the COMPLETE updated LayoutIntent JSON. "
                + "Output ONLY valid JSON."
            )

            result = await run_pipeline(
                items            = items,
                prompt           = enriched_prompt,
                diagram_name     = req.diagram_name,
                client_id        = req.client_id,
                request_id       = request_id,
                input_hash       = input_hash,
                deployment_hints = deployment_hints,
                reference_context_text=req.feedback,
            )

            # Restore original (un-enriched) prompt so subsequent refinements
            # don't accumulate stacked prompts.
            if isinstance(result, dict) and "_refine_context" in result:
                result["_refine_context"]["prompt"] = req.prompt

            return JSONResponse(status_code=200, content=result)

    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {exc}")
    except Exception as exc:
        logger.error("Error in /refine: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


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
            reference_context_text=context_total,
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


def _download_pptx_artifact(key: str) -> Response:
    object_store = getattr(app.state, "object_store", None)
    if object_store is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = object_store.get(key)
    except KeyError:
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    artifact_filename = key.split("/")[-1]
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact_filename}"'},
    )


@app.get("/download")
@app.get("/api/download")
async def download_artifact_by_key(
    key: Optional[str] = Query(default=None),
    _user: dict = Depends(require_user),
):
    if key and key.endswith(".pptx"):
        return _download_pptx_artifact(key)
    raise HTTPException(status_code=400, detail="Query param key is required.")


@app.get("/download/{filename}")
@app.get("/api/download/{filename}")
async def download_file(
    filename:     str,
    client_id:    Optional[str] = Query(default=None),
    diagram_name: Optional[str] = Query(default=None),
    key:          Optional[str] = Query(default=None),
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
    if key and key.endswith(".pptx"):
        return _download_pptx_artifact(key)

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


@app.get("/api/job/{job_id}")
async def get_job(job_id: str, _user: dict = Depends(require_user)):
    """
    Poll for the result of an async job started by /api/upload-bom or /api/clarify.
    Returns {"status":"pending",...} while the job is running,
    or the full result dict when complete,
    or raises HTTP 500 if the job failed.
    """
    job = _JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    if job["status"] == "pending":
        return JSONResponse(status_code=200, content={"status": "pending", "job_id": job_id})
    if job["status"] == "error":
        raise HTTPException(status_code=500, detail=job["error"] or "Job failed")
    return JSONResponse(status_code=200, content=job["result"])


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


async def _run_freeform_diagram_pipeline(
    *,
    notes: str,
    context: str,
    questionnaire: str,
    diagram_name: str,
    client_id: str,
    request_id: str,
    input_hash: str,
    deployment_hints: Optional[dict] = None,
) -> dict:
    try:
        items, prompt = freeform_arch_text_to_llm_input(
            notes,
            context=context,
            questionnaire_text=questionnaire,
        )
    except ValueError:
        return _freeform_clarify_response(
            client_id=client_id,
            diagram_name=diagram_name,
            request_id=request_id,
            input_hash=input_hash,
            notes=notes,
            context=context,
            questionnaire=questionnaire,
            deployment_hints=deployment_hints or {},
        )

    return await run_pipeline(
        items=items,
        prompt=prompt,
        diagram_name=diagram_name,
        client_id=client_id,
        request_id=request_id,
        input_hash=input_hash,
        deployment_hints=deployment_hints,
        reference_context_text="\n\n".join(part for part in (notes, context, questionnaire) if part and part.strip()),
    )


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


# ── /api/chat convenience endpoints ─────────────────────────────────────────

def _make_orchestrator_text_runner():
    """
    Return an async callable (prompt, system_msg) -> str for the orchestrator.
    Uses the inference runner already wired to app.state.
    """
    runner = getattr(app.state, "llm_runner", None)

    def _sync_runner(prompt: str, system_msg: str, model_profile: str = "orchestrator") -> str:
        if runner is None:
            raise RuntimeError("LLM runner not initialised.")
        # The inference runner is a (prompt, client_id) callable that returns
        # parsed JSON or raises.  For the orchestrator we need raw text back.
        # We use a dedicated inference call with the writing model settings.
        if _INFERENCE_AVAILABLE and INFERENCE_ENABLED:
            from agent.llm_inference_client import run_inference
            profile = (model_profile or "orchestrator").strip()
            llm_cfg = resolve_agent_llm_config(_cfg, profile)
            return run_inference(
                prompt=prompt,
                system_message=system_msg,
                model_id=llm_cfg.get("model_id", INFERENCE_MODEL_ID),
                endpoint=llm_cfg.get("service_endpoint", INFERENCE_ENDPOINT),
                compartment_id=COMPARTMENT_ID,
                max_tokens=int(llm_cfg.get("max_tokens", 4000)),
                temperature=float(llm_cfg.get("temperature", 0.7)),
                top_p=float(llm_cfg.get("top_p", 0.9)),
                top_k=int(llm_cfg.get("top_k", 0)),
            )
        raise RuntimeError("Inference not enabled — cannot run orchestrator.")

    async def _async_runner(prompt: str, system_msg: str, model_profile: str = "orchestrator") -> str:
        import asyncio
        return await asyncio.to_thread(_sync_runner, prompt, system_msg, model_profile)

    return _async_runner


def _make_orchestrator_tool_runner():
    """
    Return an async callable (prompt, system_msg, tools, label) -> dict | str
    for native tool use in the orchestrator loop.
    """
    def _sync_runner(
        prompt: str,
        system_msg: str,
        schemas: list,
        model_profile: str = "orchestrator",
    ) -> dict | str:
        if not (_INFERENCE_AVAILABLE and INFERENCE_ENABLED):
            raise RuntimeError("Inference not enabled.")
        from agent.llm_inference_client import run_inference_with_tools
        llm_cfg = resolve_agent_llm_config(_cfg, model_profile)
        return run_inference_with_tools(
            prompt=prompt,
            system_message=system_msg,
            tools=[s.to_api_dict() for s in schemas],
            tool_choice="auto",
            model_id=llm_cfg.get("model_id", INFERENCE_MODEL_ID),
            endpoint=llm_cfg.get("service_endpoint", INFERENCE_ENDPOINT),
            compartment_id=COMPARTMENT_ID,
            max_tokens=int(llm_cfg.get("max_tokens", 4000)),
            temperature=float(llm_cfg.get("temperature", 0.0)),
            top_p=float(llm_cfg.get("top_p", 0.9)),
        )

    async def _async_runner(
        prompt: str,
        system_msg: str,
        schemas: list,
        model_profile: str = "orchestrator",
    ) -> dict | str:
        import asyncio
        return await asyncio.to_thread(_sync_runner, prompt, system_msg, schemas, model_profile)

    return _async_runner


def _make_terraform_text_runner():
    """
    Return a sync callable (prompt, system_msg) -> str for Terraform stages.
    """
    def _text_runner(prompt: str, system_msg: str) -> str:
        if _INFERENCE_AVAILABLE and INFERENCE_ENABLED:
            from agent.llm_inference_client import run_inference
            llm_cfg = resolve_agent_llm_config(_cfg, "terraform")
            return run_inference(
                prompt=prompt,
                system_message=system_msg,
                model_id=llm_cfg.get("model_id", TERRAFORM_MODEL_ID),
                endpoint=llm_cfg.get("service_endpoint", INFERENCE_ENDPOINT),
                compartment_id=COMPARTMENT_ID,
                max_tokens=int(llm_cfg.get("max_tokens", TERRAFORM_MAX_TOKENS)),
                temperature=float(llm_cfg.get("temperature", TERRAFORM_TEMPERATURE)),
                top_p=float(llm_cfg.get("top_p", TERRAFORM_TOP_P)),
                top_k=int(llm_cfg.get("top_k", TERRAFORM_TOP_K)),
            )
        raise RuntimeError("Inference not enabled - cannot run Terraform graph.")

    return _text_runner


async def _run_orchestrator_turn(
    *,
    req: OrchestratorChatRequest,
    store,
    text_runner,
    tool_runner=None,
    orch_cfg: dict,
    reasoning_sink=None,
) -> dict:
    """
    Run one orchestrator turn via legacy or LangGraph-compatible adapter.
    """
    max_tool_iterations = int(orch_cfg.get("max_tool_iterations", 5))
    max_refinements = int(orch_cfg.get("max_refinements", 3))
    a2a_base_url = os.environ.get("A2A_BASE_URL", "http://localhost:8080")
    specialist_mode = "langgraph" if bool(
        orch_cfg.get("specialists_langgraph_enabled", False)
    ) else "legacy"

    from agent import orchestrator_agent

    return await orchestrator_agent.run_turn(
        customer_id=req.customer_id,
        customer_name=req.customer_name,
        user_message=req.message,
        store=store,
        text_runner=text_runner,
        tool_runner=tool_runner,
        a2a_base_url=a2a_base_url,
        max_tool_iterations=max_tool_iterations,
        specialist_mode=specialist_mode,
        max_refinements=max_refinements,
        reasoning_sink=reasoning_sink,
        se_id=getattr(req, "se_id", "default"),
    )


def _chunk_reply_text(text: str, chunk_size: int = 48) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for token in text.split():
        candidate = f"{current} {token}".strip()
        if current and len(candidate) > chunk_size:
            chunks.append(current)
            current = token
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


_TOOL_WAITING_LABELS = {
    "generate_bom": ("BOM", "BOM specialist"),
    "generate_diagram": ("diagram", "diagram specialist"),
    "generate_terraform": ("Terraform", "Terraform specialist"),
    "generate_pov": ("POV", "POV specialist"),
    "generate_jep": ("JEP", "JEP specialist"),
    "generate_waf": ("Well-Architected", "Well-Architected specialist"),
    "generate_poc_plan": ("POC Strategy", "POC strategist — 3 parallel evaluations"),
    "generate_presentation": ("Presentation", "presentation specialist"),
    "save_notes": ("notes", "notes tool"),
    "get_summary": ("summary", "summary tool"),
    "get_document": ("document", "document tool"),
}


def _tool_started_stream_event(
    *,
    event: str,
    customer_id: str,
    trace_id: str,
) -> dict | None:
    prefix = "tool_started:"
    if not str(event or "").startswith(prefix):
        return None
    tool_name = str(event or "")[len(prefix):].strip()
    if not tool_name:
        return None
    hat, label = _TOOL_WAITING_LABELS.get(tool_name, (tool_name, tool_name))
    return {
        "trace_id": trace_id,
        "customer_id": customer_id,
        "event_type": "status",
        "status": "tool_started",
        "tool": tool_name,
        "hat": hat,
        "message": f"Archie put on the {hat} hat and is calling the {label}.",
    }


async def _persist_bom_xlsx_downloads(customer_id: str, store, result: dict) -> dict:
    return await _server_persist_bom_xlsx_downloads(
        customer_id,
        store,
        result,
        bom_service_factory=lambda: getattr(app.state, "bom_service", None) or get_shared_bom_service(),
        logger=logger,
    )


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
    reference_selection_hint = dict(inp.get("reference_architecture") or {})

    # ── Resolve resources ────────────────────────────────────────────────────
    raw_resources = None
    if "resources_from_bucket" in inp and inp["resources_from_bucket"]:
        ref = A2AObjectRef(**inp["resources_from_bucket"])
        raw_resources = await _a2a_fetch_resources(ref)
    elif "resources" in inp and inp["resources"]:
        raw_resources = inp["resources"]

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
    if raw_resources is None:
        if not notes.strip():
            raise HTTPException(422, "generate_diagram requires 'resources', 'resources_from_bucket', or inline BOM notes")
        aux_context = context
        if questionnaire.strip():
            aux_context = f"{aux_context}\n\nQUESTIONNAIRE:\n{questionnaire}".strip() if aux_context else f"QUESTIONNAIRE:\n{questionnaire}"
        try:
            items, prompt = inline_bom_text_to_llm_input(notes, context=aux_context, questionnaire_text=questionnaire)
        except ValueError:
            input_hash = compute_input_hash(
                notes.strip(), "\n", aux_context, "\n", canonical_json(deployment_hints)
            )
            return await _run_freeform_diagram_pipeline(
                notes=notes,
                context=aux_context,
                questionnaire=questionnaire,
                diagram_name=diagram_name,
                client_id=task.client_id,
                request_id=request_id,
                input_hash=input_hash,
                deployment_hints=deployment_hints,
            )
        input_hash = compute_input_hash(
            notes.strip(), "\n", aux_context, "\n", canonical_json(deployment_hints)
        )
    else:
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
        prompt = build_layout_intent_prompt(items, context=context_total)
    cache_key = (task.client_id, diagram_name, input_hash)
    if cache_key in IDEMPOTENCY_CACHE:
        return IDEMPOTENCY_CACHE[cache_key]
    result = await run_pipeline(
        items,
        prompt,
        diagram_name,
        task.client_id,
        request_id,
        input_hash,
        deployment_hints=deployment_hints,
        reference_context_text=context_total or notes,
        reference_selection_hint=reference_selection_hint,
    )
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

    result = await run_pipeline(
        items,
        prompt,
        diagram_name,
        task.client_id,
        request_id,
        input_hash,
        reference_context_text=context,
    )
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

    if pending.get("freeform_notes"):
        combined_notes = (
            str(pending.get("freeform_notes", "") or "").strip()
            + f"\n\nCLARIFICATION ANSWERS:\n{answers.strip()}\n"
        ).strip()
        result = await _run_freeform_diagram_pipeline(
            notes=combined_notes,
            context=str(pending.get("freeform_context", "") or ""),
            questionnaire=str(pending.get("freeform_questionnaire", "") or ""),
            diagram_name=diagram_name,
            client_id=task.client_id,
            request_id=request_id,
            input_hash=input_hash,
            deployment_hints=dict(pending.get("deployment_hints") or {}),
        )
    else:
        enriched = (
            pending["prompt"]
            + f"\n\nCLARIFICATION ANSWERS:\n{answers.strip()}\n\n"
            + "Now produce the layout spec JSON. Output ONLY valid JSON."
        )
        result = await run_pipeline(
            pending["items"], enriched, diagram_name,
            task.client_id, request_id, input_hash,
            reference_context_text=answers,
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


async def call_diagram_editor_llm(prompt: str, system_message: str = "") -> str:
    """
    Async wrapper for the diagram editor (/api/refine).

    Prefers app.state.editor_runner (temperature=0, sufficient max_tokens for
    full LayoutIntent JSON output) over app.state.text_runner (temperature=0.7,
    writing-agent budget).  Falls back to text_runner so that tests that inject
    only text_runner still work without extra setup.
    """
    runner = (
        getattr(app.state, "editor_runner", None)
        or getattr(app.state, "text_runner", None)
    )
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Diagram editor runner is not initialised. "
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




def _route_dependencies() -> SimpleNamespace:
    return SimpleNamespace(
        app=lambda: app,
        logger=logger,
        agent_id=AGENT_ID,
        agent_version=AGENT_VERSION,
        require_user=require_user,
        require_admin_user=require_admin_user,
        current_trace_id=_current_trace_id,
        config=lambda: _cfg,
        fleet_config=lambda: FLEET_CFG,
        default_model_id=lambda: INFERENCE_MODEL_ID,
        require_object_store=_require_object_store,
        new_job=_new_job,
        complete_job=_complete_job,
        fail_job=_fail_job,
        build_artifact_manifest=_build_artifact_manifest,
        persist_bom_xlsx_downloads=_persist_bom_xlsx_downloads,
        make_orchestrator_text_runner=_make_orchestrator_text_runner,
        make_orchestrator_tool_runner=_make_orchestrator_tool_runner,
        run_orchestrator_turn=lambda: _run_orchestrator_turn,
        writing_inference_config=lambda: _WRITING_INFERENCE_CONFIG,
        inference_settings=lambda: {
            "endpoint": INFERENCE_ENDPOINT,
            "model_id": INFERENCE_MODEL_ID,
            "compartment_id": COMPARTMENT_ID,
            "max_tokens": WRITING_MAX_TOKENS,
            "temperature": WRITING_TEMPERATURE,
            "top_p": WRITING_TOP_P,
            "top_k": WRITING_TOP_K,
        },
        ensure_waf_test_pillars=_ensure_waf_test_pillars,
        generate_pov=lambda: generate_pov,
        generate_jep=lambda: generate_jep,
        kickoff_jep=lambda: kickoff_jep,
        generate_waf=lambda: generate_waf,
        a2a_tasks=lambda: A2A_TASKS,
        a2a_generate_diagram=lambda: _a2a_generate_diagram,
        a2a_upload_bom=lambda: _a2a_upload_bom,
        a2a_clarify=lambda: _a2a_clarify,
    )


_deps = _route_dependencies()
_a2a_router = create_a2a_router(_deps)
_build_agent_card = _a2a_router.build_agent_card
_make_a2a_task = _a2a_router.make_a2a_task
app.include_router(create_bom_router(_deps))
app.include_router(create_briefing_router(_deps))
app.include_router(create_chat_router(_deps))
app.include_router(_a2a_router)
app.include_router(create_documents_router(_deps))



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
