"""
agent/tools/diagram.py
----------------------
ToolHandler implementation for the generate_diagram pipeline.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from agent import archie_memory
from agent.persistence_objectstore import ObjectStoreBase, persist_artifacts
from skillforge.types import MemorySnapshot, ToolResult


def _safe_diagram_name(value: Any, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    if name:
        return name
    fallback_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(fallback or "").strip()).strip("._")
    return fallback_name or "diagram"


def _ensure_drawio_mxfile(drawio_xml: str, *, diagram_name: str = "OCI Architecture") -> str:
    xml = str(drawio_xml or "").strip()
    if not xml:
        return ""
    lowered = xml.lower()
    if "<mxfile" in lowered:
        return xml
    if "<mxgraphmodel" in lowered:
        safe_name = (
            str(diagram_name or "OCI Architecture")
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f'<mxfile host="app.diagrams.net"><diagram name="{safe_name}">{xml}</diagram></mxfile>'
    return xml


class DiagramHandler:
    def __init__(
        self,
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
        text_runner: Any,
        a2a_base_url: str = "",
    ) -> None:
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name
        self._text_runner = text_runner
        self._a2a_base_url = a2a_base_url

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        from agent.archie_loop import _call_generate_diagram

        ctx = context
        decision_context = memory.decision_context if memory else {}
        user_message = str(args.get("_user_message", "") or "")
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name="generate_diagram",
            args=args,
            context=ctx,
            decision_context=decision_context,
            user_message=user_message,
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name="generate_diagram",
            args=args,
            context=ctx,
        )
        prior_diagram_key = _prior_diagram_key(memory=memory, context=ctx)
        if prior_diagram_key:
            args = {
                **args,
                "prior_diagram_key": prior_diagram_key,
                "drawio_key": prior_diagram_key,
            }

        if ctx and not archie_memory._diagram_has_sufficient_context(
            context=ctx,
            args=args,
            user_message=user_message,
        ):
            message = (
                "Please upload or paste BOM/resource details first, or describe "
                "the workload/components you want in the diagram."
            )
            return ToolResult(
                summary=message,
                status="needs_input",
                clarification=message,
            )

        try:
            summary, artifact_key, result_data = await _call_generate_diagram(
                args=args,
                customer_id=self._customer_id,
                a2a_base_url=self._a2a_base_url,
            )
        except Exception as exc:
            return ToolResult(
                summary=f"Diagram generation failed: {exc}",
                status="blocked",
            )

        if result_data.get("diagram_recovery_status") == "needs_clarification":
            clarify = summary
            return ToolResult(
                summary=clarify,
                status="needs_input",
                clarification=clarify,
            )
        if not artifact_key:
            drawio_xml = str(result_data.get("drawio_xml") or "")
            if drawio_xml.strip():
                diagram_name = _safe_diagram_name(
                    result_data.get("diagram_name") or args.get("diagram_name"),
                    trace_id or "diagram",
                )
                drawio_xml = _ensure_drawio_mxfile(drawio_xml, diagram_name=diagram_name)
                result_data["drawio_xml"] = drawio_xml
                latest = await asyncio.to_thread(
                    persist_artifacts,
                    self._store,
                    "agent3",
                    self._customer_id,
                    diagram_name,
                    {"diagram.drawio": drawio_xml.encode("utf-8")},
                )
                if latest:
                    artifact_key = str(
                        (latest.get("artifacts", {}) or {}).get("diagram.drawio")
                        or ""
                    )
                    result_data["diagram_name"] = diagram_name
                    result_data["drawio_key"] = artifact_key
                    result_data["object_key"] = artifact_key
                    summary = f"Diagram generated. Key: {artifact_key}"
        return ToolResult(
            summary=summary,
            status="ok",
            artifact_key=artifact_key,
            data=result_data,
        )


def _prior_diagram_key(
    *,
    memory: MemorySnapshot | None,
    context: dict[str, Any],
) -> str:
    if memory:
        prior_artifacts = memory.prior_artifacts or {}
        value = prior_artifacts.get("generate_diagram", "")
        if isinstance(value, str) and value.strip():
            return value.strip()

    agents = context.get("agents", {}) if isinstance(context, dict) else {}
    if not isinstance(agents, dict):
        return ""
    diagram_ctx = agents.get("diagram", {})
    if not isinstance(diagram_ctx, dict):
        return ""
    value = diagram_ctx.get("diagram_key", "")
    return value.strip() if isinstance(value, str) and value.strip() else ""
