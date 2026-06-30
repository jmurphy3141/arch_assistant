from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent import archie_session, archie_memory, context_store, document_store
from agent import sub_agent_client
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

    # More-specific tool matches must come before general ones (e.g. JEP before BOM
    # because the JEP prompt mentions "bill of materials" as a deliverable section).
    _TOOL_FOR_KEYWORDS = [
        ("generate_jep",       ["jep/poc", "jep plan", "14-day jep"]),
        ("generate_terraform", ["terraform bundle", "terraform"]),
        ("generate_waf",       ["well-architected", "waf review"]),
        ("generate_pov",       ["customer pov", "draft a customer pov"]),
        ("generate_diagram",   ["draw.io", "architecture diagram"]),
        ("generate_bom",       ["bill of materials", "xlsx", "bom"]),
    ]

    _orchestrator_calls: list[int] = [0]

    async def _dummy_text_runner(prompt: str = "", *_args, **_kwargs) -> str:
        label = _args[1] if len(_args) > 1 else (_kwargs.get("label") or "")
        if "step3_planning" in str(label):
            return (
                "STEP 1 — UNDERSTAND:\nDeterministic test — user wants a generation deliverable.\n"
                "STEP 2 — MEMORY ASSESSMENT:\nContext seeded. No gaps.\n"
                "STEP 3 — PLAN + HAT SELECTION:\nCall the appropriate generation tool."
            )
        if str(label) in ("expert_pre_action", "pre_action_light"):
            return "KNOWN FACTS: test\nGAPS: none\nEXPERT ASSESSMENT: proceed\nSUB-AGENT INSTRUCTIONS: use defaults"
        if str(label) in ("expert_post_review",):
            return "EXPERT_APPROVED"
        # orchestrator / react loop — return tool-call JSON on first call, prose on subsequent
        _orchestrator_calls[0] += 1
        if _orchestrator_calls[0] == 1:
            p = str(prompt).lower()
            for tool, keywords in _TOOL_FOR_KEYWORDS:
                if any(kw in p for kw in keywords):
                    args = (
                        {"prompt": prompt[:120]}
                        if tool in ("generate_bom", "generate_terraform")
                        else {"bom_text": ""} if tool == "generate_diagram"
                        else {"feedback": ""}
                    )
                    import json as _json
                    return _json.dumps({"tool": tool, "args": args})
        return "Your deliverable has been generated successfully."

    def _make_text_runner():
        _orchestrator_calls[0] = 0
        return _dummy_text_runner

    async def _fake_call_sub_agent(agent_name, task="", engagement_context=None, trace_id=None, **_kwargs):
        if agent_name == "bom":
            return {
                "status": "ok",
                "result": json.dumps({"bom_payload": DETERMINISTIC_BOM_PAYLOAD}),
            }
        if agent_name in {"pov", "jep", "waf"}:
            content = {"pov": DETERMINISTIC_POV, "jep": DETERMINISTIC_JEP, "waf": DETERMINISTIC_WAF}[agent_name]
            return {"status": "ok", "result": content}
        if agent_name == "terraform":
            files = {
                "main_tf": DETERMINISTIC_TERRAFORM_FILES["main.tf"],
                "variables_tf": DETERMINISTIC_TERRAFORM_FILES["variables.tf"],
                "outputs_tf": DETERMINISTIC_TERRAFORM_FILES["outputs.tf"],
                "readme_md": "# Apex Retail OCI Terraform bundle\n",
            }
            return {
                "status": "ok",
                "result": json.dumps({"files": files, "artifact_key": "fixture/main.tf"}),
            }
        raise AssertionError(f"unexpected sub-agent {agent_name!r}")

    async def _fake_call_generate_diagram(args, *, customer_id, a2a_base_url="", **_kwargs):
        key = f"agent3/{customer_id}/prompt-file/v1/diagram.drawio"
        store.put(key, DETERMINISTIC_DRAWIO.encode("utf-8"), "application/xml")
        return (
            f"Diagram generated. Key: {key}",
            key,
            {"drawio_xml": DETERMINISTIC_DRAWIO, "node_count": 7, "trace": {"source": "deterministic_e2e"}},
        )

    monkeypatch.setattr("drawing_agent_server._make_orchestrator_text_runner", _make_text_runner)
    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)
    monkeypatch.setattr(archie_session, "_call_generate_diagram", _fake_call_generate_diagram)
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
        main_download = next(
            item
            for item in body["artifact_manifest"]["downloads"]
            if item.get("type") == "terraform" and item.get("filename") == "main.tf"
        )
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
