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
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, Response, StreamingResponse
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
    clear_conversation_history,
    clear_conversation_summary,
    list_conversation_summaries,
    list_project_summaries,
    normalize_project_id,
    save_project_engagement,
    save_terraform_bundle,
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
    get_new_notes,
    build_context_summary,
)
from agent.runtime_config import resolve_agent_llm_config
from agent.bom_service import get_shared_bom_service, new_trace_id

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
_JOB_STORE: Dict[str, dict] = {}  # job_id → {status, result, error, created_at}
_JOB_TTL = 3600                   # seconds — jobs expire after 1 hour


def _new_job() -> str:
    """Create a pending job entry and return its ID."""
    import time as _t
    jid = str(uuid.uuid4())
    _JOB_STORE[jid] = {"status": "pending", "result": None, "error": None, "created_at": _t.time()}
    # Opportunistically prune expired jobs
    cutoff = _t.time() - _JOB_TTL
    for k in [k for k, v in list(_JOB_STORE.items()) if v["created_at"] < cutoff]:
        del _JOB_STORE[k]
    return jid


def _complete_job(jid: str, result: dict) -> None:
    if jid in _JOB_STORE:
        _JOB_STORE[jid]["status"] = "complete"
        _JOB_STORE[jid]["result"] = result


def _fail_job(jid: str, detail: str) -> None:
    if jid in _JOB_STORE:
        _JOB_STORE[jid]["status"] = "error"
        _JOB_STORE[jid]["error"]  = detail


# ── Pydantic models ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:   str
    client_id: Optional[str] = "default"


class ClarifyRequest(BaseModel):
    answers:               str
    client_id:             Optional[str] = "default"
    diagram_name:          Optional[str] = "oci_architecture"
    # Stateless path: client echoes these back from the need_clarification response.
    # When present, /clarify uses them directly instead of looking up PENDING_CLARIFY.
    items_json:            Optional[str] = None
    prompt:                Optional[str] = None
    deployment_hints_json: Optional[str] = None
    # Auto WAF orchestration: echo back from upload-bom if auto_waf=True
    auto_waf:              Optional[bool] = False
    customer_id:           Optional[str]  = ""
    customer_name:         Optional[str]  = ""


class RefineRequest(BaseModel):
    """Request to refine an already-generated diagram based on free-text feedback."""
    feedback:     str
    client_id:    Optional[str] = "default"
    diagram_name: Optional[str] = "oci_architecture"
    # Stateless: echo back from the _refine_context field of the ok response
    items_json:            Optional[str] = None
    prompt:                Optional[str] = None
    prev_spec:             Optional[str] = None   # JSON-encoded previous LayoutIntent
    deployment_hints_json: Optional[str] = None   # echo from _refine_context to preserve mr_mode


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


class JepRevisionRequest(BaseModel):
    customer_id: str
    reason: Optional[str] = None


class WafRequest(BaseModel):
    customer_id:   str
    customer_name: str
    feedback:      Optional[str] = None


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


class TerraformGenerateRequest(BaseModel):
    customer_id: str
    customer_name: str
    prompt: Optional[str] = ""


def _terraform_fallback_files() -> dict[str, str]:
    """Deterministic OCI Terraform starter bundle used when graph asks for clarification."""
    return {
        "main.tf": (
            'terraform {\n'
            '  required_version = ">= 1.4.0"\n'
            "  required_providers {\n"
            "    oci = {\n"
            '      source  = "oracle/oci"\n'
            '      version = ">= 5.0.0"\n'
            "    }\n"
            "  }\n"
            "}\n\n"
            'provider "oci" {\n'
            "  region = var.region\n"
            "}\n\n"
            'resource "oci_core_vcn" "main" {\n'
            "  compartment_id = var.compartment_id\n"
            '  cidr_block     = "10.0.0.0/16"\n'
            '  display_name   = "${var.prefix}-vcn"\n'
            '  dns_label      = "mainvcn"\n'
            "}\n\n"
            'resource "oci_core_subnet" "private" {\n'
            "  compartment_id      = var.compartment_id\n"
            "  vcn_id              = oci_core_vcn.main.id\n"
            '  cidr_block          = "10.0.1.0/24"\n'
            '  display_name        = "${var.prefix}-private-subnet"\n'
            '  dns_label           = "privsub"\n'
            "  prohibit_public_ip_on_vnic = true\n"
            "}\n"
        ),
        "variables.tf": (
            'variable "region" {\n'
            '  type    = string\n'
            '  default = "us-ashburn-1"\n'
            "}\n\n"
            'variable "compartment_id" {\n'
            "  type = string\n"
            "}\n\n"
            'variable "prefix" {\n'
            "  type    = string\n"
            '  default = "ai-poc"\n'
            "}\n"
        ),
        "outputs.tf": (
            'output "vcn_id" {\n'
            "  value = oci_core_vcn.main.id\n"
            "}\n\n"
            'output "private_subnet_id" {\n'
            "  value = oci_core_subnet.private.id\n"
            "}\n"
        ),
        "terraform.tfvars.example": (
            'region         = "us-ashburn-1"\n'
            'compartment_id = "ocid1.compartment.oc1..exampleuniqueID"\n'
            'prefix         = "install-test"\n'
        ),
    }


# ── A2A v1.0 (Oracle Agent Spec 26.1.0) models ────────────────────────────────

class A2Av1Part(BaseModel):
    kind:     str = "text"
    text:     str = ""
    data:     dict = {}
    mimeType: str = ""


class A2Av1Message(BaseModel):
    role:      str = "user"
    parts:     List[A2Av1Part] = []
    contextId: str = ""
    messageId: str = ""


class A2Av1JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id:      str = ""
    method:  str = ""
    params:  dict = {}


# In-memory task store for A2A v1.0 tasks (keyed by task_id)
A2A_TASKS: Dict[str, dict] = {}


class OrchestratorChatRequest(BaseModel):
    customer_id:   str
    customer_name: str
    message:       str
    project_id:    Optional[str] = None
    project_name:  Optional[str] = None


class BomConversationTurn(BaseModel):
    role: str
    content: str


class BomChatRequest(BaseModel):
    message: str
    conversation: List[BomConversationTurn] = []
    model_id: Optional[str] = None


class BomXlsxRequest(BaseModel):
    bom_payload: Dict[str, Any]


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
    """Serve the React SPA. Falls back to legacy index.html when dist not built."""
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
    return FileResponse(str(_LEGACY_INDEX), headers=headers)


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


@app.post("/clarify")
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


@app.post("/refine")
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


@app.get("/api/bom/config")
async def bom_config(_user: dict = Depends(require_user)):
    service = getattr(app.state, "bom_service", None) or get_shared_bom_service()
    default_model_id = INFERENCE_MODEL_ID or "bom-default"
    return service.config(default_model_id=default_model_id)


@app.get("/api/bom/health")
async def bom_health(_user: dict = Depends(require_user)):
    service = getattr(app.state, "bom_service", None) or get_shared_bom_service()
    payload = service.health()
    payload["trace_id"] = _current_trace_id()
    return payload


@app.post("/api/bom/chat")
async def bom_chat(req: BomChatRequest, _user: dict = Depends(require_user)):
    service = getattr(app.state, "bom_service", None) or get_shared_bom_service()
    trace_id = _current_trace_id() or new_trace_id()
    model_id = req.model_id or INFERENCE_MODEL_ID or "bom-default"
    started = time.perf_counter()
    result = await anyio.to_thread.run_sync(
        functools.partial(
            service.chat,
            message=req.message,
            conversation=[{"role": t.role, "content": t.content} for t in req.conversation],
            trace_id=trace_id,
            model_id=model_id,
            text_runner=getattr(app.state, "text_runner", None),
        )
    )
    trace = result.get("trace", {}) if isinstance(result, dict) else {}
    logger.info(
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


@app.post("/api/bom/generate-xlsx")
async def bom_generate_xlsx(req: BomXlsxRequest, _user: dict = Depends(require_user)):
    service = getattr(app.state, "bom_service", None) or get_shared_bom_service()
    workbook_bytes = await anyio.to_thread.run_sync(
        functools.partial(service.generate_xlsx, req.bom_payload)
    )
    filename = f"oci-bom-{time.strftime('%Y%m%d-%H%M%S')}.xlsx"
    return StreamingResponse(
        io.BytesIO(workbook_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )


@app.get("/api/bom/{customer_id}/download/{filename}")
async def bom_download_xlsx(
    customer_id: str,
    filename: str,
    _user: dict = Depends(require_user),
):
    safe_filename = _validate_bom_xlsx_filename(filename)
    object_store = getattr(app.state, "object_store", None)
    if object_store is None:
        raise HTTPException(status_code=404, detail="File not found")
    key = _bom_xlsx_key(customer_id, safe_filename)
    if not _bom_xlsx_download_is_valid(object_store, key):
        raise HTTPException(status_code=404, detail="BOM XLSX file not found")
    try:
        data = object_store.get(key)
    except KeyError:
        raise HTTPException(status_code=404, detail="BOM XLSX file not found")
    return Response(
        content=data,
        media_type=_BOM_XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


@app.post("/api/bom/refresh-data")
async def bom_refresh_data(_user: dict = Depends(require_admin_user)):
    service = getattr(app.state, "bom_service", None) or get_shared_bom_service()
    started = time.perf_counter()
    payload = await anyio.to_thread.run_sync(service.refresh_data)
    logger.info(
        "bom_refresh_data trace_id=%s source=%s pricing_skus=%s latency_ms=%d",
        _current_trace_id(),
        payload.get("source", "unknown"),
        payload.get("pricing_sku_count", 0),
        int((time.perf_counter() - started) * 1000),
    )
    payload["trace_id"] = _current_trace_id()
    return payload


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
    """Oracle Agent Spec v26.1.0 schemaVersion 1.0 card (primary)."""
    host = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8000")
    orch_cfg = _cfg.get("orchestrator", {})
    return JSONResponse({
        "schemaVersion": "1.0",
        "humanReadableId": f"oracle-oci-fleet/{AGENT_ID}",
        "name": "OCI SA Orchestrator + Drawing Agent",
        "agentVersion": AGENT_VERSION,
        "url": host,
        "provider": {"name": "Oracle"},
        "capabilities": {"streaming": False, "pushNotifications": False},
        "authSchemes": [{"type": "none"}],
        "skills": [
            {
                "id": "orchestrate_engagement",
                "name": "Orchestrate SA Engagement",
                "description": (
                    "Accept a natural-language SA message and orchestrate "
                    "notes intake, POV, diagram, WAF, or JEP generation. "
                    "Uses contextId as customer_id for session continuity."
                ),
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain", "application/json"],
            },
            {
                "id": "generate_diagram",
                "name": "Generate Architecture Diagram",
                "description": "Generate OCI draw.io diagram from BOM or resource list.",
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["application/json"],
            },
        ],
        "fleet": {
            "fleet_id":     FLEET_CFG.get("fleet_id", "oci-agent-fleet"),
            "position":     FLEET_CFG.get("position", 3),
            "total_agents": FLEET_CFG.get("total_agents", 7),
        },
    })


@app.get("/.well-known/agent-card-legacy.json")
def agent_card_legacy():
    """Legacy schema_version 0.1 card — kept for backward compatibility."""
    host = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8000")
    return JSONResponse(_build_agent_card(host))


# ── A2A v1.0 (Oracle Agent Spec 26.1.0) endpoints ────────────────────────────

def _make_a2a_task(context_id: str) -> dict:
    """Create and register a new A2A v1.0 Task dict."""
    task_id = str(uuid.uuid4())
    task = {
        "id":        task_id,
        "contextId": context_id,
        "status":    "SUBMITTED",
        "artifacts": [],
    }
    A2A_TASKS[task_id] = task
    return task


@app.post("/message:send")
async def a2a_message_send(request: Request):
    """
    A2A v1.0 /message:send — JSON-RPC 2.0 entry point.

    Routes by params.skill:
      orchestrate_engagement  → orchestrator_agent.run_turn()
      generate_diagram        → existing drawing pipeline
      (no skill)              → defaults to orchestrate_engagement
    """
    body = await request.json()
    rpc_id  = body.get("id", "")
    params  = body.get("params", {})
    message = params.get("message", {})
    context_id = message.get("contextId", "default")
    skill      = params.get("skill", "orchestrate_engagement")

    # Extract text from message parts
    parts = message.get("parts", [])
    user_text = " ".join(
        p.get("text", "") for p in parts if p.get("kind", "text") == "text"
    ).strip()

    task = _make_a2a_task(context_id)
    task["status"] = "WORKING"

    try:
        if skill == "generate_diagram":
            # Delegate to internal drawing pipeline via the existing A2A handler
            legacy_task = A2ATask(
                task_id=task["id"],
                skill="generate_diagram",
                inputs={"resources": [], "notes": user_text},
                client_id=context_id,
            )
            result = await _a2a_generate_diagram(legacy_task)
            result_status = str(result.get("status", "error") or "error").lower()
            if result_status == "ok":
                task["status"] = "COMPLETED"
            elif result_status == "need_clarification":
                task["status"] = "INPUT_REQUIRED"
            else:
                task["status"] = "FAILED"

            artifacts = [
                {
                    "artifactId": "a1",
                    "name": "reply",
                    "parts": [{"kind": "text", "text": result_status}],
                },
            ]
            drawio_key = (result.get("object_key") or result.get("drawio_key") or "")
            if drawio_key:
                artifacts.append(
                    {
                        "artifactId": "a2",
                        "name": "drawio_key",
                        "parts": [{"kind": "data", "mimeType": "application/json",
                                   "data": {"key": drawio_key}}],
                    }
                )
            questions = result.get("questions", [])
            if isinstance(questions, list) and questions:
                artifacts.append(
                    {
                        "artifactId": "a3",
                        "name": "questions",
                        "parts": [{"kind": "data", "mimeType": "application/json",
                                   "data": {"questions": questions}}],
                    }
                )
            clarify_context = result.get("_clarify_context")
            if isinstance(clarify_context, dict) and clarify_context:
                artifacts.append(
                    {
                        "artifactId": "a4",
                        "name": "clarify_context",
                        "parts": [{"kind": "data", "mimeType": "application/json",
                                   "data": clarify_context}],
                    }
                )
            task["artifacts"] = artifacts
        else:
            # orchestrate_engagement (default)
            store = getattr(app.state, "object_store", None)
            if store is None:
                raise RuntimeError("Object store not initialised.")

            text_runner = _make_orchestrator_text_runner()
            orch_cfg = _cfg.get("orchestrator", {})
            customer_name = params.get("customer_name", context_id)

            from agent import orchestrator_agent
            turn_result = await orchestrator_agent.run_turn(
                customer_id=context_id,
                customer_name=customer_name,
                user_message=user_text,
                store=store,
                text_runner=text_runner,
                a2a_base_url=os.environ.get("A2A_BASE_URL", "http://localhost:8080"),
                max_tool_iterations=int(orch_cfg.get("max_tool_iterations", 5)),
                max_refinements=int(orch_cfg.get("max_refinements", 3)),
            )
            task["status"] = "COMPLETED"
            artifacts = [
                {
                    "artifactId": "a1",
                    "name": "reply",
                    "parts": [{"kind": "text", "text": turn_result["reply"]}],
                }
            ]
            for tool_name, artifact_key in turn_result.get("artifacts", {}).items():
                artifacts.append({
                    "artifactId": f"artifact-{tool_name}",
                    "name": tool_name,
                    "parts": [{"kind": "data", "mimeType": "application/json",
                               "data": {"key": artifact_key}}],
                })
            task["artifacts"] = artifacts

    except Exception as exc:
        logger.error("/message:send error context=%s skill=%s: %s", context_id, skill, exc)
        task["status"] = "FAILED"
        task["error"] = str(exc)

    return JSONResponse({
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": task,
    })


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Poll A2A v1.0 task status."""
    task = A2A_TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found.")
    return JSONResponse({"jsonrpc": "2.0", "id": None, "result": task})


@app.post("/tasks/{task_id}:cancel")
async def cancel_task(task_id: str):
    """Cancel a pending or working A2A v1.0 task."""
    task = A2A_TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found.")
    if task["status"] in ("SUBMITTED", "WORKING"):
        task["status"] = "CANCELLED"
    return JSONResponse({"jsonrpc": "2.0", "id": None, "result": task})


# ── /api/chat convenience endpoints ─────────────────────────────────────────

def _make_orchestrator_text_runner():
    """
    Return a sync callable (prompt, system_msg) -> str for the orchestrator.
    Uses the inference runner already wired to app.state.
    """
    runner = getattr(app.state, "llm_runner", None)

    def _text_runner(prompt: str, system_msg: str, model_profile: str = "orchestrator") -> str:
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

    return _text_runner


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
    orch_cfg: dict,
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

    if bool(orch_cfg.get("langgraph_enabled", False)):
        try:
            from agent import langgraph_orchestrator

            return await langgraph_orchestrator.run_turn(
                customer_id=req.customer_id,
                customer_name=req.customer_name,
                user_message=req.message,
                store=store,
                text_runner=text_runner,
                a2a_base_url=a2a_base_url,
                max_tool_iterations=max_tool_iterations,
                specialist_mode=specialist_mode,
                max_refinements=max_refinements,
            )
        except Exception as exc:
            logger.warning(
                "LangGraph orchestrator path failed; falling back to legacy orchestrator. error=%s",
                exc,
            )
            specialist_mode = "legacy"

    from agent import orchestrator_agent

    return await orchestrator_agent.run_turn(
        customer_id=req.customer_id,
        customer_name=req.customer_name,
        user_message=req.message,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        max_tool_iterations=max_tool_iterations,
        specialist_mode=specialist_mode,
        max_refinements=max_refinements,
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


def _build_artifact_manifest(customer_id: str, result: dict) -> dict:
    """
    Build UI-friendly artifact metadata from orchestrator result.
    """
    manifest: dict[str, list[dict]] = {"downloads": []}
    seen_diagram_keys: set[str] = set()
    seen_bom_keys: set[str] = set()

    def _append_diagram_download(artifact_key: str, *, tool_name: str, scenario_label: str = "") -> None:
        if not artifact_key or artifact_key in seen_diagram_keys:
            return
        seen_diagram_keys.add(artifact_key)
        parts = [part for part in str(artifact_key or "").split("/") if part]
        version_index = next((idx for idx, part in enumerate(parts) if re.fullmatch(r"v\d+", part)), -1)
        artifact_filename = parts[-1] if parts else "diagram.drawio"
        artifact_client_id = parts[version_index - 2] if version_index >= 2 else customer_id
        diagram_name = parts[version_index - 1] if version_index >= 1 else "oci_architecture"
        item = {
            "type": "diagram",
            "tool": tool_name,
            "key": artifact_key,
            "filename": artifact_filename,
            "download_url": (
                f"/api/download/{urllib.parse.quote(artifact_filename)}"
                f"?client_id={urllib.parse.quote(artifact_client_id)}"
                f"&diagram_name={urllib.parse.quote(diagram_name)}"
            ),
        }
        if scenario_label:
            item["label"] = scenario_label
        manifest["downloads"].append(item)

    for tool_call in result.get("tool_calls", []) or []:
        if tool_call.get("tool") != "generate_diagram":
            continue
        _append_diagram_download(
            str(tool_call.get("artifact_key", "") or ""),
            tool_name="generate_diagram",
            scenario_label=str(tool_call.get("scenario_label", "") or ""),
        )

    artifacts = result.get("artifacts", {}) or {}
    for tool_name, artifact_key in artifacts.items():
        if tool_name == "generate_diagram":
            _append_diagram_download(str(artifact_key or ""), tool_name=tool_name)

    for tool_call in result.get("tool_calls", []) or []:
        if tool_call.get("tool") != "generate_bom":
            continue
        result_data = tool_call.get("result_data", {}) or {}
        if not isinstance(result_data, dict):
            continue
        if not _bom_result_is_exportable(result_data) or not _result_has_bom_xlsx_metadata(result_data):
            continue
        xlsx = result_data.get("bom_xlsx") if isinstance(result_data.get("bom_xlsx"), dict) else {}
        artifact_key = str(result_data.get("xlsx_artifact_key") or xlsx.get("key") or "").strip()
        filename = str(result_data.get("xlsx_filename") or xlsx.get("filename") or "").strip()
        if not artifact_key or not filename or artifact_key in seen_bom_keys:
            continue
        seen_bom_keys.add(artifact_key)
        manifest["downloads"].append(
            {
                "type": "bom",
                "tool": "generate_bom",
                "key": artifact_key,
                "filename": filename,
                "download_url": (
                    f"/api/bom/{urllib.parse.quote(customer_id)}/download/"
                    f"{urllib.parse.quote(filename)}"
                ),
            }
        )

    for tool_call in result.get("tool_calls", []) or []:
        if tool_call.get("tool") != "generate_terraform":
            continue
        result_data = tool_call.get("result_data", {}) or {}
        bundle = result_data.get("bundle")
        if bundle and isinstance(bundle.get("files"), dict):
            for filename in sorted(bundle["files"].keys()):
                manifest["downloads"].append(
                    {
                        "type": "terraform",
                        "tool": "generate_terraform",
                        "filename": filename,
                        "download_url": f"/api/terraform/{customer_id}/download/{filename}",
                    }
                )
    return manifest


_BOM_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_BOM_XLSX_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.xlsx$")
_BOM_XLSX_METADATA_SUFFIX = ".metadata.json"


def _bom_xlsx_key(customer_id: str, filename: str) -> str:
    return f"customers/{customer_id}/bom/xlsx/{filename}"


def _bom_xlsx_metadata_key(xlsx_key: str) -> str:
    return f"{xlsx_key}{_BOM_XLSX_METADATA_SUFFIX}"


def _validate_bom_xlsx_filename(filename: str) -> str:
    safe_name = Path(str(filename or "")).name
    if safe_name != filename or not _BOM_XLSX_FILENAME_RE.fullmatch(safe_name):
        raise HTTPException(status_code=400, detail="Invalid BOM XLSX filename")
    return safe_name


def _bom_result_is_exportable(result_data: dict) -> bool:
    result_type = str(result_data.get("type", "") or "").strip().lower()
    if result_type and result_type != "final":
        return False
    payload = result_data.get("bom_payload") if isinstance(result_data.get("bom_payload"), dict) else {}
    line_items = payload.get("line_items") if isinstance(payload.get("line_items"), list) else []
    if not line_items:
        return False
    if isinstance(result_data.get("archie_question_bundle"), dict):
        return False
    if result_data.get("checkpoint_required") is True:
        return False
    for governor in (
        result_data.get("governor", {}) if isinstance(result_data.get("governor"), dict) else {},
        (result_data.get("trace", {}) or {}).get("governor", {}) if isinstance(result_data.get("trace"), dict) else {},
    ):
        status = str((governor or {}).get("overall_status", "") or "").strip().lower()
        if status in {"checkpoint_required", "blocked"}:
            return False
    review = result_data.get("archie_expert_review")
    if not isinstance(review, dict):
        trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
        review = trace.get("archie_expert_review") if isinstance(trace.get("archie_expert_review"), dict) else {}
    verdict = str((review or {}).get("verdict", "") or "").strip().lower()
    if verdict and verdict != "pass":
        return False
    trace_verdict = str(
        ((result_data.get("trace", {}) or {}).get("review_verdict", "") if isinstance(result_data.get("trace"), dict) else "")
        or ""
    ).strip().lower()
    if trace_verdict and trace_verdict != "pass":
        return False
    if _structured_bom_result_uses_default_sizing(result_data):
        return False
    return True


def _structured_bom_result_uses_default_sizing(result_data: dict) -> bool:
    structured = result_data.get("structured_inputs")
    if not isinstance(structured, dict):
        structured = result_data.get("inputs") if isinstance(result_data.get("inputs"), dict) else {}
    if not structured:
        return False
    payload = result_data.get("bom_payload") if isinstance(result_data.get("bom_payload"), dict) else {}
    produced = _bom_payload_sizing(payload)
    compute = structured.get("compute", {}) if isinstance(structured.get("compute"), dict) else {}
    memory = structured.get("memory", {}) if isinstance(structured.get("memory"), dict) else {}
    storage = structured.get("storage", {}) if isinstance(structured.get("storage"), dict) else {}
    required = {
        "ocpu": _positive_float(compute.get("ocpu")),
        "ram_gb": _positive_float(memory.get("gb")),
        "storage_gb": (_positive_float(storage.get("block_tb")) or 0.0) * 1024.0 if _positive_float(storage.get("block_tb")) else None,
    }
    for key, value in required.items():
        if value is None:
            continue
        if float(produced.get(key, 0.0) or 0.0) + 0.0001 < value:
            return True
    return False


def _positive_float(value: Any) -> float | None:
    if value in (None, "", [], {}):
        return None
    try:
        parsed = float(value)
    except Exception:
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            return None
        parsed = float(match.group(0))
    return parsed if parsed > 0 else None


def _bom_payload_sizing(payload: dict) -> dict[str, float]:
    cpu_skus = {"B93113", "B97384", "B111129", "B94176", "B93297"}
    mem_skus = {"B93114", "B97385", "B111130", "B94177", "B93298"}
    produced = {"ocpu": 0.0, "ram_gb": 0.0, "storage_gb": 0.0}
    for row in payload.get("line_items", []) or []:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku", "") or "").upper()
        desc = str(row.get("description", "") or "").lower()
        category = str(row.get("category", "") or "").lower()
        qty = _positive_float(row.get("quantity")) or 0.0
        if sku in cpu_skus or ("ocpu" in desc and category == "compute"):
            produced["ocpu"] += qty
        elif sku in mem_skus or ("memory" in desc and category == "compute"):
            produced["ram_gb"] += qty
        elif category == "storage" or "storage" in desc or "volume" in desc:
            produced["storage_gb"] += qty
    return produced


def _bom_xlsx_metadata(filename: str, key: str, result_data: dict | None = None) -> dict:
    result_data = result_data or {}
    review = result_data.get("archie_expert_review")
    if not isinstance(review, dict):
        trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
        review = trace.get("archie_expert_review") if isinstance(trace.get("archie_expert_review"), dict) else {}
    payload = result_data.get("bom_payload") if isinstance(result_data.get("bom_payload"), dict) else {}
    resolved_inputs = payload.get("resolved_inputs") if isinstance(payload.get("resolved_inputs"), list) else []
    trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
    context_source = str(trace.get("bom_context_source", result_data.get("bom_context_source", "")) or "")
    return {
        "schema_version": "1.0",
        "tool": "generate_bom",
        "status": "approved",
        "checkpoint_required": False,
        "filename": filename,
        "key": key,
        "archie_review_verdict": str((review or {}).get("verdict", "") or "pass"),
        "resolved_input_count": len(resolved_inputs),
        "context_source": context_source,
        "grounding": "revision-grounded" if "revision" in context_source else ("context-grounded" if context_source and context_source != "direct_request" else "generic"),
    }


def _bom_xlsx_download_is_valid(store, key: str) -> bool:
    meta_key = _bom_xlsx_metadata_key(key)
    if not key.lower().endswith(".xlsx") or not store.head(key) or not store.head(meta_key):
        return False
    try:
        metadata = json.loads(store.get(meta_key).decode("utf-8"))
    except Exception:
        return False
    if not isinstance(metadata, dict):
        return False
    if metadata.get("tool") != "generate_bom":
        return False
    if str(metadata.get("status", "") or "").lower() not in {"approved", "final"}:
        return False
    if metadata.get("checkpoint_required") is True:
        return False
    if str(metadata.get("archie_review_verdict", "pass") or "pass").lower() != "pass":
        return False
    return True


def _result_has_bom_xlsx_metadata(result_data: dict) -> bool:
    xlsx = result_data.get("bom_xlsx") if isinstance(result_data.get("bom_xlsx"), dict) else {}
    metadata = result_data.get("xlsx_metadata")
    if not isinstance(metadata, dict):
        metadata = xlsx.get("metadata") if isinstance(xlsx.get("metadata"), dict) else {}
    return (
        isinstance(metadata, dict)
        and metadata.get("tool") == "generate_bom"
        and str(metadata.get("status", "") or "").lower() in {"approved", "final"}
        and metadata.get("checkpoint_required") is not True
        and str(metadata.get("archie_review_verdict", "pass") or "pass").lower() == "pass"
    )


async def _persist_bom_xlsx_downloads(customer_id: str, store, result: dict) -> dict:
    """
    Persist downloadable XLSX workbooks for successful BOM tool calls.
    Mutates and returns result so artifact manifests can expose the links.
    """
    service = getattr(app.state, "bom_service", None) or get_shared_bom_service()
    for index, tool_call in enumerate(result.get("tool_calls", []) or []):
        if tool_call.get("tool") != "generate_bom":
            continue
        result_data = tool_call.get("result_data")
        if not isinstance(result_data, dict):
            continue
        if not _bom_result_is_exportable(result_data):
            continue
        if result_data.get("xlsx_artifact_key") or isinstance(result_data.get("bom_xlsx"), dict):
            continue
        payload = result_data.get("bom_payload")
        if not isinstance(payload, dict) or not payload:
            continue
        if not isinstance(payload.get("line_items"), list) or not payload.get("line_items"):
            continue
        workbook_bytes = await anyio.to_thread.run_sync(
            functools.partial(service.generate_xlsx, payload)
        )
        filename = f"oci-bom-{time.strftime('%Y%m%d-%H%M%S')}-{index + 1}-{uuid.uuid4().hex[:8]}.xlsx"
        key = _bom_xlsx_key(customer_id, filename)
        metadata = _bom_xlsx_metadata(filename, key, result_data)
        store.put(key, workbook_bytes, _BOM_XLSX_CONTENT_TYPE)
        store.put(
            _bom_xlsx_metadata_key(key),
            json.dumps(metadata, sort_keys=True).encode("utf-8"),
            "application/json",
        )
        result_data["xlsx_artifact_key"] = key
        result_data["xlsx_filename"] = filename
        result_data["xlsx_metadata"] = metadata
        result_data["bom_xlsx"] = {"key": key, "filename": filename, "metadata": metadata}
        trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
        stages = list(trace.get("bom_trace_stages", []) or [])
        if "XLSX persisted" not in stages:
            stages.append("XLSX persisted")
        trace["bom_trace_stages"] = stages
        result_data["trace"] = trace
        try:
            context = read_context(store, customer_id)
            record_agent_run(
                context,
                "bom",
                [],
                {
                    "xlsx_artifact_key": key,
                    "xlsx_filename": filename,
                    "xlsx_metadata": metadata,
                    "bom_xlsx": {"key": key, "filename": filename, "metadata": metadata},
                },
            )
            attach_bom_xlsx_to_latest(
                context,
                {"key": key, "filename": filename, "metadata": metadata},
            )
            write_context(store, customer_id, context)
        except Exception as exc:
            logger.warning("Could not record BOM XLSX artifact in context: %s", exc)
    return result


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


@app.post("/api/chat")
async def api_chat(req: OrchestratorChatRequest):
    """
    Convenience REST endpoint for the chat UI.
    Calls the orchestrator and returns the reply + tool call metadata.
    """
    store = _require_object_store()
    text_runner = _make_orchestrator_text_runner()
    orch_cfg = _cfg.get("orchestrator", {})

    try:
        result = await _run_orchestrator_turn(
            req=req,
            store=store,
            text_runner=text_runner,
            orch_cfg=orch_cfg,
        )
        result = await _persist_bom_xlsx_downloads(req.customer_id, store, result)
        artifact_manifest = _build_artifact_manifest(req.customer_id, result)
        project_membership = _persist_chat_project_membership(store, req)
        return {
            "status":         "ok",
            "trace_id":       _current_trace_id(),
            "project_id":     project_membership["project_id"],
            "project_name":   project_membership["project_name"],
            "engagement_id":  req.customer_id,
            "reply":          result["reply"],
            "tool_calls":     result["tool_calls"],
            "artifacts":      result["artifacts"],
            "artifact_manifest": artifact_manifest,
            "history_length": result["history_length"],
        }
    except Exception as exc:
        logger.error(
            "/api/chat error customer=%s trace_id=%s: %s",
            req.customer_id,
            _current_trace_id(),
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/chat/stream")
async def api_chat_stream(
    req: OrchestratorChatRequest,
    mode: str = Query(default="sse", pattern="^(sse|chunked)$"),
):
    """
    Streaming chat endpoint for v1.5 UI.
    Modes:
      - sse: text/event-stream
      - chunked: NDJSON (application/x-ndjson)
    """
    store = _require_object_store()
    text_runner = _make_orchestrator_text_runner()
    orch_cfg = _cfg.get("orchestrator", {})
    trace_id = _current_trace_id()

    async def _run_orchestrator_with_stream_notifications(queue: asyncio.Queue[dict]):
        from agent.notifications import notification_sink

        loop = asyncio.get_running_loop()

        def _sink(event: str, customer_id: str, _detail: str = "") -> None:
            payload = _tool_started_stream_event(
                event=event,
                customer_id=customer_id,
                trace_id=trace_id,
            )
            if payload is not None:
                loop.call_soon_threadsafe(queue.put_nowait, payload)

        with notification_sink(_sink):
            result = await _run_orchestrator_turn(
                req=req,
                store=store,
                text_runner=text_runner,
                orch_cfg=orch_cfg,
            )
        result = await _persist_bom_xlsx_downloads(req.customer_id, store, result)
        project_membership = _persist_chat_project_membership(store, req)
        return result, project_membership

    async def _drain_status_queue(queue: asyncio.Queue[dict], task: asyncio.Task, formatter):
        while not task.done():
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            yield formatter(payload)
        while not queue.empty():
            yield formatter(queue.get_nowait())

    async def _sse_events():
        started = {
            "trace_id": trace_id,
            "customer_id": req.customer_id,
            "event_type": "status",
            "status": "started",
        }
        yield f"event: status\ndata: {json.dumps(started)}\n\n"
        try:
            status_queue: asyncio.Queue[dict] = asyncio.Queue()
            task = asyncio.create_task(_run_orchestrator_with_stream_notifications(status_queue))
            async for event_text in _drain_status_queue(
                status_queue,
                task,
                lambda payload: f"event: status\ndata: {json.dumps(payload)}\n\n",
            ):
                yield event_text
            result, project_membership = await task
            for tool_call in result.get("tool_calls", []):
                if (
                    tool_call.get("tool") == "generate_terraform"
                    and isinstance(tool_call.get("result_data"), dict)
                ):
                    for stage in tool_call.get("result_data", {}).get("stages", []):
                        stage_payload = {
                            "trace_id": trace_id,
                            "customer_id": req.customer_id,
                            "event_type": "terraform_stage",
                            "stage": stage,
                        }
                        yield f"event: terraform_stage\ndata: {json.dumps(stage_payload)}\n\n"
                payload = {
                    "trace_id": trace_id,
                    "customer_id": req.customer_id,
                    "event_type": "tool",
                    "tool_call": tool_call,
                }
                yield f"event: tool\ndata: {json.dumps(payload)}\n\n"
            for chunk in _chunk_reply_text(result.get("reply", "")):
                payload = {
                    "trace_id": trace_id,
                    "customer_id": req.customer_id,
                    "event_type": "token",
                    "delta": chunk,
                }
                yield f"event: token\ndata: {json.dumps(payload)}\n\n"
            completed = {
                "trace_id": trace_id,
                "customer_id": req.customer_id,
                "project_id": project_membership["project_id"],
                "project_name": project_membership["project_name"],
                "engagement_id": req.customer_id,
                "event_type": "completion",
                "reply": result.get("reply", ""),
                "tool_calls": result.get("tool_calls", []),
                "artifacts": result.get("artifacts", {}),
                "artifact_manifest": _build_artifact_manifest(req.customer_id, result),
                "history_length": result.get("history_length", 0),
            }
            yield f"event: completion\ndata: {json.dumps(completed)}\n\n"
        except Exception as exc:
            logger.error(
                "/api/chat/stream error customer=%s trace_id=%s: %s",
                req.customer_id,
                trace_id,
                exc,
            )
            error_payload = {
                "trace_id": trace_id,
                "customer_id": req.customer_id,
                "event_type": "error",
                "error": str(exc),
            }
            yield f"event: error\ndata: {json.dumps(error_payload)}\n\n"

    async def _ndjson_events():
        started = {
            "trace_id": trace_id,
            "customer_id": req.customer_id,
            "event_type": "status",
            "status": "started",
        }
        yield json.dumps(started) + "\n"
        try:
            status_queue: asyncio.Queue[dict] = asyncio.Queue()
            task = asyncio.create_task(_run_orchestrator_with_stream_notifications(status_queue))
            async for event_text in _drain_status_queue(
                status_queue,
                task,
                lambda payload: json.dumps(payload) + "\n",
            ):
                yield event_text
            result, project_membership = await task
            for tool_call in result.get("tool_calls", []):
                if (
                    tool_call.get("tool") == "generate_terraform"
                    and isinstance(tool_call.get("result_data"), dict)
                ):
                    for stage in tool_call.get("result_data", {}).get("stages", []):
                        stage_payload = {
                            "trace_id": trace_id,
                            "customer_id": req.customer_id,
                            "event_type": "terraform_stage",
                            "stage": stage,
                        }
                        yield json.dumps(stage_payload) + "\n"
                payload = {
                    "trace_id": trace_id,
                    "customer_id": req.customer_id,
                    "event_type": "tool",
                    "tool_call": tool_call,
                }
                yield json.dumps(payload) + "\n"
            for chunk in _chunk_reply_text(result.get("reply", "")):
                payload = {
                    "trace_id": trace_id,
                    "customer_id": req.customer_id,
                    "event_type": "token",
                    "delta": chunk,
                }
                yield json.dumps(payload) + "\n"
            completed = {
                "trace_id": trace_id,
                "customer_id": req.customer_id,
                "project_id": project_membership["project_id"],
                "project_name": project_membership["project_name"],
                "engagement_id": req.customer_id,
                "event_type": "completion",
                "reply": result.get("reply", ""),
                "tool_calls": result.get("tool_calls", []),
                "artifacts": result.get("artifacts", {}),
                "artifact_manifest": _build_artifact_manifest(req.customer_id, result),
                "history_length": result.get("history_length", 0),
            }
            yield json.dumps(completed) + "\n"
        except Exception as exc:
            logger.error(
                "/api/chat/stream error customer=%s trace_id=%s: %s",
                req.customer_id,
                trace_id,
                exc,
            )
            error_payload = {
                "trace_id": trace_id,
                "customer_id": req.customer_id,
                "event_type": "error",
                "error": str(exc),
            }
            yield json.dumps(error_payload) + "\n"

    if mode == "chunked":
        return StreamingResponse(_ndjson_events(), media_type="application/x-ndjson")
    return StreamingResponse(_sse_events(), media_type="text/event-stream")


@app.get("/api/chat/{customer_id}/history")
async def api_chat_history(customer_id: str, max_turns: int = Query(default=30, ge=1, le=200)):
    """Return conversation history for a customer (most recent max_turns)."""
    store = _require_object_store()
    history = await anyio.to_thread.run_sync(
        functools.partial(load_conversation_history, store, customer_id, max_turns)
    )
    return {
        "status": "ok",
        "trace_id": _current_trace_id(),
        "customer_id": customer_id,
        "history": history,
    }


@app.get("/api/chat/history")
async def api_chat_history_index(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str = Query(default=""),
):
    """
    Return aggregated conversation history across customers.
    Used by the v1.5 sidebar list.
    """
    store = _require_object_store()
    result = await anyio.to_thread.run_sync(
        functools.partial(
            list_conversation_summaries,
            store,
            page=page,
            page_size=page_size,
            search=search,
        )
    )
    return {
        "status": "ok",
        "trace_id": _current_trace_id(),
        "items": result["items"],
        "pagination": result["pagination"],
    }


@app.get("/api/chat/projects")
async def api_chat_project_index(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: str = Query(default=""),
):
    """
    Return grouped project/customer summaries with engagement chat summaries.
    """
    store = _require_object_store()
    result = await anyio.to_thread.run_sync(
        functools.partial(
            list_project_summaries,
            store,
            page=page,
            page_size=page_size,
            search=search,
        )
    )
    return {
        "status": "ok",
        "trace_id": _current_trace_id(),
        "items": result["items"],
        "pagination": result["pagination"],
    }


@app.delete("/api/chat/{customer_id}/history")
async def api_clear_chat_history(customer_id: str):
    """Clear conversation history for a customer."""
    store = _require_object_store()
    await anyio.to_thread.run_sync(
        functools.partial(clear_conversation_history, store, customer_id)
    )
    return {
        "status": "ok",
        "trace_id": _current_trace_id(),
        "customer_id": customer_id,
        "message": "History cleared.",
    }


@app.post("/api/chat/{customer_id}/reset-context")
async def api_reset_chat_context(customer_id: str):
    """Reset active Archie context, chat history, summary, and active notes."""
    store = _require_object_store()

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
        "trace_id": _current_trace_id(),
        "customer_id": customer_id,
        "message": "Context reset.",
        **counts,
    }


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


# ── Notes endpoints ──────────────────────────────────────────────────────────

@app.post("/notes/upload")
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


@app.get("/notes/{customer_id}")
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

@app.post("/pov/generate")
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


@app.get("/pov/{customer_id}/latest")
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


@app.get("/pov/{customer_id}/versions")
@app.get("/api/pov/{customer_id}/versions")
async def pov_versions(customer_id: str):
    """List all POV versions for a customer."""
    store = _require_object_store()
    versions = await anyio.to_thread.run_sync(
        functools.partial(list_versions, store, "pov", customer_id)
    )
    return {"status": "ok", "customer_id": customer_id, "doc_type": "pov", "versions": versions}


# ── JEP endpoints ────────────────────────────────────────────────────────────

@app.post("/jep/generate")
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
        policy_block = await anyio.to_thread.run_sync(
            functools.partial(jep_generate_policy_block_payload, store, req.customer_id)
        )
        if policy_block is not None:
            return JSONResponse(status_code=409, content=policy_block)

        result = await anyio.to_thread.run_sync(_run_jep)
        jep_state = await anyio.to_thread.run_sync(
            functools.partial(mark_jep_generated, store, req.customer_id)
        )
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
            "jep_state":     jep_state,
            "errors":        [],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error in /jep/generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/jep/{customer_id}/latest")
@app.get("/api/jep/{customer_id}/latest")
async def jep_latest(customer_id: str):
    """Return the latest JEP document for a customer."""
    store = _require_object_store()
    content = await anyio.to_thread.run_sync(
        functools.partial(get_latest_doc, store, "jep", customer_id)
    )
    if content is None:
        raise HTTPException(status_code=404, detail=f"No JEP found for customer_id={customer_id!r}")
    jep_state = await anyio.to_thread.run_sync(
        functools.partial(sync_jep_state, store, customer_id)
    )
    return {
        "status": "ok",
        "customer_id": customer_id,
        "doc_type": "jep",
        "content": content,
        "jep_state": jep_state,
    }


@app.get("/jep/{customer_id}/versions")
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
        jep_state = await anyio.to_thread.run_sync(
            functools.partial(mark_jep_approved, store, req.customer_id)
        )
        from agent.notifications import notify
        notify("jep_approved", req.customer_id,
               f"Approved JEP uploaded for {req.customer_name}")
        return {
            "status": "ok",
            "customer_id": req.customer_id,
            "doc_type": "jep",
            "key": key,
            "jep_state": jep_state,
        }
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
    jep_state = await anyio.to_thread.run_sync(
        functools.partial(sync_jep_state, store, customer_id)
    )
    return {
        "status": "ok",
        "customer_id": customer_id,
        "doc_type": "jep",
        "content": content,
        "jep_state": jep_state,
    }


@app.post("/api/jep/revision-request")
async def jep_revision_request(req: JepRevisionRequest):
    """Request revision after an approved JEP; unlocks a subsequent generate."""
    store = _require_object_store()
    try:
        jep_state = await anyio.to_thread.run_sync(
            functools.partial(request_jep_revision, store, req.customer_id, req.reason or "")
        )
        return {
            "status": "ok",
            "customer_id": req.customer_id,
            "doc_type": "jep",
            "revision_requested": True,
            "jep_state": jep_state,
        }
    except ValueError:
        jep_state = await anyio.to_thread.run_sync(
            functools.partial(sync_jep_state, store, req.customer_id)
        )
        return JSONResponse(
            status_code=409,
            content={
                "status": "policy_block",
                "customer_id": req.customer_id,
                "doc_type": "jep",
                "reason_codes": ["REVISION_REQUEST_INVALID_STATE"],
                "missing_fields": list(jep_state.get("missing_fields", [])),
                "required_next_step": jep_state.get("required_next_step", ""),
                "retry_instructions": [
                    "Approve a generated JEP first, then request revision.",
                ],
                "jep_state": jep_state,
            },
        )
    except Exception as exc:
        logger.error("Error in /jep/revision-request: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


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
        jep_state = await anyio.to_thread.run_sync(
            functools.partial(sync_jep_state, store, req.customer_id)
        )
        return {
            "status":        "ok",
            "customer_id":   req.customer_id,
            "questions":     result["questions"],
            "extracted":     result["extracted"],
            "questions_key": result["questions_key"],
            "jep_state":     jep_state,
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
        jep_state = await anyio.to_thread.run_sync(
            functools.partial(sync_jep_state, store, req.customer_id)
        )
        return {
            "status": "ok",
            "customer_id": req.customer_id,
            "answers_saved": len(req.answers),
            "jep_state": jep_state,
        }
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
    jep_state = await anyio.to_thread.run_sync(
        functools.partial(sync_jep_state, store, customer_id)
    )
    return {"status": "ok", "customer_id": customer_id, "jep_state": jep_state, **data}


# ── WAF endpoints ─────────────────────────────────────────────────────────────

@app.post("/waf/generate")
@app.post("/api/waf/generate")
async def waf_generate(req: WafRequest):
    """
    Generate or update a Well-Architected Framework review for a customer.

    Reads engagement context and all prior agent outputs (diagram, POV, etc.)
    to produce a structured review across the five OCI WAF pillars:
    Security and Compliance, Reliability and Resilience,
    Performance and Cost Optimization, Operational Efficiency, Distributed Cloud.

    Saves to: waf/{customer_id}/v{n}.md + LATEST.md
    """
    store = _require_object_store()

    def _run_waf():
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
        return generate_waf(
            req.customer_id, req.customer_name, store, text_runner,
            feedback=req.feedback or "",
        )

    try:
        result = await anyio.to_thread.run_sync(_run_waf)
        content = _ensure_waf_test_pillars(result["content"])
        return {
            "status":         "ok",
            "agent_version":  AGENT_VERSION,
            "customer_id":    req.customer_id,
            "doc_type":       "waf",
            "version":        result["version"],
            "key":            result["key"],
            "latest_key":     result["latest_key"],
            "content":        content,
            "overall_rating": result["overall_rating"],
            "errors":         [],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error in /waf/generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/waf/{customer_id}/latest")
@app.get("/api/waf/{customer_id}/latest")
async def waf_latest(customer_id: str):
    """Return the latest WAF review for a customer."""
    store = _require_object_store()
    content = await anyio.to_thread.run_sync(
        functools.partial(get_latest_doc, store, "waf", customer_id)
    )
    if content is None:
        raise HTTPException(status_code=404, detail=f"No WAF review found for customer_id={customer_id!r}")
    return {
        "status": "ok",
        "customer_id": customer_id,
        "doc_type": "waf",
        "content": _ensure_waf_test_pillars(content),
    }


@app.get("/waf/{customer_id}/versions")
@app.get("/api/waf/{customer_id}/versions")
async def waf_versions(customer_id: str):
    """List all WAF review versions for a customer."""
    store = _require_object_store()
    versions = await anyio.to_thread.run_sync(
        functools.partial(list_versions, store, "waf", customer_id)
    )
    return {"status": "ok", "customer_id": customer_id, "doc_type": "waf", "versions": versions}


# ── Terraform endpoints ────────────────────────────────────────────────────────

@app.post("/terraform/generate")
@app.post("/api/terraform/generate")
async def terraform_generate(req: TerraformGenerateRequest):
    """
    Generate Terraform bundle using the v1.5 graph chain and persist files.
    """
    store = _require_object_store()
    text_runner = _make_terraform_text_runner()
    from agent.graphs import terraform_graph

    try:
        context = await anyio.to_thread.run_sync(
            functools.partial(read_context, store, req.customer_id, req.customer_name)
        )
        new_note_keys, new_notes_text = await anyio.to_thread.run_sync(
            functools.partial(get_new_notes, store, context, "terraform")
        )
        context_summary = build_context_summary(context)

        prompt = (req.prompt or "").strip()
        if not prompt:
            prompt = (
                "Generate a complete OCI Terraform deployment for this customer.\n"
                f"Customer ID: {req.customer_id}\n"
                f"Customer Name: {req.customer_name}\n\n"
                "Assumptions (use these defaults instead of asking clarification questions):\n"
                "- This is a greenfield deployment.\n"
                "- Region: us-ashburn-1.\n"
                "- Network: one VCN with public and private subnets.\n"
                "- Security: NSGs and least-privilege security list rules.\n"
                "- Compute/workload: GPU-capable platform suitable for AI workloads.\n"
                "- Availability: multi-AD where possible, otherwise resilient single-region design.\n\n"
                "Prior fleet context:\n"
                f"{context_summary or '(none)'}\n\n"
                "Meeting notes incorporated in this run:\n"
                f"{new_notes_text or '(none)'}\n\n"
                "Output requirements:\n"
                "- Return production-usable Terraform files.\n"
                "- Include provider, variables, outputs, and example tfvars.\n"
                "- Make pragmatic defaults when details are missing.\n"
            )

        summary, _artifact_key, result_data = await terraform_graph.run(
            args={"prompt": prompt},
            skill_root=Path(__file__).parent / "gstack_skills",
            text_runner=text_runner,
        )
        used_fallback = False
        files = result_data.get("files", {})
        if not result_data.get("ok"):
            if (req.prompt or "").strip():
                return {
                    "status": "need_clarification",
                    "trace_id": _current_trace_id(),
                    "customer_id": req.customer_id,
                    "customer_name": req.customer_name,
                    "summary": summary,
                    "blocking_questions": result_data.get("blocking_questions", []),
                    "stages": result_data.get("stages", []),
                }
            used_fallback = True
            files = _terraform_fallback_files()
            summary = (
                "Terraform generated using fallback starter bundle because the planner "
                "requested clarification. Fill `compartment_id` and tune network/workload variables."
            )

        persisted = await anyio.to_thread.run_sync(
            functools.partial(
                save_terraform_bundle,
                store,
                req.customer_id,
                files,
                {
                    "customer_name": req.customer_name,
                    "summary": summary[:500],
                    "trace_id": _current_trace_id(),
                },
            )
        )
        context = await anyio.to_thread.run_sync(
            functools.partial(read_context, store, req.customer_id, req.customer_name)
        )
        context = record_agent_run(
            context,
            "terraform",
            new_note_keys,
            {
                "version": persisted["version"],
                "file_count": len(persisted.get("files", {})),
                "prefix_key": f"customers/{req.customer_id}/terraform/v{persisted['version']}",
                "key": persisted["key"],
                "latest_key": persisted["latest_key"],
                "summary": summary[:500],
            },
        )
        await anyio.to_thread.run_sync(
            functools.partial(write_context, store, req.customer_id, context)
        )
        files_list = sorted(list(persisted["files"].keys()))
        return {
            "status": "ok",
            "trace_id": _current_trace_id(),
            "customer_id": req.customer_id,
            "customer_name": req.customer_name,
            "doc_type": "terraform",
            "summary": summary,
            "version": persisted["version"],
            "key": persisted["key"],
            "latest_key": persisted["latest_key"],
            "files": files_list,
            "file_count": len(files_list),
            "fallback_used": used_fallback,
            "stages": result_data.get("stages", []),
        }
    except Exception as exc:
        logger.error("Error in /api/terraform/generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/terraform/{customer_id}/latest")
@app.get("/api/terraform/{customer_id}/latest")
async def terraform_latest(customer_id: str):
    store = _require_object_store()
    latest = await anyio.to_thread.run_sync(
        functools.partial(get_latest_terraform_bundle, store, customer_id)
    )
    if not latest:
        raise HTTPException(status_code=404, detail=f"No Terraform bundle for customer_id={customer_id!r}")
    file_contents: dict[str, str] = {}
    for filename, key in (latest.get("files", {}) or {}).items():
        try:
            data = await anyio.to_thread.run_sync(functools.partial(store.get, key))
            file_contents[filename] = data.decode("utf-8", errors="replace")
        except KeyError:
            file_contents[filename] = ""
    return {
        "status": "ok",
        "trace_id": _current_trace_id(),
        "customer_id": customer_id,
        "doc_type": "terraform",
        "files": file_contents,
        "latest": latest,
    }


@app.get("/terraform/{customer_id}/versions")
@app.get("/api/terraform/{customer_id}/versions")
async def terraform_versions(customer_id: str):
    store = _require_object_store()
    versions = await anyio.to_thread.run_sync(
        functools.partial(list_terraform_versions, store, customer_id)
    )
    return {
        "status": "ok",
        "trace_id": _current_trace_id(),
        "customer_id": customer_id,
        "doc_type": "terraform",
        "versions": versions,
    }


@app.get("/context/{customer_id}")
@app.get("/api/context/{customer_id}")
async def get_customer_context(customer_id: str):
    """Return accumulated per-customer cross-agent context."""
    store = _require_object_store()
    try:
        context = await anyio.to_thread.run_sync(
            functools.partial(read_context, store, customer_id, "")
        )
        return {
            "status": "ok",
            "customer_id": customer_id,
            "context": context,
        }
    except Exception as exc:
        logger.error("Error in /context/%s: %s", customer_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/terraform/{customer_id}/download/{filename}")
async def terraform_download(customer_id: str, filename: str):
    store = _require_object_store()
    content = await anyio.to_thread.run_sync(
        functools.partial(get_terraform_file, store, customer_id, filename)
    )
    if content is None:
        raise HTTPException(status_code=404, detail=f"Terraform file {filename!r} not found for customer {customer_id!r}")
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
