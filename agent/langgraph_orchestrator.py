"""
Compatibility-safe orchestrator adapter for the v1.5 LangGraph migration.

Current behavior:
- Executes a graph-driven model/tool loop when LangGraph is available.
- Falls back to existing orchestrator logic only when LangGraph is unavailable.

Future behavior:
- Extend graph with richer planner/reviewer state and parallel branches.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, TypedDict

import agent.document_store as document_store
from agent.persistence_objectstore import ObjectStoreBase
from agent import orchestrator_agent as legacy_orchestrator

try:
    from langgraph.graph import END, StateGraph

    _HAS_LANGGRAPH = True
except Exception:
    END = "__end__"  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]
    _HAS_LANGGRAPH = False


class OrchestratorState(TypedDict, total=False):
    user_message: str
    prompt: str
    reply: str
    done: bool
    pending_tool: dict[str, Any] | None
    tool_calls: list[dict[str, Any]]
    artifacts: dict[str, str]
    new_turns: list[dict[str, Any]]
    iterations: int


async def _run_turn_via_langgraph(
    *,
    customer_id: str,
    customer_name: str,
    user_message: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    a2a_base_url: str,
    max_tool_iterations: int,
    specialist_mode: str,
) -> dict:
    history = document_store.load_conversation_history(store, customer_id)
    summary = document_store.load_conversation_summary(store, customer_id)

    initial_turns = [
        {
            "role": "user",
            "content": user_message,
            "timestamp": legacy_orchestrator._now(),
            "customer_name": customer_name,
        }
    ]

    initial_state: OrchestratorState = {
        "user_message": user_message,
        "prompt": legacy_orchestrator._build_prompt(history, summary, user_message),
        "reply": "",
        "done": False,
        "pending_tool": None,
        "tool_calls": [],
        "artifacts": {},
        "new_turns": initial_turns,
        "iterations": 0,
    }

    async def model_node(state: OrchestratorState) -> OrchestratorState:
        iterations = int(state.get("iterations", 0))
        prompt = state.get("prompt", "")

        if iterations >= max_tool_iterations:
            raw = await asyncio.to_thread(
                text_runner,
                prompt + "\n\nProvide a brief summary of what was accomplished.",
                legacy_orchestrator.ORCHESTRATOR_SYSTEM_MSG,
            )
            return {
                "reply": raw.strip(),
                "done": True,
                "pending_tool": None,
            }

        raw = await asyncio.to_thread(
            text_runner, prompt, legacy_orchestrator.ORCHESTRATOR_SYSTEM_MSG
        )
        tool_call = legacy_orchestrator._parse_tool_call(raw)
        if tool_call is None:
            return {
                "reply": raw.strip(),
                "done": True,
                "pending_tool": None,
            }

        assistant_tool_turn = {
            "role": "assistant",
            "content": raw.strip(),
            "timestamp": legacy_orchestrator._now(),
            "tool_call": tool_call,
        }
        new_turns = list(state.get("new_turns", []))
        new_turns.append(assistant_tool_turn)
        return {
            "pending_tool": tool_call,
            "new_turns": new_turns,
            "done": False,
        }

    async def tool_node(state: OrchestratorState) -> OrchestratorState:
        pending_tool = state.get("pending_tool")
        if not pending_tool:
            return {"done": True}

        tool_name = str(pending_tool.get("tool", ""))
        tool_args = pending_tool.get("args", {}) or {}

        result_summary, artifact_key, result_data = await legacy_orchestrator._execute_tool(
            tool_name,
            tool_args,
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            specialist_mode=specialist_mode,
            user_message=str(state.get("user_message", "")),
        )

        tool_calls = list(state.get("tool_calls", []))
        tool_calls.append(
            {
                "tool": tool_name,
                "args": tool_args,
                "result_summary": result_summary,
                "result_data": result_data,
            }
        )

        artifacts = dict(state.get("artifacts", {}))
        if artifact_key:
            artifacts[tool_name] = artifact_key

        new_turns = list(state.get("new_turns", []))
        new_turns.append(
            {
                "role": "tool",
                "tool": tool_name,
                "result_summary": result_summary,
                "timestamp": legacy_orchestrator._now(),
            }
        )

        return {
            "prompt": legacy_orchestrator._append_tool_result(
                state.get("prompt", ""), tool_name, result_summary
            ),
            "pending_tool": None,
            "tool_calls": tool_calls,
            "artifacts": artifacts,
            "new_turns": new_turns,
            "iterations": int(state.get("iterations", 0)) + 1,
        }

    def route_from_model(state: OrchestratorState) -> str:
        if state.get("done", False):
            return "done"
        if state.get("pending_tool"):
            return "tool"
        return "model"

    graph = StateGraph(OrchestratorState)
    graph.add_node("model", model_node)
    graph.add_node("tool", tool_node)
    graph.set_entry_point("model")
    graph.add_conditional_edges(
        "model",
        route_from_model,
        {"done": END, "tool": "tool", "model": "model"},
    )
    graph.add_edge("tool", "model")

    compiled = graph.compile()
    final_state = await compiled.ainvoke(initial_state)

    reply = str(final_state.get("reply", "")).strip()
    final_turns = list(final_state.get("new_turns", []))
    final_turns.append(
        {"role": "assistant", "content": reply, "timestamp": legacy_orchestrator._now()}
    )
    document_store.save_conversation_turns(store, customer_id, final_turns)

    return {
        "reply": reply,
        "tool_calls": list(final_state.get("tool_calls", [])),
        "artifacts": dict(final_state.get("artifacts", {})),
        "history_length": len(history) + len(final_turns),
    }


async def run_turn(
    *,
    customer_id: str,
    customer_name: str,
    user_message: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    a2a_base_url: str,
    max_tool_iterations: int,
    specialist_mode: str = "langgraph",
) -> dict:
    """
    LangGraph-compatible orchestrator entry point.
    """
    if not _HAS_LANGGRAPH:
        return await legacy_orchestrator.run_turn(
            customer_id=customer_id,
            customer_name=customer_name,
            user_message=user_message,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            max_tool_iterations=max_tool_iterations,
            specialist_mode=specialist_mode,
        )

    return await _run_turn_via_langgraph(
        customer_id=customer_id,
        customer_name=customer_name,
        user_message=user_message,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        max_tool_iterations=max_tool_iterations,
        specialist_mode=specialist_mode,
    )
