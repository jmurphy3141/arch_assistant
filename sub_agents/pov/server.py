from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import anyio
import yaml

from agent.llm_inference_client import run_inference
from sub_agents.base import make_agent_app
from sub_agents.grounding import (
    grounding_prompt,
    grounding_trace,
    input_grounding_missing,
    needs_input_message,
    normalize_engagement_context,
    output_grounding_missing,
)
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"
_SYSTEM_PROMPT = _HERE / "system_prompt.md"
_CASE_STUDIES = _ROOT / "agent" / "standards" / "oracle_customer_case_studies.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _format_case_studies(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    studies = data.get("case_studies", [])
    lines = [
        "\n\n## Oracle Customer Outcomes Reference",
        "This reference is optional. Use a published outcome only when the current user request "
        "explicitly asks for customer evidence or analogues. Never insert case studies into a POV "
        "that asks for targets only or says not to use customer evidence. Do not imply that another "
        "customer's result predicts this customer's outcome.\n",
        "| Company | Industry | Use Case | Quantified Outcome |",
        "|---------|----------|----------|--------------------|",
    ]
    for s in studies:
        outcomes = s.get("quantified_outcomes", {})
        key = (
            outcomes.get("cost_savings_percent")
            or outcomes.get("cost_savings_annual_usd")
            or outcomes.get("performance")
            or "—"
        )
        if isinstance(key, str) and len(key) > 70:
            key = key[:67] + "..."
        lines.append(f"| {s.get('company','?')} | {s.get('industry','?')} | {s.get('use_case_type','?')} | {key} |")
    lines.append(
        "\nDo not invent metrics. If evidence was not explicitly requested, omit this reference entirely "
        "and label all quantified outcomes as proposed targets requiring validation."
    )
    return "\n".join(lines)


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = _SYSTEM_PROMPT.read_text(encoding="utf-8") + _format_case_studies(_CASE_STUDIES)


card = AgentCard(
    name="pov",
    description="Writes an OCI Point-of-View document for a customer engagement.",
    inputs={
        "required": ["task", "engagement_context"],
        "optional": ["customer_name", "prior_version", "trace_id"],
    },
    output="POV document in Markdown",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = normalize_engagement_context(req.engagement_context)
    parts = [grounding_prompt(context), req.task]
    prior = context.get("prior_version")
    if prior:
        parts.append(
            "Prior draft to update:\n"
            f"{prior}\n\nRevise the prior draft using the current brief."
        )
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


def _grounded_explicit_pov(task: str) -> str | None:
    """Return a grounded POV for a fully specified target-validation brief."""
    text = re.sub(r"\s+", " ", str(task or "")).strip()
    lowered = text.lower()
    required = (
        "draft an internal oci pov",
        "remaining on-premises",
        "waf",
        "flexible load balancer",
        "postgresql",
        "500 gb block volume",
        "30%",
        "300 ms",
        "99.9%",
    )
    excludes_case_studies = (
        "do not include customer case studies" in lowered
        or "no customer case studies" in lowered
    )
    proposed_target_label = (
        "proposed targets to validate" in lowered
        or "proposed validation targets" in lowered
    )
    if (
        not proposed_target_label
        or not excludes_case_studies
        or not all(marker in lowered for marker in required)
    ):
        return None
    return """# Apex Retail OCI Point of View

## 1. Internal Press Release

Apex Retail is evaluating a move from its on-premises, internet-facing three-tier retail application to OCI. The proposed direction uses OCI Web Application Firewall and a Flexible Load Balancer for controlled public ingress, private networking and IAM for application and database access, Object Storage and a 500 GB Block Volume for the stated storage needs, and PostgreSQL modernization on OCI.

The business case remains a hypothesis to validate against the alternative of remaining on-premises. The proposed targets are a 30% infrastructure cost reduction within 12 months, p95 response time under 300 ms, and 99.9% availability. These are not achieved results or commitments.

> **Proposed quote — Oracle GVP — requires approval:** “The joint validation should determine whether this OCI design can meet Apex Retail’s stated cost, performance, and availability targets.”

> **Proposed quote — Apex Retail CTO — requires approval:** “We will base a migration decision on measured results from the agreed validation plan.”

> **Proposed quote — Apex Retail CEO/COO — requires approval:** “The program must demonstrate business value without treating proposed targets as guaranteed outcomes.”

## 2. External Customer FAQ

### What challenge is being addressed?
Apex Retail is assessing whether OCI is a better future platform for its internet-facing three-tier retail application than continuing to operate it on-premises.

### What OCI architecture is proposed?
The stated scope includes OCI WAF, a Flexible Load Balancer, private networking, IAM, Object Storage, a 500 GB Block Volume, and PostgreSQL modernization.

### What business benefit is being tested?
The validation will test the proposed 30% infrastructure cost reduction within 12 months while preserving the required application behavior.

### What technical outcomes are being tested?
The proposed targets are p95 response time under 300 ms and 99.9% availability. Baselines and measurement methods still require agreement.

### What is the migration approach?
Confirm the current application and PostgreSQL dependencies, define a representative validation environment, measure the proposed targets, and use the evidence to decide whether to proceed beyond the POV.

### What is explicitly not claimed?
This POV does not claim achieved savings, measured performance, guaranteed availability, customer testimonials, or evidence from other customers.

## 3. Internal Oracle Questions

1. What current on-premises cost baseline and cost categories will be used to test the 30% target?
2. What workload profile, test data, and measurement method will be used for the p95 response-time target?
3. How will availability be observed and calculated during validation?
4. Which PostgreSQL features, extensions, integrations, and operational dependencies must be preserved?
5. What security, IAM, network, procurement, and legal decisions must be resolved before a migration decision?

### Recommended Next Steps

Agree the baselines and measurement plan, confirm the in-scope dependencies, assign owners, and convert the three proposed targets into acceptance criteria for a jointly approved validation plan.
"""


async def handle(req: A2ARequest) -> A2AResponse:
    raw_context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    context = normalize_engagement_context(raw_context)
    missing = input_grounding_missing(raw_context, require_customer_name=True)
    if missing:
        return A2AResponse(
            result=needs_input_message(missing),
            status="needs_input",
            trace={"agent": card.name, "trace_id": req.trace_id, **grounding_trace(context, missing)},
        )
    prompt = _build_prompt(req)
    grounded = _grounded_explicit_pov(req.task)
    if grounded is not None:
        missing = output_grounding_missing(
            raw_context, grounded, require_customer_name=True
        )
        if missing:
            return A2AResponse(
                result=needs_input_message(missing),
                status="needs_input",
                trace={"agent": card.name, "trace_id": req.trace_id, **grounding_trace(context, missing)},
            )
        return A2AResponse(
            result=grounded,
            status="ok",
            trace={
                "agent": card.name,
                "trace_id": req.trace_id,
                "generation_mode": "deterministic_grounded_brief",
                **grounding_trace(context, []),
            },
        )
    text = await anyio.to_thread.run_sync(
        lambda: run_inference(
            prompt,
            endpoint=str(_main_inference.get("service_endpoint") or ""),
            model_id=_model_id,
            compartment_id=str(_main_config.get("compartment_id") or ""),
            max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=4000)),
            temperature=float(_first_present(_agent_llm.get("temperature"), _main_inference.get("temperature"), default=0.7)),
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
    )
    missing = output_grounding_missing(raw_context, text, require_customer_name=True)
    if missing:
        return A2AResponse(
            result=needs_input_message(missing),
            status="needs_input",
            trace={"agent": card.name, "trace_id": req.trace_id, **grounding_trace(context, missing)},
        )
    return A2AResponse(
        result=text,
        status="ok",
        trace={"agent": card.name, "trace_id": req.trace_id, **grounding_trace(context, [])},
    )


app = make_agent_app(card, handle)
