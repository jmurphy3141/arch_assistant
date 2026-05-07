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
