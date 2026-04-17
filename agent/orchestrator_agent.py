"""
agent/orchestrator_agent.py
----------------------------
Conversational orchestrator (Agent 0).

Accepts a natural-language SA message, decides which sub-agents to invoke
using a ReAct-style agentic loop, and returns a structured reply.

Conversation history is persisted per customer_id in OCI Object Storage at
  conversations/{customer_id}/history.json

Inter-agent calls:
  generate_diagram  → POST /message:send  (A2A v1.0 self-call via httpx)
  generate_pov      → pov_agent.generate_pov()     [in-process]
  generate_waf      → waf_agent.generate_waf()     [in-process]
  generate_jep      → jep_agent.generate_jep()     [in-process]
  save_notes        → document_store.save_note()   [in-process]
  get_summary       → context_store               [in-process]
  get_document      → document_store.get_latest_doc() [in-process]
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable

from agent.persistence_objectstore import ObjectStoreBase
import agent.document_store as document_store
import agent.context_store as context_store

logger = logging.getLogger(__name__)

# ── System message ─────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM_MSG = """\
You are an Oracle OCI Solutions Architect Assistant helping an Oracle SA run a
customer engagement end-to-end.  You have access to tools that call specialist
agents.  Use them to fulfil the SA's requests.

When you need to take an action, output ONLY the following JSON on a single
line — no other text on that line, no markdown fences:
{"tool": "<name>", "args": {<key>: <value>}}

AVAILABLE TOOLS
  save_notes       {"text": "<notes text>"}
      Save SA-pasted meeting notes for this customer.

  get_summary      {}
      Return the current engagement state (what has been generated so far).

  generate_pov     {"feedback": "<optional correction text>"}
      Draft or update the Point of View document.  Call get_summary first
      to confirm notes exist.

  generate_diagram {"bom_text": "<optional inline BOM description>"}
      Generate an OCI architecture diagram.  If no BOM has been uploaded,
      ask the SA to upload or paste one before calling this tool.

  generate_waf     {}
      Run an OCI Well-Architected Framework review against the latest diagram.

  generate_jep     {"feedback": "<optional correction text>"}
      Draft or update the Joint Execution Plan.

  generate_terraform {"prompt": "<optional constraints or module goals>"}
      Generate Terraform via the specialist chain. If blocked, return
      clarification questions for the SA.

  get_document     {"type": "pov" | "jep" | "waf"}
      Retrieve the latest generated document content.

RULES
- When the SA pastes or describes notes, always call save_notes first.
- Before generating a POV or JEP, call get_summary to confirm notes exist.
- Before generating a diagram without an uploaded BOM, ask the SA to provide one.
- Never fabricate document content — always call the appropriate tool.
- Respond in Markdown when not calling a tool.  Be concise.
"""

# ── Main entry point ──────────────────────────────────────────────────────────

async def run_turn(
    *,
    customer_id: str,
    customer_name: str,
    user_message: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    a2a_base_url: str = "http://localhost:8080",
    max_tool_iterations: int = 5,
    specialist_mode: str = "legacy",
) -> dict:
    """
    Process one SA message and return the orchestrator response.

    Returns:
        {
            "reply":          str,         # Markdown response to show the SA
            "tool_calls":     list[dict],  # tools invoked this turn
            "artifacts":      dict,        # {type: object_key} for newly produced artifacts
            "history_length": int,
        }
    """
    from agent.notifications import notify

    # Load conversation state
    history = document_store.load_conversation_history(store, customer_id)
    summary = document_store.load_conversation_summary(store, customer_id)

    new_turns: list[dict] = [
        {
            "role": "user",
            "content": user_message,
            "timestamp": _now(),
            "customer_name": customer_name,
        }
    ]
    tool_calls: list[dict] = []
    artifacts:  dict       = {}

    prompt = _build_prompt(history, summary, user_message)
    reply  = ""

    for _iteration in range(max_tool_iterations):
        raw = await asyncio.to_thread(text_runner, prompt, ORCHESTRATOR_SYSTEM_MSG)
        tool_call = _parse_tool_call(raw)

        if tool_call is None:
            reply = raw.strip()
            break

        tool_name = tool_call.get("tool", "")
        tool_args = tool_call.get("args", {})
        logger.info("Orchestrator tool call: %s args=%s customer=%s",
                    tool_name, tool_args, customer_id)

        result_summary, artifact_key, result_data = await _execute_tool(
            tool_name, tool_args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
        )

        notify(f"tool:{tool_name}", customer_id, result_summary)

        tool_calls.append({
            "tool":           tool_name,
            "args":           tool_args,
            "result_summary": result_summary,
            "result_data":    result_data,
        })
        if artifact_key:
            artifacts[tool_name] = artifact_key

        tool_turn = {
            "role":           "tool",
            "tool":           tool_name,
            "result_summary": result_summary,
            "timestamp":      _now(),
        }
        new_turns.append({
            "role":      "assistant",
            "content":   raw.strip(),
            "timestamp": _now(),
            "tool_call": tool_call,
        })
        new_turns.append(tool_turn)

        # Feed tool result back into next prompt
        prompt = _append_tool_result(prompt, tool_name, result_summary)

    else:
        # Cap reached without a plain-text response — ask LLM for a summary
        raw = await asyncio.to_thread(
            text_runner,
            prompt + "\n\nProvide a brief summary of what was accomplished.",
            ORCHESTRATOR_SYSTEM_MSG,
        )
        reply = raw.strip()

    new_turns.append({"role": "assistant", "content": reply, "timestamp": _now()})
    document_store.save_conversation_turns(store, customer_id, new_turns)

    return {
        "reply":          reply,
        "tool_calls":     tool_calls,
        "artifacts":      artifacts,
        "history_length": len(history) + len(new_turns),
    }


# ── Tool dispatch ─────────────────────────────────────────────────────────────

async def _execute_tool(
    tool_name: str,
    args: dict,
    *,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
    specialist_mode: str = "legacy",
) -> tuple[str, str, dict]:
    """
    Execute a tool call and return (result_summary, artifact_key).
    artifact_key is "" when no persistent artifact was produced.
    """
    if specialist_mode == "langgraph":
        from agent import langgraph_specialists

        adapter_result = await langgraph_specialists.execute_tool(
            tool_name,
            args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        )
        if len(adapter_result) == 2:
            summary, key = adapter_result
            return summary, key, {}
        return adapter_result

    if tool_name == "save_notes":
        text = args.get("text", "")
        if not text.strip():
            return "No notes text provided.", "", {}
        ts    = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        key   = await asyncio.to_thread(
            document_store.save_note,
            store, customer_id, f"note_{ts}.md",
            text.encode("utf-8"),
        )
        return f"Notes saved. Key: {key}", key, {}

    if tool_name == "get_summary":
        ctx     = await asyncio.to_thread(context_store.read_context, store, customer_id, customer_name)
        summary = context_store.build_context_summary(ctx)
        return summary or "No engagement activity yet.", "", {}

    if tool_name == "generate_pov":
        from agent import pov_agent
        feedback = args.get("feedback", "")
        result   = await asyncio.to_thread(
            pov_agent.generate_pov,
            customer_id, customer_name, store, text_runner,
            feedback=feedback,
        )
        key = result.get("key", "")
        return f"POV v{result.get('version')} saved. Key: {key}", key, {}

    if tool_name == "generate_diagram":
        s, k = await _call_generate_diagram(args, customer_id, a2a_base_url)
        return s, k, {}

    if tool_name == "generate_waf":
        from agent import waf_agent
        result = await asyncio.to_thread(
            waf_agent.generate_waf,
            customer_id, customer_name, store, text_runner,
        )
        key    = result.get("key", "")
        rating = result.get("overall_rating", "")
        return f"WAF review {rating} saved. Key: {key}", key, {}

    if tool_name == "generate_jep":
        from agent import jep_agent
        feedback = args.get("feedback", "")
        result   = await asyncio.to_thread(
            jep_agent.generate_jep,
            customer_id, customer_name, store, text_runner,
            feedback=feedback,
        )
        key = result.get("key", "")
        return f"JEP v{result.get('version')} saved. Key: {key}", key, {}

    if tool_name == "generate_terraform":
        return (
            "Terraform generation is not yet enabled in legacy mode. "
            "Enable orchestrator.specialists_langgraph_enabled to use the v1.5 chain.",
            "",
            {},
        )

    if tool_name == "get_document":
        doc_type = args.get("type", "pov")
        content  = await asyncio.to_thread(
            document_store.get_latest_doc, store, doc_type, customer_id,
        )
        if content is None:
            return f"No {doc_type.upper()} found for this customer.", "", {}
        preview = content[:500].strip()
        return f"{doc_type.upper()} content (first 500 chars):\n{preview}", "", {}

    return f"Unknown tool: {tool_name!r}", "", {}


async def _call_generate_diagram(
    args: dict,
    customer_id: str,
    a2a_base_url: str,
) -> tuple[str, str]:
    """Call the drawing agent via A2A v1.0 /message:send."""
    try:
        import httpx
    except ImportError:
        return "httpx not installed — cannot call diagram agent.", ""

    bom_text = args.get("bom_text", "")
    message_text = bom_text if bom_text.strip() else "Generate a diagram for this engagement."

    payload = {
        "jsonrpc": "2.0",
        "id":      f"orch-{_now()}",
        "method":  "message/send",
        "params": {
            "message": {
                "role":      "user",
                "parts":     [{"kind": "text", "text": message_text}],
                "contextId": customer_id,
            },
            "skill": "generate_diagram",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(f"{a2a_base_url}/message:send", json=payload)
        data = resp.json()
        result = data.get("result", {})
        status = result.get("status", "UNKNOWN")
        task_id = result.get("id", "")
        if status == "COMPLETED":
            artifacts = result.get("artifacts", [])
            for art in artifacts:
                if art.get("name") == "drawio_key":
                    parts = art.get("parts", [{}])
                    key = (parts[0].get("data") or {}).get("key", "")
                    if key:
                        return f"Diagram generated. Key: {key}", key
            return f"Diagram generated (task {task_id}).", ""
        if status in ("WORKING", "SUBMITTED"):
            return f"Diagram generation started (task {task_id}). Poll /tasks/{task_id}.", ""
        return f"Diagram generation returned status={status}.", ""
    except Exception as exc:
        logger.warning("Diagram A2A call failed: %s", exc)
        return f"Diagram generation failed: {exc}", ""


# ── Prompt assembly ───────────────────────────────────────────────────────────

def _build_prompt(history: list[dict], summary: str, user_message: str) -> str:
    parts: list[str] = []

    if summary:
        parts.append(f"[Prior conversation summary]\n{summary}\n")

    for turn in history:
        role = turn.get("role", "")
        if role == "user":
            parts.append(f"SA: {turn.get('content', '')}")
        elif role == "assistant":
            content = turn.get("content", "")
            if content:
                parts.append(f"ASSISTANT: {content}")
        elif role == "tool":
            parts.append(
                f"[Tool result: {turn.get('tool', '')}] "
                f"{turn.get('result_summary', '')}"
            )

    parts.append(f"SA: {user_message}")
    parts.append("ASSISTANT:")
    return "\n\n".join(parts)


def _append_tool_result(prompt: str, tool_name: str, result_summary: str) -> str:
    return prompt.rstrip("ASSISTANT:").rstrip() + (
        f"\n\n[Tool result: {tool_name}] {result_summary}\n\nASSISTANT:"
    )


# ── Tool call parser ──────────────────────────────────────────────────────────

_TOOL_RE = re.compile(r'\{\s*"tool"\s*:.+?\}', re.DOTALL)


def _parse_tool_call(text: str) -> dict | None:
    m = _TOOL_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group())
        if "tool" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
