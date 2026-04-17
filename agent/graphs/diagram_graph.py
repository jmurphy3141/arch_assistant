from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def run(
    *,
    args: dict,
    customer_id: str,
    a2a_base_url: str,
) -> tuple[str, str]:
    """
    LangGraph-compatible diagram specialist entrypoint.
    """
    try:
        import httpx
    except ImportError:
        return "httpx not installed — cannot call diagram agent.", ""

    bom_text = args.get("bom_text", "")
    message_text = (
        bom_text if bom_text.strip() else "Generate a diagram for this engagement."
    )
    payload = {
        "jsonrpc": "2.0",
        "id": f"orch-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": message_text}],
                "contextId": customer_id,
            },
            "skill": "generate_diagram",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{a2a_base_url}/message:send", json=payload)
        data = resp.json()
        result = data.get("result", {})
        status = result.get("status", "UNKNOWN")
        task_id = result.get("id", "")
        if status == "COMPLETED":
            artifacts = result.get("artifacts", [])
            for art in artifacts:
                if art.get("name") == "drawio_key":
                    parts = art.get("parts", [{}])
                    key = (parts[0].get("data") or {}).get("key", "")
                    if key:
                        return f"Diagram generated. Key: {key}", key
            return f"Diagram generated (task {task_id}).", ""
        if status in ("WORKING", "SUBMITTED"):
            return f"Diagram generation started (task {task_id}). Poll /tasks/{task_id}.", ""
        return f"Diagram generation returned status={status}.", ""
    except Exception as exc:
        logger.warning("Diagram A2A call failed: %s", exc)
        return f"Diagram generation failed: {exc}", ""

