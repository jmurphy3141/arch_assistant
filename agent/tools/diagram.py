"""
agent/tools/diagram.py
----------------------
ToolHandler implementation for the generate_diagram pipeline.
"""
from __future__ import annotations

import asyncio
import json
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


def _summarise_drawio(xml: str) -> str:
    """Return a brief service-inventory string parsed from drawio XML."""
    categories: dict[str, int] = {}
    for m in re.finditer(r'shape=mxgraph\.oci2\.(\w+)', xml):
        cat = m.group(1).lower()
        categories[cat] = categories.get(cat, 0) + 1
    if not categories:
        return ""
    total = sum(categories.values())
    parts = [f"{cat}×{n}" for cat, n in sorted(categories.items())]
    return f"{total} nodes: {', '.join(parts)}"


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
        from agent.archie_session import _call_generate_diagram

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

        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            existing_prompt = str(args.get("prompt") or "")
            args = {
                **args,
                "prompt": (
                    f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n"
                    f"{existing_prompt}"
                ).strip(),
            }
        args = _hydrate_diagram_args(
            args,
            context=ctx,
            memory_raw=memory.raw if memory else {},
            customer_id=self._customer_id,
            prior_diagram_key=prior_diagram_key,
            trace_id=trace_id,
        )

        summary = ""
        artifact_key = ""
        result_data: dict[str, Any] = {}
        for attempt in range(2):
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
            validation_error = _validate_diagram_result(result_data)
            if validation_error:
                return _validation_blocked("diagram", validation_error)
            node_error = _diagram_node_count_error(args, result_data)
            if not node_error:
                break
            if attempt == 1:
                return _validation_blocked("diagram", node_error)
            args = _prepend_diagram_correction(args, node_error)

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
        xml = result_data.get("drawio_xml") or ""
        inventory = _summarise_drawio(xml) if xml else ""
        full_summary = f"{summary} ({inventory})" if inventory else summary

        from agent import context_store as _cs
        _cs.set_resolved_decisions(context, topology={
            "ha_mode": str(args.get("ha_dr_mode") or args.get("ha_mode") or "single-AD"),
            "subnet_tiers": args.get("subnet_tiers") or args.get("tiers") or [],
            "gateways": args.get("gateways") or [],
            "diagram_key": artifact_key or "",
        })

        return ToolResult(
            summary=full_summary,
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


def _hydrate_diagram_args(
    args: dict[str, Any],
    *,
    context: dict[str, Any],
    memory_raw: dict[str, Any],
    customer_id: str,
    prior_diagram_key: str,
    trace_id: str,
) -> dict[str, Any]:
    hydrated = dict(args)
    diagram_name = str(
        hydrated.get("diagram_name")
        or _find_context_value(("diagram_name", "name"), args=args, context=context, memory_raw=memory_raw)
        or trace_id
        or "diagram"
    )
    hydrated["diagram_name"] = _safe_diagram_name(diagram_name, "diagram")
    hydrated["customer_id"] = customer_id
    task = str(hydrated.get("prompt") or hydrated.get("bom_text") or "")
    block = _build_diagram_confirmed_context(
        args=args,
        context=context,
        memory_raw=memory_raw,
        artifact_key=prior_diagram_key,
    )
    revision = ""
    if prior_diagram_key and not _requests_full_redraw(task):
        revision = "[UPDATE REQUEST - PRESERVE ALL EXISTING NODES EXCEPT: changes explicitly requested by the user]"
    hydrated_task = "\n\n".join(part for part in (block, revision, task) if part).strip()
    hydrated["prompt"] = hydrated_task
    hydrated["bom_text"] = hydrated_task
    brief = dict(hydrated.get("_architect_brief", {}) or {})
    brief["user_notes"] = hydrated_task
    hydrated["_architect_brief"] = brief
    return hydrated


def _build_diagram_confirmed_context(
    *,
    args: dict[str, Any],
    context: dict[str, Any],
    memory_raw: dict[str, Any],
    artifact_key: str,
) -> str:
    value = lambda *keys, default="not stated": _context_value_or_default(  # noqa: E731
        keys,
        default=default,
        args=args,
        context=context,
        memory_raw=memory_raw,
    )
    return "\n".join(
        [
            "[CONFIRMED CONTEXT]",
            f"Existing artifact: {artifact_key or 'none'}",
            f"Components: {value('components', 'oci_services_in_scope')}",
            f"Topology: {value('topology')}",
            f"Subnet tiers: {value('subnet_tiers', 'tiers')}",
            f"Instance counts: {value('instance_counts', 'instance_count', 'node_count')}",
            "[/CONFIRMED CONTEXT]",
        ]
    )


def _requests_full_redraw(task: str) -> bool:
    text = str(task or "").lower()
    return any(phrase in text for phrase in ("full redraw", "full regeneration", "regenerate from scratch"))


def _validate_diagram_result(result_data: dict[str, Any]) -> str:
    layout_intent = result_data.get("layout_intent") or result_data.get("LayoutIntent")
    if layout_intent is None:
        return ""
    if isinstance(layout_intent, str):
        try:
            layout_intent = json.loads(layout_intent)
        except json.JSONDecodeError as exc:
            return f"layout_intent invalid JSON: {exc}"
    if not isinstance(layout_intent, dict):
        return "layout_intent is not an object"
    nodes = layout_intent.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return "nodes is empty"
    for node in nodes:
        if not isinstance(node, dict):
            return f"node is not an object: {node}"
        if "oci_type" not in node:
            return f"node missing oci_type: {node}"
        if "label" not in node:
            return f"node missing label: {node}"
        if "subnet_tier" not in node:
            return f"node missing subnet_tier: {node}"
    tiers = layout_intent.get("subnet_tiers")
    if not isinstance(tiers, list) or not tiers:
        return "subnet_tiers is empty"
    return ""


def _diagram_node_count_error(args: dict[str, Any], result_data: dict[str, Any]) -> str:
    expected = _requested_service_type_count(args)
    actual = _actual_diagram_node_count(result_data)
    if expected > 0 and actual > 0 and actual < expected:
        return f"node_count too low: expected at least {expected}, got {actual}"
    return ""


def _requested_service_type_count(args: dict[str, Any]) -> int:
    found: set[str] = set()
    known = {
        "compute",
        "database",
        "load balancer",
        "waf",
        "object storage",
        "file storage",
        "bastion",
        "nat gateway",
        "service gateway",
        "internet gateway",
        "oke",
    }
    for value in (args.get("components"), args.get("oci_services_in_scope"), args.get("prompt"), args.get("bom_text")):
        if isinstance(value, (list, tuple, set)):
            found.update(str(item).strip().lower() for item in value if str(item).strip())
        else:
            text = str(value or "").lower()
            for service in known:
                if service in text:
                    found.add(service)
    return len(found)


def _actual_diagram_node_count(result_data: dict[str, Any]) -> int:
    for path in (("node_count",), ("render_manifest", "node_count"), ("trace", "render_manifest", "node_count")):
        value: Any = result_data
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        try:
            if int(value or 0) > 0:
                return int(value)
        except (TypeError, ValueError):
            pass
    return 0


def _prepend_diagram_correction(args: dict[str, Any], correction: str) -> dict[str, Any]:
    hydrated = dict(args)
    prompt = (
        "[CORRECTION FROM PYTHON VALIDATION]\n"
        f"{correction}. Add all distinct requested OCI service types and return the diagram again.\n\n"
        f"{hydrated.get('prompt') or ''}"
    ).strip()
    hydrated["prompt"] = prompt
    hydrated["bom_text"] = prompt
    brief = dict(hydrated.get("_architect_brief", {}) or {})
    brief["user_notes"] = prompt
    hydrated["_architect_brief"] = brief
    return hydrated


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
            for key in ("facts", "requirements", "topology", "agents", "diagram"):
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
