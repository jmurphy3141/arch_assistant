"""
skillforge/forge.py
--------------------
Forge: the domain-agnostic ReAct orchestrator.

Forge owns the ReAct loop, hat dispatch, tool dispatch, and turn lifecycle.
It knows nothing about OCI, diagrams, BOMs, or Terraform. Domain knowledge
lives in registered ToolHandlers, hat markdown files, and the Memory impl.

Wiring example (in your application entry point):

    import agent.hat_engine as hat_engine
    from skillforge import Forge
    from agent.archie_memory import ArchieMemory
    from agent.tools import bom_handler, diagram_handler, ...

    forge = Forge(
        base_system_prompt=ARCHIE_SYSTEM_PROMPT,
        hat_engine=hat_engine,
        memory=ArchieMemory(),
        store=object_store,
        text_runner=llm_call,
    )
    forge.register_tool("generate_bom",     bom_handler,     memory_contract=True,  description='{"prompt": "<workload sizing>"}')
    forge.register_tool("generate_diagram", diagram_handler, memory_contract=True,  description='{"bom_text": "<optional context>"}')
    forge.register_tool("generate_pov",     pov_handler,     memory_contract=True,  description='{"feedback": "<optional correction>"}')
    forge.register_tool("save_notes",       notes_handler,   memory_contract=False, description='{"text": "<notes>"}')

    result = await forge.run_turn(
        session_id="customer-123",
        user_message="Build me a BOM for a 3-tier web app",
        context=context_blob,
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable

from skillforge.protocols import Memory, SafetyChecker, TextRunner, ToolHandler
from skillforge.registry import ToolRegistry
from skillforge.types import MemorySnapshot, ToolCall, ToolResult, TurnResult

logger = logging.getLogger(__name__)

# Sentinel returned when the LLM emits a plain reply (no tool call)
_NO_TOOL = object()

_MAX_ITERATIONS_DEFAULT = 5


class Forge:
    """
    Domain-agnostic polymath orchestrator.

    The ReAct loop:
      1. Assemble memory snapshot for this turn.
      2. Inject active hats into prompt.
      3. Call LLM → parse tool call or plain reply.
      4. If hat tool: activate/drop hat, continue loop.
      5. If domain tool: run handler (with memory if required), run safety
         check, append result, continue loop.
      6. If plain reply: finalize turn.
    """

    def __init__(
        self,
        *,
        base_system_prompt: str,
        hat_engine: Any,          # agent.hat_engine module (duck-typed)
        memory: Memory,
        store: Any,               # ObjectStoreBase (duck-typed)
        text_runner: TextRunner,
        max_iterations: int = _MAX_ITERATIONS_DEFAULT,
    ) -> None:
        self._base_system_prompt = base_system_prompt
        self._hat_engine = hat_engine
        self._memory = memory
        self._store = store
        self._text_runner = text_runner
        self._max_iterations = max_iterations
        self._registry = ToolRegistry()

    # ── Registration API ──────────────────────────────────────────────────────

    def register_tool(
        self,
        name: str,
        handler: ToolHandler,
        *,
        description: str = "",
        args_schema: dict[str, str] | None = None,
        memory_contract: bool = False,
        safety_checker: SafetyChecker | None = None,
        skill_guidance: str = "",
        parallel_safe: bool = False,
    ) -> None:
        """
        Register a domain tool.

        name:             tool name the LLM emits in its tool-call JSON
        handler:          async callable matching ToolHandler protocol
        description:      args description appended to system prompt tool list
        memory_contract:  if True, assemble MemorySnapshot and pass it to handler
        safety_checker:   deterministic check run after handler returns; blocks
                          delivery if it returns (False, reason)
        skill_guidance:   markdown prepended to the task string inside the handler
        parallel_safe:    if True, may run concurrently with other parallel_safe tools
        """
        self._registry.register(
            name,
            handler,
            description=description,
            args_schema=args_schema,
            memory_contract=memory_contract,
            safety_checker=safety_checker,
            skill_guidance=skill_guidance,
            parallel_safe=parallel_safe,
        )

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        context: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
    ) -> TurnResult:
        """
        Process one user message and return a TurnResult.

        context:  the full context store blob for this session (read-only from
                  Forge's perspective; Memory.update() returns the updated blob)
        history:  prior conversation turns (for prompt construction)
        """
        trace_id = str(uuid.uuid4())
        active_hats: list[str] = []
        hat_rounds: dict[str, int] = {}
        tool_calls: list[ToolCall] = []
        artifacts: dict[str, str] = {}

        hat_tools = self._hat_engine.get_hat_tool_definitions()
        system_msg = self._build_system_prompt(hat_tools)
        prompt = self._build_initial_prompt(user_message, history or [])

        # Assemble memory once at turn start; refreshed after each tool call
        memory_snapshot = self._memory.assemble(
            session_id=session_id,
            context=context,
            user_message=user_message,
        )

        reply = ""

        for iteration in range(self._max_iterations):
            # Stale-hat warning
            for h in active_hats:
                hat_rounds[h] = hat_rounds.get(h, 0) + 1
            stale = self._hat_engine.warn_stale_hats(active_hats, hat_rounds)
            if stale:
                logger.warning("Stale hats active > 5 rounds: %s session=%s", stale, session_id)

            # Inject hats and call LLM
            prompt_with_hats = self._hat_engine.inject_hats(prompt, active_hats)
            raw = await asyncio.to_thread(
                self._text_runner, prompt_with_hats, system_msg, "orchestrator"
            )

            parsed = _parse_tool_call(raw)

            # Plain reply — done
            if parsed is _NO_TOOL:
                reply = raw.strip()
                break

            tool_name: str = parsed.get("tool", "")
            tool_args: dict = parsed.get("args", {}) or {}

            # Hat activation
            if tool_name.startswith("use_hat_"):
                hat_name = tool_name[len("use_hat_"):]
                active_hats = self._hat_engine.apply_hat(active_hats, hat_name)
                result = ToolResult(summary=f"Hat '{hat_name}' activated.", status="ok")
                tool_calls.append(ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration))
                prompt = _append_result(prompt, tool_name, result.summary)
                continue

            # Hat deactivation
            if tool_name.startswith("drop_hat_"):
                hat_name = tool_name[len("drop_hat_"):]
                active_hats = self._hat_engine.drop_hat(active_hats, hat_name)
                hat_rounds.pop(hat_name, None)
                result = ToolResult(summary=f"Hat '{hat_name}' deactivated.", status="ok")
                tool_calls.append(ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration))
                prompt = _append_result(prompt, tool_name, result.summary)
                continue

            # Unknown tool
            spec = self._registry.get(tool_name)
            if spec is None:
                logger.warning("LLM called unregistered tool %r session=%s", tool_name, session_id)
                result = ToolResult(summary=f"Unknown tool: {tool_name}", status="blocked")
                tool_calls.append(ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration))
                prompt = _append_result(prompt, tool_name, result.summary)
                continue

            # Domain tool — call handler
            mem = memory_snapshot if spec.memory_contract else None
            try:
                result = await spec.handler(tool_args, memory=mem, context=context, trace_id=trace_id)
            except Exception as exc:
                logger.exception("Tool handler %r raised: %s session=%s", tool_name, exc, session_id)
                result = ToolResult(summary=f"Tool {tool_name} failed: {exc}", status="blocked")

            # Safety check
            if spec.safety_checker is not None and result.status == "ok":
                passed, reason = spec.safety_checker(tool_name, result)
                if not passed:
                    result = ToolResult(
                        summary=f"Blocked by safety check: {reason}",
                        status="blocked",
                        data=result.data,
                    )

            call_record = ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
            tool_calls.append(call_record)

            if result.artifact_key:
                artifacts[tool_name] = result.artifact_key

            if result.status == "blocked":
                artifacts.pop(tool_name, None)

            # Refresh memory after tool completes
            if spec.memory_contract:
                context = self._memory.update(
                    session_id=session_id,
                    tool_name=tool_name,
                    result=result,
                    context=context,
                )
                memory_snapshot = self._memory.assemble(
                    session_id=session_id,
                    context=context,
                    user_message=user_message,
                )

            prompt = _append_result(prompt, tool_name, result.summary)

        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            artifacts=artifacts,
            history_length=len(history or []) + 1,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_system_prompt(self, hat_tools: list[dict]) -> str:
        tool_block = self._registry.tool_contract_block()
        hat_block = _hat_tool_block(hat_tools)
        parts = [self._base_system_prompt.rstrip()]
        if tool_block:
            parts.append("\nTool contracts:\n" + tool_block)
        if hat_block:
            parts.append("\nHat tools:\n" + hat_block)
        return "\n".join(parts)

    def _build_initial_prompt(self, user_message: str, history: list[dict]) -> str:
        lines: list[str] = []
        for turn in history[-20:]:  # last 20 turns max
            role = turn.get("role", "")
            content = str(turn.get("content", "") or "")
            if role and content:
                lines.append(f"{role.upper()}: {content}")
        lines.append(f"USER: {user_message}")
        return "\n".join(lines)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _parse_tool_call(raw: str) -> dict | object:
    """
    Parse the LLM output. Returns a dict if a tool call was found,
    or _NO_TOOL sentinel if the LLM replied in plain text.
    """
    text = raw.strip()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and '"tool"' in line:
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict) and "tool" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass
    return _NO_TOOL


def _append_result(prompt: str, tool_name: str, summary: str) -> str:
    return prompt + f"\nTOOL_RESULT({tool_name}): {summary}"


def _hat_tool_block(hat_tools: list[dict]) -> str:
    lines = [
        "- use_hat_X activates an expert hat before the next reasoning round.",
        "- drop_hat_X deactivates an active expert hat.",
    ]
    for tool in hat_tools:
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(fn.get("name", "") or "").strip()
        if name:
            lines.append(f"- {name} {{}}")
    return "\n".join(lines)
