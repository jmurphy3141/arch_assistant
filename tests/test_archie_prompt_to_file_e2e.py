from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent import archie_loop, archie_memory, context_store, document_store
from agent.bom_service import BomService
from agent.persistence_objectstore import InMemoryObjectStore
from drawing_agent_server import app
from tests.archie_prompt_file_cases import (
    ARCHIE_PROMPT_FILE_CASES,
    DETERMINISTIC_BOM_PAYLOAD,
    DETERMINISTIC_DRAWIO,
    DETERMINISTIC_JEP,
    DETERMINISTIC_POV,
    DETERMINISTIC_TERRAFORM_FILES,
    DETERMINISTIC_WAF,
    assert_bom_xlsx,
    assert_diagram_drawio,
    assert_markdown,
    assert_no_blocked_or_needs_input,
    assert_terraform_files,
    expected_tool_call,
    manifest_download,
)

pytestmark = [pytest.mark.e2e, pytest.mark.system]


@pytest.fixture
def deterministic_client(monkeypatch):
    store = InMemoryObjectStore()
    app.state.object_store = store
    app.state.bom_service = BomService()

    async def _dummy_text_runner(*_args, **_kwargs) -> str:
        return "No free-form LLM response should be needed for these deterministic file prompts."

    def _make_text_runner():
        return _dummy_text_runner

    async def _fake_tool_core(
        tool_name,
        args,
        *,
        customer_id,
        customer_name,
        store,
        text_runner,
        a2a_base_url,
        specialist_mode="legacy",
    ):
        _ = (args, customer_name, text_runner, a2a_base_url, specialist_mode)
        if tool_name == "generate_bom":
            return (
                "Final BOM prepared.",
                "",
                {
                    "status": "ok",
                    "type": "final",
                    "result": json.dumps({"bom_payload": DETERMINISTIC_BOM_PAYLOAD}),
                    "bom_payload": DETERMINISTIC_BOM_PAYLOAD,
                    "archie_expert_review": {"verdict": "pass", "findings": []},
                    "trace": {"source": "deterministic_e2e"},
                },
            )
        if tool_name == "generate_diagram":
            key = f"agent3/{customer_id}/prompt-file/v1/diagram.drawio"
            store.put(key, DETERMINISTIC_DRAWIO.encode("utf-8"), "application/xml")
            return (
                f"Diagram generated. Key: {key}",
                key,
                {
                    "drawio_xml": DETERMINISTIC_DRAWIO,
                    "node_count": 7,
                    "trace": {"source": "deterministic_e2e"},
                },
            )
        if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
            agent_name = tool_name.replace("generate_", "")
            content = {
                "pov": DETERMINISTIC_POV,
                "jep": DETERMINISTIC_JEP,
                "waf": DETERMINISTIC_WAF,
            }[agent_name]
            saved = document_store.save_doc(
                store,
                agent_name,
                customer_id,
                content,
                {"source": "deterministic_e2e"},
            )
            return (
                f"{agent_name.upper()} v{saved['version']} saved. Key: {saved['key']}",
                saved["key"],
                {"status": "ok", "result": content, "trace": {"source": "deterministic_e2e"}},
            )
        if tool_name == "generate_terraform":
            saved = document_store.save_terraform_bundle(
                store,
                customer_id,
                DETERMINISTIC_TERRAFORM_FILES,
                {"source": "deterministic_e2e"},
            )
            key = saved["files"]["main.tf"]
            return (
                f"Terraform bundle v{saved['version']} saved. Key: {key}",
                key,
                {
                    "status": "ok",
                    "result": json.dumps({"files": DETERMINISTIC_TERRAFORM_FILES}),
                    "terraform_files": DETERMINISTIC_TERRAFORM_FILES,
                    "terraform_bundle": saved,
                    "bundle": saved,
                    "trace": {"source": "deterministic_e2e"},
                },
            )
        raise AssertionError(f"unexpected tool {tool_name!r}")

    monkeypatch.setattr("drawing_agent_server._make_orchestrator_text_runner", _make_text_runner)
    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_tool_core)
    monkeypatch.setattr(archie_memory, "_terraform_scope_details_are_bounded", lambda **_kwargs: True)

    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client, store

    app.state.object_store = None
    app.state.bom_service = None


def _seed_architecture_context(store: InMemoryObjectStore, customer_id: str, customer_name: str) -> None:
    context = context_store.read_context(store, customer_id, customer_name)
    context_store.set_archie_engagement_summary(
        context,
        (
            "Apex Retail is migrating an internet-facing OCI 3-tier web app with WAF, "
            "load balancer, web tier, private database subnet, Object Storage, and Block Volume."
        ),
    )
    context_store.record_agent_run(
        context,
        "diagram",
        [],
        {
            "diagram_key": f"agent3/{customer_id}/seed/v1/diagram.drawio",
            "node_count": 7,
            "deployment_summary": "OCI 3-tier web app with WAF and storage services.",
        },
    )
    context_store.write_context(store, customer_id, context)


@pytest.mark.parametrize("case", ARCHIE_PROMPT_FILE_CASES, ids=[case.case_id for case in ARCHIE_PROMPT_FILE_CASES])
def test_archie_prompt_to_output_file_e2e(case, deterministic_client):
    test_client, store = deterministic_client
    customer_id = f"prompt_file_{case.case_id}"
    customer_name = "Apex Retail"
    _seed_architecture_context(store, customer_id, customer_name)

    resp = test_client.post(
        "/api/chat",
        json={
            "customer_id": customer_id,
            "customer_name": customer_name,
            "message": case.prompt,
            "project_id": "prompt-file-e2e",
            "project_name": "Prompt File E2E",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert_no_blocked_or_needs_input(body)
    call = expected_tool_call(body, case.expected_tool)

    if case.case_id == "bom":
        download = manifest_download(body, "bom")
        assert store.head(download["key"])
        download_resp = test_client.get(download["download_url"])
        assert download_resp.status_code == 200
        assert_bom_xlsx(download_resp.content)
        assert call["result_data"]["bom_payload"]["line_items"]
    elif case.case_id == "diagram":
        key = call["artifact_key"]
        assert key and store.head(key)
        download = manifest_download(body, "diagram")
        assert download["key"] == key
        assert_diagram_drawio(store.get(key).decode("utf-8"))
    elif case.case_id in {"pov", "jep", "waf"}:
        key = call["artifact_key"]
        assert key and store.head(key)
        assert_markdown(case.case_id, store.get(key).decode("utf-8"))
        latest = test_client.get(f"/api/{case.case_id}/{customer_id}/latest")
        assert latest.status_code == 200
        assert_markdown(case.case_id, latest.json()["content"])
    elif case.case_id == "terraform":
        key = call["artifact_key"]
        assert key and store.head(key)
        main_download = manifest_download(body, "terraform")
        assert main_download["filename"] == "main.tf"
        assert_terraform_files(
            {
                filename: store.get(object_key).decode("utf-8")
                for filename, object_key in call["result_data"]["bundle"]["files"].items()
            }
        )
        for filename in ("main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example"):
            download_resp = test_client.get(f"/api/terraform/{customer_id}/download/{filename}")
            assert download_resp.status_code == 200
