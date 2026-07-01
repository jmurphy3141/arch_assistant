"""Grounded, deterministic Joint Execution Plan composition.

The composer accepts only the current request and same-engagement context.  It
does not call an LLM and it never fills a missing business fact with a generic
example.  Customer and workload are the only required draft inputs; unknown
logistics render as explicit placeholders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Iterable


CORE_SECTIONS = (
    "Executive Summary",
    "Objectives",
    "Scope",
    "POC Architecture",
    "Phased Execution Plan",
    "Success Criteria",
    "Resource Plan",
    "Risk Registry",
    "Approvals",
)

_REGION_RE = re.compile(r"\b(?:[a-z]{2,}-[a-z]+-\d+)\b", re.IGNORECASE)
_DURATION_RE = re.compile(r"\b(\d+[- ](?:day|week|month)s?)\b", re.IGNORECASE)
_PHASE_RE = re.compile(
    r"Phase\s*([123])\s*[-:]?\s*(Assessment|Build|Validate)\s+(?:on\s+)?"
    r"((?:days?|weeks?)\s*\d+\s*(?:-|–|to)\s*\d+|(?:day|week)\s*\d+)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"(?:[<>]=?\s*)?\d+(?:\.\d+)?\s*(?:%|ms|milliseconds?|seconds?|minutes?|hours?|"
    r"days?|weeks?|months?|(?:[a-z]+\s+){0,2}"
    r"(?:requests?|orders?|bookings?|transactions?)\s+per\s+(?:second|minute|hour)|"
    r"concurrent(?:\s+[a-z]+){0,2}\s+(?:sessions?|users?)|rps|qps|tps)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JepPhase:
    number: int
    name: str
    window: str


@dataclass(frozen=True)
class JepOwner:
    organization: str
    role: str
    commitment: str


@dataclass(frozen=True)
class JepBrief:
    customer: str
    region: str
    duration: str
    phases: tuple[JepPhase, JepPhase, JepPhase]
    scope: tuple[str, ...]
    architecture: str
    criteria: tuple[str, ...]
    owners: tuple[JepOwner, ...]
    risks: tuple[str, ...]
    approvals: str
    artifact_references: tuple[str, ...] = ()
    bom_scope: tuple[str, ...] = ()
    handoff_requirements: tuple[str, ...] = ()
    include_bom: bool = False
    include_handoff: bool = False
    expected_criteria_count: int = 3
    grounded_source: str = field(default="", repr=False)


@dataclass(frozen=True)
class JepComposition:
    status: str
    markdown: str = ""
    questions: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    brief: JepBrief | None = None


_QUESTIONS = {
    "customer": "Which customer and engagement is this JEP for?",
    "workload": "What customer workload is this JEP for?",
    "region": "Which OCI region is approved for the POC?",
    "duration": "What is the total POC duration?",
    "phases": "What are the windows for Phase 1 Assessment, Phase 2 Build, and Phase 3 Validate?",
    "scope": "Which workloads and OCI services are explicitly in scope, and what is out of scope?",
    "architecture": "What approved POC architecture or same-engagement diagram should the JEP reference?",
    "criteria": "Provide at least three measurable success criteria with numeric targets and evidence methods.",
    "owners": "Who are the Oracle and customer owners, and what time commitments have they approved?",
    "risks": "What risks should the joint team track, or may the JEP express failure risks directly from the success criteria?",
    "approvals": "Who approves the Phase 3 go/no-go decision, and what is the agreed fallback?",
    "bom_scope": "Should the JEP include a BOM section, and if so which approved BOM or exact scope should it reference?",
    "handoff": "Which handoff deliverables are required at the end of the POC?",
    "selected_poc": "Which grounded POC option has the customer selected?",
    "finalized_bom": "Finalize the same-engagement BOM/XLSX before generating this JEP.",
}


def compose_jep(task: str, engagement_context: dict[str, Any] | None = None) -> JepComposition:
    """Extract a grounded brief and deterministically compose a JEP."""
    context = engagement_context if isinstance(engagement_context, dict) else {}
    prior = str(context.get("prior_version") or "")
    if prior.strip() and _looks_like_revision(task, context):
        revised = _revise_prior_markdown(prior, task)
        if revised is None:
            return _needs_input(("revision",), (
                "State the exact grounded change to the approved JEP (for example, replace an existing value with a new value).",
            ))
        findings = validate_jep_markdown(
            revised,
            expected_criteria_count=_expected_success_criteria_count(context),
        )
        if findings:
            return _needs_input(("revision",), (
                "The requested revision would make the JEP incomplete: " + "; ".join(findings),
            ))
        return JepComposition(status="ok", markdown=revised)

    extracted, missing = extract_jep_brief(task, context)
    if missing or extracted is None:
        return _needs_input(tuple(missing))
    markdown = render_jep_markdown(extracted)
    findings = validate_jep_markdown(markdown, extracted)
    if findings:
        return _needs_input(("validation",), tuple(findings))
    return JepComposition(status="ok", markdown=markdown, brief=extracted)


def extract_jep_brief(
    task: str,
    engagement_context: dict[str, Any] | None = None,
) -> tuple[JepBrief | None, list[str]]:
    """Build a private validated brief from grounded request/context fields."""
    context = engagement_context if isinstance(engagement_context, dict) else {}
    current = _clean(task)
    context_text = _context_text(context)
    sources = _clean(f"{current}\n{context_text}")

    customer = _clean(str(context.get("customer_name") or "")) or _extract_customer(current)
    workload = _extract_workload(sources) or _selected_poc_workload(context)
    region_match = _REGION_RE.search(sources)
    region = region_match.group(0) if region_match else "[TBD]"
    duration_match = _DURATION_RE.search(sources)
    duration = duration_match.group(1) if duration_match else "[TBD]"

    phases_by_number: dict[int, JepPhase] = {}
    for match in _PHASE_RE.finditer(sources):
        number = int(match.group(1))
        phases_by_number[number] = JepPhase(number, match.group(2).title(), _normalize_window(match.group(3)))
    phases = tuple(
        phases_by_number.get(i) or JepPhase(i, name, "[TBD]")
        for i, name in ((1, "Assessment"), (2, "Build"), (3, "Validate"))
    )

    scope = _extract_scope(sources) or (("[TBD]",) if workload else ())
    poc_scope = _selected_poc_scope(context)
    if poc_scope:
        scope = poc_scope
    architecture = _extract_architecture(sources, context) or (
        f"POC architecture for {workload}; detailed OCI topology [TBD]" if workload else ""
    )
    criteria = _extract_criteria(sources)
    poc_criteria = _selected_poc_criteria(context)
    if poc_criteria:
        criteria = poc_criteria
    owners = _extract_owners(sources, customer) or (JepOwner("[TBD]", "[TBD]", "[TBD]"),)
    approvals = _extract_approvals(sources, owners) or "[TBD]"
    risks = _extract_risks(sources, criteria) or ("[TBD]", "[TBD]", "[TBD]")
    artifact_references = _artifact_references(context)
    artifact_context = (
        context.get("artifact_context")
        if isinstance(context.get("artifact_context"), dict)
        else {}
    )
    expected_criteria_count = _expected_success_criteria_count(context)
    include_bom = bool(re.search(
        r"(?:\b(?:include|add|with|containing)\b[^.]{0,60}\b(?:BOM|bill of materials)\b|"
        r"\b(?:BOM|bill of materials)\s+section\b)",
        current,
        re.IGNORECASE,
    ))
    include_handoff = bool(re.search(r"\bhandoff deliverables?\b", current, re.IGNORECASE))
    bom_scope = _extract_bom_scope({"artifact_context": artifact_context}) or (
        scope if include_bom and scope else (("[TBD]",) if include_bom else ())
    )
    handoff = _extract_handoff(sources, criteria) if include_handoff else ()
    if include_handoff and not handoff:
        handoff = ("[TBD]",)

    missing: list[str] = []
    for name, value in (("customer", customer), ("workload", workload)):
        if not value:
            missing.append(name)
    if missing:
        return None, list(dict.fromkeys(missing))
    if len(criteria) < expected_criteria_count:
        criteria = criteria + tuple(
            "[TBD]" for _ in range(expected_criteria_count - len(criteria))
        )

    brief = JepBrief(
        customer=customer,
        region=region,
        duration=duration,
        phases=phases,
        scope=scope,
        architecture=architecture,
        criteria=criteria,
        owners=owners,
        risks=risks,
        approvals=approvals,
        artifact_references=artifact_references,
        bom_scope=bom_scope,
        handoff_requirements=handoff,
        include_bom=include_bom,
        include_handoff=include_handoff,
        expected_criteria_count=expected_criteria_count,
        grounded_source=sources,
    )
    return brief, []


def render_jep_markdown(brief: JepBrief) -> str:
    """Render the canonical nine sections, with optional sections before Approvals."""
    scope_text = ", ".join(brief.scope)
    out_of_scope = _extract_out_of_scope(brief.grounded_source) or "[TBD]"
    if brief.include_bom:
        out_of_scope += " A separate BOM artifact is not created by this draft."
    refs = ""
    if brief.artifact_references:
        refs = " Same-engagement references: " + "; ".join(brief.artifact_references) + "."
    lines = [
        f"# Joint Execution Plan — {brief.customer}", "",
        "## Executive Summary", "",
        f"{brief.customer} and Oracle will execute a {brief.duration} POC in {brief.region} "
        "to validate the agreed scope and success criteria. Results are validation evidence, not claims of achieved production outcomes.", "",
        "## Objectives", "",
    ]
    lines.extend(f"- Validate: {criterion}" for criterion in brief.criteria)
    lines += ["", "## Scope", "", f"In scope: {scope_text}.",
              "", f"Out of scope: {out_of_scope}",
              "", "## POC Architecture", "", f"{brief.architecture}.{refs}".replace("..", "."),
              "", "## Phased Execution Plan", "",
              "| Phase | Window | Activities | Exit evidence |", "|---|---|---|---|"]
    phase_activity = {
        1: "Confirm the in-scope architecture, access, test method, owners, risks, and approvals.",
        2: "Configure the explicitly in-scope POC components and prepare the agreed tests.",
        3: "Run the agreed tests, record evidence, and conduct the joint go/no-go review.",
    }
    phase_exit = {
        1: "Approved scope, architecture, and test plan.",
        2: "Joint test-readiness record.",
        3: "Signed results and go/no-go record; apply the agreed fallback if criteria are not met.",
    }
    for phase in brief.phases:
        lines.append(f"| Phase {phase.number} {phase.name} | {phase.window} | {phase_activity[phase.number]} | {phase_exit[phase.number]} |")
    lines += ["", "## Success Criteria", "", "| Criterion | Evidence requirement |", "|---|---|"]
    for criterion in brief.criteria:
        lines.append(f"| {criterion} | Record the measured result for the stated target and test condition. |")
    lines += ["", "## Resource Plan", "", "| Organization | Owner | Commitment |", "|---|---|---|"]
    for owner in brief.owners:
        lines.append(f"| {owner.organization} | {owner.role} | {owner.commitment} |")
    lines += ["", "## Risk Registry", "", "| Risk | Mitigation | Owner |", "|---|---|---|"]
    owner_label = " / ".join(owner.role for owner in brief.owners)
    for risk in brief.risks:
        lines.append(f"| {risk} | Preserve evidence and apply the agreed fallback if the criterion is not met. | {owner_label} |")
    if brief.include_bom:
        lines += ["", "## Bill of Materials (BOM)", "", "The JEP references only this approved scope:", ""]
        lines.extend(f"- {item}" for item in brief.bom_scope)
        lines += ["", "This section does not create or authorize a separate BOM workbook."]
    if brief.include_handoff:
        lines += ["", "## Handoff Deliverables", ""]
        lines.extend(f"- {item}" for item in brief.handoff_requirements)
    lines += ["", "## Approvals", "", brief.approvals, ""]
    return "\n".join(lines)


def validate_jep_markdown(
    markdown: str,
    brief: JepBrief | None = None,
    *,
    expected_criteria_count: int | None = None,
) -> list[str]:
    """Validate canonical order, phase count, measurable criteria, and grounding."""
    text = str(markdown or "").strip()
    findings: list[str] = []
    headings = [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE)]
    core_positions = []
    for section in CORE_SECTIONS:
        if section not in headings:
            findings.append(f"missing required section: {section}")
        else:
            core_positions.append(headings.index(section))
    if core_positions and core_positions != sorted(core_positions):
        findings.append("required sections are out of canonical order")
    if headings and headings[-1] != "Approvals":
        findings.append("Approvals must be the final section")
    phase_section = _section(text, "Phased Execution Plan")
    phase_rows = re.findall(r"^\|\s*Phase\s+(\d+)\s+([^|]+?)\s*\|", phase_section, re.MULTILINE)
    if phase_rows != [("1", "Assessment"), ("2", "Build"), ("3", "Validate")]:
        findings.append("execution plan must contain exactly Phase 1 Assessment, Phase 2 Build, and Phase 3 Validate")
    criteria_section = _section(text, "Success Criteria")
    required_criteria = expected_criteria_count or (
        brief.expected_criteria_count if brief is not None else 3
    )
    grounded_or_tbd_criteria = len(_NUMBER_RE.findall(criteria_section)) + len(
        re.findall(r"^\|\s*\[TBD\]\s*\|", criteria_section, re.MULTILINE)
    )
    if grounded_or_tbd_criteria < required_criteria:
        findings.append(
            f"success criteria require at least {required_criteria} grounded numeric targets or explicit TBD placeholders"
        )
    risk_section = _section(text, "Risk Registry")
    if len([line for line in risk_section.splitlines() if line.startswith("|")]) < 5:
        findings.append("risk registry requires at least three entries")
    if brief is not None:
        grounded_numbers = {_norm_number(item) for item in _NUMBER_RE.findall(brief.grounded_source)}
        rendered_numbers = {_norm_number(item) for item in _NUMBER_RE.findall(text)}
        unsupported = sorted(rendered_numbers - grounded_numbers)
        if unsupported:
            findings.append("unsupported numeric facts: " + ", ".join(unsupported))
    return findings


def kickoff_questions(missing_fields: Iterable[str]) -> tuple[str, ...]:
    return tuple(_QUESTIONS.get(field, f"Please provide the grounded value for {field}.") for field in missing_fields)


def _needs_input(missing: tuple[str, ...], questions: tuple[str, ...] | None = None) -> JepComposition:
    return JepComposition(
        status="needs_input",
        questions=questions or kickoff_questions(missing),
        missing_fields=missing,
    )


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _context_text(context: dict[str, Any]) -> str:
    allowed = (
        "engagement_context_summary", "archie_memory_summary", "architect_brief",
        "resolved_decisions", "artifact_context", "facts", "client_facts",
    )
    values = [context.get(key) for key in allowed if context.get(key)]
    return " ".join(value if isinstance(value, str) else json.dumps(value, sort_keys=True) for value in values)


def _extract_customer(text: str) -> str:
    patterns = (
        r"\bfor\s+(?:Customer\s+)?([A-Z][A-Za-z0-9&.' -]+?)(?:'s|\s+to\s+validate|\s+for\s+the|\s+OCI|\.)",
        r"\bCustomer:\s*([A-Z][A-Za-z0-9&.' -]+?)(?:\.|,|\n|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _clean(match.group(1))
    return ""


def _extract_workload(text: str) -> str:
    labeled = re.search(
        r"\b(?:workload|current platform|platform|migration scope)\s*[:=]\s*"
        r"(.+?)(?:\.(?=\s|$)|;|\n|$)",
        text,
        re.IGNORECASE,
    )
    if labeled:
        return _clean(labeled.group(1)).strip('"')
    patterns = (
        r"\b(?:migrat(?:e|ing)|move|moving)\s+(.+?)(?:\s+to\s+OCI\b|\.(?=\s|$)|;|$)",
        r"\bmigration\s+of\s+(.+?)(?:\s+to\s+OCI\b|\.(?=\s|$)|;|$)",
        r"\b(?:currently\s+)?runs?\s+(.+?)(?:\.(?=\s|$)|;|$)",
        r"\b(?:hosts?|operates?)\s+(.+?)(?:\.(?=\s|$)|;|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean(match.group(1)).strip('"')
    return ""


def _extract_out_of_scope(text: str) -> str:
    match = re.search(
        r"\bOut of scope:\s*(.+?)(?:\.\s+(?:Use|Success criteria|Oracle|Include|Generate)|\n|$)",
        text,
        re.IGNORECASE,
    )
    return _clean(match.group(1)) if match else ""


def _normalize_window(value: str) -> str:
    return re.sub(r"\b(days?|weeks?)\s*", lambda m: m.group(1).title() + " ", _clean(value), flags=re.IGNORECASE).replace("to", "-")


def _extract_scope(text: str) -> tuple[str, ...]:
    match = re.search(r"\bScope:\s*(.+?)(?:\.\s+(?:Use|Success criteria|Oracle|Include|Generate)|\n|$)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\bIn scope:\s*(.+?)(?:\.\s+Out of scope|\n|$)", text, re.IGNORECASE)
    if not match:
        return ()
    return _split_items(match.group(1))


def _selected_poc_scope(context: dict[str, Any]) -> tuple[str, ...]:
    artifact = context.get("artifact_context")
    if not isinstance(artifact, dict):
        return ()
    poc = artifact.get("poc")
    if not isinstance(poc, dict):
        return ()
    return tuple(
        str(item).strip()
        for item in poc.get("oci_services", []) or []
        if str(item).strip()
    )


def _selected_poc_workload(context: dict[str, Any]) -> str:
    artifact = context.get("artifact_context")
    poc = artifact.get("poc") if isinstance(artifact, dict) else None
    if not isinstance(poc, dict):
        return ""
    return _clean(str(poc.get("selected_option_name") or ""))


def _selected_poc_criteria(context: dict[str, Any]) -> tuple[str, ...]:
    artifact = context.get("artifact_context")
    poc = artifact.get("poc") if isinstance(artifact, dict) else None
    if not isinstance(poc, dict) or not poc.get("selected_option_name"):
        return ()
    grounding = poc.get("grounding") if isinstance(poc.get("grounding"), dict) else {}
    raw = grounding.get("success_criteria") or poc.get("success_criteria")
    if isinstance(raw, (list, tuple)):
        return tuple(_clean(str(item)) for item in raw if _NUMBER_RE.search(str(item)))
    return _extract_criteria(f"Success criteria: {raw}") if raw else ()


def _expected_success_criteria_count(context: dict[str, Any]) -> int:
    """Keep the general three-criterion bar unless a selected POC is authoritative.

    A selected POC can intentionally carry one or two criteria.  In that case the
    JEP must preserve that exact count instead of inventing another target.
    """
    artifact = context.get("artifact_context")
    if not isinstance(artifact, dict):
        return 3
    poc = artifact.get("poc")
    if not isinstance(poc, dict) or not poc.get("selected_option_name"):
        return 3
    grounding = poc.get("grounding") if isinstance(poc.get("grounding"), dict) else {}
    raw = grounding.get("success_criteria") or poc.get("success_criteria")
    if isinstance(raw, (list, tuple)):
        count = len([item for item in raw if _NUMBER_RE.search(str(item))])
    else:
        count = len(_extract_criteria(f"Success criteria: {raw}")) if raw else 0
    return count if 0 < count < 3 else 3


def _extract_architecture(text: str, context: dict[str, Any]) -> str:
    artifact = context.get("artifact_context")
    if isinstance(artifact, dict):
        diagram = artifact.get("diagram")
        if isinstance(diagram, dict):
            summary = _clean(str(diagram.get("deployment_summary") or ""))
            if summary:
                return summary
            key = _clean(str(diagram.get("diagram_key") or diagram.get("artifact_ref") or ""))
            if key:
                return f"The POC architecture is the finalized same-engagement Diagram: {key}"
    match = re.search(r"\b(?:validate\s+)?migration of\s+(.+?)\s+to\s+OCI\s+([a-z]{2,}-[a-z]+-\d+)", text, re.IGNORECASE)
    if match:
        return f"The POC architecture covers {match.group(1)} in OCI {match.group(2)}"
    match = re.search(r"\bArchitecture:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    return _clean(match.group(1)) if match else ""


def _extract_criteria(text: str) -> tuple[str, ...]:
    match = re.search(r"\bSuccess criteria:\s*(.+?)(?:\.\s+(?:Oracle|Include|Generate)|\n|$)", text, re.IGNORECASE)
    if not match:
        return ()
    raw = match.group(1).strip().rstrip(".")
    parts = re.split(
        r",\s+(?=(?:p\d+|and\s+database|\d+(?:\.\d+)?%|restore|recover|process|"
        r"sustain|keep|finish|latency|availability|response))|;",
        raw,
        flags=re.IGNORECASE,
    )
    return tuple(_clean(re.sub(r"^and\s+", "", part, flags=re.IGNORECASE)).rstrip(".") for part in parts if _NUMBER_RE.search(part))


def _extract_owners(text: str, customer: str) -> tuple[JepOwner, ...]:
    multi = re.search(
        r"(Oracle\s+(?:SA|Solutions Architect)\s*,\s*[^.;]{2,100}?(?:\s*,\s*|\s+and\s+)[^.;]{2,100}?)\s+"
        r"each\s+(?:own\s+delivery\s+and\s+)?(?:commit|contribute|provide)\s+"
        r"(\d+\s*(?:hours?|hrs?)\s*(?:/|per)\s*week)",
        text,
        re.IGNORECASE,
    )
    if multi:
        roles = [
            _clean(role)
            for role in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", multi.group(1))
            if _clean(role)
        ]
        commitment = _clean(multi.group(2))
        return tuple(
            JepOwner(
                "Oracle" if re.match(r"Oracle\s+(?:SA|Solutions Architect)$", role, re.IGNORECASE) else customer,
                role,
                commitment,
            )
            for role in roles
        )
    match = re.search(
        r"(Oracle\s+(?:SA|Solutions Architect))\s+and\s+([^.;]{2,100}?)\s+each\s+"
        r"(?:own\s+delivery\s+and\s+)?(?:commit|contribute|provide)\s+"
        r"(\d+\s*(?:hours?|hrs?)\s*(?:/|per)\s*week)",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"(Oracle\s+(?:SA|Solutions Architect))\s+and\s+([^.;]{2,100}?)\s+"
            r"(?:commit|contribute|provide)\s+(\d+\s*(?:hours?|hrs?))\s+each\s+per\s+week",
            text,
            re.IGNORECASE,
        )
    if not match:
        return ()
    customer_role = _clean(match.group(2))
    commitment = _clean(match.group(3))
    customer_org = customer if customer and customer.lower() in customer_role.lower() else customer_role
    return (
        JepOwner("Oracle", _clean(match.group(1)), commitment),
        JepOwner(customer_org, customer_role, commitment),
    )


def _extract_approvals(text: str, owners: tuple[JepOwner, ...]) -> str:
    has_decision = re.search(r"go/no-go", text, re.IGNORECASE)
    has_fallback = re.search(r"\bfallback\b|\bif\s+no-go\b", text, re.IGNORECASE)
    if not owners or not has_decision or not has_fallback:
        return ""
    labels = " and ".join(owner.role for owner in owners)
    return f"{labels} approve the Phase 3 evidence and sign the go/no-go record. Go requires every stated success criterion to be met; no-go applies the agreed fallback without claiming success."


def _extract_risks(text: str, criteria: tuple[str, ...]) -> tuple[str, ...]:
    explicit = re.search(r"\bRisks?:\s*(.+?)(?:\.\s+(?:Approvals?|Include|Generate)|\n|$)", text, re.IGNORECASE)
    if explicit:
        risks = _split_items(explicit.group(1))
        if len(risks) >= 3:
            return risks
    if len(criteria) >= 2 and re.search(r"include at least three risks", text, re.IGNORECASE):
        risks = [f"The measured result may not meet: {criterion}" for criterion in criteria]
        risks.append(
            "The joint team may not capture repeatable evidence for the stated success criteria within the POC window"
        )
        return tuple(risks[:3])
    return ()


def _artifact_references(context: dict[str, Any]) -> tuple[str, ...]:
    artifact = context.get("artifact_context")
    if not isinstance(artifact, dict):
        return ()
    refs: list[str] = []
    poc = artifact.get("poc")
    if isinstance(poc, dict):
        name = str(poc.get("selected_option_name") or "").strip()
        key = str(poc.get("artifact_key") or "").strip()
        if name:
            refs.append(f"Selected POC: {name}" + (f" ({key})" if key else ""))
    diagram = artifact.get("diagram")
    if isinstance(diagram, dict):
        key = diagram.get("diagram_key") or diagram.get("artifact_ref")
        if key:
            refs.append(f"Diagram: {key}")
    bom = artifact.get("bom")
    if isinstance(bom, dict):
        key = bom.get("xlsx_artifact_key") or bom.get("payload_ref")
        if key:
            refs.append(f"BOM: {key}")
    return tuple(refs)


def _extract_bom_scope(context: dict[str, Any]) -> tuple[str, ...]:
    artifact = context.get("artifact_context")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("bom"), dict):
        return ()
    bom = artifact["bom"]
    facts = []
    for key in ("summary", "estimated_monthly_cost", "line_item_count", "xlsx_artifact_key", "payload_ref"):
        if bom.get(key) not in (None, ""):
            facts.append(f"{key.replace('_', ' ').title()}: {bom[key]}")
    return tuple(facts)


def _extract_handoff(text: str, criteria: tuple[str, ...]) -> tuple[str, ...]:
    if not re.search(r"handoff deliverables?", text, re.IGNORECASE):
        return ()
    items = ["Approved scope and POC architecture", "Signed go/no-go decision and fallback record"]
    items.extend(f"Evidence for: {criterion}" for criterion in criteria)
    return tuple(items)


def _split_items(value: str) -> tuple[str, ...]:
    cleaned = re.sub(r",\s+and\s+", ", ", _clean(value), flags=re.IGNORECASE)
    return tuple(part.strip(" .") for part in cleaned.split(",") if part.strip(" ."))


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)", markdown, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _norm_number(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _looks_like_revision(task: str, context: dict[str, Any]) -> bool:
    return bool(context.get("feedback") or re.search(r"\b(?:revise|update|change|replace|remove|add)\b", task, re.IGNORECASE))


def _revise_prior_markdown(prior: str, task: str) -> str | None:
    """Apply only explicit replacements/removals to a validated prior JEP."""
    request = _clean(task)
    replacements = re.findall(r"\breplace\s+[\"']?(.+?)[\"']?\s+with\s+[\"']?(.+?)[\"']?(?:\.|$)", request, re.IGNORECASE)
    changes = re.findall(r"\bchange\s+[\"']?(.+?)[\"']?\s+to\s+[\"']?(.+?)[\"']?(?:\.|$)", request, re.IGNORECASE)
    from_changes = re.findall(
        r"\b(?:change|update)\s+.+?\s+from\s+[\"']?(.+?)[\"']?\s+to\s+[\"']?(.+?)[\"']?(?:\.|$)",
        request,
        re.IGNORECASE,
    )
    result = prior
    applied = False
    for old, new in (*replacements, *from_changes, *changes):
        old, new = old.strip(" '\""), new.strip(" '\"")
        if old and old in result:
            result = result.replace(old, new)
            applied = True
    return result if applied else None
