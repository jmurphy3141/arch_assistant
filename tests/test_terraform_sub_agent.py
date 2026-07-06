import json

import pytest

from agent.tools.terraform import _validate_terraform_result
from sub_agents.models import A2ARequest
from sub_agents.terraform import server as terraform_server
from sub_agents.terraform.server import _parse_files, handle


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_terraform_sub_agent_returns_handler_contract(monkeypatch):
    generated = {
        "main_tf": 'provider "oci" { region = var.region }',
        "variables_tf": 'variable "region" { type = string }',
        "outputs_tf": 'output "region" { value = var.region }',
        "readme_md": "# Terraform bundle",
    }
    monkeypatch.setattr(
        "sub_agents.terraform.server.run_inference",
        lambda *_args, **_kwargs: json.dumps(generated),
    )

    response = await handle(A2ARequest(task="Generate Terraform for the confirmed POC."))

    assert response.status == "ok"
    assert _validate_terraform_result(response.result) == ""


async def test_parse_files_unwraps_one_level_double_encoded_bundle():
    expected = {
        "main_tf": 'provider "oci" { region = var.region }',
        "variables_tf": 'variable "region" { type = string }',
        "outputs_tf": 'output "region" { value = var.region }',
        "readme_md": "# Terraform bundle\n\nRun `terraform validate`.",
    }
    raw = json.dumps({"main_tf": json.dumps(expected)})

    assert _parse_files(raw) == expected


async def test_main_terraform_config_is_authoritative_for_generation(monkeypatch):
    captured = {}

    def fake_run_inference(*_args, **kwargs):
        captured.update(kwargs)
        return json.dumps({key: "content" for key in terraform_server._FILE_KEYS})

    monkeypatch.setitem(terraform_server._main_terraform, "max_tokens", 16000)
    monkeypatch.setitem(terraform_server._main_terraform, "temperature", 0.15)
    monkeypatch.setitem(terraform_server._agent_llm, "max_tokens", 6000)
    monkeypatch.setitem(terraform_server._agent_llm, "temperature", 0.4)
    monkeypatch.setattr(terraform_server, "run_inference", fake_run_inference)

    response = await handle(A2ARequest(task="Generate Terraform for the confirmed POC."))

    assert response.status == "ok"
    assert captured["max_tokens"] == 16000
    assert captured["temperature"] == 0.15
