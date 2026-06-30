"""Grounded POV brief extraction, constrained rendering, and safe fallback."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Callable

from agent import consistency_contract
from agent.grounded_prose import (
    factual_token_findings,
    has_material_prose_revision,
    has_readable_prose,
    internal_artifact_references,
    missing_exact_facts,
    unsupported_oci_services,
)


@dataclass(frozen=True)
class PovBrief:
    customer: str
    engagement_facts: tuple[str, ...]
    services: tuple[str, ...]
    proposed_targets: tuple[str, ...]
    alternative: str = ""


@dataclass(frozen=True)
class PovRenderResult:
    markdown: str
    generation_mode: str
    attempts: int
    findings: tuple[str, ...] = ()


_POV_RENDER_SYSTEM = """You render an internal Oracle OCI Point of View from a locked brief.
Write polished, professional prose from ONLY the facts in the brief. You may rephrase,
reorder, and add connective prose. You may NOT introduce any number, percentage, service,
shape, date, SLA, owner, customer evidence, benchmark, savings claim, or factual claim that
is not present in the brief. Treat every supplied outcome as proposed unless the brief says
it was measured. Do not include internal object-storage keys or file paths.

Return only Markdown with these headings: "## 1. Internal Press Release",
"## 2. External Customer FAQ", "## 3. Internal Oracle Questions", and
"### Recommended Next Steps". Proposed quotes must be visibly labeled as requiring approval.
Use substantive prose paragraphs, preserve every proposed target exactly, and name only the
services in the brief."""


def extract_pov_brief(task: str, context: dict[str, Any] | None = None) -> PovBrief:
    context = context if isinstance(context, dict) else {}
    architect = context.get("architect_brief") if isinstance(context.get("architect_brief"), dict) else {}
    facts: list[str] = []
    task_text = str(task or "")
    has_locked_task = bool(
        re.search(r"\bIn-scope services:\s*.+", task_text, re.IGNORECASE)
        and re.search(r"\bproposed (?:validation )?targets(?: exactly)?\s*[:,]", task_text, re.IGNORECASE)
    )
    source_values = (task_text,) if has_locked_task else (
        task_text,
        architect.get("user_notes"),
        architect.get("architect_context"),
    )
    for value in source_values:
        cleaned = _clean(_remove_internal_paths(str(value or "")))
        if cleaned and cleaned not in facts:
            facts.append(cleaned)
    for item in architect.get("success_criteria", []) or []:
        cleaned = _clean(_remove_internal_paths(str(item or "")))
        if cleaned and cleaned not in facts:
            facts.append(cleaned)

    source = " ".join(facts)
    customer = _clean(str(context.get("customer_name") or "")) or _extract_customer(source)
    services: list[str] = []
    explicit_services = re.search(r"\bIn-scope services:\s*(.+?)(?:\.\s|$)", str(task or ""), re.IGNORECASE)
    service_source = explicit_services.group(1) if explicit_services else source
    source_lower = service_source.lower()
    for service_id, aliases in consistency_contract._SERVICE_ALIASES:
        display = consistency_contract.display_name(service_id)
        shorthand = {
            "security.waf": ("waf",),
            "security.iam": ("iam",),
            "container.oke": ("oke",),
        }.get(service_id, ())
        markers = aliases + (display.lower(),) + shorthand
        if any(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", source_lower) for alias in markers):
            if display not in services:
                services.append(display)
    targets = tuple(dict.fromkeys(_extract_explicit_target_list(str(task or "")) or _extract_targets(source)))
    alternative_match = re.search(
        r"\b(?:alternative|compared with|compared to|versus|vs\.?|rather than)\s+(?:is\s+)?([^.;]+)",
        source,
        re.IGNORECASE,
    )
    alternative = _clean(alternative_match.group(1)) if alternative_match else ""
    return PovBrief(customer, tuple(facts), tuple(services), targets, alternative)


def render_pov_with_inference(
    brief: PovBrief,
    runner: Callable[[str, str], str],
    *,
    deterministic_fallback: str | None = None,
) -> PovRenderResult:
    payload = asdict(brief)
    fallback = deterministic_fallback or render_pov_markdown(brief)
    payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
    allowed_numbers = sorted(set(re.findall(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?%?", payload_json)))
    prompt = "Validated POV brief (the complete fact set):\n```json\n" + payload_json + "\n```"
    prompt += (
        "\n\nAllowed factual numeric tokens (no others): " + ", ".join(allowed_numbers) +
        "\nAllowed OCI services (no others): " + ", ".join(brief.services) +
        "\nDo not add a customary future-state timeframe, soak period, milestone, benchmark, "
        "availability value, or example. The brief overrides generic POV conventions."
        "\n\nSafe deterministic draft to polish without changing its facts or headings:\n```markdown\n"
        + fallback + "\n```\nRework the connective prose materially: add or rewrite at least two substantive "
        "narrative paragraphs. Do not return the deterministic draft unchanged."
    )
    latest_findings: list[str] = []
    for attempt in (1, 2):
        attempt_prompt = prompt
        if latest_findings:
            attempt_prompt += (
                "\n\nThe prior rendering was rejected. Regenerate only from the brief and fix:\n- "
                + "\n- ".join(latest_findings)
            )
        try:
            markdown = _strip_markdown_fence(str(runner(attempt_prompt, _POV_RENDER_SYSTEM) or ""))
            latest_findings = pov_grounding_findings(markdown, brief)
            if not latest_findings and not has_material_prose_revision(markdown, fallback):
                latest_findings = ["prose repeats the deterministic template without material improvement"]
        except Exception as exc:
            markdown = ""
            latest_findings = [f"renderer error: {type(exc).__name__}: {exc}"]
        if not latest_findings:
            return PovRenderResult(markdown, "generative_grounded_prose", attempt)

    fallback_findings = pov_grounding_findings(fallback, brief)
    if fallback_findings:
        raise ValueError("deterministic POV fallback failed grounding: " + "; ".join(fallback_findings))
    return PovRenderResult(fallback, "deterministic_grounded_fallback", 2, tuple(latest_findings))


def render_pov_markdown(brief: PovBrief) -> str:
    """Wooden but fact-locked fallback used only when constrained prose fails."""
    customer = brief.customer or "The customer"
    services = ", ".join(brief.services) or "the services explicitly stated in the validated brief"
    targets = "; ".join(brief.proposed_targets) or "the outcomes stated in the validated brief"
    alternative = brief.alternative or "the current-state alternative stated in the validated brief"
    return f"""# {customer} OCI Point of View

## 1. Internal Press Release

{customer} is evaluating an OCI direction grounded in the validated engagement facts. The stated direction covers {services}, measured against {alternative}. The joint team will use the validated brief as the boundary for the evaluation.

The proposed outcomes to validate are {targets}. They are targets for joint validation, not achieved results, guarantees, benchmarks, or SLA commitments.

> **Proposed Oracle executive quote — requires approval:** “The joint team will evaluate the stated direction against the agreed targets.”

> **Proposed customer technology leader quote — requires approval:** “The decision will be based on evidence gathered against the agreed targets.”

> **Proposed customer business leader quote — requires approval:** “The team will proceed only when the validated evidence supports the stated direction.”

## 2. External Customer FAQ

### What is being evaluated?
{customer} is evaluating the direction and scope stated in the validated brief.

### Which OCI services are in scope?
The stated services are {services}.

### What outcomes will be tested?
The proposed targets are {targets}.

### What is the comparison point?
The stated alternative is {alternative}.

### What is not claimed?
This POV does not claim achieved outcomes, guaranteed savings, benchmark results, customer testimonials, or SLA commitments.

## 3. Internal Oracle Questions

- What evidence method will the joint team use for each stated target?
- Which dependencies in the validated brief must be confirmed before testing?
- Which decision makers must approve the validation plan?
- What risks could prevent the stated direction from meeting the targets?
- What evidence will support a proceed or stop decision?

### Recommended Next Steps

Confirm the evidence method, owners, dependencies, and decision process for the stated targets before execution.
"""


def pov_grounding_findings(markdown: str, brief: PovBrief) -> list[str]:
    payload_text = json.dumps(asdict(brief), ensure_ascii=True, sort_keys=True)
    findings = factual_token_findings(markdown, payload_text, allowed_format_numbers=("1", "2", "3"))
    if "```" in markdown:
        findings.append("Markdown fence leaked into POV output")
    unexpected_services = unsupported_oci_services(markdown, " ".join(brief.services))
    if unexpected_services:
        findings.append("unsupported OCI services: " + ", ".join(unexpected_services))
    required = [brief.customer] if brief.customer else []
    required.extend(brief.services)
    required.extend(brief.proposed_targets)
    missing = missing_exact_facts(markdown, required)
    if missing:
        findings.append("missing required POV facts: " + "; ".join(missing))
    headings = (
        "## 1. Internal Press Release",
        "## 2. External Customer FAQ",
        "## 3. Internal Oracle Questions",
        "### Recommended Next Steps",
    )
    absent_headings = [heading for heading in headings if heading.lower() not in markdown.lower()]
    if absent_headings:
        findings.append("missing POV headings: " + ", ".join(absent_headings))
    for heading in headings[:3]:
        count = len(re.findall(rf"^{re.escape(heading)}\s*$", markdown, re.MULTILINE | re.IGNORECASE))
        if count != 1:
            findings.append(f"POV heading must appear exactly once: {heading} (found {count})")
    if not has_readable_prose(markdown):
        findings.append("POV lacks substantive prose paragraphs")
    return list(dict.fromkeys(findings))


def _extract_targets(text: str) -> list[str]:
    patterns = (
        r"\b\d+(?:\.\d+)?%(?!\w)\s+within\s+\d+(?:\.\d+)?\s+(?:days?|weeks?|months?|years?)\b",
        r"\b(?:under|below|within|at least|no more than|up to)\s+\d+(?:\.\d+)?\s*(?:ms|milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|%|rps|requests? per second)\b",
        r"\b\d+(?:\.\d+)?%(?!\w)",
        r"\b\d+(?:\.\d+)?\s*(?:OCPUs?|GB|TB)\b",
    )
    matches: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            matches.append((match.start(), _clean(match.group(0))))
    matches.sort(key=lambda item: item[0])
    result: list[str] = []
    for _, value in matches:
        if any(value.lower() in existing.lower() or existing.lower() in value.lower() for existing in result):
            continue
        result.append(value)
    return result


def _extract_explicit_target_list(text: str) -> list[str]:
    match = re.search(
        r"(?:proposed validation targets(?: exactly)?|proposed targets(?: exactly)?)(?:,\s*not achieved outcomes)?\s*:\s*"
        r"(.+?)(?:\.\s+(?:In-scope services|Treat every|No customer)|$)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return []
    raw = match.group(1).strip().rstrip(".")
    return [
        _clean(re.sub(r"^and\s+", "", item, flags=re.IGNORECASE))
        for item in re.split(r",\s+(?=(?:and\s+)?(?:under|below|within|at least|\d|p\d+|restore|recover|sustain|keep|support|process))", raw, flags=re.IGNORECASE)
        if _clean(item)
    ]


def _extract_customer(text: str) -> str:
    match = re.search(r"\b(?:for|customer:)\s+([A-Z][A-Za-z0-9&.' -]+?)(?:\s+(?:migrating|modernizing|evaluating)|[.,])", text)
    return _clean(match.group(1)) if match else ""


def _remove_internal_paths(text: str) -> str:
    cleaned = str(text or "")
    for reference in internal_artifact_references(cleaned):
        cleaned = cleaned.replace(reference, "approved same-engagement artifact")
    return cleaned


def _strip_markdown_fence(text: str) -> str:
    stripped = str(text or "").strip()
    match = re.fullmatch(r"```(?:markdown)?\s*(.*?)\s*```", stripped, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else stripped


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
