from types import SimpleNamespace

import pytest

from agent import archie_native_loop
from agent.persistence_objectstore import InMemoryObjectStore
from skillforge.registry import ToolSpec
from skillforge.types import MemorySnapshot, ToolResult


class _Memory:
    def assemble(self, *, session_id, context, user_message):
        return MemorySnapshot(
            session_id=session_id,
            facts={"facts_summary": "Current POC uses a two-tier application."},
            prior_artifacts={},
            raw=context,
        )

    def update(self, *, session_id, tool_name, result, context):
        return context


def _wire_native(monkeypatch, specs):
    monkeypatch.setattr(archie_native_loop, "build_forge", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(
        archie_native_loop,
        "get_registered_tool_specs",
        lambda _forge: tuple(specs),
    )
    monkeypatch.setattr(
        archie_native_loop,
        "get_registered_memory",
        lambda _forge: _Memory(),
    )
    monkeypatch.setattr(
        archie_native_loop.context_store,
        "read_context",
        lambda *_args: {"customer_id": "c1"},
    )
    monkeypatch.setattr(
        archie_native_loop.document_store,
        "load_conversation_history",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        archie_native_loop.document_store,
        "save_conversation_turns",
        lambda *_args: None,
    )


@pytest.mark.asyncio
async def test_existing_bom_question_uses_lookup_and_never_invents_bom(monkeypatch):
    async def get_document(args, **_kwargs):
        assert args == {"type": "bom"}
        return ToolResult(
            summary="No bom document found.",
            status="needs_input",
            clarification="No bom found. Generate one first.",
        )

    _wire_native(
        monkeypatch,
        [ToolSpec(name="get_document", handler=get_document, description="Fetch a stored document.")],
    )
    responses = iter(
        [
            {"tool": "get_document", "args": {"type": "bom"}},
            "No, there isn't a stored BOM for this engagement.",
        ]
    )

    async def tool_runner(prompt, system_message, schemas, label):
        assert system_message == archie_native_loop.SYSTEM_IDENTITY
        assert label == "orchestrator"
        assert any(schema.name == "get_document" for schema in schemas)
        return next(responses)

    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="do we have a BOM?",
        store=InMemoryObjectStore(),
        text_runner=lambda *_args: "unused",
        tool_runner=tool_runner,
    )

    assert [call["tool"] for call in result["tool_calls"]] == ["get_document"]
    assert result["artifacts"] == {}
    assert "No" in result["reply"]
    assert "line item" not in result["reply"].lower()


@pytest.mark.asyncio
async def test_bom_request_calls_registered_sub_agent_handler_and_produces_xlsx(monkeypatch):
    store = InMemoryObjectStore()
    artifact_key = "artifacts/c1/bom/v1/oci_bom.xlsx"

    async def bom_sub_agent_handler(args, **_kwargs):
        assert args == {"prompt": "make me a BOM for this POC"}
        store.put(
            artifact_key,
            b"PK\x03\x04native-bom-workbook",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        return ToolResult(
            summary="BOM generated.",
            status="ok",
            artifact_key=artifact_key,
        )

    _wire_native(
        monkeypatch,
        [ToolSpec(name="generate_bom", handler=bom_sub_agent_handler, description="Generate a BOM.")],
    )
    responses = iter(
        [
            {"tool": "generate_bom", "args": {"prompt": "make me a BOM for this POC"}},
            f"The BOM is ready: {artifact_key}",
        ]
    )

    async def tool_runner(*_args):
        return next(responses)

    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="make me a BOM for this POC",
        store=store,
        text_runner=lambda *_args: "unused",
        tool_runner=tool_runner,
    )

    assert store.head(artifact_key)
    assert artifact_key.endswith(".xlsx")
    assert result["artifacts"] == {"generate_bom": artifact_key}
    assert [call["tool"] for call in result["tool_calls"]] == ["generate_bom"]


@pytest.mark.asyncio
async def test_ha_advice_is_conversational_without_artifact(monkeypatch):
    calls = []

    async def forbidden_handler(args, **_kwargs):
        calls.append(args)
        return ToolResult(summary="unexpected", status="ok", artifact_key="unexpected")

    _wire_native(
        monkeypatch,
        [ToolSpec(name="generate_diagram", handler=forbidden_handler, description="Generate a diagram.")],
    )

    async def tool_runner(*_args):
        return "For HA here, use instances across fault domains behind an OCI Load Balancer."

    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="what would you recommend for HA here?",
        store=InMemoryObjectStore(),
        text_runner=lambda *_args: "unused",
        tool_runner=tool_runner,
    )

    assert calls == []
    assert result["tool_calls"] == []
    assert result["artifacts"] == {}
    assert "HA" in result["reply"]
