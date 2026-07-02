from __future__ import annotations

from io import BytesIO
from pathlib import PurePath
from typing import Any


_CONTENT_TYPES = {
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/csv": "csv",
}

_EXTENSIONS = {
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".pdf": "pdf",
    ".docx": "docx",
    ".csv": "csv",
}


def _detect_type(content_bytes: bytes, content_type: str, filename: str) -> str:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type in _CONTENT_TYPES:
        return _CONTENT_TYPES[normalized_type]

    suffix = PurePath(filename or "").suffix.lower()
    if suffix in _EXTENSIONS:
        return _EXTENSIONS[suffix]

    if content_bytes.startswith(b"%PDF-"):
        return "pdf"
    if content_bytes.startswith(b"PK\x03\x04"):
        return "docx"
    return "unsupported"


def _result(
    *,
    text: str,
    detected_type: str,
    extraction_status: str,
    warning: str | None = None,
) -> dict[str, Any]:
    return {
        "text": text,
        "detected_type": detected_type,
        "extraction_status": extraction_status,
        "char_count": len(text),
        "warning": warning,
    }


def extract_text(content_bytes: bytes, content_type: str, filename: str) -> dict[str, Any]:
    """
    Extract readable text from an uploaded note file.

    Extraction failures are represented in the returned metadata instead of
    being raised, so raw note persistence can remain independent from parsing.
    """
    detected_type = _detect_type(content_bytes, content_type, filename)
    if detected_type == "unsupported":
        return _result(text="", detected_type=detected_type, extraction_status="unsupported")

    try:
        if detected_type in {"txt", "md", "csv"}:
            text = content_bytes.decode("utf-8", errors="replace")
        elif detected_type == "pdf":
            import pdfplumber

            with pdfplumber.open(BytesIO(content_bytes)) as pdf:
                text = "\n\n".join(page.extract_text() or "" for page in pdf.pages).strip()
        elif detected_type == "docx":
            import docx

            document = docx.Document(BytesIO(content_bytes))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
        else:
            return _result(text="", detected_type="unsupported", extraction_status="unsupported")
    except Exception as exc:
        return _result(
            text="",
            detected_type=detected_type,
            extraction_status="failed",
            warning=str(exc),
        )

    return _result(text=text, detected_type=detected_type, extraction_status="ok")
