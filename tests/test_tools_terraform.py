import sys
import types

import pytest

from agent import sub_agent_client
from agent.tools import terraform as terraform_module
from agent.tools.terraform import TerraformHandler
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def memory_arg_stubs(monkeypatch):
    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_hydrate_tool_args_from_context",
        lambda tool_name, args, context, decision_context, user_message: args,
    )
    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_enforce_memory_contract_on_tool_args",
        lambda tool_name, args, context: args,
    )
    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_has_architecture_definition",
        lambda context: True,
    )
    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_terraform_scope_is_bounded",
        lambda context, args, decision_context, user_message: True,
    )


def make_handler():
    return TerraformHandler(
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


def install_archie_session_stub(monkeypatch, parse_result):
    module = types.ModuleType("agent.archie_session")
    module._parse_terraform_sub_agent_result = parse_result
    monkeypatch.setitem(sys.modules, "agent.archie_session", module)


async def test_terraform_ok(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "terraform"
        assert task == "make terraform"
        assert engagement_context["customer_id"] == "cust-1"
        assert engagement_context["customer_name"] == "ACME"
        assert trace_id == "trace-1"
        return {"status": "ok", "result": "resource 'oci_core_vcn' 'main' {}"}

    def fake_save_terraform_bundle(store, customer_id, files, metadata):
        assert customer_id == "cust-1"
        assert files == {"main.tf": "..."}
        assert metadata["source"] == "sub_agent_client"
        return {"version": 1, "latest_key": "tf/main.tf"}

    install_archie_session_stub(monkeypatch, lambda result: {"main.tf": "..."})
    monkeypatch.setattr(
        terraform_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    monkeypatch.setattr(
        terraform_module.document_store,
        "save_terraform_bundle",
        fake_save_terraform_bundle,
    )

    result = await make_handler()(
        {"prompt": "make terraform"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert result.artifact_key == "tf/main.tf"


async def test_terraform_blocks_hardcoded_ocid_in_tf_file(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "terraform"
        return {"status": "ok", "result": "{}"}

    def fail_save_terraform_bundle(*args, **kwargs):
        raise AssertionError("Terraform bundle with hardcoded OCID must not be saved")

    install_archie_session_stub(
        monkeypatch,
        lambda result: {
            "main.tf": 'resource "oci_core_vcn" "main" { compartment_id = "ocid1.compartment.oc1..aaa" }',
            "variables.tf": 'variable "compartment_id" { type = string }',
            "README.md": "clean",
        },
    )
    monkeypatch.setattr(
        terraform_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    monkeypatch.setattr(
        terraform_module.document_store,
        "save_terraform_bundle",
        fail_save_terraform_bundle,
    )

    result = await make_handler()(
        {"prompt": "make terraform"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "blocked"
    assert result.data == {"ocid_violations": ["main.tf"]}
    assert "main.tf" in result.summary


async def test_terraform_allows_ocid_stubs_outside_tf_files(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "terraform"
        return {"status": "ok", "result": "{}"}

    def fake_save_terraform_bundle(store, customer_id, files, metadata):
        assert files["README.md"] == "example ocid1.compartment.oc1..aaa"
        assert files["terraform.tfvars.example"] == 'compartment_id = "ocid1.compartment.oc1..example"'
        return {
            "version": 1,
            "latest_key": "tf/latest.json",
            "files": {"main.tf": "tf/main.tf"},
        }

    install_archie_session_stub(
        monkeypatch,
        lambda result: {
            "main.tf": "resource \"oci_core_vcn\" \"main\" { compartment_id = var.compartment_id }",
            "variables.tf": "variable \"compartment_id\" { type = string }",
            "README.md": "example ocid1.compartment.oc1..aaa",
            "terraform.tfvars.example": 'compartment_id = "ocid1.compartment.oc1..example"',
        },
    )
    monkeypatch.setattr(
        terraform_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    monkeypatch.setattr(
        terraform_module.document_store,
        "save_terraform_bundle",
        fake_save_terraform_bundle,
    )

    result = await make_handler()(
        {"prompt": "make terraform"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert result.artifact_key == "tf/main.tf"


async def test_terraform_needs_input(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "needs_input", "result": "Please define VCN CIDR."}

    monkeypatch.setattr(
        terraform_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "make terraform"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "needs_input"
    assert result.clarification == "Please define VCN CIDR."


async def test_terraform_scope_not_bounded(monkeypatch):
    called = False

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "resource x {}"}

    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_has_architecture_definition",
        lambda context: True,
    )
    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_terraform_scope_is_bounded",
        lambda context, args, decision_context, user_message: False,
    )
    monkeypatch.setattr(
        terraform_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "make terraform"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "needs_input"
    assert called is False


async def test_terraform_sub_agent_error(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        raise sub_agent_client.SubAgentError("connection refused")

    monkeypatch.setattr(
        terraform_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "make terraform"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "blocked"
