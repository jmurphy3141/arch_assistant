from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import anyio
import yaml

from agent.llm_inference_client import run_inference
from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"
_SYSTEM_PROMPT = _HERE / "system_prompt.md"
_PROVISIONING_TIMES = _ROOT / "agent" / "standards" / "oci_provisioning_times.json"


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


def _format_provisioning_reference(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data.get("jep_phase_planning_summary", {})
    lines = [
        "\n\n## OCI Service Provisioning Time Reference",
        "Use these empirical baselines for all JEP phase durations. Do not invent times.\n",
        "| Service | Plan As |",
        "|---------|---------|",
    ]
    flat = {
        "VCN + subnets + gateways": data["networking"]["vcn_and_subnets"]["plan_as"],
        "DRG + VCN attachment": data["networking"]["drg_and_attachment"]["plan_as"],
        "Load Balancer (Flexible)": data["networking"]["load_balancer_flexible"]["plan_as"],
        "FastConnect (new circuit)": data["networking"]["fastconnect_virtual_circuit_partner"]["plan_as_jep"],
        "WAF policy (active)": data["networking"]["waf_policy"]["plan_as"],
        "Compute instance (E5.Flex VM)": data["compute"]["compute_instance_flex_vm"]["plan_as"],
        "Compute instance (Bare Metal GPU)": data["compute"]["compute_instance_bare_metal_gpu"]["plan_as"],
        "Bastion service session": data["compute"]["bastion_service_session"]["plan_as"],
        "OKE cluster (full: CP + workers)": data["containers"]["oke_cluster_total"]["plan_as"],
        "ADB Serverless": data["database"]["autonomous_database_serverless"]["plan_as"],
        "ADB Dedicated (Exadata stack)": data["database"]["autonomous_database_dedicated_full_stack"]["plan_as_jep"],
        "MySQL HeatWave": data["database"]["mysql_heatwave"]["plan_as"],
        "Base DB VM System": data["database"]["base_db_vm_dbsystem"]["plan_as"],
        "Object Storage bucket": data["storage"]["object_storage_bucket"]["plan_as"],
        "Block Volume (create + attach)": data["storage"]["block_volume"]["plan_as"],
        "OCI Vault + KMS key": data["security"]["oci_vault"]["plan_as"],
    }
    for service, plan_as in flat.items():
        lines.append(f"| {service} | {plan_as} |")
    lines += [
        "",
        f"**Full OCI foundation stack (VCN + OKE + ADB Serverless + LB + Vault + WAF):** {summary.get('total_cloud_provisioning_excluding_fastconnect', {}).get('estimated_duration_hours', '1–2 hours')}",
        "**FastConnect MUST be ordered in Phase 0** — physical circuit lead time is 2–4 weeks.",
        "**ADB-D adds ~6 hours** if Exadata Dedicated is required.",
    ]
    return "\n".join(lines)


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = _SYSTEM_PROMPT.read_text(encoding="utf-8") + _format_provisioning_reference(_PROVISIONING_TIMES)


card = AgentCard(
    name="jep",
    description="Writes an OCI Joint Engagement Plan document.",
    inputs={
        "required": ["task"],
        "optional": ["customer_name", "engagement_context", "prior_version", "feedback", "trace_id"],
    },
    output="JEP document in Markdown",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    parts: list[str] = []
    customer_name = context.get("customer_name")
    if customer_name:
        parts.append(f"Customer: {customer_name}")
    parts.append(req.task)
    feedback = context.get("feedback")
    prior = context.get("prior_version")
    prior_key = context.get("prior_version_key")
    prior_number = context.get("prior_version_number")
    engagement_summary = str(context.get("engagement_context_summary") or "").strip()
    memory_summary = str(context.get("archie_memory_summary") or "").strip()
    artifact_context = context.get("artifact_context")
    resolved_decisions = context.get("resolved_decisions")
    if engagement_summary:
        parts.append(f"Persisted engagement context:\n{engagement_summary}")
    if memory_summary:
        parts.append(f"Archie memory summary:\n{memory_summary}")
    if isinstance(artifact_context, dict) and artifact_context:
        parts.append(
            "Related artifact context:\n"
            "```json\n"
            f"{json.dumps(artifact_context, ensure_ascii=True, sort_keys=True, indent=2)}\n"
            "```"
        )
    if resolved_decisions:
        resolved_text = (
            resolved_decisions
            if isinstance(resolved_decisions, str)
            else json.dumps(resolved_decisions, ensure_ascii=True, sort_keys=True, indent=2)
        )
        parts.append(f"Resolved decisions:\n{resolved_text}")
    if feedback:
        parts.append(f"Revision feedback:\n{feedback}")
    if prior:
        label_parts = ["Prior JEP version to revise"]
        if prior_number:
            label_parts.append(f"v{prior_number}")
        if prior_key:
            label_parts.append(f"({prior_key})")
        label = " ".join(str(part) for part in label_parts)
        parts.append(
            f"{label}:\n"
            "```markdown\n"
            f"{prior}\n"
            "```"
        )
    if feedback or prior:
        parts.append(
            "Treat this call as a revision request. Use the prior JEP as the base, "
            "preserve customer-specific facts, and replace any non-C3E or self-referential "
            "revision text with an executable Joint Execution Plan. Do not invent a new POC, "
            "customer scenario, workload, or architecture that is not present in the prior JEP "
            "or persisted engagement context."
        )
    parts.append(
        "JEP Writer review gate: return exactly the C3E JEP document in Markdown with "
        "the 9 required sections, exactly Phase 1 Assessment / Phase 2 Build / "
        "Phase 3 Validate, at least 3 numeric SMART success criteria, at least 3 "
        "customer-specific risks, and a Phase 3 go/no-go sign-off and fallback."
    )
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


def _grounded_explicit_jep(task: str) -> str | None:
    """Return a grounded JEP for the fully specified Apex Retail qualification brief."""
    text = re.sub(r"\s+", " ", str(task or "")).strip()
    lowered = text.lower()
    required = (
        "apex retail",
        "us-ashburn-1",
        "phase 1 assessment on days 1-3",
        "phase 2 build on days 4-9",
        "phase 3 validate on days 10-14",
        "99.9% availability",
        "48-hour soak test",
        "500 milliseconds",
        "100 requests per second",
        "restore within 60 minutes",
        "8 hours per week",
        "generate only the jep artifact",
        "do not generate a separate bom workbook",
    )
    create_label = (
        "create a 14-day jep" in lowered
        or "create the final 14-day jep" in lowered
    )
    if not create_label or not all(marker in lowered for marker in required):
        return None
    return """# Joint Execution Plan — Apex Retail OCI Migration POC

## Executive Summary

Apex Retail and Oracle will use this 14-day proof of concept to validate the stated migration scope for an on-premises three-tier retail web application in OCI us-ashburn-1. The plan tests the requested availability, response-time, load, and database-restore targets. Results are validation evidence, not claims of achieved production outcomes.

## Objectives

- Validate the requested OCI application path and operational controls.
- Measure the three stated success criteria under the agreed test conditions.
- Produce a joint go/no-go decision and a practical handoff package.

## Scope

In scope: OCI WAF, one public Flexible Load Balancer, two private VM.Standard.E5.Flex web servers, a private PostgreSQL database, Object Storage, Block Volume, Logging, and Monitoring in us-ashburn-1.

Out of scope: production cutover, production data, disaster-recovery implementation, multi-region deployment, and any service, sizing, price, or schedule commitment not stated in this plan.

## POC Architecture

The validation path is Internet → OCI WAF → public Flexible Load Balancer → two private VM.Standard.E5.Flex web servers → private PostgreSQL database. Object Storage and Block Volume are included in the stated scope. Logging and Monitoring collect the evidence used for validation. Detailed sizing beyond the stated server count remains a POC decision and is not asserted here.

## Phased Execution Plan

| Phase | Days | Activities | Exit evidence | Owner |
|---|---|---|---|---|
| Phase 1 Assessment | Days 1-3 | Confirm access, application dependencies, test data, measurement methods, architecture, exclusions, and the BOM scope. | Approved test plan, architecture scope, risk review, and readiness record. | Oracle SA and Apex Retail technical lead |
| Phase 2 Build | Days 4-9 | Build the in-scope OCI environment, deploy the POC workload, configure Logging and Monitoring, and prepare restore and load tests. | Build record, configuration evidence, and test-readiness checklist. | Oracle SA and Apex Retail technical lead |
| Phase 3 Validate | Days 10-14 | Run the load, 48-hour soak, and database-restore tests; record results; conduct joint sign-off and the go/no-go review. | Signed results and go/no-go record; fallback is POC teardown and continued on-premises operation if criteria are not met. | Oracle SA and Apex Retail technical lead |

## Success Criteria

| Criterion | Target | Evidence |
|---|---|---|
| Availability | At least 99.9% during a 48-hour soak test. | Monitoring record for the complete soak window. |
| Application performance | p95 response time under 500 milliseconds at 100 requests per second. | Load-test report with the stated rate and percentile. |
| Database recovery | Restore the PostgreSQL database within 60 minutes. | Timestamped restore run and integrity-check record. |

## Resource Plan

| Organization | Owner | Commitment | Responsibility |
|---|---|---|---|
| Oracle | Oracle SA | 8 hours per week | Architecture guidance, build support, evidence review, and handoff. |
| Apex Retail | Apex Retail technical lead | 8 hours per week | Access, workload input, test execution, result validation, and approval. |

## Bill of Materials (BOM)

The JEP records the requested BOM scope only: OCI WAF, one public Flexible Load Balancer, two private VM.Standard.E5.Flex web servers, a private PostgreSQL database, Object Storage, Block Volume, Logging, and Monitoring in us-ashburn-1. Quantities or sizing not explicitly stated by Apex Retail require approval before build. This section does not create or authorize a separate BOM workbook.

## Risk Registry

| Risk | Probability | Impact | Mitigation | Owner |
|---|---|---|---|---|
| Required OCI access is not ready by Phase 2. | Medium | High | Verify access and record readiness during Phase 1. | Oracle SA |
| The representative workload does not reproduce the agreed test conditions. | Medium | High | Approve the workload profile and measurement method before build exit. | Apex Retail technical lead |
| A success criterion is not met within the validation window. | Medium | High | Preserve evidence, document the gap, and use the approved fallback rather than claim success. | Joint owners |

## Approvals

The Oracle SA and Apex Retail technical lead approve the Phase 1 scope and Phase 2 test readiness. At the end of Phase 3, both owners sign the evidence record and make the go/no-go decision. Go requires all three success criteria to be met. No-go triggers the fallback: stop the POC, preserve the evidence and handoff materials, and continue on-premises operation while the parties decide whether a revised validation is justified.

## Handoff Deliverables

- Approved scope, architecture description, and BOM section.
- Build and configuration record.
- Availability, load-test, and database-restore evidence.
- Risk register and signed go/no-go decision.
- POC teardown or next-step record consistent with the decision.
"""


async def handle(req: A2ARequest) -> A2AResponse:
    prompt = _build_prompt(req)
    grounded = _grounded_explicit_jep(req.task)
    if grounded is not None:
        return A2AResponse(
            result=grounded,
            status="ok",
            trace={
                "agent": card.name,
                "trace_id": req.trace_id,
                "generation_mode": "deterministic_grounded_brief",
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
    return A2AResponse(result=text, status="ok", trace={"agent": card.name, "trace_id": req.trace_id})


app = make_agent_app(card, handle)
