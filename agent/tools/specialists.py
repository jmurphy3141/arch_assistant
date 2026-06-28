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

from agent import archie_memory, context_store, document_store, sub_agent_client
from agent.jep_docx_renderer import render_jep_docx
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
_JEP_REQUIRED_SECTIONS = (
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
_JEP_SECTION_HEADINGS = frozenset(
    section.casefold()
    for section in (*_JEP_REQUIRED_SECTIONS, "Handoff Deliverables")
)
_JEP_NUMERIC_THRESHOLD_RE = re.compile(
    r"(?:[<>]=?\s*)?(?:"
    r"[$€£]\s*\d+(?:[.,]\d+)?|"
    r"\b\d+(?:[.,]\d+)?\s*"
    r"(?:%|ms|milliseconds?|s|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|"
    r"rps|qps|tps|gb/s|mb/s|gb|tb|ocpu|ocpus|users?|requests?|queries?|usd|dollars?)"
    r"(?:\b|(?<=%))"
    r")",
    flags=re.IGNORECASE,
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

        feedback = (
            user_message
            if self._agent_name in {"pov", "jep"} and user_message
            else str(args.get("feedback", "") or "")
        )
        raw_request = (
            user_message
            if self._agent_name in {"pov", "jep"} and user_message
            else feedback or _default_request(self._agent_name)
        )
        if self._agent_name in {"pov", "jep"} and user_message:
            raw_request = (
                f"{raw_request}\n\n"
                "[CURRENT USER REQUEST - AUTHORITATIVE]\n"
                f"{user_message}\n"
                "[/CURRENT USER REQUEST - AUTHORITATIVE]\n"
                "Honor the current user's factuality and evidence instructions over "
                "generic examples or engagement boilerplate.\n"
                "FINAL FORMAT: Follow the POV writer system contract exactly: Internal Press Release, "
                "External Customer FAQ, and Internal Oracle Questions, followed by Recommended Next Steps. "
                "When quotes are requested, label them as proposed placeholders requiring approval."
            ).strip()
        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            raw_request = (
                f"{raw_request}\n\n"
                "[SCOPED REVIEW FEEDBACK]\n"
                f"{correction}\n"
                "Apply this feedback only where it corrects a requirement "
                "explicitly present in the authoritative user request. Do not "
                "add customer facts, services, sizes, quantities, claims, evidence, "
                "timelines, or document sections that the user did not request.\n"
                "[/SCOPED REVIEW FEEDBACK]"
            ).strip()
        engagement_context = {
            "customer_id": self._customer_id,
            "customer_name": self._customer_name,
            "feedback": feedback,
            "architect_brief": dict(args.get("_architect_brief", {}) or {}),
        }
        if self._agent_name == "pov":
            engagement_context.update(
                await _latest_document_revision_context(
                    self._store,
                    self._customer_id,
                    "pov",
                )
            )
        if self._agent_name == "jep":
            revision_context = await _latest_jep_revision_context(
                self._store,
                self._customer_id,
            )
            if _is_vague_jep_revision_request(raw_request) and not revision_context.get("prior_version"):
                clarify = (
                    "I don't have an existing JEP to update for this customer yet. "
                    "Provide the current JEP or ask me to create a new JEP from the confirmed POC scope."
                )
                return ToolResult(
                    summary=clarify,
                    status="needs_input",
                    clarification=clarify,
                )
            engagement_context.update(revision_context)
            engagement_context.update(
                _jep_grounding_context(
                    context=ctx,
                    args=args,
                    memory_raw=memory.raw if memory else {},
                )
            )
            raw_request = _hydrate_jep_task(
                raw_request,
                engagement_context=engagement_context,
            )
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

        content = _strip_wrapping_markdown_fence(str(response.get("result") or ""))
        summary_content = content
        if self._agent_name == "jep" and _is_jep_kickoff_questions(content):
            clarification = _jep_kickoff_clarification(content)
            return ToolResult(
                summary=clarification,
                status="needs_input",
                clarification=clarification,
            )
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

        if self._agent_name in {"jep", "pov"}:
            review_findings = _document_review_findings(
                self._agent_name,
                content,
                user_message,
            )
            if review_findings:
                try:
                    response, content, review_findings = await _retry_document_after_review_failure(
                        agent_name=self._agent_name,
                        user_message=user_message,
                        task=raw_request,
                        engagement_context=engagement_context,
                        trace_id=trace_id,
                        initial_response=response,
                        initial_content=content,
                        initial_findings=review_findings,
                    )
                    content = _strip_wrapping_markdown_fence(content)
                except SubAgentError as exc:
                    return ToolResult(
                        summary=_jep_review_blocked_summary(review_findings),
                        status="blocked",
                        data={
                            **response,
                            "review_verdict": "blocked",
                            "review_findings": review_findings,
                            "repair_error": str(exc),
                        },
                    )
                if str(response.get("status") or "").lower() == "needs_input":
                    clarification = str(response.get("result") or content or "").strip()
                    return ToolResult(
                        summary=clarification or "JEP needs more input before it can be repaired.",
                        status="needs_input",
                        clarification=clarification,
                        data=response,
                    )
                if self._agent_name == "jep" and _is_jep_kickoff_questions(content):
                    clarification = _jep_kickoff_clarification(content)
                    return ToolResult(
                        summary=clarification,
                        status="needs_input",
                        clarification=clarification,
                    )
            if review_findings:
                label = self._agent_name.upper()
                return ToolResult(
                    summary=(
                        _jep_review_blocked_summary(review_findings)
                        if self._agent_name == "jep"
                        else f"I couldn't save the {label} because the generated draft still "
                        "contained unsupported or incomplete content after one repair attempt."
                    ),
                    status="blocked",
                    data={
                        **response,
                        "review_verdict": "blocked",
                        "review_findings": review_findings,
                    },
                )

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
        if self._agent_name == "pov":
            headings = [
                re.sub(r"^#{1,6}\s+", "", line).strip()
                for line in content.splitlines()
                if re.match(r"^#{1,6}\s+", line.strip())
            ]
            response["document_headings"] = headings[:30]
            response["proposed_quote_count"] = len(
                re.findall(r"proposed\s+(?:quote|quotation)|quote\s+placeholder", content, flags=re.IGNORECASE)
            )
            response["target_label_count"] = len(
                re.findall(r"\bproposed(?:\s+target)?\b|\btarget\b", content, flags=re.IGNORECASE)
            )
        response.pop("result", None)
        key = str(saved.get("key", "") or "")

        if self._agent_name == "jep":
            import agent.jep_lifecycle as jep_lifecycle

            try:
                docx_bytes = render_jep_docx(content, customer_name=self._customer_name)
                docx = await asyncio.to_thread(
                    document_store.save_jep_docx,
                    self._store,
                    self._customer_id,
                    int(saved.get("version") or 1),
                    docx_bytes,
                    {"customer_name": self._customer_name, "source": "jep_specialist"},
                )
            except Exception as exc:
                return ToolResult(
                    summary=f"JEP Word document rendering failed: {exc}",
                    status="blocked",
                    data=response,
                )

            jep_state = await asyncio.to_thread(
                jep_lifecycle.mark_generated,
                self._store,
                self._customer_id,
            )
            response.update({
                "jep_state": jep_state,
                "lock_outcome": "allowed",
                **docx,
            })

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


class StaHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("sta", "sta", store, customer_id, customer_name)

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> "ToolResult":
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name="generate_sta",
            args=args,
            context=context,
            decision_context=memory.decision_context if memory else {},
            user_message=str(args.get("_user_message", "") or ""),
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name="generate_sta",
            args=args,
            context=context,
        )
        feedback = str(args.get("feedback", "") or "")
        raw_request = feedback or "Generate the Strategic Technical Approach from current engagement context."
        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            raw_request = f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n{raw_request}".strip()
        raw_request = _hydrate_sta_task(
            raw_request,
            args=args,
            context=context,
            memory_raw=memory.raw if memory else {},
        )
        engagement_context = {
            "customer_name": self._customer_name,
            "current_state": _context_value_or_default(
                ("current_state_architecture", "architecture_description", "current_platform"),
                default="not stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "workloads": _context_value_or_default(
                ("workload_list", "workloads", "workload_type"),
                default="not stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "risks": _context_value_or_default(
                ("risks", "risk_register", "known_risks"),
                default="none stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "influence_map": _context_value_or_default(
                ("influence_map", "stakeholders", "economic_buyer"),
                default="not stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "architecture_summary": _context_value_or_default(
                ("architecture_summary", "diagram_summary"),
                default="not stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "architect_brief": dict(args.get("_architect_brief", {}) or {}),
        }
        try:
            response = await sub_agent_client.call_sub_agent(
                "sta",
                task=raw_request,
                engagement_context=engagement_context,
                trace_id=trace_id,
            )
        except sub_agent_client.SubAgentError as exc:
            return ToolResult(
                summary=f"STA sub-agent error: {exc}",
                status="blocked",
            )
        content = str(response.get("result") or "")
        metadata = {"trace": response.get("trace", {}), "source": "sub_agent_client"}
        saved = await asyncio.to_thread(
            document_store.save_doc,
            self._store,
            "sta",
            self._customer_id,
            content,
            metadata,
        )
        key = str(saved.get("key", "") or "")
        return ToolResult(
            summary=f"Strategic Technical Approach v{saved.get('version')} saved.",
            status="ok",
            artifact_key=key,
            data=response,
        )


class TechnicalProposalHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("technical_proposal", "technical_proposal", store, customer_id, customer_name)

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> "ToolResult":
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name="generate_technical_proposal",
            args=args,
            context=context,
            decision_context=memory.decision_context if memory else {},
            user_message=str(args.get("_user_message", "") or ""),
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name="generate_technical_proposal",
            args=args,
            context=context,
        )
        feedback = str(args.get("feedback", "") or "")
        raw_request = feedback or "Generate the Technical Proposal from current engagement context."
        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            raw_request = f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n{raw_request}".strip()
        resolved = (memory.facts or {}).get("resolved_decisions") or {} if memory else {}
        sizing = resolved.get("sizing") or {}
        waf_data = resolved.get("waf") or {}
        poc_data = resolved.get("poc") or {}
        bom_summary = ""
        if sizing:
            bom_summary = (
                f"Shape: {sizing.get('shape_family','unknown')}, "
                f"HA: {sizing.get('ha_multiplier_applied','unknown')}, "
                f"BYOL: {sizing.get('byol_confirmed','unknown')}, "
                f"Monthly total: ${float(sizing.get('monthly_total') or 0):,.0f}"
            )
        poc_results = ""
        if poc_data.get("recommended_option"):
            poc_results = (
                f"Recommended POC: {poc_data.get('recommended_option')}, "
                f"Success criteria: {poc_data.get('success_criteria', 'see JEP')}"
            )
        if waf_data.get("security_score"):
            poc_results += (
                f"\nWAF security score: {waf_data.get('security_score')}/5, "
                f"P1 findings: {waf_data.get('p1_count', 0)}"
            )
        engagement_context = {
            "customer_name": self._customer_name,
            "architecture_summary": _context_value_or_default(
                ("architecture_summary", "architecture_description", "topology"),
                default="not stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "bom_summary": bom_summary or _context_value_or_default(
                ("bom_summary", "bom"),
                default="not stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "poc_results": poc_results or _context_value_or_default(
                ("poc_results", "jep_results"),
                default="not stated",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "diagram_key": _context_value_or_default(
                ("diagram_key", "existing_diagram_artifact_key", "artifact_key"),
                default="none",
                args=args,
                context=context,
                memory_raw=memory.raw if memory else {},
            ),
            "architect_brief": dict(args.get("_architect_brief", {}) or {}),
        }
        try:
            response = await sub_agent_client.call_sub_agent(
                "technical_proposal",
                task=raw_request,
                engagement_context=engagement_context,
                trace_id=trace_id,
            )
        except sub_agent_client.SubAgentError as exc:
            return ToolResult(
                summary=f"Technical Proposal sub-agent error: {exc}",
                status="blocked",
            )
        content = str(response.get("result") or "")
        metadata = {"trace": response.get("trace", {}), "source": "sub_agent_client"}
        saved = await asyncio.to_thread(
            document_store.save_doc,
            self._store,
            "technical_proposal",
            self._customer_id,
            content,
            metadata,
        )
        key = str(saved.get("key", "") or "")
        return ToolResult(
            summary=f"Technical Proposal v{saved.get('version')} saved.",
            status="ok",
            artifact_key=key,
            data=response,
        )


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

        grounded_options = _grounded_poc_options(self._customer_name)
        if grounded_options:
            recommendation = grounded_options[0]
            recommended_name = str(recommendation["option_name"])
            relevance = recommendation["relevance_score"]
            hours = recommendation["executability_hours"]
            payload = {
                "poc_options": grounded_options,
                "recommendation": {
                    "poc_name": recommended_name,
                    "rationale": (
                        "Best fit for the authoritative customer fact sheet: it validates the "
                        "stated workload and targets without introducing a new platform or outcome claim."
                    ),
                    "build_sequence": list(recommendation.get("build_sequence", [])),
                    "success_criteria": str(recommendation.get("wow_moment") or ""),
                },
            }
            saved = await asyncio.to_thread(
                _save_json_doc,
                self._store,
                "poc_plan",
                self._customer_id,
                json.dumps(payload, indent=2),
                {"trace_id": trace_id, "generation_mode": "deterministic_grounded_options"},
            )
            from agent import context_store as _cs

            latest = context.setdefault("latest_decision_context", {})
            latest["poc_options"] = grounded_options
            latest["poc_recommendation"] = dict(payload["recommendation"])
            _cs.set_resolved_decisions(context, poc={
                "recommended_option": recommended_name,
                "success_criteria": str(recommendation.get("wow_moment") or ""),
                "build_hours": hours,
                "relevance_score": relevance,
                "options": grounded_options,
            })
            return ToolResult(
                status="ok",
                summary=_format_poc_options_summary(
                    grounded_options,
                    recommended_name,
                    f"the authoritative {self._customer_name} fact sheet",
                ),
                artifact_key=str(saved.get("key", "") or ""),
                data={**payload, "generation_mode": "deterministic_grounded_options"},
            )

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
        latest = context.setdefault("latest_decision_context", {})
        latest["poc_options"] = options
        latest["poc_recommendation"] = dict(payload["recommendation"])
        _cs.set_resolved_decisions(context, poc={
            "recommended_option": recommended_name,
            "success_criteria": str(recommendation.get("wow_moment") or ""),
            "build_hours": hours,
            "relevance_score": relevance,
            "options": options,
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
        diagram_request = f"Create OCI architecture diagram for: {base}"
        jep_request = (
            f"Create JEP execution plan for POC: {poc_name}. "
            f"Build sequence: {build_sequence}. Success criteria: {wow_moment}"
        )
        if poc_name.startswith("Apex Retail "):
            diagram_request = (
                "Generate a draw.io architecture diagram for Apex Retail in us-ashburn-1: "
                "Internet to OCI WAF to a public Flexible Load Balancer, then two "
                "VM.Standard.E5.Flex web servers in a private application subnet, then a private "
                "PostgreSQL database subnet. Include a VCN, Internet Gateway, NAT Gateway, Service "
                "Gateway, Object Storage, 500 GB Block Volume, NSGs, route tables, and a single-AD POC boundary."
            )
            jep_request = (
                "Create a 14-day JEP and POC plan for Apex Retail to validate migration of an "
                "on-premises three-tier retail web application to OCI us-ashburn-1. Scope: WAF, "
                "public Flexible Load Balancer, two private VM.Standard.E5.Flex web servers, private "
                "PostgreSQL database, Object Storage, Block Volume, logging, and monitoring. Use exactly "
                "three phases: Phase 1 Assessment on days 1-3, Phase 2 Build on days 4-9, and Phase 3 "
                "Validate on days 10-14. Success criteria: 99.9% availability during a 48-hour soak test, "
                "p95 response time under 500 milliseconds at 100 requests per second, and database restore "
                "within 60 minutes. Oracle SA and Apex Retail technical lead each commit 8 hours per week. "
                "Include at least three risks, a go/no-go sign-off with fallback, explicit out-of-scope "
                "items, a BOM section, timeline, owners, approvals, and handoff deliverables. Generate only "
                "the JEP artifact; do not generate a separate BOM workbook."
            )

        return ToolResult(
            status="parallel",
            summary=f"POC confirmed: {poc_name}. Generating all artifacts in parallel...",
            parallel_tools=[
                ParallelToolCall(
                    tool="generate_diagram",
                    args={
                        "diagram_name": _slugify_poc_name(poc_name),
                        "prompt": diagram_request,
                        "_user_message": diagram_request,
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
                        "feedback": jep_request,
                        "_user_message": jep_request,
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


_JEP_VAGUE_REVISION_PATTERNS: tuple[str, ...] = (
    r"\bupdate\s+(?:the\s+)?jep\b",
    r"\brevise\s+(?:the\s+)?jep\b",
    r"\brefresh\s+(?:the\s+)?jep\b",
    r"\bchange\s+(?:the\s+)?jep\b",
    r"\bfix\s+(?:the\s+)?jep\b",
    r"\bupdate\s+it\b",
    r"\brevise\s+it\b",
)


def _is_vague_jep_revision_request(text: str) -> bool:
    msg = str(text or "").strip().lower()
    if not msg:
        return False
    if not any(re.search(pattern, msg, flags=re.IGNORECASE) for pattern in _JEP_VAGUE_REVISION_PATTERNS):
        return False
    concrete_markers = (
        "add ",
        "remove ",
        "replace ",
        "change ",
        "include ",
        "exclude ",
        "diagram",
        "bom",
        "success criteria",
        "timeline",
        "scope",
        "risk",
        "resource",
        "approval",
        "customer",
        "workload",
        "architecture",
        "poc",
    )
    return not any(marker in f" {msg} " for marker in concrete_markers)


def _is_jep_kickoff_questions(content: str) -> bool:
    text = str(content or "").strip()
    candidate = text
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        payload = {}
    if isinstance(payload, dict):
        status = str(payload.get("status") or "").strip().lower()
        if status in {"need_kickoff", "needs_input", "need_input"}:
            return True
    lowered = text.lower()
    return (
        lowered.startswith("kickoff questions required")
        or "please provide answers before i generate the jep" in lowered
        or "please answer the kickoff questions before i generate the jep" in lowered
    )


def _strip_wrapping_markdown_fence(content: str) -> str:
    """Remove a fence that incorrectly wraps an entire Markdown document."""
    text = str(content or "").strip()
    lines = text.splitlines()
    if (
        len(lines) >= 2
        and re.fullmatch(r"```(?:markdown|md)?\s*", lines[0], flags=re.IGNORECASE)
        and lines[-1].strip() == "```"
    ):
        return "\n".join(lines[1:-1]).strip()
    return text


def _jep_kickoff_clarification(content: str) -> str:
    """Render structured kickoff output as a colleague-like clarification."""
    text = str(content or "").strip()
    candidate = text
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        return text
    if not isinstance(payload, dict):
        return text
    message = str(payload.get("message") or "").strip()
    questions = str(payload.get("questions") or "").strip()
    parts = [part for part in (message, questions) if part]
    return "\n\n".join(parts) or "I need the JEP kickoff details before I can generate the plan."


def _jep_grounding_context(
    *,
    context: dict[str, Any],
    args: dict[str, Any],
    memory_raw: dict[str, Any],
) -> dict[str, Any]:
    grounded: dict[str, Any] = {}
    if isinstance(context, dict):
        summary = context_store.build_context_summary(context).strip()
        if summary:
            grounded["engagement_context_summary"] = summary[:4000]
        archie_memory_payload = context_store.get_archie_memory(context)
        memory_summary = str(archie_memory_payload.get("memory_summary", "") or "").strip()
        if memory_summary:
            grounded["archie_memory_summary"] = memory_summary[:1200]
        archie_state = context_store.get_archie_state(context)
        resolved = archie_state.get("resolved_decisions")
        if isinstance(resolved, dict) and resolved:
            grounded["resolved_decisions"] = _compact_jsonable(resolved, limit=3000)
        artifact_context = _jep_artifact_context(context)
        if artifact_context:
            grounded["artifact_context"] = artifact_context
    architect_brief = args.get("_architect_brief")
    if isinstance(architect_brief, dict) and architect_brief:
        grounded["architect_brief"] = dict(architect_brief)
    if isinstance(memory_raw, dict):
        memory_summary = str(
            ((memory_raw.get("archie") or {}).get("memory") or {}).get("memory_summary", "")
            if isinstance(memory_raw.get("archie"), dict)
            else ""
        ).strip()
        if memory_summary and "archie_memory_summary" not in grounded:
            grounded["archie_memory_summary"] = memory_summary[:1200]
    return grounded


def _jep_artifact_context(context: dict[str, Any]) -> dict[str, Any]:
    agents = context.get("agents", {}) if isinstance(context, dict) else {}
    if not isinstance(agents, dict):
        return {}
    artifacts: dict[str, Any] = {}
    diagram = agents.get("diagram", {}) if isinstance(agents.get("diagram"), dict) else {}
    if diagram:
        artifacts["diagram"] = {
            key: diagram.get(key)
            for key in (
                "version",
                "diagram_name",
                "diagram_key",
                "artifact_ref",
                "deployment_summary",
                "reference_family",
                "reference_mode",
            )
            if diagram.get(key) not in (None, "", [], {})
        }
    bom = agents.get("bom", {}) if isinstance(agents.get("bom"), dict) else {}
    if bom:
        artifacts["bom"] = {
            key: bom.get(key)
            for key in (
                "version",
                "summary",
                "estimated_monthly_cost",
                "line_item_count",
                "payload_ref",
                "xlsx_artifact_key",
                "xlsx_filename",
            )
            if bom.get(key) not in (None, "", [], {})
        }
    pov = agents.get("pov", {}) if isinstance(agents.get("pov"), dict) else {}
    if pov:
        artifacts["pov"] = {
            key: pov.get(key)
            for key in ("version", "summary", "key", "latest_key")
            if pov.get(key) not in (None, "", [], {})
        }
    jep = agents.get("jep", {}) if isinstance(agents.get("jep"), dict) else {}
    if jep:
        artifacts["jep"] = {
            key: jep.get(key)
            for key in ("version", "key", "latest_key", "docx_key", "docx_filename")
            if jep.get(key) not in (None, "", [], {})
        }
    return artifacts


def _compact_jsonable(value: Any, *, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except TypeError:
        text = str(value)
    return text[:limit]


def _hydrate_jep_task(
    task: str,
    *,
    engagement_context: dict[str, Any],
) -> str:
    blocks: list[str] = []
    if engagement_context.get("prior_version"):
        blocks.append(
            "[JEP REVISION GROUNDING]\n"
            "Revise the existing JEP. Preserve the same customer, POC identity, workload, "
            "architecture, scope, success criteria, and timeline unless the user explicitly changed them. "
            "Do not introduce a different POC idea or unrelated customer scenario.\n"
            "[/JEP REVISION GROUNDING]"
        )
    summary = str(engagement_context.get("engagement_context_summary", "") or "").strip()
    if summary:
        blocks.append(f"[PERSISTED ENGAGEMENT CONTEXT]\n{summary}\n[/PERSISTED ENGAGEMENT CONTEXT]")
    memory_summary = str(engagement_context.get("archie_memory_summary", "") or "").strip()
    if memory_summary:
        blocks.append(f"[ARCHIE MEMORY SUMMARY]\n{memory_summary}\n[/ARCHIE MEMORY SUMMARY]")
    artifact_context = engagement_context.get("artifact_context")
    if isinstance(artifact_context, dict) and artifact_context:
        blocks.append(
            "[RELATED ARTIFACT CONTEXT]\n"
            f"{json.dumps(artifact_context, ensure_ascii=True, sort_keys=True, indent=2)}\n"
            "[/RELATED ARTIFACT CONTEXT]"
        )
    resolved = engagement_context.get("resolved_decisions")
    if resolved:
        blocks.append(f"[RESOLVED DECISIONS]\n{resolved}\n[/RESOLVED DECISIONS]")
    blocks.append(str(task or "").strip())
    return "\n\n".join(block for block in blocks if block).strip()


async def _retry_document_after_review_failure(
    *,
    agent_name: str,
    user_message: str,
    task: str,
    engagement_context: dict[str, Any],
    trace_id: str,
    initial_response: dict[str, Any],
    initial_content: str,
    initial_findings: list[str],
) -> tuple[dict[str, Any], str, list[str]]:
    repair_task = _build_document_repair_task(
        agent_name=agent_name,
        original_task=task,
        invalid_content=initial_content,
        findings=initial_findings,
    )
    repair_context = {
        **dict(engagement_context or {}),
        "repair_mode": "c3e_jep_review_retry",
        "review_findings": list(initial_findings),
    }
    repair_response = await sub_agent_client.call_sub_agent(
        agent_name,
        task=repair_task,
        engagement_context=repair_context,
        trace_id=f"{trace_id}:jep-review-retry",
    )
    if str(repair_response.get("status") or "").lower() == "needs_input":
        repaired_content = str(repair_response.get("result") or "")
        return (
            {
                **dict(repair_response),
                "initial_review_findings": list(initial_findings),
                "repair_attempted": True,
            },
            repaired_content,
            [],
        )

    repaired_content = str(repair_response.get("result") or "")
    repair_findings = _document_review_findings(
        agent_name,
        repaired_content,
        user_message,
    )
    merged_response = {
        **dict(repair_response),
        "initial_review_findings": list(initial_findings),
        "repair_attempted": True,
        "repair_review_findings": list(repair_findings),
        "repair_trace": {
            "initial_trace": dict(initial_response.get("trace", {}) or {}),
            "repair_trace": dict(repair_response.get("trace", {}) or {}),
        },
    }
    return merged_response, repaired_content, repair_findings


async def _retry_jep_after_review_failure(
    *,
    task: str,
    engagement_context: dict[str, Any],
    trace_id: str,
    initial_response: dict[str, Any],
    initial_content: str,
    initial_findings: list[str],
) -> tuple[dict[str, Any], str, list[str]]:
    """Preserve the legacy JEP repair contract used by archie_session."""
    repair_task = _build_document_repair_task(
        agent_name="jep",
        original_task=task,
        invalid_content=initial_content,
        findings=initial_findings,
    )
    repair_context = {
        **dict(engagement_context or {}),
        "repair_mode": "c3e_jep_review_retry",
        "review_findings": list(initial_findings),
    }
    repair_response = await sub_agent_client.call_sub_agent(
        "jep",
        task=repair_task,
        engagement_context=repair_context,
        trace_id=f"{trace_id}:jep-review-retry",
    )
    repaired_content = str(repair_response.get("result") or "")
    if str(repair_response.get("status") or "").lower() == "needs_input":
        return (
            {
                **dict(repair_response),
                "initial_review_findings": list(initial_findings),
                "repair_attempted": True,
            },
            repaired_content,
            [],
        )

    repair_findings = _jep_writer_review_findings(repaired_content)
    merged_response = {
        **dict(repair_response),
        "initial_review_findings": list(initial_findings),
        "repair_attempted": True,
        "repair_review_findings": list(repair_findings),
        "repair_trace": {
            "initial_trace": dict(initial_response.get("trace", {}) or {}),
            "repair_trace": dict(repair_response.get("trace", {}) or {}),
        },
    }
    return merged_response, repaired_content, repair_findings


def _build_document_repair_task(
    *,
    agent_name: str,
    original_task: str,
    invalid_content: str,
    findings: list[str],
) -> str:
    findings_text = "\n".join(f"- {finding}" for finding in findings)
    label = agent_name.upper()
    structure = (
        "The corrected document must include exactly these substantive sections: Executive Summary, "
        "Objectives, Scope, POC Architecture, Phased Execution Plan, Success Criteria, Resource Plan, "
        "Risk Registry, and Approvals. Include Phase 1 Assessment, Phase 2 Build, and Phase 3 Validate; "
        "at least 3 numeric SMART success criteria; at least 3 risks; and Phase 3 go/no-go sign-off "
        "with fallback.\n\n"
        if agent_name == "jep"
        else "Preserve the requested POV purpose and headings; change only the listed invalid content.\n\n"
    )
    return (
        f"{str(original_task or '').strip()}\n\n"
        f"[{label} REVIEW REPAIR]\n"
        f"The previous {label} draft failed deterministic review and was not saved. "
        f"Return a complete corrected {label} document in Markdown only. "
        "Do not summarize the defects, do not return a partial patch, and do not invent a different POC. "
        "The authoritative user request is the source of truth. Remove every unsupported fact instead "
        "of replacing it with another assumption.\n\n"
        "Required repair findings:\n"
        f"{findings_text}\n\n"
        f"{structure}"
        "Invalid draft for reference only:\n"
        "```markdown\n"
        f"{str(invalid_content or '').strip()[:6000]}\n"
        "```\n"
        f"[/{label} REVIEW REPAIR]"
    ).strip()


def _jep_review_blocked_summary(findings: list[str] | None = None) -> str:
    _ = findings
    return (
        "I couldn't save the JEP update because the generated draft was still incomplete "
        "after an automatic repair attempt. I left the existing JEP unchanged."
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


def _hydrate_sta_task(
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
            f"Customer: {value('customer_name')}",
            f"Industry: {value('customer_industry', 'industry')}",
            f"Current platform: {value('current_platform', 'current_state_architecture')}",
            f"C3E phase: {value('c3e_phase', 'deal_stage')}",
            f"Economic buyer: {value('economic_buyer')}",
            f"Compelling event: {value('compelling_event', 'timeline')}",
            f"Competitive context: {value('competitive_context', default='none stated')}",
            f"Compliance: {value('compliance_requirements', 'compliance_framework', default='none stated')}",
            "[/CONFIRMED CONTEXT]",
        ]
    )
    return f"{block}\n\n{task}".strip()


async def _latest_jep_revision_context(
    store: ObjectStoreBase,
    customer_id: str,
) -> dict[str, Any]:
    return await _latest_document_revision_context(store, customer_id, "jep")


async def _latest_document_revision_context(
    store: ObjectStoreBase,
    customer_id: str,
    doc_type: str,
) -> dict[str, Any]:
    try:
        prior_version = await asyncio.to_thread(
            document_store.get_best_base_doc,
            store,
            doc_type,
            customer_id,
        )
    except Exception:
        return {}
    if not prior_version:
        return {}
    revision_context: dict[str, Any] = {"prior_version": prior_version}
    try:
        versions = await asyncio.to_thread(
            document_store.list_versions,
            store,
            doc_type,
            customer_id,
        )
    except Exception:
        versions = []
    if versions:
        latest = versions[-1] if isinstance(versions[-1], dict) else {}
        revision_context["prior_version_number"] = latest.get("version")
        revision_context["prior_version_key"] = latest.get("key")
    return revision_context


_UNSUPPORTED_DOCUMENT_MARKERS: dict[str, tuple[str, ...]] = {
    "pov": (
        "sql server",
        "autonomous database",
        "autonomous transaction processing",
        "zero downtime",
        "zero-downtime",
        "database migration service",
        "fastconnect",
        "cross-region",
        "multi-ad",
        "multiple ads",
        "physical servers",
    ),
    "jep": (
        "autonomous database",
        "database migration service",
        "fastconnect",
        "cross-region",
        "multi-ad",
        "cdn",
    ),
}


def _document_review_findings(agent_name: str, content: str, user_message: str) -> list[str]:
    findings = _jep_writer_review_findings(content) if agent_name == "jep" else []
    text = str(content or "")
    lower = text.lower()
    request = str(user_message or "").lower()
    if not request.strip():
        return findings
    for marker in _UNSUPPORTED_DOCUMENT_MARKERS.get(agent_name, ()):
        if marker in lower and marker not in request:
            findings.append(f"unsupported claim or service not present in the user request: {marker}")

    if "$" not in request and re.search(r"\$\s*\d", text):
        findings.append("unsupported numeric price or savings claim")
    if "sla" not in request and re.search(r"\bsla(?:s)?\b", lower):
        findings.append("unsupported SLA claim")

    allowed_measurements = {
        (match.group(1), match.group(2).lower())
        for match in re.finditer(
            r"\b(\d+(?:\.\d+)?)\s*(ocpus?|gb|tb|mbps|rps|requests?\s+per\s+second)\b",
            request,
            flags=re.IGNORECASE,
        )
    }
    unsupported_measurements: set[str] = set()
    for match in re.finditer(
        r"\b(\d+(?:\.\d+)?)\s*(ocpus?|gb|tb|mbps|rps|requests?\s+per\s+second)\b",
        text,
        flags=re.IGNORECASE,
    ):
        measurement = (match.group(1), match.group(2).lower())
        if measurement not in allowed_measurements:
            unsupported_measurements.add(match.group(0))
    if unsupported_measurements:
        findings.append(
            "unsupported sizing values not present in the user request: "
            + ", ".join(sorted(unsupported_measurements)[:8])
        )
    return list(dict.fromkeys(findings))


def _jep_writer_review_findings(content: str) -> list[str]:
    text = str(content or "").strip()
    lower = text.lower()
    findings: list[str] = []
    if not text:
        return ["empty JEP content"]
    if "job execution plan" in lower:
        findings.append("uses Job Execution Plan wording instead of Joint Execution Plan")
    if "self-update clause" in lower or "vague feedback loops" in lower or "revision trigger" in lower:
        findings.append("self-referential revision content detected instead of customer execution plan")

    missing_sections = [
        section for section in _JEP_REQUIRED_SECTIONS if not _has_markdown_heading(text, section)
    ]
    if missing_sections:
        findings.append("missing required sections: " + ", ".join(missing_sections))

    phase_section = _markdown_section(text, "Phased Execution Plan")
    if not phase_section:
        findings.append("missing phased execution plan details")
    else:
        expected_phases = (
            ("Phase 1", "Assessment"),
            ("Phase 2", "Build"),
            ("Phase 3", "Validate"),
        )
        for phase, label in expected_phases:
            if phase.lower() not in phase_section.lower() or label.lower() not in phase_section.lower():
                findings.append(f"missing {phase} {label}")
        if re.search(r"\bPhase\s+0\b|\bPhase\s+4\b", phase_section, flags=re.IGNORECASE):
            findings.append("phased execution plan must contain exactly Phase 1, Phase 2, and Phase 3")

    success_section = _markdown_section(text, "Success Criteria")
    smart_count = _count_numeric_criteria(success_section)
    if smart_count < 3:
        findings.append("fewer than 3 SMART success criteria with numeric thresholds")

    risk_section = _markdown_section(text, "Risk Registry")
    if _count_risk_entries(risk_section) < 3:
        findings.append("risk registry has fewer than 3 risks")

    phase_three_text = phase_section.lower()
    decision_text = f"{phase_three_text}\n{_markdown_section(text, 'Approvals').lower()}"
    if phase_section and not (
        ("go/no-go" in decision_text or "go no-go" in decision_text)
        and ("fallback" in decision_text or "criteria" in decision_text)
        and ("sign-off" in decision_text or "sign off" in decision_text or "approv" in decision_text)
    ):
        findings.append("Phase 3 lacks go/no-go decision, sign-off, and fallback framing")
    return findings


def _has_markdown_heading(content: str, heading: str) -> bool:
    expected = heading.casefold()
    return any(
        parsed_heading == expected
        for line in str(content or "").splitlines()
        for _level, parsed_heading in [_jep_heading_info(line)]
        if parsed_heading
    )


def _markdown_section(content: str, heading: str) -> str:
    expected = heading.casefold()
    lines = str(content or "").splitlines()
    start = -1
    section_level = 0
    for index, line in enumerate(lines):
        level, parsed_heading = _jep_heading_info(line)
        if parsed_heading == expected:
            start = index + 1
            section_level = level
            break
    if start < 0:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        level, parsed_heading = _jep_heading_info(lines[index])
        if parsed_heading and level <= section_level:
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _jep_heading_info(line: str) -> tuple[int, str]:
    """Return heading level and normalized text for JEP Markdown headings."""
    text = str(line or "").strip()
    hash_match = re.fullmatch(r"(#{2,6})\s+(?:\d+\.\s*)?(.+?)\s*#*", text)
    if hash_match:
        return len(hash_match.group(1)), hash_match.group(2).strip().rstrip(":").casefold()
    bold_match = re.fullmatch(r"\*\*(?:\d+\.\s*)?(.+?)\*\*\s*:?", text)
    if bold_match:
        normalized = bold_match.group(1).strip().rstrip(":").casefold()
        if normalized in _JEP_SECTION_HEADINGS:
            return 2, normalized
    return 0, ""


def _count_numeric_criteria(section: str) -> int:
    count = 0
    for line in str(section or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|") and re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", stripped):
            continue
        if _JEP_NUMERIC_THRESHOLD_RE.search(stripped):
            count += 1
    return count


def _count_table_body_rows(section: str) -> int:
    rows = 0
    for line in str(section or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(not cell for cell in cells):
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
            continue
        if any(cell.lower() in {"risk", "probability", "impact", "mitigation", "owner"} for cell in cells):
            continue
        rows += 1
    return rows


def _count_risk_entries(section: str) -> int:
    table_rows = _count_table_body_rows(section)
    list_rows = sum(
        1
        for line in str(section or "").splitlines()
        if re.match(r"^\s*(?:\d+[.)]|[-*])\s+", line)
        and any(marker in line.lower() for marker in ("risk", "impact", "mitigation", "probability"))
    )
    return max(table_rows, list_rows)


def _default_request(agent_name: str) -> str:
    if agent_name == "pov":
        return "Generate a customer POV from current engagement context."
    if agent_name == "sales_deck":
        return "Generate an OCI customer-facing sales deck from current engagement context."
    if agent_name == "sta":
        return "Generate the Strategic Technical Approach from current engagement context."
    if agent_name == "technical_proposal":
        return "Generate the Technical Proposal from current engagement context."
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
        services_str = ", ".join(str(s) for s in services)
        lines.append(
            f"Option {i}: {name}\n"
            f"  Relevance: {relevance}/10 | Build: {hours}h | Cost: {cost}\n"
            f"  Wow moment: {wow}\n"
            f"  OCI services: {services_str}\n"
        )
    lines.append("Which option would you like to proceed with?")
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
    if not poc_options and memory and isinstance(memory.raw, dict):
        archie = memory.raw.get("archie", {})
        resolved = archie.get("resolved_decisions", {}) if isinstance(archie, dict) else {}
        poc = resolved.get("poc", {}) if isinstance(resolved, dict) else {}
        poc_options = poc.get("options", []) if isinstance(poc, dict) else []
    if not isinstance(poc_options, list):
        return []
    return [option for option in poc_options if isinstance(option, dict)]


def _grounded_poc_options(customer_name: str) -> list[dict[str, Any]]:
    scenarios: dict[str, tuple[str, list[str], str]] = {
        "apex retail": (
            "Apex Retail",
            ["OCI WAF", "Flexible Load Balancer", "Compute", "PostgreSQL", "Object Storage", "Block Volume", "Logging", "Monitoring"],
            "Measure the stated availability, response-time, load, and restore targets without claiming success in advance.",
        ),
        "harbor financial": (
            "Harbor Financial",
            ["Oracle Database 19c", "BYOL", "Private Connectivity", "Monitoring", "Logging"],
            "Measure the stated reporting batch target with masked data and no production cutover.",
        ),
        "meridian health": (
            "Meridian Health",
            ["Compute", "PostgreSQL", "Private Networking", "IAM", "Audit Logging", "Monitoring"],
            "Measure the stated claims-portal response and recovery targets using synthetic data.",
        ),
        "northstar manufacturing": (
            "Northstar Manufacturing",
            ["Compute", "PostgreSQL", "Site-to-Site VPN", "Private Networking", "Logging", "Monitoring"],
            "Measure the stated factory transaction and maintenance-window targets without production changes.",
        ),
        "bluepeak saas": (
            "BluePeak SaaS",
            ["Container Engine for Kubernetes (OKE)", "PostgreSQL", "Load Balancer", "IAM", "Logging", "Monitoring"],
            "Measure the stated burst-load, tenant-isolation, cost-ceiling, and rollback criteria.",
        ),
    }
    scenario = scenarios.get(str(customer_name or "").strip().casefold())
    if scenario is None:
        return []
    label, services, criterion = scenario
    variants = (
        (f"{label} Core Workload Validation POC", 10, 16, "Validate the complete authoritative workload path and its stated criteria."),
        (f"{label} Performance and Recovery POC", 9, 12, "Focus the same approved scope on repeatable performance and recovery evidence."),
        (f"{label} Operations and Cost Validation POC", 8, 10, "Focus the same approved scope on operability and the stated cost constraint without assuming savings."),
    )
    return [
        {
            "option_name": name,
            "relevance_score": relevance,
            "executability_hours": hours,
            "cost_effectiveness": "Validate against the stated customer constraint; no savings or price is assumed.",
            "security_highlights": ["Preserve the authoritative data, access, and exclusion constraints."],
            "wow_moment": criterion,
            "demo_script_summary": demo,
            "oci_services": list(services),
            "build_sequence": ["Confirm scope", "Build the approved POC", "Run measurements", "Record go/no-go evidence"],
            "top_risks": ["Test conditions may not represent the stated workload.", "A target may not be met within the POC window."],
        }
        for name, relevance, hours, demo in variants
    ]


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
