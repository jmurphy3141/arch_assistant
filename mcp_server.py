"""
mcp_server.py
--------------
Model Context Protocol (MCP) server for the OCI Drawing Agent.

Exposes the agent's tools so MCP-compatible clients (e.g. Claude Desktop,
Claude Code) can call them directly without going through the REST API.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _tools() -> list:
    return [
        {
            "name":        "upload_bom",
            "description": "Parse a BOM Excel file and generate an OCI architecture diagram.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bom_path":     {"type": "string",  "description": "Local path to BOM .xlsx file"},
                    "context":      {"type": "string",  "description": "Optional requirements notes"},
                    "diagram_name": {"type": "string",  "description": "Output filename stem"},
                    "client_id":    {"type": "string"},
                },
                "required": ["bom_path"],
            },
        },
        {
            "name":        "generate_diagram",
            "description": "Generate an OCI architecture diagram from a pre-parsed resource list.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resources": {
                        "type":        "array",
                        "description": "List of {id, type, label, layer} objects",
                        "items":       {"type": "object"},
                    },
                    "context":      {"type": "string"},
                    "diagram_name": {"type": "string"},
                    "client_id":    {"type": "string"},
                },
                "required": ["resources"],
            },
        },
        {
            "name":        "clarify",
            "description": "Submit answers to clarification questions from upload_bom.",
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
            "name":        "get_oci_catalogue",
            "description": "List all OCI resource types the agent can diagram.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _call_tool(name: str, inputs: Dict[str, Any]) -> Any:
    import requests

    base = "http://localhost:8080"

    if name == "upload_bom":
        bom_path = inputs["bom_path"]
        with open(bom_path, "rb") as f:
            files = {"file": (bom_path, f)}
            data  = {
                "context":      inputs.get("context", ""),
                "diagram_name": inputs.get("diagram_name", "oci_architecture"),
                "client_id":    inputs.get("client_id", "mcp"),
            }
            resp = requests.post(f"{base}/upload-bom", files=files, data=data, timeout=120)
        return resp.json()

    if name == "generate_diagram":
        resp = requests.post(f"{base}/generate", json=inputs, timeout=120)
        return resp.json()

    if name == "clarify":
        resp = requests.post(f"{base}/clarify", json=inputs, timeout=120)
        return resp.json()

    if name == "get_oci_catalogue":
        resp = requests.get(f"{base}/mcp/tools/get_oci_catalogue", timeout=10)
        return resp.json()

    raise ValueError(f"Unknown tool: {name!r}")


def _handle(msg: dict) -> dict | None:
    method = msg.get("method")
    req_id = msg.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": "oci-drawing-agent", "version": "1.0.0"},
        })

    if method == "tools/list":
        return ok({"tools": _tools()})

    if method == "tools/call":
        params    = msg.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        try:
            result = _call_tool(tool_name, arguments)
            return ok({"content": [{"type": "text", "text": json.dumps(result)}]})
        except Exception as e:
            return err(-32603, str(e))

    if method == "notifications/initialized":
        return None  # no response for notifications

    return err(-32601, f"Method not found: {method!r}")


def serve_stdio():
    """Run MCP server over stdin/stdout (JSON-RPC 2.0)."""
    logger.info("OCI Drawing Agent MCP server starting on stdio")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _handle(msg)
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    serve_stdio()
