"""
agent/tools/specialists.py
--------------------------
ToolHandler implementations for POV, JEP, and WAF specialist sub-agents.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

from agent import archie_memory, document_store, sub_agent_client
from agent.persistence_objectstore import ObjectStoreBase
from agent.sub_agent_client import SubAgentError
from skillforge.types import MemorySnapshot, ParallelToolCall, ToolResult


REQUIRED_WAF_PILLARS = frozenset(
    {
        "Security",
        "Reliability",
        "Performance Efficiency",
        "Cost Optimisation",
        "Operational Excellence",
        "Continuous Improvement",
    }
)


def build_inference_runner(app_state, *, inference_config: dict):
    """
    Return a text_runner callable using app.state if available,
    otherwise build one from inference_config.

    inference_config keys: endpoint, model_id, compartment_id,
    max_tokens, temperature, top_p, top_k.
    """
    existing = getattr(app_state, "text_runner", None)
    if existing:
        return existing

    def runner(prompt, system_message=""):
        from agent.llm_inference_client import run_inference as _ri
        return _ri(
            prompt,
            endpoint=inference_config["endpoint"],
            model_id=inference_config["model_id"],
            compartment_id=inference_config["compartment_id"],
            max_tokens=inference_config.get("max_tokens", 4096),
            temperature=inference_config.get("temperature", 0.7),
            top_p=inference_config.get("top_p", 0.9),
            top_k=inference_config.get("top_k", 50),
            system_message=system_message,
        )
    return runner


class _SpecialistHandler:
    """Base pattern for sub-agent specialist tools (pov, jep, waf)."""

    def __init__(
        self,
        agent_name: str,
        doc_type: str,
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
    ) -> None:
        self._agent_name = agent_name
        self._doc_type = doc_type
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        if self._agent_name == "jep":
            import agent.jep_lifecycle as jep_lifecycle

            policy_block = await asyncio.to_thread(
                jep_lifecycle.generate_policy_block_payload,
                self._store,
                self._customer_id,
            )
            if policy_block is not None:
                return ToolResult(
                    summary=(
                        "JEP generation is locked because an approved JEP exists. "
                        "Request revision first."
                    ),
                    status="blocked",
                    data={
                        "jep_state": policy_block.get("jep_state", {}),
                        "reason_codes": list(policy_block.get("reason_codes", [])),
                        "required_next_step": policy_block.get(
                            "required_next_step", ""
                        ),
                        "lock_outcome": "blocked",
                    },
                )

        ctx = context
        decision_context = memory.decision_context if memory else {}
        user_message = str(args.get("_user_message", "") or "")
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name=f"generate_{self._agent_name}",
            args=args,
            context=ctx,
            decision_context=decision_context,
            user_message=user_message,
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name=f"generate_{self._agent_name}",
            args=args,
            context=ctx,
        )

        if (
            self._agent_name == "pov"
            and ctx
            and not archie_memory._pov_has_sufficient_context(
                context=ctx,
                decision_context=decision_context,
                args=args,
                user_message=user_message,
            )
        ):
            clarify = "POV clarification required before Archie drafts the customer narrative."
            return ToolResult(
                summary=clarify,
                status="needs_input",
                clarification=clarify,
                data={"questions": archie_memory._pov_targeted_questions()},
            )

        feedback = str(args.get("feedback", "") or "")
        raw_request = feedback or _default_request(self._agent_name)
        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            raw_request = (
                f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n"
                f"{raw_request}"
            ).strip()
        engagement_context = {
            "customer_id": self._customer_id,
            "customer_name": self._customer_name,
            "feedback": feedback,
            "architect_brief": dict(args.get("_architect_brief", {}) or {}),
        }
        if self._agent_name == "waf":
            raw_request = _hydrate_waf_task(
                raw_request,
                args=args,
                context=ctx,
                memory_raw=memory.raw if memory else {},
            )

        try:
            response = await sub_agent_client.call_sub_agent(
                self._agent_name,
                task=raw_request,
                engagement_context=engagement_context,
                trace_id=trace_id,
            )
        except SubAgentError as exc:
            return ToolResult(
                summary=f"{self._agent_name.upper()} sub-agent error: {exc}",
                status="blocked",
            )

        if str(response.get("status") or "").lower() == "needs_input":
            clarification = str(response.get("result") or "")
            return ToolResult(
                summary=clarification or f"{self._agent_name.upper()} needs more input.",
                status="needs_input",
                clarification=clarification,
            )

        content = str(response.get("result") or "")
        summary_content = content
        if self._agent_name == "waf":
            missing_pillars = _missing_waf_pillars(content)
            if missing_pillars:
                missing_text = ", ".join(missing_pillars)
                return ToolResult(
                    summary=(
                        "WAF review incomplete - missing pillars: "
                        f"{missing_text}. Re-running with correction."
                    ),
                    status="blocked",
                    data={"missing_pillars": missing_pillars},
                )
            content = _ensure_waf_markdown_sections(content)
            response["result"] = content

        metadata = {"trace": response.get("trace", {}), "source": "sub_agent_client"}
        if self._agent_name == "sales_deck":
            saved = await asyncio.to_thread(
                _save_json_doc,
                self._store,
                self._doc_type,
                self._customer_id,
                content,
                metadata,
            )
        else:
            saved = await asyncio.to_thread(
                document_store.save_doc,
                self._store,
                self._doc_type,
                self._customer_id,
                content,
                metadata,
            )
        response["result_length"] = len(content)
        response.pop("result", None)
        key = str(saved.get("key", "") or "")

        if self._agent_name == "jep":
            import agent.jep_lifecycle as jep_lifecycle

            jep_state = await asyncio.to_thread(
                jep_lifecycle.mark_generated,
                self._store,
                self._customer_id,
            )
            response.update({"jep_state": jep_state, "lock_outcome": "allowed"})

        findings_summary = ""
        if self._agent_name == "waf":
            try:
                import json as _json

                waf_data = (
                    _json.loads(summary_content)
                    if summary_content.strip().startswith("{")
                    else {}
                )
                pillars = waf_data.get("pillars") or {}
                total_findings = sum(
                    len(v.get("findings", []))
                    for v in pillars.values()
                    if isinstance(v, dict)
                )
                p1_count = sum(
                    1
                    for v in pillars.values()
                    if isinstance(v, dict)
                    for f in v.get("findings", [])
                    if f.get("severity") == "P1"
                )
                if total_findings:
                    findings_summary = (
                        f" {total_findings} findings ({p1_count} P1)."
                    )
            except Exception:
                pass

        if self._agent_name == "waf":
            try:
                from agent import context_store as _cs
                import json as _json2
                _waf_data = (
                    _json2.loads(summary_content)
                    if summary_content.strip().startswith("{")
                    else {}
                )
                _pillars = _waf_data.get("pillars") or {}
                _sec = (_pillars.get("Security") or {})
                _p1s = [
                    f for p in _pillars.values()
                    if isinstance(p, dict)
                    for f in (p.get("findings") or [])
                    if f.get("severity") == "P1"
                ]
                _cs.set_resolved_decisions(context, waf={
                    "security_score": _sec.get("maturity_score"),
                    "p1_count": len(_p1s),
                    "compliance_framework": list((_waf_data.get("compliance_mapping") or {}).keys()),
                    "waf_key": key,
                })
            except Exception:
                pass

        if self._agent_name == "poc_strategist":
            pass  # POC write-back is in PocStrategistHandler

        return ToolResult(
            summary=(
                f"{self._agent_name.upper()} v{saved.get('version')} saved."
                f"{findings_summary}"
            ),
            status="ok",
            artifact_key=key,
            data=response,
        )


class PovHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("pov", "pov", store, customer_id, customer_name)


class JepHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("jep", "jep", store, customer_id, customer_name)


class WafHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("waf", "waf", store, customer_id, customer_name)


class TechResearchHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("tech_research", "research", store, customer_id, customer_name)


class SalesDeckHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("sales_deck", "deck", store, customer_id, customer_name)

class PocStrategistHandler:
    def __init__(self, store, customer_id, customer_name):
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        action = str(args.get("action", "") or "").strip().lower()
        confirmed_option_name = str(args.get("confirmed_option_name", "") or "").strip()
        user_message = str(args.get("_user_message", "") or args.get("prompt", "") or "")

        # action="confirm" with confirmed_option_name takes priority
        if action == "confirm" or confirmed_option_name:
            poc_options = _poc_options_from_memory(memory)
            if not poc_options:
                clarification = "No POC options in memory yet. Call generate_poc_plan with action='explore' first."
                return ToolResult(status="needs_input", summary=clarification, clarification=clarification)
            # Match by name, fall back to first option
            matched = next(
                (o for o in poc_options if str(o.get("option_name", "")).lower() == confirmed_option_name.lower()),
                poc_options[0],
            )
            return self._build_fanout_result(matched, memory)

        # Legacy: confirmation detected from _user_message (fallback for non-action calls)
        if action != "explore":
            confirmed_option = _detect_poc_confirmation(user_message, memory)
            if confirmed_option is not None:
                return self._build_fanout_result(confirmed_option, memory)
            if _poc_confirmation_index(user_message) is not None and not _poc_options_from_memory(memory):
                clarification = "No POC options yet. Call generate_poc_plan with action='explore' first."
                return ToolResult(status="needs_input", summary=clarification, clarification=clarification)

        decision_context = memory.decision_context if memory else {}
        pain = str(decision_context.get("pain_statement") or "").strip()
        platform = str(decision_context.get("current_platform") or "").strip()

        # Accept context summary or free-form prompt as pain/platform fallback
        if not pain:
            pain = str(args.get("context", "") or args.get("customer_context", "") or user_message or "").strip()
        if not platform:
            platform = str(decision_context.get("current_state", "") or "").strip() or "unspecified"

        if not pain:
            clarification = "NEEDS_CLARIFICATION: What is the customer's primary pain?"
            return ToolResult(status="needs_input", summary=clarification, clarification=clarification)

        customer_context = {
            "customer_id": self._customer_id,
            "customer_name": self._customer_name,
            "pain_statement": pain,
            "current_platform": platform,
            "decision_context": decision_context,
        }
        base_task = (
            f"Customer: {self._customer_name}\n"
            f"Context: {user_message}\n\n"
            f"Decision context:\n{json.dumps(decision_context, indent=2, sort_keys=True)}"
        )
        angles = [
            "migration_modernization",
            "performance_scale_ai",
            "cost_optimization_tco",
        ]

        async def _call_angle(angle: str, delay: float) -> tuple[str, Any]:
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await sub_agent_client.call_sub_agent(
                    "poc_strategist",
                    task=base_task,
                    engagement_context={
                        "angle": angle,
                        "customer_id": self._customer_id,
                        "customer_context": customer_context,
                    },
                    trace_id=trace_id,
                )
                return angle, response
            except Exception as exc:
                return angle, exc

        results = await asyncio.gather(
            *[_call_angle(angle, delay) for angle, delay in zip(angles, [0, 1.5, 3.0])],
        )

        options: list[dict[str, Any]] = []
        failures: list[str] = []
        for angle, response in results:
            if isinstance(response, Exception):
                failures.append(f"{angle}: {response}")
                continue
            if str(response.get("status") or "").lower() != "ok":
                failures.append(f"{angle}: status={response.get('status')}")
                continue
            raw = str(response.get("result") or "")
            try:
                option = json.loads(raw)
            except json.JSONDecodeError:
                # Fallback: extract first {...} block in case the LLM added preamble
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    try:
                        option = json.loads(m.group(0))
                    except json.JSONDecodeError as exc2:
                        failures.append(f"{angle}: invalid_json={exc2}")
                        continue
                else:
                    failures.append(f"{angle}: no_json_found in result")
                    continue
            if isinstance(option, dict):
                option.setdefault("angle", angle)
                options.append(option)

        if not options:
            return ToolResult(
                status="blocked",
                summary="All 3 POC exploration angles failed.",
                data={"failures": failures},
            )

        options.sort(key=_poc_option_score, reverse=True)
        recommendation = options[0]
        recommended_name = str(recommendation.get("option_name") or "recommended POC")
        relevance = recommendation.get("relevance_score", 0)
        hours = recommendation.get("executability_hours", 0)
        payload = {
            "poc_options": options,
            "recommendation": {
                "poc_name": recommended_name,
                "rationale": (
                    f"Best fit for the stated pain '{pain}': highest relevance "
                    f"({relevance}/10) with {hours}h build time."
                ),
                "build_sequence": [],
                "success_criteria": str(recommendation.get("wow_moment") or ""),
            },
        }

        saved = await asyncio.to_thread(
            _save_json_doc,
            self._store,
            "poc_plan",
            self._customer_id,
            json.dumps(payload, indent=2),
            {"trace_id": trace_id, "failures": failures},
        )

        from agent import context_store as _cs
        _cs.set_resolved_decisions(context, poc={
            "recommended_option": recommended_name,
            "success_criteria": str(recommendation.get("wow_moment") or ""),
            "build_hours": hours,
            "relevance_score": relevance,
        })

        return ToolResult(
            status="ok",
            summary=_format_poc_options_summary(options, recommended_name, pain),
            artifact_key=str(saved.get("key", "") or ""),
            data=payload,
        )

    def _build_fanout_result(
        self,
        option: dict[str, Any],
        memory: MemorySnapshot | None,
    ) -> ToolResult:
        poc_name = str(option.get("option_name") or "POC")
        services = [
            str(service)
            for service in option.get("oci_services", [])
            if str(service).strip()
        ]
        service_text = ", ".join(services)
        dc = memory.decision_context if memory else {}
        region = str(dc.get("region") or "us-chicago-1")
        build_sequence = option.get("build_sequence", [])
        demo_summary = str(option.get("demo_script_summary") or "")
        wow_moment = str(option.get("wow_moment") or "")
        base = (
            f"POC: {poc_name}. "
            f"Services: {service_text or 'use the confirmed POC option services'}. "
            f"Demo: {demo_summary or wow_moment}"
        ).strip()

        return ToolResult(
            status="parallel",
            summary=f"POC confirmed: {poc_name}. Generating all artifacts in parallel...",
            parallel_tools=[
                ParallelToolCall(
                    tool="generate_diagram",
                    args={
                        "diagram_name": _slugify_poc_name(poc_name),
                        "prompt": f"Create OCI architecture diagram for: {base}",
                        "_user_message": f"Create OCI architecture diagram for: {base}",
                    },
                ),
                ParallelToolCall(
                    tool="generate_bom",
                    args={
                        "prompt": f"Generate BOM for POC: {base}. Region: {region}",
                        "_user_message": f"Generate BOM for POC: {base}. Region: {region}",
                    },
                ),
                ParallelToolCall(
                    tool="generate_jep",
                    args={
                        "feedback": (
                            f"Create JEP execution plan for POC: {poc_name}. "
                            f"Build sequence: {build_sequence}. Success criteria: {wow_moment}"
                        ),
                        "_user_message": (
                            f"Create JEP execution plan for POC: {poc_name}. "
                            f"Build sequence: {build_sequence}. Success criteria: {wow_moment}"
                        ),
                    },
                ),
                ParallelToolCall(
                    tool="generate_terraform",
                    args={
                        "prompt": f"Generate Terraform for: {base}. Region: {region}",
                        "_user_message": f"Generate Terraform for: {base}. Region: {region}",
                    },
                ),
                ParallelToolCall(
                    tool="generate_presentation",
                    args={
                        "_user_message": f"Create client PowerPoint deck for POC: {base}",
                        "poc_option": option,
                    },
                ),
            ],
        )

def _hydrate_waf_task(
    task: str,
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> str:
    value = lambda *keys, default="not stated": _context_value_or_default(  # noqa: E731
        keys,
        default=default,
        args=args,
        context=context,
        memory_raw=memory_raw,
    )
    block = "\n".join(
        [
            "[CONFIRMED CONTEXT]",
            f"Architecture: {value('architecture_description', 'architecture_summary', 'topology')}",
            f"Public exposure: {value('public_exposure', 'internet_facing')}",
            f"Compliance: {value('compliance_requirements', 'compliance', default='none stated')}",
            f"Diagram: {value('existing_diagram_artifact_key', 'diagram_key', 'artifact_key', default='none')}",
            f"Subnet tiers: {value('subnet_tiers', 'tiers')}",
            "[/CONFIRMED CONTEXT]",
        ]
    )
    return f"{block}\n\n{task}".strip()


def _context_value_or_default(
    keys: tuple[str, ...],
    *,
    default: str,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> Any:
    value = _find_context_value(keys, args=args, context=context, memory_raw=memory_raw)
    return default if not _has_context_value(value) else value


def _find_context_value(
    keys: tuple[str, ...],
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> Any:
    for source in _context_sources(args=args, context=context, memory_raw=memory_raw):
        for key in keys:
            if key in source and _has_context_value(source.get(key)):
                return source[key]
    return None


def _context_sources(
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source in (args, context, memory_raw):
        if isinstance(source, dict):
            sources.append(source)
            for key in ("facts", "requirements", "topology", "agents", "diagram", "waf"):
                nested = source.get(key)
                if isinstance(nested, dict):
                    sources.append(nested)
    return sources


def _has_context_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return False
    return True


def _validation_blocked(tool: str, error: str) -> ToolResult:
    return ToolResult(
        summary=f"{tool.upper()} validation failed: {error}",
        status="blocked",
        data={"validation_error": error, "validation_stage": "python_contract"},
    )


def _default_request(agent_name: str) -> str:
    if agent_name == "pov":
        return "Generate a customer POV from current engagement context."
    if agent_name == "sales_deck":
        return "Generate an OCI customer-facing sales deck from current engagement context."
    return f"Generate the {agent_name.upper()} from current engagement context."


_CONFIRMATION_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"\boption\s*1\b", 0),
    (r"\boption\s*2\b", 1),
    (r"\boption\s*3\b", 2),
    (r"\bgo\s+with\b", 0),
    (r"\bproceed\b", 0),
    (r"\bproceed\s+with\b", 0),
    (r"\bconfirm\b", 0),
    (r"\blet'?s\s+do\b", 0),
)


def _format_poc_options_summary(
    options: list[dict[str, Any]],
    recommended_name: str,
    pain: str,
) -> str:
    lines = [
        f"Generated {len(options)} POC options for: {pain}\n",
        f"RECOMMENDED: {recommended_name}\n",
    ]
    for i, opt in enumerate(options, 1):
        name = str(opt.get("option_name") or f"Option {i}")
        relevance = opt.get("relevance_score", "?")
        hours = opt.get("executability_hours", "?")
        cost = str(opt.get("cost_effectiveness") or "")
        wow = str(opt.get("wow_moment") or "")
        services = opt.get("oci_services") or []
        services_str = ", ".join(str(s) for s in services[:6])
        lines.append(
            f"Option {i}: {name}\n"
            f"  Relevance: {relevance}/10 | Build: {hours}h | Cost: {cost}\n"
            f"  Wow moment: {wow}\n"
            f"  OCI services: {services_str}\n"
        )
    lines.append(
        "Present all options to the user with their key differentiators. "
        "Highlight the recommended option. Ask which they want to proceed with."
    )
    return "\n".join(lines)


def _poc_confirmation_index(user_message: str) -> int | None:
    text = str(user_message or "").lower()
    for pattern, index in _CONFIRMATION_PATTERNS:
        if re.search(pattern, text):
            return index
    return None


def _poc_options_from_memory(memory: MemorySnapshot | None) -> list[dict[str, Any]]:
    decision_context = memory.decision_context if memory else {}
    poc_options = decision_context.get("poc_options", [])
    if not isinstance(poc_options, list):
        return []
    return [option for option in poc_options if isinstance(option, dict)]


def _detect_poc_confirmation(
    user_message: str,
    memory: MemorySnapshot | None,
) -> dict[str, Any] | None:
    """Return the confirmed POC option dict, or None if no confirmation matched."""
    index = _poc_confirmation_index(user_message)
    if index is None:
        return None
    poc_options = _poc_options_from_memory(memory)
    if not poc_options:
        return None
    safe_index = min(index, len(poc_options) - 1)
    return poc_options[safe_index]


def _slugify_poc_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (slug or "poc")[:40]


def _poc_option_score(option: dict[str, Any]) -> float:
    try:
        relevance = float(option.get("relevance_score") or 0)
    except (TypeError, ValueError):
        relevance = 0.0
    try:
        hours = float(option.get("executability_hours") or 8)
    except (TypeError, ValueError):
        hours = 8.0
    return relevance / max(hours, 1.0)


def _save_json_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
    content: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    version = document_store._get_next_version(store, doc_type, customer_id)
    try:
        parsed = json.loads(content)
        content = json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        pass

    content_bytes = content.encode("utf-8")
    version_key = document_store._doc_key(doc_type, customer_id, f"v{version}.json", customer_first=False)
    version_customer_key = document_store._doc_key(doc_type, customer_id, f"v{version}.json", customer_first=True)
    latest_key = document_store._doc_key(doc_type, customer_id, "LATEST.json", customer_first=False)
    latest_customer_key = document_store._doc_key(doc_type, customer_id, "LATEST.json", customer_first=True)
    manifest_key = document_store._doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=False)
    manifest_customer_key = document_store._doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=True)

    document_store._put_dual(
        store,
        customer_key=version_customer_key,
        legacy_key=version_key,
        content=content_bytes,
        content_type="application/json",
    )
    document_store._put_dual(
        store,
        customer_key=latest_customer_key,
        legacy_key=latest_key,
        content=content_bytes,
        content_type="application/json",
    )

    manifest = document_store._get_first_json(
        store,
        [manifest_customer_key, manifest_key],
        {"versions": []},
    )
    manifest["versions"].append({
        "version": version,
        "key": version_key,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    })
    document_store._put_dual(
        store,
        customer_key=manifest_customer_key,
        legacy_key=manifest_key,
        content=json.dumps(manifest, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    return {"version": version, "key": version_key, "latest_key": latest_key}


def _missing_waf_pillars(content: str) -> list[str]:
    text = str(content or "").strip()
    if not text.startswith("{"):
        return []
    try:
        waf_data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(waf_data, dict):
        return []
    pillars = waf_data.get("pillars") or {}
    if not isinstance(pillars, dict):
        pillars = {}
    present = {str(key) for key in pillars.keys()}
    return sorted(REQUIRED_WAF_PILLARS - present)


def _ensure_waf_markdown_sections(content: str) -> str:
    text = str(content or "").strip()
    lowered = text.lower()
    required = {
        "security and compliance": "Security and Compliance",
        "reliability and resilience": "Reliability and Resilience",
        "performance and cost optimization": "Performance and Cost Optimization",
        "operational efficiency": "Operational Efficiency",
        "distributed cloud": "Distributed Cloud",
    }
    missing = [title for marker, title in required.items() if marker not in lowered]
    if not missing:
        return text
    lines = [text, "", "## Archie WAF Section Alignment"]
    for title in missing:
        lines.append("")
        lines.append(f"### {title}")
        lines.append("See the corresponding pillar findings above; this heading is retained for WAF artifact consumers.")
    return "\n".join(lines).strip() + "\n"
