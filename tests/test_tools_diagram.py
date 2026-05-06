import sys
import types

import pytest

from agent.tools import diagram as diagram_module
from agent.tools.diagram import DiagramHandler
from skillforge.types import MemorySnapshot


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


def make_handler():
    return DiagramHandler(
        store=object(),
        customer_id="cust-1",
        customer_name="ACME",
        text_runner=None,
        a2a_base_url="http://127.0.0.1:8000",
    )


def make_memory():
    return MemorySnapshot(
        session_id="s1",
        decision_context={"constraints": {"region": "us-ashburn-1"}},
        raw={"customer_id": "cust-1"},
    )


def install_archie_loop_stub(monkeypatch, call_generate_diagram):
    module = types.ModuleType("agent.archie_loop")
    module._call_generate_diagram = call_generate_diagram
    monkeypatch.setitem(sys.modules, "agent.archie_loop", module)


async def test_diagram_ok(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        assert customer_id == "cust-1"
        assert a2a_base_url == "http://127.0.0.1:8000"
        return (
            "Diagram generated. Key: diagrams/foo.drawio",
            "diagrams/foo.drawio",
            {},
        )

    install_archie_loop_stub(monkeypatch, fake_call_generate_diagram)

    result = await make_handler()(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "diagrams/foo.drawio"


async def test_diagram_insufficient_context(monkeypatch):
    called = False

    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        nonlocal called
        called = True
        return ("Diagram generated.", "diagrams/foo.drawio", {})

    install_archie_loop_stub(monkeypatch, fake_call_generate_diagram)
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_diagram_has_sufficient_context",
        lambda context, args, user_message: False,
    )

    result = await make_handler()(
        {"prompt": "draw it"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "needs_input"
    assert called is False


async def test_diagram_needs_clarification(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        return (
            "Clarify components.",
            "",
            {"diagram_recovery_status": "needs_clarification"},
        )

    install_archie_loop_stub(monkeypatch, fake_call_generate_diagram)

    result = await make_handler()(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert result.clarification == "Clarify components."


async def test_diagram_sub_agent_error(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        raise Exception("connection refused")

    install_archie_loop_stub(monkeypatch, fake_call_generate_diagram)

    result = await make_handler()(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "blocked"
