from io import BytesIO

import openpyxl
import pytest

from agent import archie_native_loop, context_store, document_store
from agent.archie_wiring import build_forge
from agent.file_reader_tools import get_file_reader_tool_specs
from agent.persistence_objectstore import InMemoryObjectStore


def _workbook_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "BOM"
    sheet.append(["SKU", "Description", "Quantity"])
    sheet.append(["B91628", "OCI Generative AI tokens", 12])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _handler(store: InMemoryObjectStore):
    specs = get_file_reader_tool_specs(
        store=store, engagement_id="acme", customer_name="Acme"
    )
    assert [spec.name for spec in specs] == ["read_file_content"]
    return specs[0].handler


@pytest.mark.asyncio
async def test_reads_stored_bom_workbook_rows() -> None:
    store = InMemoryObjectStore()
    key = "customers/acme/bom/xlsx/oci-bom.xlsx"
    store.put(key, _workbook_bytes())
    context = context_store.read_context(store, "acme", "Acme")
    context_store.register_artifact(context, "bom", key=key)
    context_store.write_context(store, "acme", context)

    result = await _handler(store)(
        {"type": "bom"}, memory=None, context=context, trace_id="read"
    )

    assert result.status == "ok"
    assert "[Sheet: BOM]" in result.data["content"]
    assert "B91628 | OCI Generative AI tokens | 12" in result.data["content"]


@pytest.mark.asyncio
async def test_reads_uploaded_text_by_name() -> None:
    store = InMemoryObjectStore()
    document_store.save_note(
        store, "acme", "requirements.txt", b"RTO is 15 minutes"
    )

    result = await _handler(store)(
        {"file": "requirements.txt"}, memory=None, context={}, trace_id="read"
    )

    assert result.status == "ok"
    assert result.data["content"] == "RTO is 15 minutes"


@pytest.mark.asyncio
async def test_missing_file_requests_a_resolvable_reference() -> None:
    result = await _handler(InMemoryObjectStore())(
        {"file": "missing.docx"}, memory=None, context={}, trace_id="read"
    )

    assert result.status == "needs_input"
    assert "name/key" in result.clarification


@pytest.mark.asyncio
async def test_general_primitives_are_native_only() -> None:
    store = InMemoryObjectStore()
    seen: set[str] = set()

    def tool_runner(_prompt, _system, schemas, _label):
        seen.update(schema.name for schema in schemas)
        return "Done."

    await archie_native_loop.run_turn(
        customer_id="acme",
        customer_name="Acme",
        user_message="Hello",
        store=store,
        text_runner=lambda *_args: "unused",
        tool_runner=tool_runner,
    )

    assert {"read_file_content", "compute", "export_artifact"} <= seen
    forge = build_forge(
        store=store,
        customer_id="acme",
        customer_name="Acme",
        text_runner=lambda *_args: "unused",
    )
    assert not {
        "read_file_content",
        "compute",
        "export_artifact",
    }.intersection(forge._registry._tools)
