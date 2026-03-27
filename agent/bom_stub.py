"""
agent/bom_stub.py
------------------
Stub BOM generator for JEP documents.

Extracts hardware/software/storage specs from meeting notes using the LLM.
Returns a structured dict and can render it as Markdown tables.

NOTE: This is a stub pending integration with Agent 2 (BOM Sizing Agent).
When Agent 2 is available, replace generate_stub_bom() with an A2A call to
Agent 2's generate_bom skill, which will provide pricing and full OCI shapes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

_STUB_SYSTEM = (
    "You are an Oracle Cloud expert extracting Bill of Materials information. "
    "Output ONLY valid JSON. No markdown fences, no preamble, no explanation. "
    "Every response must start with '{' and end with '}'."
)

_STUB_PROMPT = """\
Extract Bill of Materials information from these meeting notes for an OCI POC.

Notes:
{notes}

Output JSON with this exact structure:
{{
    "source": "stub",
    "agent": "agent3-bom-stub",
    "note": "Pending Agent 2 (BOM Sizing Agent) integration",
    "duration_days": <integer, default 14 if not specified>,
    "funding": "Oracle",
    "hardware": [
        {{"item": "<component name>", "shape": "<OCI shape or spec string>", "quantity": <int>, "unit_cost": "TBD", "notes": "<extra context>"}}
    ],
    "software": [
        {{"item": "<software name>", "version": "<version string>", "notes": "<extra context>"}}
    ],
    "storage": [
        {{"item": "<storage type>", "capacity": "<size string>", "notes": "<extra context>"}}
    ]
}}

Rules:
- Extract GPU type/model, CPU OCPU count, RAM size, NVMe storage from notes.
- Extract software: OS, CUDA version, Kubernetes version, container runtime, ML framework.
- Extract storage requirements (file storage, object storage, block volume).
- If a field is not mentioned, use sensible defaults or empty lists.
- duration_days: look for "14 day", "2 week", "30 day" etc.
Output ONLY valid JSON.
"""


def generate_stub_bom(
    notes_text: str,
    text_runner: Callable[[str, str], str],
    *,
    customer_name: str = "Customer",
) -> dict:
    """
    Generate a stub BOM dict from meeting notes via LLM extraction.

    Args:
        notes_text: All meeting notes as a single concatenated string.
        text_runner: callable(prompt, system_message) -> str.
            Must return raw text (the same runner used for writing agents).
        customer_name: Used only for log messages.

    Returns:
        BOM dict with keys: source, agent, note, duration_days, funding,
        hardware, software, storage.
        Falls back to a minimal dict if LLM output cannot be parsed.
    """
    prompt = _STUB_PROMPT.format(notes=notes_text[:6000])
    try:
        raw = text_runner(prompt, _STUB_SYSTEM)
        # Strip fences if the model disobeyed the system prompt
        raw = raw.strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            raw = m.group(1).strip()
        bom = json.loads(raw)
        logger.info("Stub BOM extracted for %s: %d hardware items", customer_name, len(bom.get("hardware", [])))
        return bom
    except Exception as exc:
        logger.warning("Stub BOM parse failed for %s (%s) — using minimal fallback", customer_name, exc)
        return {
            "source": "stub",
            "agent": "agent3-bom-stub",
            "note": "Pending Agent 2 (BOM Sizing Agent) integration — auto-extract failed",
            "duration_days": 14,
            "funding": "Oracle",
            "hardware": [],
            "software": [],
            "storage": [],
        }


def bom_to_markdown(bom: dict) -> str:
    """
    Render a BOM dict as Markdown tables suitable for insertion into a JEP.
    """
    lines: list[str] = []

    lines.append(
        f"> **Note**: {bom.get('note', 'Pending Agent 2 (BOM Sizing Agent) integration')}"
    )
    lines.append("")
    lines.append(
        f"**POC Duration**: {bom.get('duration_days', 14)} days  |  "
        f"**Funding**: {bom.get('funding', 'Oracle')}"
    )
    lines.append("")

    hardware = bom.get("hardware", [])
    if hardware:
        lines += [
            "**Hardware:**",
            "",
            "| Item | Shape / Spec | Qty | Unit Cost | Notes |",
            "|------|--------------|-----|-----------|-------|",
        ]
        for h in hardware:
            lines.append(
                f"| {h.get('item', '')} | {h.get('shape', '')} | "
                f"{h.get('quantity', '')} | {h.get('unit_cost', 'TBD')} | "
                f"{h.get('notes', '')} |"
            )
        lines.append("")

    software = bom.get("software", [])
    if software:
        lines += [
            "**Software:**",
            "",
            "| Item | Version | Notes |",
            "|------|---------|-------|",
        ]
        for s in software:
            lines.append(
                f"| {s.get('item', '')} | {s.get('version', '')} | {s.get('notes', '')} |"
            )
        lines.append("")

    storage = bom.get("storage", [])
    if storage:
        lines += [
            "**Storage:**",
            "",
            "| Item | Capacity | Notes |",
            "|------|----------|-------|",
        ]
        for s in storage:
            lines.append(
                f"| {s.get('item', '')} | {s.get('capacity', '')} | {s.get('notes', '')} |"
            )
        lines.append("")

    if not hardware and not software and not storage:
        lines.append(
            "*BOM details not extracted — provide meeting notes with hardware/software specs, "
            "or integrate Agent 2 for full BOM generation.*"
        )

    return "\n".join(lines)
