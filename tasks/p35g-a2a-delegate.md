# Task p35g: Add A2ADelegate — Wrap Sub-Agent HTTP Calls as ToolHandlers

## Goal

Create `skillforge/delegate.py` containing `A2ADelegate` — a callable that wraps
an A2A HTTP endpoint so it looks identical to a local tool handler from Forge's
perspective. This is a pure addition to `skillforge/`; no existing Archie code
is changed in this task.

After this task, teams can replace bespoke handler classes with:
```python
forge.register_tool(
    "generate_bom",
    A2ADelegate(base_url="http://localhost:8081", endpoint="/generate/bom"),
    memory_contract=True,
)
```

The migration of Archie's existing handlers to use A2ADelegate is task p35g-migrate
(a separate, follow-on task).

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/
pytest tests/test_forge.py -v --tb=short 2>&1 | tail -3
```

Both must pass before you start.

---

## Scope

**Only create:**
- `skillforge/delegate.py`
- `tests/test_a2a_delegate.py`

**Only modify:**
- `skillforge/__init__.py` — add `A2ADelegate` to exports

**Do NOT touch `agent/`, `archie_wiring.py`, or any existing handler.**

---

## What to implement

### `skillforge/delegate.py`

```python
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
                "artifacts": memory.artifacts,
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
```

### `skillforge/__init__.py` — add export

Add `A2ADelegate` to the existing exports:
```python
from skillforge.delegate import A2ADelegate
```

---

## Test: `tests/test_a2a_delegate.py`

Use `pytest-httpx` or `respx` if available; otherwise mock `httpx.AsyncClient`
with `unittest.mock.patch`.

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from skillforge.delegate import A2ADelegate, _default_result_adapter
from skillforge.types import MemorySnapshot, ToolResult


@pytest.mark.asyncio
async def test_delegate_ok_response():
    """Successful HTTP response is parsed into ToolResult."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "summary": "BOM generated",
        "status": "ok",
        "artifact_key": "bom/v1.xlsx",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("skillforge.delegate.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        delegate = A2ADelegate(base_url="http://localhost:8081", endpoint="/generate/bom")
        result = await delegate({}, memory=None, context={}, trace_id="t1")

    assert result.status == "ok"
    assert result.summary == "BOM generated"
    assert result.artifact_key == "bom/v1.xlsx"


@pytest.mark.asyncio
async def test_delegate_timeout_returns_blocked():
    """Timeout is caught and returned as blocked ToolResult."""
    import httpx

    with patch("skillforge.delegate.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        delegate = A2ADelegate(base_url="http://localhost:8081", endpoint="/generate/bom")
        result = await delegate({}, memory=None, context={}, trace_id="t1")

    assert result.status == "blocked"
    assert "timed out" in result.summary


@pytest.mark.asyncio
async def test_delegate_http_error_returns_blocked():
    """HTTP 500 is caught and returned as blocked ToolResult."""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch("skillforge.delegate.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)
        )
        mock_client_cls.return_value = mock_client

        delegate = A2ADelegate(base_url="http://localhost:8081", endpoint="/generate/bom")
        result = await delegate({}, memory=None, context={}, trace_id="t1")

    assert result.status == "blocked"
    assert "500" in result.summary


@pytest.mark.asyncio
async def test_delegate_passes_memory_in_payload():
    """Memory snapshot is serialized into the request payload."""
    captured = {}

    mock_response = MagicMock()
    mock_response.json.return_value = {"summary": "ok", "status": "ok"}
    mock_response.raise_for_status = MagicMock()

    async def _fake_post(url, json=None, headers=None):
        captured["payload"] = json
        return mock_response

    with patch("skillforge.delegate.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _fake_post
        mock_client_cls.return_value = mock_client

        memory = MemorySnapshot(session_id="s1", facts={"arch": "3-tier"})
        delegate = A2ADelegate(base_url="http://localhost:8081", endpoint="/gen")
        await delegate({"workload": "web"}, memory=memory, context={}, trace_id="t1")

    assert captured["payload"]["memory"]["session_id"] == "s1"
    assert captured["payload"]["memory"]["facts"] == {"arch": "3-tier"}
    assert captured["payload"]["args"] == {"workload": "web"}


def test_default_result_adapter_minimal():
    """Adapter works with only a summary field present."""
    result = _default_result_adapter({"summary": "done"})
    assert result.status == "ok"
    assert result.summary == "done"
    assert result.artifact_key == ""


@pytest.mark.asyncio
async def test_delegate_custom_result_adapter():
    """Custom result_adapter is called with raw response dict."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"output": "custom"}
    mock_response.raise_for_status = MagicMock()

    def my_adapter(data: dict) -> ToolResult:
        return ToolResult(summary=data["output"], status="ok")

    with patch("skillforge.delegate.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        delegate = A2ADelegate(
            base_url="http://localhost:8081",
            endpoint="/gen",
            result_adapter=my_adapter,
        )
        result = await delegate({}, memory=None, context={}, trace_id="t1")

    assert result.summary == "custom"
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/delegate.py` exits 0
2. `pytest tests/test_a2a_delegate.py -v` — 6 passed
3. `pytest tests/test_forge.py -v` — no regressions (14 passed)
4. `from skillforge import A2ADelegate` works in a Python REPL
5. `grep "A2ADelegate" skillforge/__init__.py` — matches

---

## Do NOT Do

- Do not modify `archie_wiring.py` or any existing handler — migration is p35g-migrate
- Do not add retry logic — keep transport simple; retries belong in the caller
- Do not import from `agent/` in `skillforge/delegate.py`

---

## Commit Message

```
p35g: add A2ADelegate — wrap A2A sub-agent endpoints as ToolHandler callables
```
