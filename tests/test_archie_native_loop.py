from types import SimpleNamespace

import pytest

from agent import archie_memory_retrieval, archie_native_loop, context_store
from agent.tools.notes import NotesHandlers
from agent.engagement_mission import C3E_PHASE_ORDER, PHASE_ARTIFACTS
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

    async def tool_runner(_prompt, system_message, schemas, _label):
        assert system_message == archie_native_loop.SYSTEM_IDENTITY
        assert all(not schema.name.startswith("use_hat_") for schema in schemas)
        assert {
            "lookup_compute_shapes",
            "lookup_price",
            "lookup_reference_architecture",
        } <= {schema.name for schema in schemas}
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


def test_native_identity_has_standing_c3e_methodology_and_gates():
    identity = archie_native_loop.SYSTEM_IDENTITY

    assert " → ".join(C3E_PHASE_ORDER) in identity
    for artifacts in PHASE_ARTIFACTS.values():
        for artifact in artifacts:
            expected = {
                "sta": "Strategic Technical Approach",
                "pov": "POV",
                "diagram": "architecture diagram",
                "bom": "BOM",
                "jep": "JEP",
                "waf": "WAF assessment",
                "technical_proposal": "technical proposal",
                "terraform": "Terraform",
            }[artifact]
            assert expected in identity


def test_native_identity_offers_artifacts_and_reports_tool_status_honestly():
    identity = archie_native_loop.SYSTEM_IDENTITY

    assert "offer the next deliverable" in identity
    assert "only when the user explicitly asks" in identity
    assert "Report every tool's actual status" in identity
    assert "unless that tool returned the artifact key on this turn" in identity


def test_native_working_set_always_includes_live_c3e_phase_state():
    context = {
        "customer_id": "c1",
        "agents": {},
        "archie": {
            "mission": {
                "phase": "Design",
                "completed_artifacts": ["diagram"],
            },
            "client_facts": {
                "economic_buyer": "CIO",
                "platform": "VMware",
            },
        },
    }

    working_set = archie_memory_retrieval.assemble_working_set(
        context=context,
        session_summary="",
        history=[],
        working_set_turns=6,
        char_budget=4000,
    )

    assert "[LIVE C3E PHASE STATE]" in working_set
    assert '"phase": "Design"' in working_set
    assert '"next_required": ["bom"]' in working_set
    assert '"blockers": []' in working_set


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


@pytest.mark.asyncio
async def test_needs_input_is_unambiguous_and_reply_does_not_claim_success(monkeypatch):
    async def producer(_args, **_kwargs):
        return ToolResult(
            summary="POC duration is required.",
            status="needs_input",
            clarification="Please provide POC duration.",
        )

    _wire_native(
        monkeypatch,
        [ToolSpec(name="generate_poc_plan", handler=producer, description="Generate POC plan.")],
    )
    responses = iter(
        [
            {"tool": "generate_poc_plan", "args": {}},
            "Please provide the POC duration.",
        ]
    )

    async def tool_runner(prompt, *_args):
        if "[TOOL RESULT" in prompt:
            assert "NO ARTIFACT PRODUCED" in prompt
            assert "Status: needs_input" in prompt
            assert "Please provide POC duration" in prompt
        return next(responses)

    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="Make the POC plan.",
        store=InMemoryObjectStore(),
        text_runner=lambda *_args: "unused",
        tool_runner=tool_runner,
    )

    assert result["tool_calls"][0]["result_status"] == "needs_input"
    assert not any(word in result["reply"].lower() for word in ("saved", "ready", ".md"))


@pytest.mark.asyncio
async def test_native_artifact_index_is_immediately_retrievable(monkeypatch):
    store = InMemoryObjectStore()
    artifact_key = "artifacts/c1/bom/v1/oci_bom.xlsx"
    notes = NotesHandlers(store, "c1", "Acme")

    async def producer(_args, **_kwargs):
        store.put(artifact_key, b"workbook", "application/octet-stream")
        return ToolResult(
            summary="Northwind BOM generated.",
            status="ok",
            data={"xlsx_artifact_key": artifact_key},
        )

    monkeypatch.setattr(archie_native_loop, "build_forge", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(
        archie_native_loop,
        "get_registered_tool_specs",
        lambda _forge: (
            ToolSpec(name="generate_bom", handler=producer, description="Generate BOM."),
            ToolSpec(name="get_document", handler=notes.get_document, description="Get document."),
        ),
    )
    monkeypatch.setattr(archie_native_loop, "get_registered_memory", lambda _forge: _Memory())

    first = iter([{"tool": "generate_bom", "args": {}}, "The BOM was generated."])
    await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="Generate the BOM.",
        store=store,
        text_runner=lambda *_args: "unused",
        tool_runner=lambda *_args: next(first),
    )

    second = iter(
        [
            {"tool": "get_document", "args": {"type": "bom"}},
            "The BOM excludes Gen AI token costs.",
        ]
    )
    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="Did the BOM include Gen AI token costs?",
        store=store,
        text_runner=lambda *_args: "unused",
        tool_runner=lambda *_args: next(second),
    )

    assert [call["tool"] for call in result["tool_calls"]] == ["get_document"]
    assert result["tool_calls"][0]["artifact_key"] == artifact_key
    indexed = context_store.read_context(store, "c1", "Acme")["artifacts"]
    assert indexed["bom"]["key"] == artifact_key
    listed = await archie_memory_retrieval.NativeMemoryTools(store, "c1", "Acme").list_artifacts(
        {}, memory=None, context={}, trace_id="list"
    )
    assert any(item["type"] == "bom" and item["key"] == artifact_key for item in listed.data["artifacts"])


@pytest.mark.asyncio
async def test_native_bom_payload_is_persisted_before_artifact_indexing(monkeypatch):
    store = InMemoryObjectStore()
    artifact_key = "customers/c1/bom/xlsx/native.xlsx"

    async def fake_persist(result, *, customer_id, store):
        assert customer_id == "c1"
        return ToolResult(
            summary=result.summary,
            status="ok",
            artifact_key=artifact_key,
            data={**result.data, "xlsx_artifact_key": artifact_key},
        )

    async def producer(_args, **_kwargs):
        return ToolResult(
            summary="BOM generated.",
            status="ok",
            data={"bom_payload": {"line_items": [{"description": "Compute"}]}},
        )

    monkeypatch.setattr(archie_native_loop, "_persist_native_bom_artifact", fake_persist)
    _wire_native(
        monkeypatch,
        [ToolSpec(name="generate_bom", handler=producer, description="Generate BOM.")],
    )
    responses = iter([{"tool": "generate_bom", "args": {}}, "BOM generated."])
    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="Generate a BOM.",
        store=store,
        text_runner=lambda *_args: "unused",
        tool_runner=lambda *_args: next(responses),
    )

    assert result["tool_calls"][0]["artifact_key"] == artifact_key
    assert result["artifacts"] == {"generate_bom": artifact_key}
