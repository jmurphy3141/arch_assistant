"""Grounded POC option composition with optional presentation-only polishing."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any


_SERVICE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("OCI WAF", ("oci waf", "web application firewall", " waf")),
    ("Flexible Load Balancer", ("flexible load balancer", "public load balancer", "private load balancer")),
    ("VM.Standard.E5.Flex", ("vm.standard.e5.flex", "e5.flex")),
    ("VM.Standard.E6.Flex", ("vm.standard.e6.flex", "e6.flex", "e6 flex")),
    ("Container Engine for Kubernetes (OKE)", ("container engine for kubernetes", " oke")),
    ("PostgreSQL DB System", ("postgresql db system", "private postgresql", "postgresql")),
    ("Oracle Base Database Service", ("base database service", "oracle database 19c")),
    ("Object Storage", ("object storage",)),
    ("Block Volume", ("block volume", "block storage")),
    ("File Storage", ("file storage",)),
    ("Site-to-Site VPN", ("site-to-site vpn", "site to site vpn")),
    ("FastConnect", ("fastconnect",)),
    ("OCI IAM", ("oci iam", " iam")),
    ("Audit Logging", ("audit logging", "audit logs")),
    ("Logging", (" logging",)),
    ("Monitoring", (" monitoring",)),
)

_NUMBER_RE = re.compile(
    r"(?:[<>]=?\s*)?\$?\d+(?:[.,]\d+)?\s*(?:%|ms|milliseconds?|seconds?|minutes?|hours?|"
    r"days?|weeks?|months?|requests?(?:\s+per\s+second)?|rps|qps|tps|ocpus?|gb|tb)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PocBrief:
    customer_name: str
    workload: str
    pain: str
    region: str
    allowed_services: tuple[str, ...]
    duration_days: int
    owner_weekly_hours: int
    owner_count: int
    success_criteria: tuple[str, ...]
    exclusions: tuple[str, ...]
    grounded_source: str

    @property
    def delivery_capacity_hours(self) -> int:
        weeks = max(1, math.ceil(self.duration_days / 7))
        return weeks * self.owner_weekly_hours * self.owner_count


def build_poc_brief(
    *,
    customer_name: str,
    user_message: str,
    decision_context: dict[str, Any] | None = None,
    engagement_context: dict[str, Any] | None = None,
) -> PocBrief | None:
    brief, _missing = assess_poc_brief(
        customer_name=customer_name,
        user_message=user_message,
        decision_context=decision_context,
        engagement_context=engagement_context,
    )
    return brief


def assess_poc_brief(
    *,
    customer_name: str,
    user_message: str,
    decision_context: dict[str, Any] | None = None,
    engagement_context: dict[str, Any] | None = None,
) -> tuple[PocBrief | None, tuple[str, ...]]:
    source = _targeted_poc_source(
        user_message=user_message,
        decision_context=decision_context or {},
        engagement_context=engagement_context or {},
    )
    lower = f" {source.lower()} "
    exclusions_match = re.search(r"\b(?:exclude|excluded|out of scope)[: ]+(.+?)(?:\.|$)", source, re.IGNORECASE)
    exclusions = _split_grounded_list(exclusions_match.group(1)) if exclusions_match else ()
    exclusion_text = " ".join(exclusions).casefold()
    services = tuple(
        canonical
        for canonical, aliases in _SERVICE_ALIASES
        if any(alias in lower for alias in aliases)
        and not any(alias.strip() and alias.strip() in exclusion_text for alias in aliases)
        and not any(
            re.search(rf"\b(?:not|no)\s+{re.escape(alias.strip())}\b", lower)
            for alias in aliases
            if alias.strip()
        )
    )
    region_match = re.search(r"\b[a-z]{2,}-[a-z]+-\d+\b", source, re.IGNORECASE)
    duration_match = re.search(
        r"\b(\d+)\s*(?:-\s*|calendar\s+)?days?\b",
        source,
        re.IGNORECASE,
    )
    commitment_match = re.search(
        r"\b(\d+)\s*(?:hours?|hrs?)\s*(?:each\s+)?(?:/|per)\s*week\b",
        source,
        re.IGNORECASE,
    )
    owners_text = _extract_owners_text(source)
    owner_count = _explicit_owner_count(owners_text) if owners_text else 0
    criteria = _extract_success_criteria(source)
    workload = _extract_workload(source)
    pain = _extract_grounded_motivation(source, criteria)
    missing: list[str] = []
    for name, present in (
        ("customer", bool(customer_name.strip())),
        ("workload", bool(workload)),
        ("pain", bool(pain)),
        ("region", bool(region_match)),
        ("duration", bool(duration_match)),
        ("owners", bool(commitment_match and owner_count >= 2)),
        ("services", bool(services)),
        ("criteria", bool(criteria)),
    ):
        if not present:
            missing.append(name)
    if missing:
        return None, tuple(missing)
    return PocBrief(
        customer_name=customer_name.strip(),
        workload=workload,
        pain=pain,
        region=region_match.group(0),
        allowed_services=services,
        duration_days=int(duration_match.group(1)),
        owner_weekly_hours=int(commitment_match.group(1)),
        owner_count=owner_count,
        success_criteria=criteria,
        exclusions=exclusions,
        grounded_source=source,
    ), ()


_POC_QUESTIONS = {
    "customer": "Which customer is this POC for?",
    "workload": "What workload and current platform are we validating?",
    "pain": "What customer pain or business risk must this POC address?",
    "region": "Which OCI region is approved?",
    "duration": "How many calendar days are available for the POC?",
    "owners": "Who are the Oracle and customer owners, and how many hours per week can each commit?",
    "services": "Which OCI services are explicitly in scope?",
    "criteria": "What measurable success criterion, with a numeric target, will determine success?",
}


def poc_brief_questions(missing_fields: tuple[str, ...]) -> tuple[str, ...]:
    """Return one bounded discovery batch; never repeat more than three gaps."""
    return tuple(_POC_QUESTIONS[field] for field in missing_fields[:3] if field in _POC_QUESTIONS)


def _targeted_poc_source(
    *,
    user_message: str,
    decision_context: dict[str, Any],
    engagement_context: dict[str, Any],
) -> str:
    """Assemble targeted facts in precedence order without raw conversation history."""
    archie = engagement_context.get("archie") if isinstance(engagement_context.get("archie"), dict) else {}
    resolved = archie.get("resolved_decisions") if isinstance(archie.get("resolved_decisions"), dict) else {}
    resolved = {key: value for key, value in resolved.items() if key != "poc"}
    approved = archie.get("latest_approved_constraints") if isinstance(archie.get("latest_approved_constraints"), dict) else {}
    resolved_questions = archie.get("resolved_questions") if isinstance(archie.get("resolved_questions"), list) else []
    assumptions = archie.get("latest_approved_assumptions") if isinstance(archie.get("latest_approved_assumptions"), list) else []
    summaries = [
        archie.get("engagement_summary"),
        archie.get("latest_notes_summary"),
        engagement_context.get("engagement_context_summary"),
    ]
    # Current-turn facts come first so regex extraction prefers explicit corrections.
    parts: list[Any] = [
        user_message,
        decision_context,
        *summaries,
        resolved,
        resolved_questions,
        approved,
        engagement_context.get("client_facts"),
        assumptions,
    ]
    rendered = [
        value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        for value in parts
        if value not in (None, "", [], {})
    ]
    return re.sub(r"\s+", " ", " ".join(rendered)).strip()


def compose_grounded_options(brief: PocBrief) -> list[dict[str, Any]]:
    criterion = "; ".join(brief.success_criteria)
    capacity = brief.delivery_capacity_hours
    service_text = ", ".join(brief.allowed_services)
    patterns = (
        (
            f"{brief.customer_name} Core Workload Validation POC",
            10,
            "Validate the complete confirmed workload path against the stated success criteria.",
        ),
        (
            f"{brief.customer_name} Performance and Recovery Validation POC",
            9,
            "Measure the confirmed performance, availability, and recovery targets without claiming success in advance.",
        ),
        (
            f"{brief.customer_name} Operational Readiness Validation POC",
            8,
            "Validate repeatable operation and evidence handoff for the same approved scope.",
        ),
    )
    return [
        {
            "option_name": name,
            "relevance_score": relevance,
            "executability_hours": capacity,
            "delivery_capacity_basis": (
                f"{brief.owner_count} owners x {brief.owner_weekly_hours} hours per week x "
                f"{max(1, math.ceil(brief.duration_days / 7))} calendar weeks"
            ),
            "cost_effectiveness": "No price or savings is asserted; validate against the confirmed customer constraint.",
            "security_highlights": _grounded_security_highlights(brief.allowed_services),
            "wow_moment": criterion,
            "demo_script_summary": summary,
            "oci_services": list(brief.allowed_services),
            "build_sequence": [
                "Confirm the selected scope and evidence method",
                f"Configure only the approved {service_text} scope in {brief.region}",
                "Run the stated measurements",
                "Record the joint go/no-go evidence",
            ],
            "top_risks": [
                "The test conditions may not represent the confirmed workload.",
                "A stated success criterion may not be met within the confirmed POC window.",
            ],
            "grounding": {
                "region": brief.region,
                "duration_days": brief.duration_days,
                "success_criteria": list(brief.success_criteria),
                "exclusions": list(brief.exclusions),
            },
        }
        for name, relevance, summary in patterns
    ]


def apply_presentation_polish(
    base_options: list[dict[str, Any]],
    polished: Any,
    *,
    brief: PocBrief,
) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(polished, dict) or not isinstance(polished.get("options"), list):
        return base_options, "fallback_invalid_shape"
    candidates = polished["options"]
    if len(candidates) != len(base_options):
        return base_options, "fallback_wrong_option_count"
    grounded_numbers = {_normalize_number(value) for value in _NUMBER_RE.findall(brief.grounded_source)}
    output: list[dict[str, Any]] = []
    for base, candidate in zip(base_options, candidates):
        if not isinstance(candidate, dict):
            return base_options, "fallback_invalid_option"
        title = str(candidate.get("option_name") or "").strip()
        summary = str(candidate.get("demo_script_summary") or "").strip()
        if not title or not summary:
            return base_options, "fallback_missing_presentation"
        rendered = f"{title} {summary}"
        candidate_numbers = {_normalize_number(value) for value in _NUMBER_RE.findall(rendered)}
        if candidate_numbers - grounded_numbers:
            return base_options, "fallback_unsupported_number"
        lowered = f" {rendered.lower()} "
        allowed_lower = {service.casefold() for service in brief.allowed_services}
        for service, aliases in _SERVICE_ALIASES:
            if any(alias in lowered for alias in aliases) and service.casefold() not in allowed_lower:
                return base_options, "fallback_unsupported_service"
        updated = dict(base)
        updated["option_name"] = title
        updated["demo_script_summary"] = summary
        output.append(updated)
    return output, "polished"


def _extract_success_criteria(source: str) -> tuple[str, ...]:
    match = re.search(
        r"\b(?:desired outcomes?|success criteria|measurable (?:success )?(?:criteria|targets)|"
        r"performance targets?|acceptance criteria|targets include)[: ]+"
        r"(.+?)(?:\.\s+(?:POC|The target|Scope|Constraints|Owners?|Delivery owners?|"
        r"Oracle|Risks?|Goals?|Excluded|Out of scope|Region|In scope|Workload)|$)",
        source,
        re.IGNORECASE,
    )
    if not match:
        return ()
    raw = match.group(1).strip().rstrip(".")
    parts = re.split(
        r"(?:,\s+|\s+and\s+)(?=(?:and\s+)?(?:p\d+|\d|database|restore|recover|process|sustain|"
        r"finish|keep|availability|throughput|response))|;",
        raw,
        flags=re.IGNORECASE,
    )
    return tuple(re.sub(r"^and\s+", "", part.strip(), flags=re.IGNORECASE) for part in parts if re.search(r"\d", part))


def _extract_workload(source: str) -> str:
    labeled = _extract_labeled_value(
        source,
        ("workload", "current platform", "platform", "migration scope"),
    )
    if labeled:
        return labeled
    patterns = (
        r"\b(?:migrat(?:e|ing)|move|moving)\s+(.+?)(?:\s+to\s+OCI\b|\.\s|;\s|$)",
        r"\bmigration\s+of\s+(.+?)(?:\s+to\s+OCI\b|\.\s|;\s|$)",
        r"\b(?:currently\s+)?runs?\s+(.+?)(?:\.\s|;\s|$)",
        r"\b(?:hosts?|operates?)\s+(.+?)(?:\.\s|;\s|$)",
        r"\b(?:is|are)\s+(?:an?\s+)?(?:on-premises|on-prem|hosted|legacy)\s+(.+?)"
        r"(?:\.\s|;\s|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, source, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_grounded_motivation(source: str, criteria: tuple[str, ...]) -> str:
    labeled = _extract_labeled_value(
        source,
        ("pains", "pain", "risk", "risks", "goal", "goals", "objective", "objectives"),
    )
    if labeled:
        return labeled
    match = re.search(
        r"\b(?:pain is|pains are|goal is|goals are|must prove|needs? to prove|"
        r"needs? to reduce|wants? to reduce|at risk from)\s+(.+?)(?:\.\s|;\s|$)",
        source,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    # A stated measurable outcome is grounded motivation even when the fact
    # sheet does not use a Pain/Goal label. Preserve it verbatim.
    return "; ".join(criteria)


def _extract_owners_text(source: str) -> str:
    patterns = (
        r"(?:^|[.!?]\s+)([^.!?]{3,180}?)\s+each\s+(?:own\s+delivery\s+and\s+)?"
        r"(?:will\s+)?(?:commit|contribute|provide)\s+\d+\s*(?:hours?|hrs?)\s*(?:/|per)\s*week\b",
        r"(?:^|[.!?]\s+)([^.!?]{3,180}?)\s+(?:own\s+delivery\s+and\s+)?"
        r"(?:will\s+)?(?:commit|contribute|provide)\s+\d+\s*(?:hours?|hrs?)\s+each\s+(?:/|per)\s*week\b",
        r"\b(?:owners?|delivery owners?)[: ]+([^.;]{3,180}?)(?:;|\.)\s*"
        r"(?:commitment[: ]*)?\d+\s*(?:hours?|hrs?)\s*(?:/|per)\s*week\b",
    )
    for pattern in patterns:
        match = re.search(pattern, source, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_labeled_value(source: str, labels: tuple[str, ...]) -> str:
    label_expr = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"\b(?:{label_expr})[: ]+(.+?)(?:\.\s|;\s|$)", source, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _split_grounded_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r",|\band\b", value) if part.strip())


def _explicit_owner_count(value: str) -> int:
    value = re.sub(r"^(?:owners?|delivery owners?)[: ]+", "", value.strip(), flags=re.IGNORECASE)
    owners = [
        part.strip()
        for part in re.split(r",\s*(?:and\s+)?|\s+and\s+", value)
        if part.strip()
    ]
    return len(owners)


def _grounded_security_highlights(services: tuple[str, ...]) -> list[str]:
    highlights = []
    for service in services:
        if service in {"Site-to-Site VPN", "OCI IAM", "Audit Logging", "Logging", "OCI WAF"}:
            highlights.append(f"Use the confirmed {service} control within the POC boundary.")
    return highlights or ["Preserve the confirmed private-access and evidence constraints."]


def _normalize_number(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
