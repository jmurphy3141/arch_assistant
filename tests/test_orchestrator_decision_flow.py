"""Current-contract orchestration coverage for routing, memory, and handoffs."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from agent import archie_session, context_store
from agent.archie_wiring import build_forge
from agent.persistence_objectstore import InMemoryObjectStore
from skillforge.types import ToolResult, TurnResult


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolate_orchestration_globals():
    archie_session._forge_cache.clear()
    archie_session._PENDING_UPDATE_WORKFLOWS.clear()
    yield
    archie_session._forge_cache.clear()
    archie_session._PENDING_UPDATE_WORKFLOWS.clear()


class RecordingForge:
    def __init__(self, result_by_tool: dict[str, ToolResult] | None = None):
        self.invocations: list[tuple[str, dict]] = []
        self.reasoning_messages: list[str] = []
        self.result_by_tool = result_by_tool or {}

    async def invoke_tool(self, tool_name, args, **_kwargs):
        self.invocations.append((tool_name, dict(args)))
        return self.result_by_tool.get(
            tool_name,
            ToolResult(summary=f"{tool_name} complete", status="ok"),
        )

    async def run_turn(self, *, user_message, **_kwargs):
        self.reasoning_messages.append(user_message)
        return TurnResult(reply="Architecture advice only; no artifact generated.")


def _run_with_forge(monkeypatch, message: str, forge: RecordingForge, *, store=None):
    monkeypatch.setattr(archie_session, "_get_forge", lambda **_kwargs: forge)
    engagement_id = f"eng-{uuid.uuid4().hex}"
    return asyncio.run(archie_session.run_turn(
        customer_id=engagement_id,
        customer_name="Fictional Customer",
        user_message=message,
        store=store or InMemoryObjectStore(),
        text_runner=lambda *_args, **_kwargs: "unused",
    ))


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Build the XLSX BOM for the selected POC.", "generate_bom"),
        ("Generate a draw.io topology file.", "generate_diagram"),
        ("Generate Terraform for my diagram.", "generate_terraform"),
        ("Generate only the JEP artifact.", "generate_jep"),
    ],
)
def test_explicit_stage_invokes_only_requested_tool(monkeypatch, message, expected):
    forge = RecordingForge()
    result = _run_with_forge(monkeypatch, message, forge)

    assert [name for name, _args in forge.invocations] == [expected]
    assert forge.reasoning_messages == []
    assert [call["tool"] for call in result["tool_calls"]] == [expected]


def test_diagram_reference_does_not_regenerate_diagram(monkeypatch):
    forge = RecordingForge()
    _run_with_forge(monkeypatch, "Create Terraform for the latest diagram.", forge)
    assert [name for name, _args in forge.invocations] == ["generate_terraform"]


def test_advice_and_tradeoffs_remain_conversational(monkeypatch):
    forge = RecordingForge()
    result = _run_with_forge(
        monkeypatch,
        "Should this private workload use VPN or FastConnect, and why?",
        forge,
    )
    assert forge.invocations == []
    assert len(forge.reasoning_messages) == 1
    assert result["tool_calls"] == []


def test_conversational_question_batch_is_bounded(monkeypatch):
    forge = RecordingForge()

    async def many_questions(**_kwargs):
        return TurnResult(reply="Known context.\nOne?\nTwo?\nThree?\nFour?\nFive?")

    forge.run_turn = many_questions
    result = _run_with_forge(monkeypatch, "Help me discover the architecture.", forge)
    assert result["reply"].count("?") == 3


def test_note_capture_and_direct_context_reload_use_current_handlers():
    store = InMemoryObjectStore()
    engagement_id = f"notes-{uuid.uuid4().hex}"
    context = context_store.read_context(store, engagement_id, "Granite Harbor")
    forge = build_forge(
        store=store,
        customer_id=engagement_id,
        customer_name="Granite Harbor",
        text_runner=lambda *_args, **_kwargs: "unused",
        step3_planning=False,
    )

    saved = asyncio.run(forge.invoke_tool(
        "save_notes",
        {"text": "Java payments on PostgreSQL; private VPN connectivity; no public edge."},
        session_id=engagement_id,
        context=context,
    ))
    reloaded = context_store.read_context(store, engagement_id, "Granite Harbor")

    assert saved.status == "ok"
    assert saved.artifact_key and store.head(saved.artifact_key)
    summary = context_store.build_context_summary(reloaded)
    assert "Java payments" in summary
    assert "private VPN" in summary


def test_explicit_note_capture_invokes_save_notes_without_artifact_generation(monkeypatch):
    forge = RecordingForge({"save_notes": ToolResult(summary="Notes saved.", status="ok")})
    result = _run_with_forge(
        monkeypatch,
        "Please save this discovery note only: private Java API in us-ashburn-1.",
        forge,
    )

    assert [name for name, _args in forge.invocations] == ["save_notes"]
    assert [call["tool"] for call in result["tool_calls"]] == ["save_notes"]


def test_needs_input_status_is_preserved_for_explicit_stage(monkeypatch):
    forge = RecordingForge({
        "generate_bom": ToolResult(
            summary="Which compute count, OCPU, memory, and storage should I price?",
            status="needs_input",
            clarification="Provide sizing.",
        )
    })
    result = _run_with_forge(monkeypatch, "Build the BOM.", forge)

    assert result["tool_calls"][0]["result_status"] == "needs_input"
    assert "compute count" in result["reply"]


def test_selected_poc_stage_artifact_reference_is_retained(monkeypatch):
    key = "customers/example/artifacts/jep/v1.md"
    forge = RecordingForge({
        "generate_jep": ToolResult(
            summary="JEP Markdown and DOCX are ready.",
            status="ok",
            artifact_key=key,
            data={"docx_key": "customers/example/artifacts/jep/v1.docx"},
        )
    })
    result = _run_with_forge(monkeypatch, "Generate only the JEP artifact.", forge)

    assert result["artifacts"] == {"generate_jep": key}
    assert result["tool_calls"][0]["artifact_key"] == key
