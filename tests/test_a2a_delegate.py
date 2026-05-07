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
