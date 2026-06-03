from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

import drawing_agent_server as srv
from agent import note_extractor
from agent.document_store import get_all_notes_text, save_note, save_note_text
from agent.persistence_objectstore import InMemoryObjectStore


def _minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _minimal_docx(text: str) -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_extract_text_and_markdown() -> None:
    txt = note_extractor.extract_text(b"plain notes", "text/plain", "notes.bin")
    md = note_extractor.extract_text(b"# Markdown notes", "application/octet-stream", "notes.md")

    assert txt["detected_type"] == "txt"
    assert txt["extraction_status"] == "ok"
    assert txt["text"] == "plain notes"
    assert md["detected_type"] == "md"
    assert md["text"] == "# Markdown notes"


def test_extract_pdf_text() -> None:
    result = note_extractor.extract_text(_minimal_pdf("PDF customer notes"), "application/pdf", "notes.pdf")

    assert result["detected_type"] == "pdf"
    assert result["extraction_status"] == "ok"
    assert "PDF customer notes" in result["text"]


def test_extract_docx_text() -> None:
    result = note_extractor.extract_text(
        _minimal_docx("DOCX customer notes"),
        "application/octet-stream",
        "notes.docx",
    )

    assert result["detected_type"] == "docx"
    assert result["extraction_status"] == "ok"
    assert result["text"] == "DOCX customer notes"


def test_extract_unsupported_binary() -> None:
    result = note_extractor.extract_text(b"\x89PNG\r\n\x1a\n", "image/png", "diagram.png")

    assert result["detected_type"] == "unsupported"
    assert result["extraction_status"] == "unsupported"
    assert result["text"] == ""
    assert result["char_count"] == 0


def test_extract_failure_returns_warning(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(note_extractor, "_detect_type", lambda *_args: "pdf")
    monkeypatch.setitem(__import__("sys").modules, "pdfplumber", type("BadPdf", (), {"open": boom}))

    result = note_extractor.extract_text(b"%PDF-bad", "application/pdf", "bad.pdf")

    assert result["detected_type"] == "pdf"
    assert result["extraction_status"] == "failed"
    assert "parser exploded" in result["warning"]


def test_get_all_notes_text_prefers_saved_extracted_text() -> None:
    store = InMemoryObjectStore()
    save_note(store, "acme", "notes.pdf", b"%PDF-binary", "application/pdf")
    save_note_text(store, "acme", "notes.pdf", "readable extracted notes")

    assert "readable extracted notes" in get_all_notes_text(store, "acme")
    assert "%PDF-binary" not in get_all_notes_text(store, "acme")


def test_get_all_notes_text_legacy_fallback_and_unsupported_empty() -> None:
    store = InMemoryObjectStore()
    save_note(store, "acme", "legacy.md", b"legacy markdown", "text/markdown")
    save_note(store, "acme", "image.png", b"\x89PNG\r\n\x1a\n", "image/png")

    text = get_all_notes_text(store, "acme")

    assert "=== legacy.md ===" in text
    assert "legacy markdown" in text
    assert "image.png" not in text


def test_upload_note_returns_extraction_metadata_for_unsupported_binary() -> None:
    store = InMemoryObjectStore()
    srv.app.state.object_store = store
    srv.app.state.llm_runner = object()
    srv.app.state.persistence_config = {}
    try:
        with TestClient(srv.app, raise_server_exceptions=True) as client:
            response = client.post(
                "/api/notes/upload",
                files={"file": ("image.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")},
                data={"customer_id": "acme", "note_name": "image.png"},
            )
    finally:
        srv.app.state.object_store = None
        srv.app.state.llm_runner = None

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["key"] == "notes/acme/image.png"
    assert body["extraction_status"] == "unsupported"
    assert body["detected_type"] == "unsupported"


def test_upload_note_extraction_failure_still_saves_raw(monkeypatch) -> None:
    store = InMemoryObjectStore()
    srv.app.state.object_store = store
    srv.app.state.llm_runner = object()
    srv.app.state.persistence_config = {}

    def failed_extract(*_args, **_kwargs):
        return {
            "text": "",
            "detected_type": "pdf",
            "extraction_status": "failed",
            "char_count": 0,
            "warning": "parser failed",
        }

    monkeypatch.setattr("server.routes.documents.extract_text", failed_extract)
    try:
        with TestClient(srv.app, raise_server_exceptions=True) as client:
            response = client.post(
                "/api/notes/upload",
                files={"file": ("bad.pdf", io.BytesIO(b"%PDF-bad"), "application/pdf")},
                data={"customer_id": "acme", "note_name": "bad.pdf"},
            )
    finally:
        srv.app.state.object_store = None
        srv.app.state.llm_runner = None

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_status"] == "failed"
    assert body["warning"] == "parser failed"
    assert store.head("notes/acme/bad.pdf")
    assert store.head("customers/acme/notes/bad.pdf")
