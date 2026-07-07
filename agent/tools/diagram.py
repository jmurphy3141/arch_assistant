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

from agent import archie_memory, consistency_contract
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
        conflicts = consistency_contract.request_conflicts(user_message, ctx)
        if conflicts:
            clarification = (
                "The Diagram request conflicts with an approved upstream decision. "
                "Confirm an impact update before changing dependent artifacts."
            )
            return ToolResult(
                summary=clarification,
                status="needs_input",
                clarification=clarification,
                data={
                    "consistency_conflicts": conflicts,
                    "required_next_step": "confirm update all",
                },
            )
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
            if (
                artifact_key
                and args.get("_consistency_requirements")
                and not str(result_data.get("drawio_xml") or "").strip()
            ):
                try:
                    result_data["drawio_xml"] = self._store.get(artifact_key).decode("utf-8")
                except Exception as exc:
                    return ToolResult(
                        summary=f"Diagram consistency validation could not read the persisted artifact: {exc}",
                        status="blocked",
                        data={
                            "validation_stage": "cross_artifact_artifact_read",
                            "artifact_key": artifact_key,
                        },
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
                or _diagram_consistency_error(
                    list(args.get("_consistency_requirements", []) or []),
                    str(args.get("_consistency_ha_mode") or ""),
                    str(result_data.get("drawio_xml") or ""),
                    expected_region=str(args.get("_consistency_region") or ""),
                    required_private_tiers=list(args.get("_required_private_tiers", []) or []),
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
        contract_report = _diagram_consistency_report(
            list(args.get("_consistency_requirements", []) or []),
            str(args.get("_consistency_ha_mode") or ""),
            str(xml or ""),
            expected_region=str(args.get("_consistency_region") or ""),
            required_private_tiers=list(args.get("_required_private_tiers", []) or []),
        )
        consistency_contract.record_final_diagram(
            context,
            artifact_key=artifact_key,
            represented_components=list(contract_report.get("represented_components", []) or []),
            ha_mode=str(args.get("_consistency_ha_mode") or args.get("ha_dr_mode") or ""),
        )
        result_data["consistency_report"] = contract_report
        result_data["consistency_contract_revision"] = int(
            consistency_contract.get_contract(context).get("revision", 0) or 0
        )

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


_SIZING_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:gb|gib|tb|tib|ocpu|vcpu)s?\b", re.IGNORECASE
)


def _request_declares_own_sizing(task: str) -> bool:
    """True when the request already states its own explicit sizing.

    A request that names its own compute/memory figures (e.g. "~371.3 GiB
    memory", "37.3 vCPU") is a fresh, self-contained architecture — its own
    numbers are authoritative and must not be overridden by an unrelated
    prior BOM that happens to share the same customer_id/engagement.
    Require at least two distinct sizing mentions so an incidental one-off
    number doesn't disable the parity check on a request that is genuinely
    meant to match an existing BOM.
    """
    return len(_SIZING_UNIT_RE.findall(str(task or ""))) >= 2


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
    if line_items and not _request_declares_own_sizing(task):
        hydrated["_bom_line_items"] = line_items
    scope_items = baseline.get("scope_items", []) if isinstance(baseline, dict) else []
    scope_items = [dict(item) for item in scope_items if isinstance(item, dict)]
    contract = consistency_contract.get_contract(context)
    required_private_tiers = _required_private_tiers(args, context, memory_raw)
    if required_private_tiers:
        hydrated["_required_private_tiers"] = required_private_tiers
    requirements = consistency_contract.required_components(context)
    scope_by_id = {
        str(item.get("canonical_service_id") or ""): item
        for item in scope_items
        if item.get("canonical_service_id")
    }
    for requirement in requirements:
        scope = scope_by_id.get(str(requirement.get("service_id") or ""))
        if scope and isinstance(scope.get("assumed_sizing"), dict):
            requirement["assumed_sizing"] = dict(scope["assumed_sizing"])
    if requirements:
        hydrated["_consistency_requirements"] = requirements
        hydrated["_consistency_ha_mode"] = str(contract.get("ha_mode") or "")
        hydrated["_consistency_region"] = str(contract.get("region") or "")
    block = _build_diagram_confirmed_context(
        args=args,
        context=context,
        memory_raw=memory_raw,
        artifact_key=prior_diagram_key,
    )
    bom_block = _diagram_bom_context_block(line_items, scope_items=scope_items)
    contract_block = ""
    if requirements:
        contract_block = (
            "[AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
            "Include and label every component exactly; preserve database and connectivity identity.\n"
            f"HA mode: {contract.get('ha_mode') or 'not stated'}\n"
            + "\n".join(
                f"- {item.get('display_name')} ({item.get('service_id')})"
                for item in requirements
            )
            + "\n[/AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]"
        )
    tier_block = ""
    if required_private_tiers:
        tier_block = (
            "[REQUIRED PRIVATE TIERS]\n"
            "Render one distinct private subnet for each tier below; do not merge tiers.\n"
            + "\n".join(f"- {tier}" for tier in required_private_tiers)
            + "\n[/REQUIRED PRIVATE TIERS]"
        )
    revision = ""
    if prior_diagram_key and not _requests_full_redraw(task):
        revision = "[UPDATE REQUEST - PRESERVE ALL EXISTING NODES EXCEPT: changes explicitly requested by the user]"
    hydrated_task = "\n\n".join(
        part for part in (block, contract_block, tier_block, bom_block, revision, task) if part
    ).strip()
    hydrated["prompt"] = hydrated_task
    hydrated["bom_text"] = hydrated_task
    brief = dict(hydrated.get("_architect_brief", {}) or {})
    brief["user_notes"] = hydrated_task
    hydrated["_architect_brief"] = brief
    return hydrated


def _required_private_tiers(*sources: Any) -> list[str]:
    """Extract explicit private tier separation from engagement scope."""
    text = " ".join(json.dumps(source, default=str) for source in sources).lower()
    explicit: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("subnet_tiers", "tiers"):
            values = source.get(key)
            if isinstance(values, list):
                explicit.extend(str(value).lower() for value in values)
    if explicit:
        return list(dict.fromkeys(
            "application" if "app" in value else "database" if "db" in value or "data" in value else "web"
            for value in explicit
            if any(token in value for token in ("web", "app", "database", "db", "data"))
        ))
    separation = any(phrase in text for phrase in (
        "separate private subnet", "distinct private subnet", "three private tiers",
        "separate private tiers", "three separate private subnet",
    ))
    if not separation:
        return []
    tiers = []
    if "web" in text or "iis" in text or "apache" in text or "storefront" in text:
        tiers.append("web")
    if "application" in text or " app " in text or "claims-service" in text or "claims application" in text:
        tiers.append("application")
    if "database" in text or "postgresql" in text or "oracle" in text:
        tiers.append("database")
    return tiers


def _diagram_bom_context_block(
    line_items: list[dict[str, Any]],
    *,
    scope_items: list[dict[str, Any]] | None = None,
) -> str:
    scope_items = scope_items or []
    if not line_items and not scope_items:
        return ""
    rows = []
    for item in line_items:
        description = str(item.get("description") or item.get("sku") or "").strip()
        quantity = item.get("quantity")
        metric = str(item.get("metric") or "").strip()
        notes = str(item.get("notes") or "").strip()
        lowered = description.lower()
        is_compute = (
            str(item.get("canonical_service_id") or "").startswith("compute.")
            or str(item.get("sku") or "").upper() in {
                "B93113", "B97384", "B111129", "B94176", "B93297",
                "B93114", "B97385", "B111130", "B94177", "B93298",
            }
            or "compute" in lowered
        )
        if isinstance(quantity, (int, float)) and "performance units" in lowered:
            rendered = f"{quantity:g} Performance Units for Block Volume"
        elif isinstance(quantity, (int, float)) and is_compute and "ocpu" in lowered:
            count = int(item.get("instance_count") or 1)
            rendered = (
                f"{description}: {quantity:g} OCPU total across {count} instances "
                f"({quantity / max(count, 1):g} OCPU each)"
            )
        elif isinstance(quantity, (int, float)) and is_compute and "memory" in lowered:
            count = int(item.get("instance_count") or 1)
            rendered = (
                f"{description}: {quantity:g} GB memory total across {count} instances "
                f"({quantity / max(count, 1):g} GB each)"
            )
        elif isinstance(quantity, (int, float)) and "block volume" in lowered and "storage" in lowered:
            rendered = f"{quantity:g} GB Balanced Block Volume"
        elif isinstance(quantity, (int, float)) and "object storage" in lowered:
            rendered = f"{quantity:g} GB Object Storage"
        elif isinstance(quantity, (int, float)) and "file storage" in lowered:
            rendered = f"{quantity:g} GB File Storage"
        elif "web application firewall" in lowered:
            rendered = "OCI WAF Policy ×1"
        else:
            rendered = f"{description}: {quantity:g} {metric}" if isinstance(quantity, (int, float)) else description
        rows.append(f"- {rendered}. {notes}")
    for item in scope_items:
        description = str(item.get("description") or item.get("canonical_service_id") or "").strip()
        sizing = item.get("assumed_sizing") if isinstance(item.get("assumed_sizing"), dict) else {}
        sizing_text = json.dumps(sizing, sort_keys=True) if sizing else "no quantitative sizing"
        rows.append(f"- {description}: {sizing_text}. Scope coverage from selected POC.")
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
        is_compute = (
            str(item.get("canonical_service_id") or "").startswith("compute.")
            or str(item.get("sku") or "").upper() in {
                "B93113", "B97384", "B111129", "B94176", "B93297",
                "B93114", "B97385", "B111130", "B94177", "B93298",
            }
            or "compute" in description
        )
        if "performance units" in description:
            if not (f"{quantity:g} performance units" in labels or f"{quantity:g} vpu" in labels):
                missing.append(f"Block Volume {quantity:g} performance units")
            continue
        if is_compute and "ocpu" in description and f"{quantity:g} ocpu" not in labels and "2 ocpu" not in labels:
            missing.append(f"compute {quantity:g} OCPU total")
        elif is_compute and "memory" in description:
            instance_count = int(item.get("instance_count") or 1)
            per_instance = quantity / max(instance_count, 1)
            if not (
                f"{quantity:g} gb" in labels
                or f"{per_instance:g} gb each" in labels
                or f"{per_instance:g} gb ram" in labels
            ):
                missing.append(f"compute {quantity:g} GB memory total")
        elif "block volume" in description and "storage" in description and f"{quantity:g} gb" not in labels:
            missing.append(f"Block Volume {quantity:g} GB")
        elif "load balancer" in description and "load balancer" not in labels:
            missing.append("Flexible Load Balancer")
        elif "web application firewall" in description and not re.search(r"waf policy|web application firewall policy", labels):
            missing.append("WAF policy")
        elif "postgresql" in description and not ("postgresql" in labels and f"{quantity:g} ocpu" in labels):
            missing.append(f"PostgreSQL {quantity:g} OCPU")
        elif "oracle base database" in description and not (
            "oracle base database" in labels and f"{quantity:g} ocpu" in labels
        ):
            missing.append(f"Oracle Base Database Service {quantity:g} OCPU")
        elif "object storage" in description and not (
            f"{quantity:g} gb object storage" in labels
            or (quantity == 1024 and "1 tb object storage" in labels)
        ):
            missing.append(f"Object Storage {quantity:g} GB")
        elif "file storage" in description and not (
            f"{quantity:g} gb file storage" in labels
            or f"file storage {quantity:g} gb" in labels
            or (quantity == 1024 and "1 tb file storage" in labels)
        ):
            missing.append(f"File Storage {quantity:g} GB")
    return "missing BOM diagram coverage: " + ", ".join(missing) if missing else ""


def _diagram_consistency_report(
    requirements: list[dict[str, Any]],
    ha_mode: str,
    drawio_xml: str,
    *,
    expected_region: str = "",
    required_private_tiers: list[str] | None = None,
) -> dict[str, Any]:
    labels = _diagram_labels(drawio_xml)
    required_ids = [
        str(item.get("service_id") or "")
        for item in requirements
        if isinstance(item, dict) and item.get("service_id")
    ]
    represented = [service_id for service_id in required_ids if _diagram_represents(service_id, labels)]
    missing = sorted(set(required_ids) - set(represented))
    detected = {
        service_id
        for service_id in _DIAGRAM_COMPONENT_TOKENS
        if _diagram_represents(service_id, labels)
    }
    if any(service_id.startswith("compute.vm.standard.") for service_id in detected):
        detected.discard("compute.vm")
    unexpected = sorted(detected - set(required_ids))
    topology_findings: list[str] = []
    sizing_findings: list[str] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        service_id = str(requirement.get("service_id") or "")
        sizing = requirement.get("assumed_sizing") if isinstance(requirement.get("assumed_sizing"), dict) else {}
        if service_id == "database.postgresql" and sizing.get("ocpu"):
            expected = float(sizing["ocpu"])
            if f"{expected:g} ocpu" not in labels:
                sizing_findings.append(f"PostgreSQL assumed sizing {expected:g} OCPU is not represented")
    normalized_ha = str(ha_mode or "").lower()
    if "single-ad" in normalized_ha and not re.search(r"single[- ]ad|availability domain\s*1", labels):
        topology_findings.append("single-AD POC boundary is not explicitly represented")
    if ("multi-ad" in normalized_ha or "high availability" in normalized_ha) and not re.search(
        r"multi[- ]ad|availability domain\s*2|ad-2|standby", labels
    ):
        topology_findings.append("multi-AD/HA topology is not explicitly represented")
    if expected_region and expected_region.lower() not in labels:
        topology_findings.append(f"region {expected_region} is not explicitly represented")
    required_private_tiers = required_private_tiers or []
    private_subnet_count = _private_subnet_count(drawio_xml)
    if required_private_tiers and private_subnet_count < len(required_private_tiers):
        topology_findings.append(
            f"private subnet count {private_subnet_count} is fewer than required tiers {len(required_private_tiers)} "
            f"({', '.join(required_private_tiers)})"
        )
    return {
        "verdict": (
            "pass"
            if not missing and not unexpected and not topology_findings and not sizing_findings
            else "blocked"
        ),
        "required_components": sorted(set(required_ids)),
        "represented_components": sorted(set(represented)),
        "missing_components": missing,
        "unexpected_components": unexpected,
        "topology_findings": topology_findings,
        "sizing_findings": sizing_findings,
        "required_private_tiers": required_private_tiers,
        "private_subnet_count": private_subnet_count,
    }


def _diagram_consistency_error(
    requirements: list[dict[str, Any]],
    ha_mode: str,
    drawio_xml: str,
    *,
    expected_region: str = "",
    required_private_tiers: list[str] | None = None,
) -> str:
    if not requirements:
        return ""
    report = _diagram_consistency_report(
        requirements,
        ha_mode,
        drawio_xml,
        expected_region=expected_region,
        required_private_tiers=required_private_tiers,
    )
    if report["verdict"] == "pass":
        return ""
    findings = [
        *(f"missing {consistency_contract.display_name(service_id)}" for service_id in report["missing_components"]),
        *(f"unexpected {consistency_contract.display_name(service_id)}" for service_id in report["unexpected_components"]),
        *report["topology_findings"],
        *report["sizing_findings"],
    ]
    return "cross-artifact diagram inconsistency: " + ", ".join(findings)


def _diagram_labels(drawio_xml: str) -> str:
    values = re.findall(r'\bvalue=["\']([^"\']*)["\']', str(drawio_xml or ""), flags=re.IGNORECASE)
    labels = " ".join(html.unescape(value).replace("&#xa;", " ") for value in values)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", labels)).lower()


_DIAGRAM_COMPONENT_TOKENS = {
        "database.postgresql": ("postgresql", "postgres"),
        "database.autonomous": ("autonomous database", "adb"),
        "database.mysql": ("mysql",),
        "database.oracle": ("oracle database", "base database"),
        "network.load_balancer.flexible": ("flexible load balancer", "load balancer"),
        "security.waf": ("waf", "web application firewall"),
        "security.iam": ("oci iam", "identity and access management", "iam policy"),
        "compute.vm.standard.e5.flex": ("vm.standard.e5.flex", "e5.flex", "standard e5"),
        "compute.vm.standard.e6.flex": ("vm.standard.e6.flex", "e6.flex", "standard e6"),
        "compute.vm": ("compute", "application server", "web server"),
        "storage.object": ("object storage",),
        "storage.block": ("block volume", "block storage"),
        "storage.file": ("file storage", "fss", "nfs"),
        "network.vpn.site_to_site": ("site-to-site vpn", "site to site vpn", "ipsec"),
        "network.fastconnect": ("fastconnect",),
        "observability.logging": ("logging",),
        "observability.monitoring": ("monitoring", "apm"),
        "container.oke": ("oke", "container engine for kubernetes"),
}


def _private_subnet_count(drawio_xml: str) -> int:
    values = re.findall(r'\bvalue=["\']([^"\']*)["\']', str(drawio_xml or ""), flags=re.IGNORECASE)
    return sum(
        1
        for value in values
        if re.search(r"\bprivate\b.*\bsubnet\b|\bsubnet\b.*\bprivate\b", html.unescape(value), re.IGNORECASE)
    )


def _diagram_represents(service_id: str, labels: str) -> bool:
    return any(
        token in labels
        for token in _DIAGRAM_COMPONENT_TOKENS.get(
            service_id,
            (consistency_contract.display_name(service_id).lower(),),
        )
    )


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
