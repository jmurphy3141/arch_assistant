"""Tool handler for Oracle-standard PowerPoint presentation generation."""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import Any

from agent import document_store, sub_agent_client
from agent.persistence_objectstore import ObjectStoreBase
from agent.sub_agent_client import SubAgentError
from skillforge.types import MemorySnapshot, ToolResult


PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class PresentationHandler:
    def __init__(self, store: ObjectStoreBase, customer_id: str, customer_name: str):
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
        decision_context = memory.decision_context if memory else {}
        poc_option = args.get("poc_option") or decision_context.get("poc_recommendation")
        if not isinstance(poc_option, dict) or not poc_option:
            clarification = "NEEDS_CLARIFICATION: No POC has been planned yet. Run generate_poc_plan first."
            return ToolResult(
                status="needs_input",
                summary=clarification,
                clarification=clarification,
            )

        customer_name = (
            self._customer_name
            or str(context.get("customer_name") or "").strip()
            or str(decision_context.get("customer_name") or "").strip()
        )
        if not customer_name:
            clarification = "NEEDS_CLARIFICATION: What is the customer's name?"
            return ToolResult(
                status="needs_input",
                summary=clarification,
                clarification=clarification,
            )

        task_payload = {
            "poc_name": poc_option.get("option_name") or poc_option.get("poc_name") or "OCI POC",
            "customer_name": customer_name,
            "pain_statement": decision_context.get("pain_statement", ""),
            "oci_services": poc_option.get("oci_services", []),
            "bom_summary": decision_context.get("bom_summary", ""),
            "bom_rows": decision_context.get("bom_rows", []),
            "jep_phases": decision_context.get("jep_phases", []),
        }

        try:
            response = await sub_agent_client.call_sub_agent(
                "presentation",
                task=f"Generate POC deck for {customer_name}",
                engagement_context=task_payload,
                trace_id=trace_id,
            )
        except SubAgentError as exc:
            return ToolResult(
                status="blocked",
                summary=f"Presentation sub-agent error: {exc}",
            )

        status = str(response.get("status") or "").lower()
        if status != "ok":
            return ToolResult(
                status="error",
                summary=f"Presentation generation failed: {response.get('result')}",
                data={"response": response},
            )

        try:
            pptx_bytes = base64.b64decode(str(response.get("result") or ""), validate=True)
        except Exception as exc:
            return ToolResult(
                status="error",
                summary=f"Presentation generation returned invalid PPTX bytes: {exc}",
                data={"response_status": response.get("status")},
            )

        saved = await asyncio.to_thread(
            _save_pptx_doc,
            self._store,
            self._customer_id,
            pptx_bytes,
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )

        return ToolResult(
            status="ok",
            summary=f"PowerPoint deck generated: {task_payload['poc_name']}",
            artifact_key=str(saved.get("key", "") or ""),
            data={"artifact_key": saved.get("key"), "trace": response.get("trace", {})},
        )


def _save_pptx_doc(
    store: ObjectStoreBase,
    customer_id: str,
    content: bytes,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    version = document_store._get_next_version(store, "presentation", customer_id)
    version_key = document_store._doc_key("presentation", customer_id, f"v{version}.pptx", customer_first=False)
    version_customer_key = document_store._doc_key("presentation", customer_id, f"v{version}.pptx", customer_first=True)
    latest_key = document_store._doc_key("presentation", customer_id, "LATEST.pptx", customer_first=False)
    latest_customer_key = document_store._doc_key("presentation", customer_id, "LATEST.pptx", customer_first=True)
    manifest_key = document_store._doc_key("presentation", customer_id, "MANIFEST.json", customer_first=False)
    manifest_customer_key = document_store._doc_key("presentation", customer_id, "MANIFEST.json", customer_first=True)

    document_store._put_dual(
        store,
        customer_key=version_customer_key,
        legacy_key=version_key,
        content=content,
        content_type=PPTX_CONTENT_TYPE,
    )
    document_store._put_dual(
        store,
        customer_key=latest_customer_key,
        legacy_key=latest_key,
        content=content,
        content_type=PPTX_CONTENT_TYPE,
    )

    manifest = document_store._get_first_json(
        store,
        [manifest_customer_key, manifest_key],
        {"versions": []},
    )
    manifest["versions"].append(
        {
            "version": version,
            "key": version_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
    )
    document_store._put_dual(
        store,
        customer_key=manifest_customer_key,
        legacy_key=manifest_key,
        content=json.dumps(manifest, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    return {"version": version, "key": version_key, "latest_key": latest_key}
