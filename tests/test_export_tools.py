from io import BytesIO

import openpyxl
import pytest

from agent import context_store, png_exporter
from agent.export_tools import get_export_tool_specs
from agent.persistence_objectstore import InMemoryObjectStore


def _workbook_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["SKU", "Quantity"])
    sheet.append(["B91628", 12])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _handler(store: InMemoryObjectStore):
    specs = get_export_tool_specs(
        store=store, engagement_id="acme", customer_name="Acme"
    )
    assert [spec.name for spec in specs] == ["export_artifact"]
    return specs[0].handler


def _index(store: InMemoryObjectStore, artifact_type: str, key: str) -> dict:
    context = context_store.read_context(store, "acme", "Acme")
    context_store.register_artifact(context, artifact_type, key=key)
    context_store.write_context(store, "acme", context)
    return context


@pytest.mark.asyncio
async def test_exports_xlsx_to_sibling_csv() -> None:
    store = InMemoryObjectStore()
    source_key = "customers/acme/bom/xlsx/oci-bom.xlsx"
    store.put(source_key, _workbook_bytes())
    context = _index(store, "bom", source_key)

    result = await _handler(store)(
        {"type": "bom", "format": "csv"},
        memory=None,
        context=context,
        trace_id="export",
    )

    assert result.status == "ok"
    assert result.artifact_key == "customers/acme/bom/xlsx/oci-bom.csv"
    assert b"SKU,Quantity" in store.get(result.artifact_key)
    assert result.data["download_url"].startswith("/api/download?key=")


@pytest.mark.asyncio
async def test_png_export_reports_missing_cli(monkeypatch) -> None:
    store = InMemoryObjectStore()
    source_key = "customers/acme/diagrams/v1/diagram.drawio"
    store.put(source_key, b"<mxfile />")
    context = _index(store, "diagram", source_key)
    monkeypatch.setattr(png_exporter, "DRAWIO_CLI", None)

    result = await _handler(store)(
        {"type": "diagram", "format": "png"},
        memory=None,
        context=context,
        trace_id="export",
    )

    assert result.status == "error"
    assert "PNG export CLI unavailable" in result.summary


@pytest.mark.asyncio
async def test_unknown_export_request_never_raises() -> None:
    result = await _handler(InMemoryObjectStore())(
        {"type": "bom", "format": "pdf"},
        memory=None,
        context={},
        trace_id="export",
    )

    assert result.status == "needs_input"
