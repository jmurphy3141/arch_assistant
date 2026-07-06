import json

import pytest

from agent.tools.terraform import _validate_terraform_result
from sub_agents.models import A2ARequest
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
