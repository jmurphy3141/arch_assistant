from __future__ import annotations

import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tests.archie_prompt_file_cases import (
    ARCHIE_PROMPT_FILE_CASES,
    assert_bom_xlsx,
    assert_diagram_drawio,
    assert_markdown,
    assert_no_blocked_or_needs_input,
    assert_terraform_files,
    expected_tool_call,
    manifest_download,
)

pytestmark = [pytest.mark.live, pytest.mark.e2e]

_RUN_LIVE = os.environ.get("RUN_ARCHIE_PROMPT_FILE_LIVE", "0") == "1"
_BASE_URL = os.environ.get("AGENT_BASE_URL", "").rstrip("/")

requires_live_prompt_file = pytest.mark.skipif(
    not _RUN_LIVE or not _BASE_URL,
    reason="Set RUN_ARCHIE_PROMPT_FILE_LIVE=1 and AGENT_BASE_URL to run live Archie prompt-to-file tests",
)

_TS = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")


def _url(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return f"{_BASE_URL}{path_or_url}"


def _get_json(path: str, *, timeout: int = 120) -> dict:
    try:
        with urllib.request.urlopen(_url(path), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"GET {_url(path)} returned {exc.code}: {body[:800]}") from exc


def _get_bytes(path: str, *, timeout: int = 120) -> bytes:
    try:
        with urllib.request.urlopen(_url(path), timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"GET {_url(path)} returned {exc.code}: {body[:800]}") from exc


def _post_json(path: str, body: dict, *, timeout: int = 420) -> dict:
    request = urllib.request.Request(
        _url(path),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"POST {_url(path)} returned {exc.code}: {response_body[:1200]}") from exc


@requires_live_prompt_file
@pytest.mark.parametrize("case", ARCHIE_PROMPT_FILE_CASES, ids=[case.case_id for case in ARCHIE_PROMPT_FILE_CASES])
def test_archie_prompt_to_output_file_live(case):
    customer_id = f"prompt_file_live_{case.case_id}_{_TS}"
    customer_name = "Apex Retail Live Prompt File"

    body = _post_json(
        "/api/chat",
        {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "message": case.prompt,
            "project_id": "prompt-file-live",
            "project_name": "Prompt File Live",
        },
    )

    assert_no_blocked_or_needs_input(body)
    expected_tool_call(body, case.expected_tool)

    if case.case_id == "bom":
        download = manifest_download(body, "bom")
        assert_bom_xlsx(_get_bytes(download["download_url"]))
    elif case.case_id == "diagram":
        download = manifest_download(body, "diagram")
        assert_diagram_drawio(_get_bytes(download["download_url"]).decode("utf-8", errors="replace"))
    elif case.case_id in {"pov", "jep", "waf"}:
        latest = _get_json(f"/api/{case.case_id}/{urllib.parse.quote(customer_id)}/latest")
        assert_markdown(case.case_id, latest["content"])
    elif case.case_id == "terraform":
        downloads = {
            item["filename"]: item
            for item in (body.get("artifact_manifest", {}) or {}).get("downloads", []) or []
            if item.get("type") == "terraform"
        }
        files = {
            filename: _get_bytes(item["download_url"]).decode("utf-8", errors="replace")
            for filename, item in downloads.items()
            if filename in {"main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example"}
        }
        if set(files) != {"main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example"}:
            latest = _get_json(f"/api/terraform/{urllib.parse.quote(customer_id)}/latest")
            files.update(latest.get("files", {}) or {})
        assert_terraform_files(files)
