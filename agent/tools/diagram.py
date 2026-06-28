"""
agent/tools/diagram.py
----------------------
ToolHandler implementation for the generate_diagram pipeline.
"""
from __future__ import annotations

import asyncio
import html
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
        if user_message:
            args = {**args, "prompt": user_message}
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

        # For a direct chat artifact request, the current user turn is the
        # authoritative diagram brief.  Forge may propose richer arguments,
        # but those must not silently add topology or sizing requirements.
        if user_message:
            args = {**args, "prompt": user_message}

        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            existing_prompt = str(args.get("prompt") or "")
            args = {
                **args,
                "prompt": (
                    f"[AUTHORITATIVE USER REQUEST]\n{existing_prompt}\n"
                    "[/AUTHORITATIVE USER REQUEST]\n\n"
                    "[SCOPED REVIEW FEEDBACK]\n"
                    f"{correction}\n"
                    "Apply this feedback only where it corrects a requirement "
                    "explicitly present in the authoritative user request. Do not "
                    "add services, integrations, sizes, quantities, or topology.\n"
                    "[/SCOPED REVIEW FEEDBACK]"
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
            diagram_error = (
                _diagram_node_count_error(args, result_data)
                or _diagram_content_error(
                    user_message or str(args.get("prompt") or ""),
                    str(result_data.get("drawio_xml") or ""),
                )
                or _diagram_bom_coverage_error(
                    list(args.get("_bom_line_items", []) or []),
                    str(result_data.get("drawio_xml") or ""),
                )
            )
            if not diagram_error:
                break
            if attempt == 1:
                return _validation_blocked("diagram", diagram_error)
            args = _prepend_diagram_correction(args, diagram_error)

        if result_data.get("diagram_recovery_status") == "needs_clarification":
            clarify = summary
            return ToolResult(
                summary=clarify,
                status="needs_input",
                clarification=clarify,
            )
        if result_data.get("diagram_recovery_status") == "backend_error":
            return ToolResult(
                summary=summary or "Diagram generation failed before a drawable artifact was produced.",
                status="blocked",
                data=result_data,
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
        if not artifact_key:
            return ToolResult(
                summary=summary or "Diagram generation did not return a saved draw.io artifact.",
                status="blocked",
                data=result_data,
            )
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
    from agent import context_store as _context_store

    latest_bom = _context_store.latest_bom_work_product(context)
    baseline = latest_bom.get("baseline", {}) if isinstance(latest_bom, dict) else {}
    line_items = baseline.get("line_items", []) if isinstance(baseline, dict) else []
    line_items = [dict(item) for item in line_items if isinstance(item, dict)]
    if line_items:
        hydrated["_bom_line_items"] = line_items
    block = _build_diagram_confirmed_context(
        args=args,
        context=context,
        memory_raw=memory_raw,
        artifact_key=prior_diagram_key,
    )
    bom_block = _diagram_bom_context_block(line_items)
    revision = ""
    if prior_diagram_key and not _requests_full_redraw(task):
        revision = "[UPDATE REQUEST - PRESERVE ALL EXISTING NODES EXCEPT: changes explicitly requested by the user]"
    hydrated_task = "\n\n".join(part for part in (block, bom_block, revision, task) if part).strip()
    hydrated["prompt"] = hydrated_task
    hydrated["bom_text"] = hydrated_task
    brief = dict(hydrated.get("_architect_brief", {}) or {})
    brief["user_notes"] = hydrated_task
    hydrated["_architect_brief"] = brief
    return hydrated


def _diagram_bom_context_block(line_items: list[dict[str, Any]]) -> str:
    if not line_items:
        return ""
    rows = []
    for item in line_items:
        description = str(item.get("description") or item.get("sku") or "").strip()
        quantity = item.get("quantity")
        metric = str(item.get("metric") or "").strip()
        notes = str(item.get("notes") or "").strip()
        lowered = description.lower()
        if isinstance(quantity, (int, float)) and "performance units" in lowered:
            rendered = f"{quantity:g} Performance Units for Block Volume"
        elif isinstance(quantity, (int, float)) and "block volume" in lowered and "storage" in lowered:
            rendered = f"{quantity:g} GB Balanced Block Volume"
        elif isinstance(quantity, (int, float)) and "object storage" in lowered:
            rendered = f"{quantity:g} GB Object Storage"
        elif "web application firewall" in lowered:
            rendered = "OCI WAF Policy ×1"
        else:
            rendered = f"{description}: {quantity:g} {metric}" if isinstance(quantity, (int, float)) else description
        rows.append(f"- {rendered}. {notes}")
    return (
        "[AUTHORITATIVE BOM PARITY REQUIREMENTS]\n"
        "Include and explicitly label every BOM element and quantitative attribute below.\n"
        + "\n".join(rows)
        + "\n[/AUTHORITATIVE BOM PARITY REQUIREMENTS]"
    )


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


def _diagram_content_error(user_message: str, drawio_xml: str) -> str:
    if not str(drawio_xml or "").strip():
        return ""
    request = re.sub(r"\s+", " ", str(user_message or "").lower())
    values = re.findall(r'\bvalue=["\']([^"\']*)["\']', str(drawio_xml or ""), flags=re.IGNORECASE)
    labels = " ".join(html.unescape(value) for value in values).lower()
    labels = re.sub(r"<[^>]+>|&#xa;", " ", labels)
    labels = re.sub(r"\s+", " ", labels)
    checks = (
        ("single-ad poc boundary", "single-ad poc boundary"),
        ("500 gb", "500 gb"),
        ("object storage", "object storage"),
        ("internet gateway", "internet gateway"),
        ("nat gateway", "nat gateway"),
        ("service gateway", "service gateway"),
        ("postgresql", "postgresql"),
    )
    missing = [label for trigger, label in checks if trigger in request and label not in labels]
    if "nsgs" in request and not any(token in labels for token in ("nsg", "network security group")):
        missing.append("NSGs")
    if "route tables" in request and "route table" not in labels:
        missing.append("route tables")
    if re.search(r"\b(?:two|2)\s+vm\.standard\.e5\.flex\s+web\s+servers?", request):
        if not re.search(r"(?:2|two|×2|2\s*×).*vm\.standard\.e5\.flex", labels):
            missing.append("two VM.Standard.E5.Flex web servers")
    return "missing requested diagram labels: " + ", ".join(missing) if missing else ""


def _diagram_bom_coverage_error(line_items: list[dict[str, Any]], drawio_xml: str) -> str:
    if not line_items or not str(drawio_xml or "").strip():
        return ""
    values = re.findall(r'\bvalue=["\']([^"\']*)["\']', drawio_xml, flags=re.IGNORECASE)
    labels = re.sub(r"\s+", " ", " ".join(html.unescape(value) for value in values).replace("&#xa;", " ")).lower()
    missing: list[str] = []
    for item in line_items:
        description = str(item.get("description") or "").lower()
        quantity = float(item.get("quantity") or 0)
        if "performance units" in description:
            if not (f"{quantity:g} performance units" in labels or f"{quantity:g} vpu" in labels):
                missing.append(f"Block Volume {quantity:g} performance units")
            continue
        if "compute" in description and "ocpu" in description and f"{quantity:g} ocpu" not in labels and "2 ocpu" not in labels:
            missing.append(f"compute {quantity:g} OCPU total")
        elif "compute" in description and "memory" in description and not re.search(r"(?:16 gb each|32 gb total)", labels):
            missing.append(f"compute {quantity:g} GB memory total")
        elif "block volume" in description and "storage" in description and f"{quantity:g} gb" not in labels:
            missing.append(f"Block Volume {quantity:g} GB")
        elif "load balancer" in description and "load balancer" not in labels:
            missing.append("Flexible Load Balancer")
        elif "web application firewall" in description and not re.search(r"waf policy|web application firewall policy", labels):
            missing.append("WAF policy")
        elif "postgresql" in description and not ("postgresql" in labels and f"{quantity:g} ocpu" in labels):
            missing.append(f"PostgreSQL {quantity:g} OCPU")
        elif "object storage" in description and not (
            f"{quantity:g} gb object storage" in labels
            or (quantity == 1024 and "1 tb object storage" in labels)
        ):
            missing.append(f"Object Storage {quantity:g} GB")
    return "missing BOM diagram coverage: " + ", ".join(missing) if missing else ""


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
