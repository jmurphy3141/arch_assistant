"""
a2a_server.py
--------------
DEPRECATED — A2A is now served directly by drawing_agent_server.py.

  New endpoint:  POST /api/a2a/task          (on port 8000/8080)
  Agent card:    GET  /.well-known/agent.json (primary)
                 GET  /.well-known/agent-card.json (alias)

This standalone server (port 8081) is kept only for backward compatibility
with any existing integrations.  New orchestrators should call the main server
directly — it shares state (PENDING_CLARIFY, IDEMPOTENCY_CACHE) and goes
through the OCI Load Balancer correctly.

Skills now available in the main server:
  generate_diagram  — inline resources or OCI bucket ref
  upload_bom        — parse Excel BOM from OCI bucket ref
  clarify_diagram   — continue a pending clarification round
"""
import warnings
warnings.warn(
    "a2a_server.py is deprecated. Use POST /api/a2a/task on the main server.",
    DeprecationWarning,
    stacklevel=1,
)
from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="OCI Drawing Agent — A2A Server")

# Public host for agent card URLs (override with env var in production)
PUBLIC_HOST = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8080")


# ── A2A Models ────────────────────────────────────────────────────────────────

class A2ATask(BaseModel):
    task_id:   str
    skill:     str                    # e.g. "generate_diagram"
    inputs:    Dict[str, Any] = {}
    client_id: Optional[str] = "default"


class A2AResponse(BaseModel):
    task_id: str
    status:  str                      # "ok" | "need_clarification" | "error"
    outputs: Dict[str, Any] = {}


# ── Agent Card ────────────────────────────────────────────────────────────────

@app.get("/.well-known/agent-card.json")
def agent_card():
    return JSONResponse({
        "schema_version": "1.0",
        "name":           "OCI Drawing Agent",
        "description":    (
            "Generates OCI architecture draw.io diagrams from a Bill of Materials. "
            "Part of the OCI Agent Fleet (Agent 3 of 7)."
        ),
        "vendor":     "Oracle",
        "version":    "1.0.0",
        "capabilities": [
            "diagram-generation",
            "bom-parsing",
            "clarification-flow",
        ],
        "skills": [
            {
                "id":          "generate_diagram",
                "description": "Generate a draw.io OCI architecture diagram from a resource list.",
                "inputs": {
                    "resources":    "list of {id, type, label, layer}",
                    "context":      "optional requirements text",
                    "diagram_name": "output filename stem",
                },
                "outputs": {
                    "drawio_xml": "string",
                    "spec":       "layout spec JSON",
                },
            },
        ],
        "endpoints": {
            "a2a_task":   {"path": "/a2a/task",   "method": "POST"},
            "upload_bom": {"path": "/upload-bom", "method": "POST"},
            "clarify":    {"path": "/clarify",    "method": "POST"},
            "health":     {"path": "/health",     "method": "GET"},
        },
    })


# ── A2A Task Endpoint ─────────────────────────────────────────────────────────

@app.post("/a2a/task", response_model=A2AResponse)
async def handle_task(task: A2ATask):
    """
    Receive a task from another agent and dispatch to the appropriate skill.
    """
    if task.skill == "generate_diagram":
        return await _skill_generate_diagram(task)

    raise HTTPException(status_code=400, detail=f"Unknown skill: {task.skill!r}")


async def _skill_generate_diagram(task: A2ATask) -> A2AResponse:
    """Call the drawing pipeline and return drawio XML."""
    import httpx

    inputs = task.inputs
    resources    = inputs.get("resources", [])
    context      = inputs.get("context", "")
    diagram_name = inputs.get("diagram_name", "oci_architecture")

    if not resources:
        raise HTTPException(status_code=422, detail="'resources' list is required")

    payload = {
        "resources":    resources,
        "context":      context,
        "diagram_name": diagram_name,
        "client_id":    task.client_id,
    }

    # Delegate to the FastAPI drawing server's /generate endpoint (same process)
    async with httpx.AsyncClient(base_url="http://localhost:8080") as client:
        resp = await client.post("/generate", json=payload, timeout=120)

    data = resp.json()
    return A2AResponse(
        task_id=task.task_id,
        status=data.get("status", "error"),
        outputs=data,
    )


@app.get("/health")
def health():
    return {"status": "ok", "agent": "oci-drawing-agent-a2a"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
