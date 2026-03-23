"""
tests/scenarios/conftest.py
----------------------------
Shared fixtures for scenario tests.

All fixtures ensure:
  - No real OCI calls are made.
  - Global server state (IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY)
    is cleared before and after each test.
  - app.state.llm_runner is reset to None after each test to prevent leak.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.scenarios.fakes import FakeLLMRunner, InMemoryObjectStoreFake, MINIMAL_SPEC


@pytest.fixture(autouse=True)
def clear_server_state():
    """Clear all global server state before and after every scenario test."""
    from drawing_agent_server import IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()
    yield
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()


@pytest.fixture
def fake_runner():
    """Default FakeLLMRunner returning MINIMAL_SPEC."""
    return FakeLLMRunner(MINIMAL_SPEC)


@pytest.fixture
def fake_store():
    """Fresh InMemoryObjectStoreFake."""
    return InMemoryObjectStoreFake()


def _make_test_client(runner, store=None, persistence_config=None):
    """
    Helper used by fixtures: set app.state before TestClient starts
    so that startup() skips OCI initialisation (llm_runner is already set).
    """
    from drawing_agent_server import app
    app.state.llm_runner = runner
    app.state.object_store = store
    app.state.persistence_config = persistence_config or {}
    client = TestClient(app, raise_server_exceptions=True)
    return client


@pytest.fixture
def api_client(fake_runner):
    """TestClient with fake LLM, no object store."""
    client = _make_test_client(fake_runner)
    with client:
        yield client
    from drawing_agent_server import app
    app.state.llm_runner = None
    app.state.object_store = None


@pytest.fixture
def api_client_with_store(fake_runner, fake_store):
    """TestClient with fake LLM + in-memory object store."""
    client = _make_test_client(
        fake_runner, store=fake_store, persistence_config={"prefix": "diagrams"}
    )
    with client:
        yield client, fake_store
    from drawing_agent_server import app
    app.state.llm_runner = None
    app.state.object_store = None
