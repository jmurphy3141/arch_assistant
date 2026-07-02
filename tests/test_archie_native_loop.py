from types import SimpleNamespace

import pytest

from agent import archie_memory_retrieval, archie_native_loop, context_store
from agent.tools.notes import NotesHandlers
from agent.engagement_mission import C3E_PHASE_ORDER
from agent.persistence_objectstore import InMemoryObjectStore
from skillforge.protocols import ArgSchema
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
        assert label == "native_orchestrator"
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


def test_native_identity_has_standing_c3e_methodology_without_generation_pressure():
    identity = archie_native_loop.SYSTEM_IDENTITY

    assert " → ".join(C3E_PHASE_ORDER) in identity
    assert "C3E guides the conversation, never generation" in identity
    assert "next-required artifact only to decide whether to offer" in identity
    assert "phase or artifact gate is never a request to produce" in identity


def test_native_identity_offers_artifacts_and_reports_tool_status_honestly():
    identity = archie_native_loop.SYSTEM_IDENTITY

    assert "offer the next deliverable" in identity
    assert "only when the user explicitly requests" in identity
    assert identity.count("Call a generate_* tool only") == 1
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
    producer_calls = 0

    async def producer(_args, **_kwargs):
        nonlocal producer_calls
        producer_calls += 1
        return ToolResult(
            summary="POC duration is required.",
            status="needs_input",
            clarification="Please provide POC duration.",
        )

    _wire_native(
        monkeypatch,
        [ToolSpec(name="generate_poc_plan", handler=producer, description="Generate POC plan.")],
    )
    responses = iter([{"tool": "generate_poc_plan", "args": {}}])

    async def tool_runner(_prompt, *_args):
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
    assert producer_calls == 1
    assert result["reply"] == "Please provide POC duration."
    assert not any(word in result["reply"].lower() for word in ("saved", "ready", ".md"))


@pytest.mark.asyncio
async def test_identical_tool_call_reuses_prior_result_without_redispatch(monkeypatch):
    producer_calls = 0

    async def producer(_args, **_kwargs):
        nonlocal producer_calls
        producer_calls += 1
        return ToolResult(summary="Lookup complete.", status="ok")

    _wire_native(
        monkeypatch,
        [ToolSpec(name="lookup", handler=producer, description="Lookup.")],
    )
    responses = iter(
        [
            {"tool": "lookup", "args": {"query": "same"}},
            {"tool": "lookup", "args": {"query": "same"}},
            "Done.",
        ]
    )

    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="Look it up.",
        store=InMemoryObjectStore(),
        text_runner=lambda *_args: "unused",
        tool_runner=lambda *_args: next(responses),
    )

    assert producer_calls == 1
    assert len(result["tool_calls"]) == 2
    assert result["reply"] == "Done."


@pytest.mark.asyncio
async def test_poc_option_selection_is_persisted_through_confirm_tool(monkeypatch):
    selected: dict[str, str] = {}
    prior_context = {
        "customer_id": "c1",
        "latest_decision_context": {
            "poc_options": [
                {"option_name": "Option 1"},
                {"option_name": "Option 2"},
            ]
        },
    }

    async def confirm(args, **_kwargs):
        assert args == {"action": "confirm", "confirmed_option_name": "option 2"}
        selected["option"] = "Option 2"
        return ToolResult(
            summary="POC confirmed: Option 2.",
            status="ok",
            data={"selected_option": {"option_name": "Option 2"}},
        )

    spec = ToolSpec(
        name="generate_poc_plan",
        handler=confirm,
        description=(
            "Persist a selected POC option with action='confirm'; the selection is not "
            "recorded until this call succeeds."
        ),
        args={
            "action": ArgSchema(
                description="Use confirm to persist a choice.",
                type="string",
                required=False,
            ),
            "confirmed_option_name": ArgSchema(
                description="The selected option.",
                type="string",
                required=False,
            ),
        },
    )
    _wire_native(monkeypatch, [spec])
    monkeypatch.setattr(
        archie_native_loop.context_store,
        "read_context",
        lambda *_args: prior_context,
    )
    responses = iter(
        [
            {
                "tool": "generate_poc_plan",
                "args": {"action": "confirm", "confirmed_option_name": "option 2"},
            },
            "Option 2 is recorded.",
        ]
    )

    async def tool_runner(_prompt, system_message, schemas, _label):
        assert "do not say it is confirmed until that call returns successfully" in system_message
        schema = next(item for item in schemas if item.name == "generate_poc_plan")
        assert "not recorded until" in schema.description
        assert {"action", "confirmed_option_name"} <= set(schema.args)
        return next(responses)

    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="Let's go with option 2.",
        store=InMemoryObjectStore(),
        text_runner=lambda *_args: "unused",
        tool_runner=tool_runner,
    )

    assert selected == {"option": "Option 2"}
    assert result["tool_calls"][0]["result_status"] == "ok"
    assert result["tool_calls"][0]["result_data"]["selected_option"] == {
        "option_name": "Option 2"
    }


@pytest.mark.asyncio
async def test_failed_poc_confirmation_does_not_claim_recorded_decision(monkeypatch):
    async def confirm(_args, **_kwargs):
        return ToolResult(
            summary="Select one of the persisted POC options.",
            status="needs_input",
            clarification="Select one of the persisted POC options.",
        )

    _wire_native(
        monkeypatch,
        [ToolSpec(name="generate_poc_plan", handler=confirm, description="Confirm POC.")],
    )
    responses = iter(
        [
            {
                "tool": "generate_poc_plan",
                "args": {"action": "confirm", "confirmed_option_name": "option 2"},
            }
        ]
    )
    result = await archie_native_loop.run_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="Let's go with option 2.",
        store=InMemoryObjectStore(),
        text_runner=lambda *_args: "unused",
        tool_runner=lambda *_args: next(responses),
    )

    assert result["reply"] == "Select one of the persisted POC options."
    assert "confirmed" not in result["reply"].lower()


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
