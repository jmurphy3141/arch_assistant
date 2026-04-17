"""
Compatibility-safe specialist adapter for the v1.5 LangGraph migration.

Current behavior:
- Uses existing specialist implementations to preserve behavior.
- Provides a single async tool execution surface that can be swapped to true
  LangGraph specialist graphs incrementally.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from agent.persistence_objectstore import ObjectStoreBase
import agent.context_store as context_store
import agent.document_store as document_store
from agent.graphs import diagram_graph, jep_graph, pov_graph, terraform_graph, waf_graph

logger = logging.getLogger(__name__)


async def execute_tool(
    tool_name: str,
    args: dict,
    *,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
) -> tuple[str, str, dict]:
    """
    Execute a specialist tool call through the LangGraph-compatible adapter.
    Returns (result_summary, artifact_key).
    """
    if tool_name == "save_notes":
        text = args.get("text", "")
        if not text.strip():
            return "No notes text provided.", "", {}
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        key = await asyncio.to_thread(
            document_store.save_note,
            store,
            customer_id,
            f"note_{ts}.md",
            text.encode("utf-8"),
        )
        return f"Notes saved. Key: {key}", key, {}

    if tool_name == "get_summary":
        ctx = await asyncio.to_thread(
            context_store.read_context, store, customer_id, customer_name
        )
        summary = context_store.build_context_summary(ctx)
        return summary or "No engagement activity yet.", "", {}

    if tool_name == "generate_pov":
        summary, key = await pov_graph.run(
            args=args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
        )
        return summary, key, {}

    if tool_name == "generate_diagram":
        summary, key = await diagram_graph.run(
            args=args,
            customer_id=customer_id,
            a2a_base_url=a2a_base_url,
        )
        return summary, key, {}

    if tool_name == "generate_waf":
        summary, key = await waf_graph.run(
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
        )
        return summary, key, {}

    if tool_name == "generate_jep":
        summary, key = await jep_graph.run(
            args=args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
        )
        return summary, key, {}

    if tool_name == "generate_terraform":
        skill_root = Path(__file__).resolve().parents[1] / "gstack_skills"
        summary, key, result_data = await terraform_graph.run(
            args=args,
            skill_root=skill_root,
            text_runner=text_runner,
        )
        if result_data.get("ok") and isinstance(result_data.get("files"), dict):
            persisted = await asyncio.to_thread(
                document_store.save_terraform_bundle,
                store,
                customer_id,
                result_data["files"],
                {
                    "source": "langgraph_specialists",
                    "stage_count": len(result_data.get("stages", [])),
                },
            )
            result_data["bundle"] = persisted
            summary = (
                summary
                + "\n\nTerraform bundle saved:"
                + f"\n- version: {persisted['version']}"
                + f"\n- key: {persisted['key']}"
            )
        return summary, key, result_data

    if tool_name == "get_document":
        doc_type = args.get("type", "pov")
        content = await asyncio.to_thread(
            document_store.get_latest_doc, store, doc_type, customer_id
        )
        if content is None:
            return f"No {doc_type.upper()} found for this customer.", "", {}
        preview = content[:500].strip()
        return f"{doc_type.upper()} content (first 500 chars):\n{preview}", "", {}

    return f"Unknown tool: {tool_name!r}", "", {}
