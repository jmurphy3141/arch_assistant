"""
agent/tools/specialists.py
--------------------------
ToolHandler implementations for POV, JEP, and WAF specialist sub-agents.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from agent import archie_memory, document_store, sub_agent_client
from agent.persistence_objectstore import ObjectStoreBase
from agent.sub_agent_client import SubAgentError
from skillforge.types import MemorySnapshot, ToolResult


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

        try:
            response = await sub_agent_client.call_sub_agent(
                self._agent_name,
                task=raw_request,
                engagement_context={
                    "customer_id": self._customer_id,
                    "customer_name": self._customer_name,
                    "feedback": feedback,
                    "architect_brief": dict(args.get("_architect_brief", {}) or {}),
                },
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


def _default_request(agent_name: str) -> str:
    if agent_name == "pov":
        return "Generate a customer POV from current engagement context."
    if agent_name == "sales_deck":
        return "Generate an OCI customer-facing sales deck from current engagement context."
    return f"Generate the {agent_name.upper()} from current engagement context."


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
