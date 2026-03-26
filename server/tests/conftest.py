"""
server/tests/conftest.py
------------------------
Shared pytest fixtures for server tests.
Injects fake LLM runner and clears global state between tests.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Make sure repo root (parent of server/) is on sys.path so `agent.*` imports
# work when pytest is run from the server/ directory.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from server.app.main import (  # noqa: E402
    IDEMPOTENCY_CACHE,
    PENDING_CLARIFY,
    SESSION_STORE,
    app,
)

# Re-use the existing FakeLLMRunner and specs from the root test suite
from tests.scenarios.fakes import MINIMAL_SPEC, FakeLLMRunner  # noqa: E402


@pytest.fixture(autouse=True)
def clear_server_state():
    """Reset all global server state before/after every test."""
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()
    yield
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()


@pytest.fixture
def fake_runner():
    return FakeLLMRunner(copy.deepcopy(MINIMAL_SPEC))


@pytest.fixture
def client(fake_runner):
    """TestClient with fake LLM runner; OCI init skipped."""
    app.state.llm_runner = fake_runner
    app.state.object_store = None
    app.state.persistence_config = {}
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.state.llm_runner = None
    app.state.object_store = None
