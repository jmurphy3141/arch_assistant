"""
skillforge/forge.py
--------------------
Forge: the domain-agnostic ReAct orchestrator.

Forge owns the ReAct loop, hat dispatch, and tool dispatch.
It has zero domain-specific knowledge — no OCI, no AWS, no Kubernetes.
Domain knowledge lives entirely in registered ToolHandlers, hat markdown
files, and the Memory implementation.

Minimal wiring for a new domain (e.g. AWS):

    from skillforge import Forge
    from my_aws import ec2_handler, cfn_handler, AWSMemory

    forge = Forge(
        base_system_prompt="You are an AWS solutions architect...",
        hat_engine=hat_engine,   # agent/hat_engine.py — mechanism is domain-agnostic
        memory=AWSMemory(),
        text_runner=my_llm_call,
    )
    forge.register_tool("size_ec2",               ec2_handler, memory_contract=True,
                        description='{"workload": "<description>"}')
    forge.register_tool("generate_cloudformation", cfn_handler, memory_contract=True,
                        description='{"modules": "<optional list>"}',
                        safety_checker=cfn_safety_check)

    result = await forge.run_turn(
        session_id="session-1",
        user_message="Size a 3-tier web app for 10k concurrent users",
        context={},
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from skillforge.protocols import (
    AsyncTextRunner,
    HatEngine,
    Memory,
    PromptEnricher,
)
from skillforge.registry import ToolRegistry, ToolSpec
from skillforge.types import MemorySnapshot, ToolCall, ToolResult, TurnResult

logger = logging.getLogger(__name__)

_TOOL_CALL_FORMAT_INSTRUCTION = (
    "\n\nTool call format — when calling a tool output ONLY this JSON on a single line:\n"
    '{"tool": "<tool_name>", "args": {<key>: <value>}}\n'
    "To reply without calling a tool, output plain prose — no JSON."
)
_NO_TOOL = object()   # sentinel: LLM emitted a plain reply, no tool call
_MAX_ITERATIONS_DEFAULT = 5
_HISTORY_WINDOW_DEFAULT = 20


class Forge:
    """
    Domain-agnostic polymath orchestrator.

    ReAct loop per turn:
      1. Assemble MemorySnapshot.
      2. [Optional] enrich prompt with per-turn context via PromptEnricher.
      3. Inject active hats into prompt.
      4. Call LLM → parse tool call or plain reply.
      5. Hat tool  → apply/drop hat, continue loop.
      6. Domain tool → inject skill_guidance, call handler, run safety check,
                       refresh memory, append result, continue loop.
      7. needs_input → surface clarification (or retry once if configured).
      8. blocked     → remove artifact, append block reason, continue loop.
      9. Plain reply → return TurnResult.
    """

    def __init__(
        self,
        *,
        base_system_prompt: str,
        hat_engine: HatEngine,
        memory: Memory,
        text_runner: AsyncTextRunner,
        prompt_enricher: PromptEnricher | None = None,
        max_iterations: int = _MAX_ITERATIONS_DEFAULT,
        history_window: int = _HISTORY_WINDOW_DEFAULT,
    ) -> None:
        self._base_system_prompt = base_system_prompt
        self._hat_engine = hat_engine
        self._memory = memory
        self._text_runner = text_runner
        self._prompt_enricher = prompt_enricher
        self._max_iterations = max_iterations
        self._history_window = history_window
        self._registry = ToolRegistry()
        # System prompt is rebuilt lazily after register_tool() calls.
        self._system_msg: str | None = None

    # ── Registration API ──────────────────────────────────────────────────────

    def register_tool(
        self,
        name: str,
        handler: Any,   # ToolHandler — Any to avoid Protocol runtime overhead
        *,
        description: str = "",
        args_schema: dict[str, str] | None = None,
        memory_contract: bool = False,
        safety_checker: Any | None = None,   # SafetyChecker
        skill_guidance: str = "",
        parallel_safe: bool = False,
        retry_on_needs_input: bool = False,
        critique_enabled: bool = False,
    ) -> None:
        """
        Register a domain tool.

        name:                 exact string the LLM emits in {"tool": "name"}
        handler:              async (args, *, memory, context, trace_id) -> ToolResult
        description:          args schema injected into system prompt tool list
        memory_contract:      pass assembled MemorySnapshot to handler as `memory`
        safety_checker:       (tool_name, result) -> (bool, str); no LLM calls
        skill_guidance:       markdown prepended to task/prompt arg before dispatch
        parallel_safe:        tool may run concurrently with other parallel_safe tools
        retry_on_needs_input: append clarification to prompt and retry once instead
                              of immediately surfacing to user
        critique_enabled:     reserve tool for post-tool critic review
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
            retry_on_needs_input=retry_on_needs_input,
            critique_enabled=critique_enabled,
        )
        self._system_msg = None   # invalidate cached system prompt

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

        session_id:  stable identifier for the user/customer session
        context:     full context store blob (read-only by Forge; Memory.update
                     returns the refreshed blob which Forge adopts locally)
        history:     prior conversation turns for prompt construction
        """
        trace_id = str(uuid.uuid4())
        active_hats: list[str] = []
        hat_rounds: dict[str, int] = {}
        tool_calls: list[ToolCall] = []
        artifacts: dict[str, str] = {}
        reply = ""

        system_msg = self._get_system_msg()
        prompt = self._build_initial_prompt(user_message, history or [])
        memory_snapshot = self._memory.assemble(
            session_id=session_id,
            context=context,
            user_message=user_message,
        )

        for iteration in range(self._max_iterations):

            # Stale-hat warning (no side effect — caller logs if desired)
            for h in active_hats:
                hat_rounds[h] = hat_rounds.get(h, 0) + 1
            stale = self._hat_engine.warn_stale_hats(active_hats, hat_rounds)
            if stale:
                logger.warning(
                    "Stale hats active > 5 rounds: %s session=%s", stale, session_id
                )

            # Per-round prompt enrichment (memory summary, decision context, etc.)
            enriched = (
                self._prompt_enricher(prompt, memory_snapshot)
                if self._prompt_enricher
                else prompt
            )
            prompt_for_llm = self._hat_engine.inject_hats(enriched, active_hats)

            raw = await self._text_runner(prompt_for_llm, system_msg, "orchestrator")
            parsed = _parse_tool_call(raw)

            # ── Plain reply — done ────────────────────────────────────────────
            if parsed is _NO_TOOL:
                reply = raw.strip()
                break

            tool_name: str = parsed.get("tool", "")
            tool_args: dict[str, Any] = dict(parsed.get("args") or {})

            # ── Hat activation ────────────────────────────────────────────────
            if tool_name.startswith("use_hat_"):
                hat_name = tool_name[len("use_hat_"):]
                try:
                    active_hats = self._hat_engine.apply_hat(active_hats, hat_name)
                except ValueError:
                    pass   # unknown hat — ignore silently
                result = ToolResult(
                    summary=f"Hat '{hat_name}' activated.", status="ok"
                )
                tool_calls.append(
                    ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
                )
                prompt = _append_result(prompt, tool_name, result.summary)
                continue

            # ── Hat deactivation ──────────────────────────────────────────────
            if tool_name.startswith("drop_hat_"):
                hat_name = tool_name[len("drop_hat_"):]
                active_hats = self._hat_engine.drop_hat(active_hats, hat_name)
                hat_rounds.pop(hat_name, None)
                result = ToolResult(
                    summary=f"Hat '{hat_name}' deactivated.", status="ok"
                )
                tool_calls.append(
                    ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
                )
                prompt = _append_result(prompt, tool_name, result.summary)
                continue

            # ── Unknown tool ──────────────────────────────────────────────────
            spec = self._registry.get(tool_name)
            if spec is None:
                logger.warning(
                    "LLM called unregistered tool %r session=%s", tool_name, session_id
                )
                result = ToolResult(
                    summary=f"Unknown tool: {tool_name}", status="blocked"
                )
                tool_calls.append(
                    ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
                )
                prompt = _append_result(prompt, tool_name, result.summary)
                continue

            # ── Domain tool ───────────────────────────────────────────────────

            # Inject skill_guidance into the task/prompt arg before dispatch.
            if spec.skill_guidance:
                task_key = "prompt" if "prompt" in tool_args else "task"
                existing = str(tool_args.get(task_key) or "")
                tool_args = {
                    **tool_args,
                    task_key: f"{spec.skill_guidance}\n\n{existing}".strip(),
                }

            mem = memory_snapshot if spec.memory_contract else None
            try:
                result = await spec.handler(
                    tool_args, memory=mem, context=context, trace_id=trace_id
                )
            except Exception as exc:
                logger.exception(
                    "Tool handler %r raised: %s session=%s", tool_name, exc, session_id
                )
                result = ToolResult(
                    summary=f"Tool {tool_name} failed internally.", status="blocked"
                )

            # Safety check (ok results only)
            if spec.safety_checker is not None and result.status == "ok":
                passed, reason = spec.safety_checker(tool_name, result)
                if not passed:
                    result = ToolResult(
                        summary=f"Safety check blocked: {reason}",
                        status="blocked",
                        data=result.data,
                    )

            # needs_input: surface or retry once
            if result.status == "needs_input":
                clarification = result.clarification or result.summary
                if spec.retry_on_needs_input and iteration == 0:
                    prompt = _append_result(
                        prompt, tool_name, f"NEEDS_INPUT: {clarification}"
                    )
                    tool_calls.append(
                        ToolCall(
                            tool=tool_name, args=tool_args, result=result, iteration=iteration
                        )
                    )
                    continue
                # Surface directly — stop loop
                reply = clarification
                tool_calls.append(
                    ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
                )
                break

            tool_calls.append(
                ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
            )

            if result.artifact_key and result.status == "ok":
                artifacts[tool_name] = result.artifact_key

            # Refresh memory after memory_contract tool
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

    async def invoke_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        session_id: str,
        context: dict[str, Any],
        trace_id: str | None = None,
    ) -> ToolResult:
        """
        Execute a single registered tool handler and return its ToolResult.

        Does NOT call the LLM. Does NOT modify conversation history.
        Safety checks and memory refresh (for memory_contract tools) are applied.

        Raises KeyError if tool_name is not registered.
        Raises no other exceptions — handler errors are caught and returned
        as ToolResult(status="blocked").
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())

        spec = self._registry.get(tool_name)
        if spec is None:
            raise KeyError(f"invoke_tool: tool {tool_name!r} is not registered")

        # Inject skill_guidance into the task/prompt arg before dispatch.
        if spec.skill_guidance:
            task_key = "prompt" if "prompt" in args else "task"
            existing = str(args.get(task_key) or "")
            args = {**args, task_key: f"{spec.skill_guidance}\n\n{existing}".strip()}

        memory_snapshot = (
            self._memory.assemble(
                session_id=session_id, context=context, user_message=""
            )
            if spec.memory_contract
            else None
        )

        try:
            result = await spec.handler(
                args, memory=memory_snapshot, context=context, trace_id=trace_id
            )
        except Exception as exc:
            logger.exception(
                "invoke_tool handler %r raised: %s session=%s", tool_name, exc, session_id
            )
            return ToolResult(
                summary=f"Tool {tool_name} failed internally.", status="blocked"
            )

        # Safety check (ok results only)
        if spec.safety_checker is not None and result.status == "ok":
            passed, reason = spec.safety_checker(tool_name, result)
            if not passed:
                return ToolResult(
                    summary=f"Safety check blocked: {reason}",
                    status="blocked",
                    data=result.data,
                )

        # Refresh memory after memory_contract tool
        if spec.memory_contract and result.status == "ok":
            context = self._memory.update(
                session_id=session_id,
                tool_name=tool_name,
                result=result,
                context=context,
            )

        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_system_msg(self) -> str:
        """Build once after all tools are registered; cache thereafter."""
        if self._system_msg is None:
            hat_tools = self._hat_engine.get_hat_tool_definitions()
            self._system_msg = _assemble_system_prompt(
                self._base_system_prompt, hat_tools, self._registry
            )
        return self._system_msg

    def _build_initial_prompt(
        self, user_message: str, history: list[dict[str, Any]]
    ) -> str:
        lines: list[str] = []
        for turn in history[-self._history_window :]:
            role = str(turn.get("role") or "")
            content = str(turn.get("content") or "")
            if role and content:
                lines.append(f"{role.upper()}: {content}")
        lines.append(f"USER: {user_message}")
        return "\n".join(lines)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _parse_tool_call(raw: str) -> dict[str, Any] | object:
    """
    Parse LLM output into a tool-call dict or return _NO_TOOL.
    Handles: inline JSON, markdown-fenced JSON, JSON mid-paragraph,
    nested args objects.
    """
    text = raw.strip()

    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\s*```", "", text)

    # Try each `{` as a potential start; scan forward to find balanced `}`
    for start in (m.start() for m in re.finditer(r"\{", text)):
        candidate = _extract_balanced(text, start)
        if candidate is None or '"tool"' not in candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "tool" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass

    return _NO_TOOL


def _extract_balanced(text: str, start: int) -> str | None:
    """Return the smallest balanced {...} substring starting at `start`, or None."""
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _append_result(prompt: str, tool_name: str, summary: str) -> str:
    return prompt + f"\nTOOL_RESULT({tool_name}): {summary}"


def _assemble_system_prompt(
    base: str, hat_tools: list[dict], registry: ToolRegistry | None
) -> str:
    parts = [base.rstrip()]
    if registry:
        tool_block = registry.tool_contract_block()
        if tool_block:
            parts.append("\nTool contracts:\n" + tool_block)
    if hat_tools:
        hat_lines = [
            "- use_hat_X activates an expert hat before the next reasoning round.",
            "- drop_hat_X deactivates an active expert hat.",
        ]
        for tool in hat_tools:
            fn = tool.get("function", {}) if isinstance(tool, dict) else {}
            name = str(fn.get("name") or "").strip()
            if name:
                hat_lines.append(f"- {name} {{}}")
        parts.append("\nHat tools:\n" + "\n".join(hat_lines))
    parts.append(_TOOL_CALL_FORMAT_INSTRUCTION)
    return "\n".join(parts)
