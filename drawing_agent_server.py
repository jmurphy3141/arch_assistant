#!/usr/bin/env python3
"""
OCI Drawing Agent - FastAPI Server
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
  GET  /download/{file}   — download generated file
  GET  /health
  GET  /.well-known/agent-card.json
  GET  /mcp/tools
"""

import os
import json
import uuid
import base64
import asyncio
import tempfile
from pathlib import Path
from typing import Dict, Optional, Any, List

import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from oci.addons.adk import Agent, AgentClient

from agent.bom_parser import bom_to_llm_input, parse_bom
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio
from agent.oci_standards import get_catalogue_summary

app = FastAPI(title="OCI Drawing Agent")

# ── Config ────────────────────────────────────────────────────────────────────
_cfg_path = Path(__file__).parent / "config.yaml"
with open(_cfg_path) as _f:
    _cfg = yaml.safe_load(_f)

REGION            = _cfg.get("region", "us-phoenix-1")
AGENT_ENDPOINT_ID = _cfg["agent_endpoint_id"]
COMPARTMENT_ID    = _cfg["compartment_id"]
MAX_STEPS         = _cfg.get("max_steps", 5)
OUTPUT_DIR        = Path(_cfg.get("output_dir", "/tmp/diagrams"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Global state ───────────────────────────────────────────────────────────────
agent: Optional[Agent] = None
SESSION_STORE:     Dict[str, str]  = {}   # client_id  → session_id
PENDING_CLARIFY:   Dict[str, dict] = {}   # client_id  → {items, prompt, diagram_name}


# ── Models ─────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:   str
    client_id: Optional[str] = "default"

class ClarifyRequest(BaseModel):
    """Submit answers to clarification questions returned by /upload-bom."""
    answers:      str           # free-text answers to the questions
    client_id:    Optional[str] = "default"
    diagram_name: Optional[str] = "oci_architecture"

class GenerateRequest(BaseModel):
    resources:    List[Dict[str, Any]]
    context:      Optional[str] = ""
    diagram_name: Optional[str] = "oci_architecture"
    client_id:    Optional[str] = "default"


# ── Utilities ──────────────────────────────────────────────────────────────────
def extract_agent_text(response) -> str:
    if not hasattr(response, "data"):
        return str(response)
    data = response.data
    print("DEBUG response.data:", str(data)[:300])

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


def call_llm(prompt: str, client_id: str) -> dict:
    """Call OCI GenAI agent and return parsed JSON dict."""
    session_id = SESSION_STORE.get(client_id)
    response   = agent.run(prompt, session_id=session_id, max_steps=MAX_STEPS)
    SESSION_STORE[client_id] = response.session_id

    raw  = extract_agent_text(response)
    print(f"LLM raw ({len(raw)} chars): {raw[:400]}")
    return json.loads(clean_json(raw))


def run_pipeline(items: list, prompt: str, diagram_name: str, client_id: str) -> dict:
    """
    Call LLM → layout engine → drawio generator.
    Returns either a diagram result dict or a clarification dict.
    """
    spec = call_llm(prompt, client_id)

    # Clarification requested by LLM
    if spec.get("status") == "need_clarification":
        PENDING_CLARIFY[client_id] = {
            "items":        items,
            "prompt":       prompt,
            "diagram_name": diagram_name,
        }
        return {
            "status":    "need_clarification",
            "questions": spec.get("questions", []),
            "client_id": client_id,
            "message":   (
                "The agent needs more information before generating the diagram. "
                "POST your answers to /clarify with the same client_id."
            ),
        }

    # Normal layout spec — run through layout engine + generator
    items_by_id = {i.id: i for i in items}
    draw_dict   = spec_to_draw_dict(spec, items_by_id)

    drawio_path = OUTPUT_DIR / f"{diagram_name}.drawio"
    generate_drawio(draw_dict, drawio_path)

    return {
        "status":       "ok",
        "drawio_path":  str(drawio_path),
        "spec":         spec,
        "node_count":   len(draw_dict.get("nodes", [])),
        "edge_count":   len(draw_dict.get("edges", [])),
    }


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    global agent
    client = AgentClient(auth_type="instance_principal", region=REGION)
    print(f"AgentClient ready — runtime: {client.runtime_endpoint}")

    agent = Agent(
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
    agent.setup()
    print("Drawing Agent ready!")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/upload-bom")
async def upload_bom(
    file:         UploadFile = File(...),
    context_file: UploadFile = File(None),   # optional requirements/notes file
    context:      str        = Form(default=""),   # optional inline text context
    diagram_name: str        = Form(default="oci_architecture"),
    client_id:    str        = Form(default="default"),
):
    """
    Upload an Excel BOM + optional context file.

    context_file: any text file (.md, .txt, .pdf text) with requirements notes.
    context:      inline context string (used if no file uploaded).

    Returns either:
      - A draw.io XML diagram
      - A clarification request with questions to answer via /clarify
    """
    try:
        # Save BOM to temp file
        suffix = Path(file.filename).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            bom_path = tmp.name

        # Read optional context file
        context_text = context
        if context_file and context_file.filename:
            raw_ctx = await context_file.read()
            try:
                context_text = raw_ctx.decode("utf-8")
            except UnicodeDecodeError:
                context_text = raw_ctx.decode("latin-1", errors="replace")
            print(f"Context file loaded: {context_file.filename} ({len(context_text)} chars)")

        # Parse BOM + build prompt
        items, prompt = bom_to_llm_input(bom_path, context=context_text)
        os.unlink(bom_path)
        print(f"BOM parsed: {len(items)} services  |  context: {len(context_text)} chars")

        result = run_pipeline(items, prompt, diagram_name, client_id)

        # Clarification requested
        if result["status"] == "need_clarification":
            return JSONResponse(status_code=200, content=result)

        # Diagram generated
        drawio_xml = Path(result["drawio_path"]).read_text()
        return {
            "status":       "ok",
            "diagram_name": diagram_name,
            "drawio_xml":   drawio_xml,
            "spec":         result["spec"],
            "node_count":   result["node_count"],
            "edge_count":   result["edge_count"],
        }

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {e}")
    except Exception as e:
        print(f"Error in /upload-bom: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clarify")
async def clarify(req: ClarifyRequest):
    """
    Submit answers to clarification questions from /upload-bom.
    Re-runs the pipeline with answers appended to the original prompt.
    """
    pending = PENDING_CLARIFY.get(req.client_id)
    if not pending:
        raise HTTPException(
            status_code=404,
            detail=f"No pending clarification for client_id '{req.client_id}'. "
                   f"Call /upload-bom first."
        )

    try:
        # Append answers to original prompt
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
        )

        # Remove pending state if we got a diagram
        if result["status"] == "ok":
            PENDING_CLARIFY.pop(req.client_id, None)
            drawio_xml = Path(result["drawio_path"]).read_text()
            return {
                "status":       "ok",
                "diagram_name": req.diagram_name,
                "drawio_xml":   drawio_xml,
                "spec":         result["spec"],
                "node_count":   result["node_count"],
                "edge_count":   result["edge_count"],
            }
        else:
            # Still needs clarification
            return JSONResponse(status_code=200, content=result)

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {e}")
    except Exception as e:
        print(f"Error in /clarify: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_from_resources(req: GenerateRequest):
    """Generate diagram from a pre-parsed resource list (JSON body)."""
    try:
        items, prompt = bom_to_llm_input.__wrapped__ if hasattr(bom_to_llm_input, '__wrapped__') else (None, None)
        # Fall back to building prompt from resources directly
        from agent.bom_parser import build_llm_prompt, ServiceItem
        items = [
            ServiceItem(
                id=r.get("id", r.get("type","svc").replace(" ","_")),
                oci_type=r.get("type",""),
                label=r.get("label", r.get("type","")),
                layer=r.get("layer","compute"),
            )
            for r in req.resources
        ]
        prompt = build_llm_prompt(items, context=req.context or "")
        result = run_pipeline(items, prompt, req.diagram_name, req.client_id)

        if result["status"] == "need_clarification":
            return JSONResponse(status_code=200, content=result)

        drawio_xml = Path(result["drawio_path"]).read_text()
        return {
            "status":       "ok",
            "diagram_name": req.diagram_name,
            "drawio_xml":   drawio_xml,
            "spec":         result["spec"],
            "node_count":   result["node_count"],
            "edge_count":   result["edge_count"],
        }
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"LLM returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat")
def chat(req: ChatRequest):
    """Free-form chat with the drawing agent."""
    try:
        session_id = SESSION_STORE.get(req.client_id)
        response   = agent.run(req.message, session_id=session_id, max_steps=MAX_STEPS)
        SESSION_STORE[req.client_id] = response.session_id
        return {
            "text":       extract_agent_text(response),
            "session_id": response.session_id,
            "client_id":  req.client_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download/{filename}")
def download_file(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=filename)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent":  "oci-drawing-agent",
        "pending_clarifications": list(PENDING_CLARIFY.keys()),
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
                    "file":         {"type": "string", "format": "binary"},
                    "context_file": {"type": "string", "format": "binary", "description": "Optional requirements/notes file"},
                    "context":      {"type": "string", "description": "Optional inline context text"},
                    "diagram_name": {"type": "string"},
                    "client_id":    {"type": "string"},
                },
                "required": ["file"]
            }
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
                "required": ["answers", "client_id"]
            }
        },
        {
            "name": "get_oci_catalogue",
            "description": "List all known OCI resource types.",
            "inputSchema": {"type": "object", "properties": {}}
        }
    ]}


@app.get("/mcp/tools/get_oci_catalogue")
def get_catalogue():
    return {"catalogue": get_catalogue_summary()}


@app.get("/.well-known/agent-card.json")
def agent_card():
    host = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8080")
    return JSONResponse({
        "schema_version": "1.0",
        "name":           "OCI Drawing Agent",
        "description":    "Generates OCI architecture draw.io diagrams from a BOM Excel file.",
        "vendor":         "Oracle",
        "capabilities":   ["diagram-generation", "bom-parsing", "clarification-flow"],
        "endpoints": {
            "upload_bom": {"path": "/upload-bom", "method": "POST"},
            "clarify":    {"path": "/clarify",    "method": "POST"},
            "chat":       {"path": "/chat",       "method": "POST"},
            "tools":      {"path": "/mcp/tools",  "method": "GET"},
        },
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
