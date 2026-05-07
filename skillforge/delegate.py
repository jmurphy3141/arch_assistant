"""
skillforge/delegate.py
----------------------
A2ADelegate: wraps a remote A2A HTTP endpoint as a ToolHandler callable.

From Forge's perspective, A2ADelegate is indistinguishable from a local handler.
It accepts the same (args, *, memory, context, trace_id) signature and returns
a ToolResult. HTTP transport details stay here; domain code stays in handlers.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import httpx

from skillforge.types import MemorySnapshot, ToolResult

logger = logging.getLogger(__name__)


class A2ADelegate:
    """
    Callable tool handler that delegates to a remote A2A sub-agent via HTTP POST.

    Parameters
    ----------
    base_url        : Base URL of the sub-agent service (e.g. "http://localhost:8081")
    endpoint        : Path to POST to (e.g. "/generate/bom")
    timeout         : HTTP timeout in seconds (default 120)
    result_adapter  : Optional callable(dict) -> ToolResult. If omitted, the
                      response JSON is expected to have "summary", "status",
                      and optionally "artifact_key" and "data" fields.
    extra_headers   : Optional dict of extra HTTP headers to send
    """

    def __init__(
        self,
        base_url: str,
        endpoint: str,
        timeout: float = 120.0,
        result_adapter: Callable[[dict], ToolResult] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        self._timeout = timeout
        self._result_adapter = result_adapter or _default_result_adapter
        self._extra_headers = extra_headers or {}

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        payload: dict[str, Any] = {
            "args": args,
            "trace_id": trace_id,
        }
        if memory is not None:
            payload["memory"] = {
                "session_id": memory.session_id,
                "facts": memory.facts,
                "constraints": memory.constraints,
                "artifacts": getattr(memory, "artifacts", memory.prior_artifacts),
            }

        headers = {"Content-Type": "application/json", **self._extra_headers}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            logger.error("A2ADelegate timeout url=%s trace=%s", self._url, trace_id)
            return ToolResult(
                summary=f"Sub-agent call timed out after {self._timeout}s.",
                status="blocked",
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "A2ADelegate HTTP error url=%s status=%d trace=%s",
                self._url, exc.response.status_code, trace_id,
            )
            return ToolResult(
                summary=f"Sub-agent returned HTTP {exc.response.status_code}.",
                status="blocked",
            )
        except Exception as exc:
            logger.exception("A2ADelegate unexpected error url=%s trace=%s", self._url, trace_id)
            return ToolResult(
                summary=f"Sub-agent call failed: {exc}",
                status="blocked",
            )

        try:
            return self._result_adapter(data)
        except Exception as exc:
            logger.exception(
                "A2ADelegate result_adapter failed url=%s trace=%s", self._url, trace_id
            )
            return ToolResult(
                summary=f"Sub-agent response could not be parsed: {exc}",
                status="blocked",
            )


def _default_result_adapter(data: dict) -> ToolResult:
    """
    Default adapter: expects response JSON with at minimum a 'summary' field.
    'status' defaults to 'ok' if absent.
    """
    return ToolResult(
        summary=str(data.get("summary", "")),
        status=str(data.get("status", "ok")),
        artifact_key=str(data.get("artifact_key", "") or ""),
        data=data.get("data") or None,
    )
