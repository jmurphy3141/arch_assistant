"""Deterministic guards for LLM-rendered customer document prose."""
from __future__ import annotations

import re
from typing import Iterable


_INTERNAL_ARTIFACT_PATTERNS = (
    re.compile(r"(?i)(?:^|[\s('`\"])(?:agent3|poc_plan)/[^\s)'`\"]+"),
    re.compile(r"(?i)(?:^|[\s('`\"])customers/[^\s/]+/bom/[^\s)'`\"]+"),
    re.compile(r"(?i)\b[^\s'\"`]+\.(?:drawio|xlsx|json)\b"),
)
_NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?%?", re.IGNORECASE)
_SHAPE_TOKEN_RE = re.compile(
    r"\b(?:VM\.Standard\.)?(?:E5|E6|A1)(?:\.[A-Za-z0-9]+)*\b",
    re.IGNORECASE,
)
_UNIT_TOKEN_RE = re.compile(r"\b(?:OCPUs?|GB|TB)\b", re.IGNORECASE)
_DATE_TOKEN_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b|\b\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
_ASSERTIVE_CLAIM_RE = re.compile(
    r"\b(?:guarantee[sd]?|minimi[sz]e[sd]?\s+(?:risk|cost|latency)|"
    r"(?:improve[sd]?|reduce[sd]?|maximi[sz]e[sd]?|optimi[sz]e[sd]?|accelerate[sd]?|deliver[sd]?)\s+"
    r"(?:availability|performance|security|savings|cost|latency|resilience|throughput)|"
    r"realistic conditions?|performance isolation|security isolation)\b",
    re.IGNORECASE,
)


def internal_artifact_references(text: str) -> list[str]:
    """Return internal object keys or file paths that must not reach prose."""
    findings: list[str] = []
    for pattern in _INTERNAL_ARTIFACT_PATTERNS:
        findings.extend(match.group(0).strip(" ('`\"") for match in pattern.finditer(str(text or "")))
    return list(dict.fromkeys(findings))


def leaks_internal_artifacts(text: str) -> bool:
    return bool(internal_artifact_references(text))


def unsupported_oci_services(rendered: str, allowed_source: str) -> list[str]:
    return _unsupported_oci_services(rendered, allowed_source)


def factual_token_findings(
    rendered: str,
    grounded_source: str,
    *,
    allowed_format_numbers: Iterable[str] = (),
) -> list[str]:
    """Reject factual tokens that do not occur in the serialized brief."""
    output = str(rendered or "")
    token_output = re.sub(r"(?m)^(#{1,6}\s*)\d+[.)]?\s*", r"\1", output)
    token_output = re.sub(r"(?m)^\s*\d+[.)]\s+", "", token_output)
    source = str(grounded_source or "")
    source_lower = source.lower()
    allowed_numbers = {_normalize_token(item) for item in _NUMBER_TOKEN_RE.findall(source)}
    allowed_numbers.update(_normalize_token(item) for item in allowed_format_numbers)
    unsupported_numbers = sorted(
        {
            token
            for token in _NUMBER_TOKEN_RE.findall(token_output)
            if _normalize_token(token) not in allowed_numbers
        },
        key=str.lower,
    )
    findings: list[str] = []
    if unsupported_numbers:
        findings.append("unsupported numeric tokens: " + ", ".join(unsupported_numbers))

    for label, pattern in (("shape", _SHAPE_TOKEN_RE), ("unit", _UNIT_TOKEN_RE), ("date", _DATE_TOKEN_RE)):
        unsupported = sorted(
            {match.group(0) for match in pattern.finditer(token_output) if match.group(0).lower() not in source_lower},
            key=str.lower,
        )
        if unsupported:
            findings.append(f"unsupported {label} tokens: " + ", ".join(unsupported))

    internal = internal_artifact_references(output)
    if internal:
        findings.append("internal artifact references: " + ", ".join(internal[:8]))
    unsupported_services = _unsupported_oci_services(output, source)
    if unsupported_services:
        findings.append("unsupported OCI services: " + ", ".join(unsupported_services))
    unsupported_claims = sorted(
        {
            match.group(0)
            for match in _ASSERTIVE_CLAIM_RE.finditer(output)
            if match.group(0).lower() not in source_lower and not _claim_is_negated(output, match.start())
        },
        key=str.lower,
    )
    if unsupported_claims:
        findings.append("unsupported outcome or benefit claims: " + ", ".join(unsupported_claims))
    return findings


def missing_exact_facts(rendered: str, facts: Iterable[str]) -> list[str]:
    """Return required brief strings that are not preserved in normalized prose."""
    normalized_rendered = _normalize_text(rendered)
    return [fact for fact in facts if _normalize_text(fact) not in normalized_rendered]


def has_readable_prose(text: str, *, minimum_paragraphs: int = 2) -> bool:
    """Require substantive prose paragraphs, not only headings/tables/bullets."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", str(text or "")):
        stripped = block.strip()
        if not stripped or stripped.startswith(("#", "|", "- ", "* ", ">")):
            continue
        if len(re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", stripped)) >= 18:
            paragraphs.append(stripped)
    return len(paragraphs) >= minimum_paragraphs


def has_material_prose_revision(rendered: str, baseline: str, *, minimum_changed: int = 2) -> bool:
    """Require multiple substantive prose paragraphs to differ from the template."""
    baseline_paragraphs = {_normalize_text(item) for item in _prose_paragraphs(baseline)}
    changed = [
        item for item in _prose_paragraphs(rendered)
        if _normalize_text(item) not in baseline_paragraphs
    ]
    return len(changed) >= minimum_changed


def _prose_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", str(text or "")):
        stripped = block.strip()
        if not stripped or stripped.startswith(("#", "|", "- ", "* ", ">")):
            continue
        if len(re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", stripped)) >= 18:
            paragraphs.append(stripped)
    return paragraphs


def _normalize_token(value: str) -> str:
    return str(value or "").replace(",", "").lower()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("–", "-").replace("—", "-")).strip().lower()


def _unsupported_oci_services(rendered: str, grounded_source: str) -> list[str]:
    from agent import consistency_contract

    output = str(rendered or "").lower()
    source = str(grounded_source or "").lower()
    unsupported: list[str] = []
    for service_id, aliases in consistency_contract._SERVICE_ALIASES:
        display = consistency_contract.display_name(service_id)
        markers = (display.lower(),) + aliases
        output_has = any(_contains_marker(output, marker) for marker in markers)
        source_has = any(_contains_marker(source, marker) for marker in markers)
        if output_has and not source_has:
            unsupported.append(display)
    return list(dict.fromkeys(unsupported))


def _contains_marker(text: str, marker: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(marker.lower())}(?![a-z0-9])", text))


def _claim_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 80):start].lower()
    sentence_prefix = re.split(r"[.!?;\n]", prefix)[-1]
    return bool(re.search(r"\b(?:not|no|without|never)\b|does not claim", sentence_prefix))
