import json
import sys
import types

import pytest

from agent import sub_agent_client
from agent.tools import specialists as specialists_module
from agent.tools.specialists import JepHandler, PovHandler, WafHandler
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def memory_arg_stubs(monkeypatch):
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_hydrate_tool_args_from_context",
        lambda tool_name, args, context, decision_context, user_message: args,
    )
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_enforce_memory_contract_on_tool_args",
        lambda tool_name, args, context: args,
    )
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_pov_has_sufficient_context",
        lambda context, decision_context, args, user_message: True,
    )
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_pov_targeted_questions",
        lambda: [{"id": "pov.scope", "question": "What audience?"}],
    )


def make_memory():
    return MemorySnapshot(
        session_id="s1",
        decision_context={"constraints": {"region": "us-ashburn-1"}},
        raw={"customer_id": "cust-1"},
    )


def install_jep_lifecycle_stub(
    monkeypatch,
    *,
    policy_block=None,
    generated_state=None,
):
    module = types.ModuleType("agent.jep_lifecycle")
    module.generate_policy_block_payload = lambda store, customer_id: policy_block
    module.mark_generated = lambda store, customer_id: (
        generated_state or {"jep_state": "generated"}
    )
    monkeypatch.setitem(sys.modules, "agent.jep_lifecycle", module)


def stub_save_doc(monkeypatch, key, version=1):
    monkeypatch.setattr(
        specialists_module.document_store,
        "save_doc",
        lambda store, doc_type, customer_id, content, metadata: {
            "key": key,
            "version": version,
        },
    )


async def test_pov_ok(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "pov"
        assert task == "Generate a customer POV from current engagement context."
        assert engagement_context["customer_id"] == "cust-1"
        return {"status": "ok", "result": "POV document text."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/pov_v1.md")

    result = await PovHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "docs/pov_v1.md"


async def test_pov_insufficient_context(monkeypatch):
    called = False

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "POV document text."}

    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_pov_has_sufficient_context",
        lambda context, decision_context, args, user_message: False,
    )
    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await PovHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert called is False


async def test_jep_ok(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "jep"
        return {"status": "ok", "result": "JEP document text."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/jep_v1.md")

    result = await JepHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.data["lock_outcome"] == "allowed"


async def test_jep_locked(monkeypatch):
    called = False
    install_jep_lifecycle_stub(
        monkeypatch,
        policy_block={
            "jep_state": {"state": "approved"},
            "reason_codes": ["already_approved"],
            "required_next_step": "Request revision.",
        },
    )

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "JEP document text."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await JepHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert called is False


async def test_waf_ok(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "waf"
        return {"status": "ok", "result": "WAF review text."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/waf_v1.md")

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "docs/waf_v1.md"


def _waf_payload(pillars):
    return json.dumps(
        {
            "overall_score": 4,
            "pillars": {
                pillar: {"score": 4, "findings": []}
                for pillar in pillars
            },
        }
    )


async def test_waf_blocks_json_result_missing_required_pillar(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "waf"
        return {
            "status": "ok",
            "result": _waf_payload(
                [
                    "Security",
                    "Reliability",
                    "Performance Efficiency",
                    "Cost Optimisation",
                    "Operational Excellence",
                ]
            ),
        }

    def fail_save_doc(*args, **kwargs):
        raise AssertionError("incomplete WAF result must not be saved")

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    monkeypatch.setattr(specialists_module.document_store, "save_doc", fail_save_doc)

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert result.data == {"missing_pillars": ["Continuous Improvement"]}
    assert "Continuous Improvement" in result.summary


async def test_waf_accepts_json_result_with_all_required_pillars(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "waf"
        return {
            "status": "ok",
            "result": _waf_payload(
                [
                    "Security",
                    "Reliability",
                    "Performance Efficiency",
                    "Cost Optimisation",
                    "Operational Excellence",
                    "Continuous Improvement",
                ]
            ),
        }

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/waf_v1.md")

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "docs/waf_v1.md"


async def test_waf_needs_input(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "needs_input", "result": "Please provide architecture context."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert result.clarification == "Please provide architecture context."


async def test_specialist_sub_agent_error(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        raise sub_agent_client.SubAgentError("timeout")

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
