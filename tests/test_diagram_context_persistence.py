import sys
import types

import pytest

from agent.context_store import read_context
from agent.archie_memory_impl import ArchieMemory
from agent.persistence_objectstore import InMemoryObjectStore
from agent.tools import diagram as diagram_module
from agent.tools.diagram import DiagramHandler
from skillforge.types import MemorySnapshot, ToolResult


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def memory_arg_stubs(monkeypatch):
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_hydrate_tool_args_from_context",
        lambda tool_name, args, context, decision_context, user_message: args,
    )
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_enforce_memory_contract_on_tool_args",
        lambda tool_name, args, context: args,
    )
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_diagram_has_sufficient_context",
        lambda context, args, user_message: True,
    )


def _install_archie_loop_stub(monkeypatch, call_generate_diagram):
    module = types.ModuleType("agent.archie_loop")
    module._call_generate_diagram = call_generate_diagram
    monkeypatch.setitem(sys.modules, "agent.archie_loop", module)


async def test_diagram_artifact_key_persists_and_flows_to_next_diagram_call(
    monkeypatch,
):
    store = InMemoryObjectStore()
    context = {"customer_id": "acme", "customer_name": "ACME"}

    updated = ArchieMemory(store=store).update(
        session_id="s1",
        tool_name="generate_diagram",
        result=ToolResult(
            summary="Diagram generated.",
            status="ok",
            artifact_key="test/v1.drawio",
            data={"diagram_name": "test-diagram"},
        ),
        context=context,
    )

    assert updated["agents"]["diagram"]["diagram_key"] == "test/v1.drawio"
    assert read_context(store, "acme")["agents"]["diagram"]["diagram_key"] == (
        "test/v1.drawio"
    )

    captured = {}

    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        captured["payload"] = args
        return ("Diagram generated. Key: test/v2.drawio", "test/v2.drawio", {})

    _install_archie_loop_stub(monkeypatch, fake_call_generate_diagram)
    memory = MemorySnapshot(
        session_id="s1",
        prior_artifacts={"generate_diagram": "test/v1.drawio"},
    )
    handler = DiagramHandler(
        store=store,
        customer_id="acme",
        customer_name="ACME",
        text_runner=None,
        a2a_base_url="http://127.0.0.1:8000",
    )

    result = await handler(
        {"prompt": "refine it"},
        memory=memory,
        context=updated,
        trace_id="trace-1",
    )

    assert result.artifact_key == "test/v2.drawio"
    assert captured["payload"]["prior_diagram_key"] == "test/v1.drawio"
    assert captured["payload"]["drawio_key"] == "test/v1.drawio"
