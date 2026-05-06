import pytest

from agent import sub_agent_client
from agent.tools import bom as bom_module
from agent.tools.bom import BomHandler
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def memory_arg_stubs(monkeypatch):
    monkeypatch.setattr(
        bom_module.archie_memory,
        "_prepare_bom_tool_args",
        lambda args, user_message, context, decision_context: args,
    )
    monkeypatch.setattr(
        bom_module.archie_memory,
        "_hydrate_tool_args_from_context",
        lambda tool_name, args, context, decision_context, user_message: args,
    )
    monkeypatch.setattr(
        bom_module.archie_memory,
        "_enforce_memory_contract_on_tool_args",
        lambda tool_name, args, context: args,
    )


def make_handler():
    return BomHandler(
        store=object(),
        customer_id="cust-1",
        customer_name="ACME",
        text_runner=None,
    )


def make_memory():
    return MemorySnapshot(
        session_id="s1",
        decision_context={"constraints": {"region": "us-ashburn-1"}},
        raw={"customer_id": "cust-1"},
    )


async def test_bom_ok(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "bom"
        assert task == "size it"
        assert engagement_context == {"customer_id": "cust-1"}
        assert trace_id == "trace-1"
        return {
            "status": "ok",
            "result": (
                '{"bom_payload": {"line_items": [], '
                '"totals": {"estimated_monthly_cost": 500}}}'
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"},
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert result.data["bom_payload"]["totals"]["estimated_monthly_cost"] == 500


async def test_bom_needs_input(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "needs_input", "result": "Please provide OCPU count."}

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert result.clarification == "Please provide OCPU count."


async def test_bom_direct_reply_shortcut(monkeypatch):
    called = False

    def fake_prepare(args, user_message, context, decision_context):
        return {**args, "_bom_direct_reply": "This is a followup without context."}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "{}"}

    monkeypatch.setattr(
        bom_module.archie_memory, "_prepare_bom_tool_args", fake_prepare
    )
    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert called is False


async def test_bom_sub_agent_error(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        raise sub_agent_client.SubAgentError("connection refused")

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "blocked"
