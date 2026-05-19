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
    ArgSchema,
    AsyncTextRunner,
    AsyncToolRunner,
    HatEngine,
    Memory,
    PromptEnricher,
    ToolSchema,
)
from skillforge.registry import ToolRegistry, ToolSpec
from skillforge.types import MemorySnapshot, ToolCall, ToolResult, TurnEvent, TurnResult

logger = logging.getLogger(__name__)

_TOOL_CALL_FORMAT_INSTRUCTION = (
    "\n\nTool call format — when calling a tool output ONLY this JSON on a single line:\n"
    '{"tool": "<tool_name>", "args": {<key>: <value>}}\n'
    "To reply without calling a tool, output plain prose — no JSON."
)
_NO_TOOL = object()   # sentinel: LLM emitted a plain reply, no tool call
_MAX_ITERATIONS_DEFAULT = 5
_HISTORY_WINDOW_DEFAULT = 20
_MANUAL_ONLY_HATS = {"critic", "governor"}
_EXPERT_THINKING_MIN_CHARS = 600
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT TASK:",
)
_STEP3_PLANNING_HEADERS = (
    "STEP 1 — UNDERSTAND:",
    "STEP 2 — MEMORY ASSESSMENT:",
    "STEP 3 — PLAN + HAT SELECTION:",
)
_EXPERT_REVIEW_MIN_CHARS = 1000
_EXPERT_REVIEW_APPROVED = "EXPERT_APPROVED"
_EXPERT_REVIEW_ITERATE = "EXPERT_ITERATE:"
_EXPERT_REVIEW_SURFACE = "EXPERT_SURFACE:"


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
        max_critique_retries: int = 1,
        history_window: int = _HISTORY_WINDOW_DEFAULT,
        auto_coordinate: bool = True,
        step3_planning: bool = False,
        pre_action_always: bool = False,
        tool_runner: AsyncToolRunner | None = None,
    ) -> None:
        self._base_system_prompt = base_system_prompt
        self._hat_engine = hat_engine
        self._memory = memory
        self._text_runner = text_runner
        self._prompt_enricher = prompt_enricher
        self._max_iterations = max_iterations
        self._max_critique_retries = max_critique_retries
        self._history_window = history_window
        self._auto_coordinate = auto_coordinate
        self._step3_planning = step3_planning
        self._pre_action_always = pre_action_always
        self._tool_runner = tool_runner
        self._registry = ToolRegistry()
        self._global_skills: list[str] = []
        # System prompt is rebuilt lazily after register_tool() calls.
        self._system_msg: str | None = None

    # ── Registration API ──────────────────────────────────────────────────────

    def set_base_prompt_file(self, path: str) -> None:
        """
        Load the base system prompt from a .md file, replacing any previously
        set base_system_prompt. Invalidates the cached system message.

        Call before the first run_turn(). Calling after turns have started
        resets the cache — the new prompt takes effect on the next turn.
        """
        with open(path) as f:
            self._base_system_prompt = f.read()
        self._system_msg = None  # invalidate cache

    def register_skill_file(self, path: str) -> None:
        """
        Register a global skill file whose content is injected into the
        system prompt on every turn, before tool-specific guidance.

        Call after construction, before the first run_turn().
        Can be called multiple times — files are appended in order.
        Invalidates the system message cache.

        Parameters
        ----------
        path : Path to a .md file. Read immediately at registration time.
        """
        with open(path) as f:
            content = f.read().strip()
        if content:
            self._global_skills.append(content)
        self._system_msg = None  # invalidate cache

    def register_tool(
        self,
        name: str,
        handler: Any,   # ToolHandler — Any to avoid Protocol runtime overhead
        *,
        description: str = "",
        args: dict[str, ArgSchema] | None = None,
        args_schema: dict[str, str] | None = None,
        memory_contract: bool = False,
        safety_checker: Any | None = None,   # SafetyChecker
        skill_guidance: str = "",
        parallel_safe: bool = False,
        retry_on_needs_input: bool = False,
        critique_enabled: bool = False,
        requires_hat: str | None = None,
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
        requires_hat:         hat name Forge must activate before dispatch
        """
        # Resolve skill_guidance from file if it looks like a path
        if skill_guidance and not skill_guidance.strip().startswith(("#", "\n", " ")):
            import os
            if os.path.isfile(skill_guidance):
                with open(skill_guidance) as f:
                    skill_guidance = f.read()

        self._registry.register(
            name,
            handler,
            description=description,
            args=args,
            args_schema=args_schema,
            memory_contract=memory_contract,
            safety_checker=safety_checker,
            skill_guidance=skill_guidance,
            parallel_safe=parallel_safe,
            retry_on_needs_input=retry_on_needs_input,
            critique_enabled=critique_enabled,
            requires_hat=requires_hat,
        )
        self._system_msg = None   # invalidate cached system prompt

    def register_tools_from_config(
        self,
        config: str | dict,
        *,
        base_dir: str | None = None,
    ) -> None:
        """
        Register tools from a YAML file path or a dict.

        Parameters
        ----------
        config   : Path to a YAML file, or a dict already parsed from YAML.
        base_dir : Base directory for resolving relative skill_guidance paths.
                   Defaults to the directory containing the config file (if path
                   given) or the current working directory.
        """
        import importlib
        import os
        import yaml

        if isinstance(config, str):
            config_path = config
            if base_dir is None:
                base_dir = os.path.dirname(os.path.abspath(config_path))
            with open(config_path) as f:
                data = yaml.safe_load(f)
        else:
            data = config
            if base_dir is None:
                base_dir = os.getcwd()

        for tool_cfg in data.get("tools", []):
            name = tool_cfg["name"]
            handler = _import_symbol(tool_cfg["handler"])
            handler_kwargs = tool_cfg.get("handler_kwargs") or {}
            if handler_kwargs:
                handler = handler(**handler_kwargs)

            # Resolve skill_guidance
            skill_guidance = tool_cfg.get("skill_guidance", "")
            if skill_guidance and os.path.exists(os.path.join(base_dir, skill_guidance)):
                with open(os.path.join(base_dir, skill_guidance)) as f:
                    skill_guidance = f.read()

            # Resolve safety_checker
            safety_checker = None
            if tool_cfg.get("safety_checker"):
                safety_checker = _import_symbol(tool_cfg["safety_checker"])

            self.register_tool(
                name,
                handler,
                memory_contract=bool(tool_cfg.get("memory_contract", False)),
                critique_enabled=bool(tool_cfg.get("critique_enabled", False)),
                skill_guidance=skill_guidance or "",
                safety_checker=safety_checker,
            )

    def _build_tool_schemas(self, active_hats: list[str]) -> list[ToolSchema]:
        excluded = {"save_notes", "get_summary", "get_document"}
        schemas: list[ToolSchema] = []
        for spec in self._registry._tools.values():
            if (
                spec.name.startswith("use_hat_")
                or spec.name.startswith("drop_hat_")
                or spec.name in excluded
            ):
                continue
            schemas.append(
                ToolSchema(
                    name=spec.name,
                    description=spec.description,
                    args=spec.args,
                )
            )
        return schemas

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        context: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        reasoning_sink: "Callable[[str, str], None] | None" = None,
    ) -> TurnResult:
        """
        Process one user message and return a TurnResult.

        session_id:  stable identifier for the user/customer session
        context:     full context store blob (read-only by Forge; Memory.update
                     returns the refreshed blob which Forge adopts locally)
        history:     prior conversation turns for prompt construction
        """
        trace_id = str(uuid.uuid4())
        load_hats = getattr(self._hat_engine, "load_hats", None)
        known = (
            set(load_hats().keys())
            if callable(load_hats)
            else set(context.get("_active_hats", []))
        )
        active_hats: list[str] = [
            h for h in context.get("_active_hats", []) if h in known
        ]
        hat_rounds: dict[str, int] = dict(context.get("_hat_rounds", {}))
        tool_calls: list[ToolCall] = []
        events: list[TurnEvent] = []
        artifacts: dict[str, str] = {}
        reply = ""
        _pending_correction: dict | None = None   # tool name + concern for next re-call

        system_msg = self._build_active_system_msg(active_hats)
        prompt = self._build_initial_prompt(user_message, history or [])
        memory_snapshot = self._memory.assemble(
            session_id=session_id,
            context=context,
            user_message=user_message,
        )
        suggestions = self._get_transition_suggestions(active_hats, user_message)
        if suggestions and not self._auto_coordinate:
            events.append(
                TurnEvent(
                    type="status",
                    message=_format_suggested_hats(suggestions),
                    data={"suggestions": suggestions},
                )
            )
        for hat_name in list(active_hats):
            coord = self._get_coordination_rules(hat_name)
            triggers = coord.get("triggers", [])
            msg_lower = user_message.lower()
            triggered = any(
                any(w.strip() in msg_lower for w in t.lower().split(","))
                for t in triggers
            )
            if not triggered:
                continue

            if self._auto_coordinate:
                for rec in coord.get("recommended_hats", []):
                    if rec in _MANUAL_ONLY_HATS or rec in active_hats:
                        continue
                    try:
                        active_hats = self._hat_engine.apply_hat(active_hats, rec)
                        logger.info(
                            "Auto-coordinate activated hat '%s' via coordination rule of '%s' session=%s",
                            rec,
                            hat_name,
                            session_id,
                        )
                        message = f"Auto-activating expert '{rec}' for this request."
                        events.append(
                            TurnEvent(
                                type="status",
                                message=message,
                                data={"message": message},
                            )
                        )
                        events.append(self._hat_activate_event(rec))
                    except ValueError:
                        pass

                for par in coord.get("parallel_with", []):
                    if par in _MANUAL_ONLY_HATS or par in active_hats:
                        continue
                    try:
                        active_hats = self._hat_engine.apply_hat(active_hats, par)
                        logger.info(
                            "Auto-coordinate activated parallel hat '%s' via coordination rule of '%s' session=%s",
                            par,
                            hat_name,
                            session_id,
                        )
                        message = f"Running '{par}' in parallel with '{hat_name}'."
                        events.append(
                            TurnEvent(
                                type="status",
                                message=message,
                                data={"message": message},
                            )
                        )
                        events.append(self._hat_activate_event(par))
                    except ValueError:
                        pass
            else:
                recommended = coord.get("recommended_hats", [])
                inactive = [h for h in recommended if h not in active_hats]
                if inactive:
                    message = f"Coordination: '{hat_name}' suggests activating {inactive}"
                    events.append(
                        TurnEvent(
                            type="status",
                            message=message,
                            data={"message": message},
                        )
                    )
                parallel = coord.get("parallel_with", [])
                inactive_parallel = [h for h in parallel if h not in active_hats]
                if inactive_parallel:
                    message = (
                        f"Parallel opportunity: {inactive_parallel} can run alongside "
                        f"'{hat_name}'"
                    )
                    events.append(
                        TurnEvent(
                            type="status",
                            message=message,
                            data={"message": message},
                        )
                    )

        # Step 3: hat-selection planning (one LLM call, fires once per turn)
        if self._step3_planning:
            prompt = await self._run_step3_planning(
                prompt=prompt,
                user_message=user_message,
                active_hats=active_hats,
                memory_snapshot=memory_snapshot,
                session_id=session_id,
                events=events,
                reasoning_sink=reasoning_sink,
            )

        _tool_retry_counts: dict[str, int] = {}
        _approved_tools: set[str] = set()

        for iteration in range(self._max_iterations):

            # Stale-hat warning (no side effect — caller logs if desired)
            for h in active_hats:
                hat_rounds[h] = hat_rounds.get(h, 0) + 1
            stale = self._hat_engine.warn_stale_hats(active_hats, hat_rounds)
            if stale:
                logger.warning(
                    "Stale hats active > 5 rounds: %s session=%s", stale, session_id
                )

            # loop_iteration visibility event
            events.append(
                TurnEvent(
                    type="loop_iteration",
                    message=(
                        f"Iteration {iteration + 1}/{self._max_iterations}"
                        + (f" — hats: {', '.join(active_hats)}" if active_hats else "")
                    ),
                    data={
                        "iteration": iteration,
                        "max_iterations": self._max_iterations,
                        "active_hats": list(active_hats),
                    },
                )
            )

            # Per-round prompt enrichment (memory summary, decision context, etc.)
            enriched = (
                self._prompt_enricher(prompt, memory_snapshot)
                if self._prompt_enricher
                else prompt
            )
            memory_prefix = self._build_memory_prefix(active_hats, memory_snapshot)
            # MEMORY VIEW blocks stay in the user prompt; expert blocks stay in system.
            prompt_for_llm = memory_prefix + self._hat_engine.inject_hats(
                enriched, active_hats
            )
            system_msg = self._build_active_system_msg(active_hats)

            if reasoning_sink:
                reasoning_sink("Thinking...", "orchestrator")

            if self._tool_runner is not None:
                schemas = self._build_tool_schemas(active_hats)
                result = await self._tool_runner(
                    prompt_for_llm, system_msg, schemas, "orchestrator"
                )
                if isinstance(result, str):
                    reply = result.strip()
                    break
                parsed = result
            else:
                # Text-based fallback — used in tests and when tool_runner is not configured
                raw = await self._text_runner(prompt_for_llm, system_msg, "orchestrator")
                parsed = _parse_tool_call(raw)
                if parsed is _NO_TOOL:
                    reply = raw.strip()
                    break

            # ── Plain reply — done ────────────────────────────────────────────
            if parsed is _NO_TOOL:
                break

            tool_name: str = parsed.get("tool", "")
            if reasoning_sink and tool_name:
                reasoning_sink(f"→ {tool_name.replace('_', ' ')}", "tool_selected")
            if tool_name in _approved_tools:
                # This tool was already called and approved this turn.
                # The orchestrator has no more actions to take — return the result.
                break
            tool_args: dict[str, Any] = dict(parsed.get("args") or {})

            if reasoning_sink and tool_name:
                reasoning_sink(f"→ {tool_name.replace('_', ' ')}", "tool_selected")

            # ── Hat activation ────────────────────────────────────────────────
            if tool_name.startswith("use_hat_"):
                hat_name = tool_name[len("use_hat_"):]
                try:
                    active_hats = self._hat_engine.apply_hat(active_hats, hat_name)
                    logger.info(
                        "Hat transition: %s → active_hats=%s session=%s",
                        hat_name,
                        active_hats,
                        session_id,
                    )
                    events.append(self._hat_activate_event(hat_name))
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
                logger.info(
                    "Hat transition: %s → active_hats=%s session=%s",
                    hat_name,
                    active_hats,
                    session_id,
                )
                events.append(
                    TurnEvent(
                        type="hat_drop",
                        message=f"Hat '{hat_name}' deactivated.",
                        data={"hat": hat_name},
                    )
                )
                hat_rounds.pop(hat_name, None)
                suggested_next_hat = self._get_suggested_next_hat(hat_name)
                if suggested_next_hat:
                    events.append(
                        TurnEvent(
                            type="status",
                            message=f"Suggested hats: {suggested_next_hat}",
                            data={
                                "dropped_hat": hat_name,
                                "suggested_hat": suggested_next_hat,
                            },
                        )
                    )
                handoff_msg = self._get_handoff_message(hat_name)
                if handoff_msg:
                    events.append(
                        TurnEvent(
                            type="status",
                            message=handoff_msg,
                            data={"message": handoff_msg},
                        )
                    )
                coord = self._get_coordination_rules(hat_name)
                synthesis = coord.get("synthesis_step")
                if synthesis:
                    logger.debug(
                        "Hat '%s' dropped; synthesis step pending: %s session=%s",
                        hat_name,
                        synthesis,
                        session_id,
                    )
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
            # ── Hat auto-activation (requires_hat gate) ───────────────────────
            if spec.requires_hat and spec.requires_hat not in active_hats:
                try:
                    active_hats = self._hat_engine.apply_hat(active_hats, spec.requires_hat)
                    logger.info(
                        "[FORGE] Auto-activated required hat '%s' for tool '%s' session=%s",
                        spec.requires_hat, tool_name, session_id,
                    )
                    events.append(
                        TurnEvent(
                            type="hat_auto_activated",
                            message=(
                                f"Hat '{spec.requires_hat}' auto-activated "
                                f"(required by tool '{tool_name}')"
                            ),
                            data={"hat": spec.requires_hat, "tool": tool_name},
                        )
                    )
                except ValueError:
                    logger.warning(
                        "[FORGE] Tool '%s' requires unknown hat '%s' — proceeding without",
                        tool_name, spec.requires_hat,
                    )

            # ── Correction injection ──────────────────────────────────────────
            if (
                _pending_correction is not None
                and _pending_correction.get("tool") == tool_name
            ):
                concern = _pending_correction["concern"]
                if concern:
                    task_key = "prompt" if "prompt" in tool_args else "task"
                    tool_args = {
                        **tool_args,
                        "_forge_correction": concern,
                    }
                    logger.info(
                        "[FORGE] Injecting correction into '%s' args session=%s: %s",
                        tool_name, session_id, concern,
                    )
                _pending_correction = None

            # Inject skill_guidance into the task/prompt arg before dispatch.
            if spec.skill_guidance:
                task_key = "prompt" if "prompt" in tool_args else "task"
                existing = str(tool_args.get(task_key) or "")
                tool_args = {
                    **tool_args,
                    task_key: f"{spec.skill_guidance}\n\n{existing}".strip(),
                }

            # Step 4: expert pre-action thinking (fires for any domain tool when hat active)
            expert_hats_active = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
            if self._pre_action_always and not expert_hats_active:
                prompt = await self._run_pre_action_light(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    session_id=session_id,
                    events=events,
                )
            if expert_hats_active:
                prompt, clarification_needed = await self._run_expert_pre_action(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    active_hats=active_hats,
                    session_id=session_id,
                    events=events,
                    iteration=iteration,
                    reasoning_sink=reasoning_sink,
                )
                if clarification_needed:
                    reply = clarification_needed
                    break

            if reasoning_sink:
                reasoning_sink(f"Running {tool_name.replace('_', ' ')}...", "tool_running")

            mem = memory_snapshot if spec.memory_contract else None
            try:
                if reasoning_sink:
                    reasoning_sink(f"Running {tool_name.replace('_', ' ')}...", "tool_running")
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

            # ── Parallel group dispatch ───────────────────────────────────────────
            if result.status == "parallel" and result.parallel_tools:
                parallel_results = await asyncio.gather(*[
                    self.invoke_tool(
                        pt.tool,
                        dict(pt.args),
                        session_id=session_id,
                        context=context,
                        trace_id=trace_id,
                    )
                    for pt in result.parallel_tools
                ])
                memory_updated = False
                for pt, pr in zip(result.parallel_tools, parallel_results):
                    tool_calls.append(
                        ToolCall(tool=pt.tool, args=pt.args, result=pr, iteration=iteration)
                    )
                    if pr.artifact_key and pr.status == "ok":
                        artifacts[pt.tool] = pr.artifact_key
                    if pr.status == "ok" and self._registry.requires_memory(pt.tool):
                        context = self._memory.update(
                            session_id=session_id,
                            tool_name=pt.tool,
                            result=pr,
                            context=context,
                        )
                        memory_updated = True
                if memory_updated:
                    memory_snapshot = self._memory.assemble(
                        session_id=session_id,
                        context=context,
                        user_message=user_message,
                    )
                combined_summary = "; ".join(
                    f"{pt.tool}: {pr.summary}"
                    for pt, pr in zip(result.parallel_tools, parallel_results)
                )
                tool_calls.append(
                    ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
                )
                prompt = _append_result(prompt, tool_name, combined_summary)
                continue

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

            # Step 6: expert post-review, then critic pass
            if reasoning_sink and spec.critique_enabled and result.status == "ok":
                reasoning_sink("Reviewing result...", "tool_review")
            if spec.critique_enabled and result.status == "ok":
                if reasoning_sink:
                    reasoning_sink("Reviewing result...", "tool_review")
                prompt, review_decision = await self._run_expert_post_review(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                    events=events,
                    memory_snapshot=memory_snapshot,
                    reasoning_sink=reasoning_sink,
                )
                if review_decision == "surface":
                    # Expert found an unfixable gap — surface to user
                    surface_msg = prompt.rsplit("EXPERT_REVIEW (surface):", 1)[-1].strip()
                    reply = surface_msg
                    break
                if review_decision == "iterate":
                    _tool_retry_counts[tool_name] = _tool_retry_counts.get(tool_name, 0) + 1
                    if _tool_retry_counts[tool_name] <= self._max_critique_retries:
                        # Extract the concern from the prompt (appended by post-review).
                        _iterate_concern = ""
                        if "EXPERT_REVIEW (iterate):" in prompt:
                            _iterate_concern = (
                                prompt.rsplit("EXPERT_REVIEW (iterate):", 1)[-1]
                                .splitlines()[0]
                                .strip()
                            )
                        _concern_clause = (
                            f" Specifically: {_iterate_concern}" if _iterate_concern else ""
                        )
                        prompt = (
                            f"{prompt}\n\n"
                            f"CORRECTION REQUIRED: The expert review found a fixable problem "
                            f"with the last '{tool_name}' call.{_concern_clause}\n"
                            f"Re-call '{tool_name}' now with corrected arguments that directly "
                            f"address this concern. Output ONLY the corrected tool call JSON."
                        )
                        _pending_correction = {
                            "tool": tool_name,
                            "concern": _iterate_concern,
                        }
                        continue
                    # Retry cap reached — accept result and proceed to critique pass
                    review_decision = "approved"
                # "approved" — fire the critic
                prompt, active_hats = await self._run_critique_pass(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                )
                _approved_tools.add(tool_name)

        context["_active_hats"] = active_hats
        context["_hat_rounds"] = hat_rounds

        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            artifacts=artifacts,
            history_length=len(history or []) + 1,
            events=events,
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

    async def _run_step3_planning(
        self,
        *,
        prompt: str,
        user_message: str,
        active_hats: list[str],
        memory_snapshot: "MemorySnapshot | None",
        session_id: str,
        events: list,
        reasoning_sink=None,
    ) -> str:
        """
        Step 3 of the manager reasoning loop: hat-selection planning.

        Fires once at the start of run_turn() before the main loop.
        Asks the manager to reason through Steps 1-3 (understand request,
        assess memory, plan and select hat) and appends the output as
        STEP3_PLANNING to the prompt for the main loop to use.

        Returns the updated prompt. No-op on exception (returns prompt unchanged).
        """
        hat_names = self._hat_engine.get_hat_tool_definitions()
        available = (
            ", ".join(
                t.get("name", "") for t in hat_names
                if t.get("name", "").startswith("use_hat_")
            )
            if hat_names else "(none registered)"
        )
        already_active = (
            f"Currently active hats: {', '.join(active_hats)}." if active_hats
            else "No hats are currently active."
        )

        planning_prompt = (
            f"{prompt}\n\n"
            "╔══════════════════════════════════╗\n"
            "║  STEP 3 — PLANNING               ║\n"
            "╚══════════════════════════════════╝\n"
            "Before entering the execution loop, reason through Steps 1–3:\n\n"
            "STEP 1 — UNDERSTAND:\n"
            "- What is the user's real goal? Name the deliverable "
            "(BOM, diagram, Terraform, POV, JEP, WAF review, or none).\n"
            "- Is this a new request, a revision, or a clarification?\n"
            "- Is anything ambiguous? If so, what is missing?\n\n"
            "STEP 2 — MEMORY ASSESSMENT:\n"
            "- What facts are already confirmed (shapes, region, services, "
            "budget, HA mode, customer name, compliance scope)?\n"
            "- What is missing or unconfirmed?\n"
            "- Is there enough to produce a complete deliverable, or must you ask first?\n\n"
            "STEP 3 — PLAN + HAT SELECTION:\n"
            f"- {already_active}\n"
            f"- Available hats: {available}\n"
            "- Which hat (if any) should you activate for this request, and why?\n"
            "- What is your execution plan?\n\n"
            "Output your reasoning as plain text using the labeled sections above.\n"
            "Do NOT call a tool here."
        )
        system_msg = self._build_active_system_msg(active_hats)

        try:
            if reasoning_sink:
                reasoning_sink("Planning approach...", "step3_planning")
            raw = await self._text_runner(planning_prompt, system_msg, "step3_planning")
        except Exception:
            logger.exception("[STEP3_PLANNING] Call failed session=%s", session_id)
            return prompt

        planning_text = raw.strip()
        if not planning_text:
            return prompt
        # Section-header guard: all 3 planning sections must be present.
        missing_planning = [h for h in _STEP3_PLANNING_HEADERS if h not in planning_text]
        if missing_planning:
            logger.warning(
                "[STEP3_PLANNING] Missing sections %s session=%s — retrying",
                missing_planning, session_id,
            )
            planning_retry_prompt = (
                f"{planning_prompt}\n\n"
                f"[Your response is missing required sections: {', '.join(missing_planning)}. "
                "You MUST include all three labeled sections exactly as shown: "
                "STEP 1 — UNDERSTAND:, STEP 2 — MEMORY ASSESSMENT:, "
                "STEP 3 — PLAN + HAT SELECTION:. Rewrite with all three sections present.]"
            )
            try:
                raw = await self._text_runner(
                    planning_retry_prompt, system_msg, "step3_planning_header_retry"
                )
                planning_text = raw.strip()
            except Exception:
                logger.exception("[STEP3_PLANNING] Header retry failed session=%s", session_id)
            still_missing = [h for h in _STEP3_PLANNING_HEADERS if h not in planning_text]
            if still_missing:
                logger.warning(
                    "[STEP3_PLANNING] Still missing sections %s after retry session=%s",
                    still_missing, session_id,
                )

        logger.info("[STEP3_PLANNING] session=%s:\n%s", session_id, planning_text)
        events.append(
            TurnEvent(
                type="step3_planning",
                message="Step 3 planning — hat selection and execution plan",
                data={"planning": planning_text, "active_hats": list(active_hats)},
            )
        )
        return f"{prompt}\n\nSTEP3_PLANNING:\n{planning_text}"

    async def _run_pre_action_light(
        self,
        *,
        prompt: str,
        tool_name: str,
        tool_args: dict,
        session_id: str,
        events: list,
    ) -> str:
        """
        Lightweight fallback pre-action reasoning for domain tools with no active
        expert hat. Asks the manager to briefly confirm the tool choice and args
        are correct before dispatch.

        Returns the updated prompt. No-op on exception (returns prompt unchanged).
        """
        light_prompt = (
            f"{prompt}\n\n"
            "╔══════════════════════════════════╗\n"
            "║  PRE-ACTION CHECK                ║\n"
            "╚══════════════════════════════════╝\n"
            f"You are about to call '{tool_name}' with args: {tool_args}\n\n"
            "Before dispatching, briefly confirm:\n"
            "GOAL CHECK: Does this tool directly address the user's current goal?\n"
            "ARGS CHECK: Are the arguments complete and correct?\n"
            "RISK CHECK: Is there any obvious risk or missing information?\n\n"
            "Write 1-2 sentences per check. Output plain text — do NOT call a tool here."
        )
        system_msg = self._get_system_msg()

        try:
            raw = await self._text_runner(light_prompt, system_msg, "pre_action_light")
        except Exception:
            logger.exception(
                "[PRE_ACTION_LIGHT] Call failed session=%s tool=%s", session_id, tool_name
            )
            return prompt

        reasoning = raw.strip()
        if not reasoning:
            return prompt

        if len(reasoning) < 50:
            logger.warning(
                "[PRE_ACTION_LIGHT] Shallow response (%d chars) session=%s tool=%s",
                len(reasoning), session_id, tool_name,
            )

        logger.info(
            "[PRE_ACTION_LIGHT] session=%s tool=%s:\n%s",
            session_id,
            tool_name,
            reasoning,
        )
        events.append(
            TurnEvent(
                type="pre_action_light",
                message=f"Pre-action check for '{tool_name}'",
                data={"tool": tool_name, "reasoning": reasoning},
            )
        )
        return f"{prompt}\n\nPRE_ACTION_CHECK ({tool_name}):\n{reasoning}"

    async def _run_expert_pre_action(
        self,
        *,
        prompt: str,
        tool_name: str,
        tool_args: dict,
        active_hats: list[str],
        session_id: str,
        events: list,
        iteration: int = 0,
        reasoning_sink=None,
    ) -> tuple[str, str | None]:
        """
        Step 4 of the manager reasoning loop: expert pre-action thinking.

        The manager thinks as the active expert before calling a sub-agent.
        Uses a structured 4-section format to force depth: KNOWN FACTS, GAPS,
        EXPERT ASSESSMENT, SUB-AGENT INSTRUCTIONS.

        Returns (updated_prompt, clarification_needed).
        clarification_needed is None when the expert is ready to proceed.
        clarification_needed is a question string when a starred prerequisite is unmet.
        No-op (returns (prompt, None)) when no expert hat is active.
        """
        expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
        if not expert_hats:
            return prompt, None

        hat_label = ", ".join(expert_hats)
        retry_context = ""
        if iteration > 0:
            # Extract the previous review concern from the prompt if present.
            concern = ""
            if "EXPERT_REVIEW (iterate):" in prompt:
                concern = prompt.rsplit("EXPERT_REVIEW (iterate):", 1)[-1].strip()
                # Trim to first line (the concern statement, not subsequent content).
                concern = concern.splitlines()[0].strip() if concern else ""
            retry_context = (
                f"\n\n⚠ RETRY CONTEXT — Attempt {iteration + 1}:\n"
                f"The previous attempt was rejected by the expert reviewer.\n"
                + (f"Reason: {concern}\n" if concern else "")
                + "Your pre-action reasoning and sub-agent instructions must directly "
                "address this failure.\n"
            )
        pre_action_prompt = (
            f"{prompt}{retry_context}\n\n"
            "╔══════════════════════════════════╗\n"
            "║  STEP 4 — EXPERT PRE-ACTION      ║\n"
            "╚══════════════════════════════════╝\n"
            f"You are wearing the [{hat_label}] hat. You ARE the expert.\n"
            f"Before calling '{tool_name}', think as a senior OCI Solutions Architect "
            "who has seen this workload pattern before. Use EXACTLY this structure:\n\n"
            "KNOWN FACTS:\n"
            "- [List every confirmed value from memory and conversation: shape, region, "
            "OCPU, memory, storage, HA mode, budget, compliance scope, customer name. "
            "No vague summaries — specific values only.]\n\n"
            "GAPS:\n"
            "- [List every unconfirmed item from this hat's Pre-Action Checklist. "
            "For each: state what you will DEFAULT and why. "
            "Only flag NEEDS_CLARIFICATION if a default is architecturally unsafe.]\n\n"
            "EXPERT ASSESSMENT:\n"
            "- WORKLOAD PATTERN: [Name the architecture pattern: "
            "3-tier web / microservices / ML inference / data platform / batch / "
            "lift-and-shift / RAG pipeline / hybrid connectivity / other. "
            "State the 1-2 critical requirements this workload must satisfy.]\n"
            "- RECOMMENDATION: [Exact solution: specific OCI services, shapes, SKUs, "
            "quantities, topology tiers. No generic advice.]\n"
            "- WHY THIS APPROACH: [One sentence: why this over the main alternative. "
            "Must reference a specific constraint from KNOWN FACTS or workload pattern.]\n"
            "- TOP RISK: [The most likely failure mode. How you are mitigating it "
            "in your sub-agent instructions.]\n"
            "- PROACTIVE FLAG: [One thing the customer should know that they have not "
            "asked. Frame as: 'Note: <specific concern or recommendation for next step>'. "
            "Example: 'Note: single-AD assumed — if SLA > 99.9%, costs double for HA.' "
            "Example: 'Note: WAF not scoped but public API present — recommend post-BOM.' "
            "If genuinely nothing relevant: None.]\n\n"
            "SUB-AGENT TASK:\n"
            "- [Exact, complete task instruction for the sub-agent. "
            "Include all sizing, shapes, services, constraints from KNOWN FACTS "
            "and your defaults from GAPS. This must be self-contained — "
            "the sub-agent has no other context.]\n\n"
            "Do NOT call a tool here. "
            "If a GAPS item is architecturally unsafe to default "
            "(e.g., GPU shape without cost confirmation, compliance scope that changes design), "
            "output only: NEEDS_CLARIFICATION: <one focused question>"
        )
        system_msg = self._build_active_system_msg(active_hats)

        try:
            if reasoning_sink:
                reasoning_sink("Expert pre-action analysis...", "expert_pre_action")
            raw = await self._text_runner(pre_action_prompt, system_msg, "expert_pre_action")
        except Exception:
            logger.exception(
                "[EXPERT_PRE_ACTION] Call failed session=%s tool=%s", session_id, tool_name
            )
            return prompt, None

        reasoning = raw.strip()

        # Shallow-response guard: retry once if response is too brief.
        if (
            len(reasoning) < _EXPERT_THINKING_MIN_CHARS
            and not reasoning.startswith("NEEDS_CLARIFICATION:")
        ):
            logger.warning(
                "[EXPERT_PRE_ACTION] Shallow response (%d chars) for tool '%s' session=%s — retrying",
                len(reasoning), tool_name, session_id,
            )
            retry_prompt = (
                f"{pre_action_prompt}\n\n"
                "[Your previous response was too brief. A senior expert would write at least "
                "3 specific bullet points per section. Retry with full depth — be specific "
                "about values, part numbers, topologies, or module names as appropriate.]"
            )
            try:
                raw = await self._text_runner(
                    retry_prompt, system_msg, "expert_pre_action_retry"
                )
                reasoning = raw.strip()
            except Exception:
                logger.exception(
                    "[EXPERT_PRE_ACTION] Retry failed session=%s tool=%s",
                    session_id,
                    tool_name,
                )
            if len(reasoning) < _EXPERT_THINKING_MIN_CHARS:
                logger.warning(
                    "[EXPERT_PRE_ACTION] Still shallow after retry (%d chars) session=%s tool=%s",
                    len(reasoning), session_id, tool_name,
                )

        # Section-header guard: all 4 required sections must be present.
        if not reasoning.startswith("NEEDS_CLARIFICATION:"):
            missing = [h for h in _EXPERT_PRE_ACTION_HEADERS if h not in reasoning]
            if missing:
                logger.warning(
                    "[EXPERT_PRE_ACTION] Missing sections %s for tool '%s' session=%s — retrying",
                    missing, tool_name, session_id,
                )
                missing_list = ", ".join(missing)
                header_retry_prompt = (
                    f"{pre_action_prompt}\n\n"
                    f"[Your response is missing required sections: {missing_list}. "
                    "You MUST include all four labeled sections exactly as shown: "
                    "KNOWN FACTS:, GAPS:, EXPERT ASSESSMENT:, SUB-AGENT INSTRUCTIONS:. "
                    "Rewrite with all four sections present.]"
                )
                try:
                    raw = await self._text_runner(
                        header_retry_prompt, system_msg, "expert_pre_action_header_retry"
                    )
                    reasoning = raw.strip()
                except Exception:
                    logger.exception(
                        "[EXPERT_PRE_ACTION] Header retry failed session=%s tool=%s",
                        session_id, tool_name,
                    )
                still_missing = [h for h in _EXPERT_PRE_ACTION_HEADERS if h not in reasoning]
                if still_missing:
                    logger.warning(
                        "[EXPERT_PRE_ACTION] Still missing sections %s after retry session=%s tool=%s",
                        still_missing, session_id, tool_name,
                    )

        if reasoning.startswith("NEEDS_CLARIFICATION:"):
            clarification = reasoning[len("NEEDS_CLARIFICATION:"):].strip()
            logger.info(
                "[EXPERT_PRE_ACTION] [%s] tool='%s' session=%s → NEEDS_CLARIFICATION: %s",
                hat_label, tool_name, session_id, clarification,
            )
            events.append(
                TurnEvent(
                    type="expert_pre_action",
                    message=f"Expert pre-action [{hat_label}]: clarification needed",
                    data={"hat": hat_label, "tool": tool_name, "clarification": clarification},
                )
            )
            return prompt, clarification

        if reasoning:
            logger.info(
                "[EXPERT_PRE_ACTION] [%s] tool='%s' session=%s:\n%s",
                hat_label, tool_name, session_id, reasoning,
            )
            events.append(
                TurnEvent(
                    type="expert_pre_action",
                    message=f"Expert pre-action [{hat_label}] for '{tool_name}'",
                    data={"hat": hat_label, "tool": tool_name, "reasoning": reasoning},
                )
            )
            prompt = f"{prompt}\n\nEXPERT_THINKING:\n{reasoning}"
        return prompt, None

    async def _run_expert_post_review(
        self,
        *,
        prompt: str,
        tool_name: str,
        result: ToolResult,
        active_hats: list[str],
        session_id: str,
        events: list,
        memory_snapshot: MemorySnapshot | None = None,
        reasoning_sink=None,
    ) -> tuple[str, str]:
        """
        Step 6 of the manager reasoning loop: expert post-action review.

        The manager, still wearing the active expert hat, reviews the sub-agent
        result against the hat's Quality Bar, Post-Action Review checklist, and
        in-scope memory snapshot values.

        Returns:
            (updated_prompt, decision)
            decision is one of:
              "approved"  — all checks pass; critic may fire
              "iterate"   — fixable gap found; caller should retry the tool
              "surface"   — unfixable gap; caller should return to user

        Logs expert review at INFO level and appends an expert_post_review
        event. No-op (returns "approved") when no expert hat is active.
        """
        expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
        if not expert_hats:
            return prompt, "approved"

        hat_label = ", ".join(expert_hats)
        memory_context = ""
        if memory_snapshot is not None:
            formatted = getattr(memory_snapshot, "formatted", "") or ""
            if formatted.strip():
                memory_context = (
                    "\n\nMEMORY SNAPSHOT (confirmed values from this session):\n"
                    f"{formatted.strip()}\n"
                )

        review_prompt = (
            f"{prompt}{memory_context}\n\n"
            "╔══════════════════════════════════╗\n"
            "║  STEP 6 — EXPERT POST-REVIEW     ║\n"
            "╚══════════════════════════════════╝\n"
            f"You are wearing the [{hat_label}] hat. You received the result of "
            f"'{tool_name}'. You are NOT rubber-stamping — review honestly.\n\n"
            "PHASE A — Quality Bar check:\n"
            "Work through each item in your hat's ## Quality Bar section.\n"
            "For each item write: PASS or FAIL: <specific value that was wrong>\n\n"
            "PHASE B — Post-Action Review checklist:\n"
            "Work through each item in your hat's ## Post-Action Review section.\n"
            "For each item write: PASS or FAIL: <specific field and expected value>\n\n"
            "PHASE C — Memory consistency check:\n"
            "Compare the result against the MEMORY SNAPSHOT above.\n"
            "Flag any value in the result that contradicts confirmed memory "
            "(e.g. wrong region, wrong shape, wrong HA mode).\n"
            "Write: CONSISTENT or CONFLICT: <field> expected=<memory value> got=<result value>\n\n"
            "PHASE D — Architectural soundness:\n"
            "Step back from the checklists. Is this the right architecture for this customer?\n"
            "- GOAL FIT: Does this output directly serve the customer's stated goal? "
            "Write: YES or CONCERN: <what it misses>\n"
            "- ANTIPATTERNS: Any single points of failure, missing security controls, "
            "obvious over/under-sizing for the stated workload? "
            "Write: NONE or FLAG: <specific issue and why it matters>\n"
            "- NEXT STEP FLAG: What should the customer do or know next that they "
            "haven't asked about? "
            "Write: NONE or SUGGEST: <specific recommendation>\n"
            "Phase D findings are advisory. They do NOT change the FINAL DECISION above. "
            "Append them after FINAL DECISION so the orchestrator can surface them.\n\n"
            "FINAL DECISION — after completing Phases A, B, and C, output EXACTLY ONE line:\n"
            f"  {_EXPERT_REVIEW_APPROVED}          — every Phase A + B item is PASS and Phase C is CONSISTENT\n"
            f"  {_EXPERT_REVIEW_ITERATE} <issue>    — at least one fixable FAIL or CONFLICT\n"
            f"  {_EXPERT_REVIEW_SURFACE} <issue>    — unfixable gap requiring user clarification\n\n"
            "You MUST complete all three phases before writing the final decision line.\n"
            "Do NOT call a tool here."
        )
        system_msg = self._build_active_system_msg(active_hats)

        try:
            if reasoning_sink:
                reasoning_sink("Expert review...", "expert_post_review")
            raw = await self._text_runner(review_prompt, system_msg, "expert_post_review")
        except Exception:
            logger.exception(
                "[EXPERT_POST_REVIEW] Call failed session=%s tool=%s",
                session_id,
                tool_name,
            )
            return prompt, "approved"

        review_text = raw.strip()
        if len(review_text) < _EXPERT_REVIEW_MIN_CHARS:
            logger.warning(
                "[EXPERT_POST_REVIEW] Shallow response (%d chars) for tool '%s' session=%s — retrying",
                len(review_text), tool_name, session_id,
            )
            retry_prompt = (
                f"{review_prompt}\n\n"
                "[Your response was too brief. You must complete all three phases "
                "(Quality Bar, Post-Action Review, Memory Consistency) with a PASS/FAIL "
                "or CONSISTENT/CONFLICT for every item before writing the final decision.]"
            )
            try:
                raw = await self._text_runner(
                    retry_prompt, system_msg, "expert_post_review_retry"
                )
                review_text = raw.strip()
            except Exception:
                logger.exception(
                    "[EXPERT_POST_REVIEW] Retry failed session=%s tool=%s",
                    session_id,
                    tool_name,
                )
            if len(review_text) < _EXPERT_REVIEW_MIN_CHARS:
                logger.warning(
                    "[EXPERT_POST_REVIEW] Still shallow after retry (%d chars) session=%s",
                    len(review_text), session_id,
                )

        # Find the decision on the LAST non-empty line (after per-item checks).
        lines = [l.strip() for l in review_text.splitlines() if l.strip()]
        final_line = lines[-1] if lines else ""
        decision = "iterate"

        if final_line.startswith(_EXPERT_REVIEW_APPROVED):
            decision = "approved"
        elif final_line.startswith(_EXPERT_REVIEW_ITERATE):
            decision = "iterate"
        elif final_line.startswith(_EXPERT_REVIEW_SURFACE):
            decision = "surface"

        logger.info(
            "[EXPERT_POST_REVIEW] [%s] tool='%s' session=%s decision=%s:\n%s",
            hat_label,
            tool_name,
            session_id,
            decision,
            review_text,
        )
        events.append(
            TurnEvent(
                type="expert_post_review",
                message=f"Expert post-review [{hat_label}] for '{tool_name}': {decision}",
                data={
                    "hat": hat_label,
                    "tool": tool_name,
                    "decision": decision,
                    "review": review_text,
                },
            )
        )

        if final_line.startswith(_EXPERT_REVIEW_ITERATE):
            concern = final_line[len(_EXPERT_REVIEW_ITERATE):].strip()
            prompt = f"{prompt}\n\nEXPERT_REVIEW (iterate): {concern}"
            return prompt, "iterate"

        if final_line.startswith(_EXPERT_REVIEW_SURFACE):
            concern = final_line[len(_EXPERT_REVIEW_SURFACE):].strip()
            prompt = f"{prompt}\n\nEXPERT_REVIEW (surface): {concern}"
            return prompt, "surface"

        if final_line.startswith(_EXPERT_REVIEW_APPROVED):
            return prompt, "approved"

        prompt = (
            f"{prompt}\n\nEXPERT_REVIEW (iterate): "
            "Expert review did not provide a valid final decision."
        )
        return prompt, "iterate"

    async def _run_critique_pass(
        self,
        *,
        prompt: str,
        tool_name: str,
        result: ToolResult,
        active_hats: list[str],
        session_id: str,
    ) -> tuple[str, list[str]]:
        """
        Fire a single critic review after a critique_enabled tool returns ok.

        Returns the updated prompt and active_hats list.
        The critic hat is activated for this call and deactivated immediately after.
        """
        critic_active = False
        try:
            active_hats = self._hat_engine.apply_hat(active_hats, "critic")
            critic_active = True
        except ValueError:
            # No critic hat registered — skip critique silently
            return prompt, active_hats

        critic_prompt = (
            f"{prompt}\n\n"
            "╔══════════════════════════════════╗\n"
            "║  CRITIC REVIEW                   ║\n"
            "╚══════════════════════════════════╝\n"
            f"You are reviewing the result of '{tool_name}'. "
            "You are NOT rubber-stamping.\n\n"
            "Apply your ## Quality Bar section to this result.\n"
            "For each Quality Bar item write: PASS or FAIL: <specific evidence>\n\n"
            "Then write EXACTLY ONE final line — nothing after it:\n"
            f"  {{\"tool\": \"critic_approve\", \"args\": {{}}}}   "
            "— if and only if every Quality Bar item is PASS\n"
            "  <plain-text first FAIL: exact field name and what was wrong>  "
            "— if any item fails\n\n"
            "Rules:\n"
            "- Do NOT approve if any item fails — name the failure.\n"
            "- Cite the specific field, SKU, or value — not vague concern.\n"
            "- Do NOT call any other tool."
        )
        enriched = self._hat_engine.inject_hats(critic_prompt, active_hats)
        system_msg = self._build_active_system_msg(active_hats)

        try:
            raw = await self._text_runner(enriched, system_msg, "critic")
        except Exception:
            logger.exception("Critic pass failed session=%s tool=%s", session_id, tool_name)
            raw = ""
        finally:
            if critic_active:
                active_hats = self._hat_engine.drop_hat(active_hats, "critic")

        parsed = _parse_tool_call(raw)
        if parsed is not _NO_TOOL and parsed.get("tool") == "critic_approve":
            # Approved — no change to prompt
            return prompt, active_hats

        # Critique injected — append as CRITIC note for next round
        critique = raw.strip()
        if critique:
            prompt = f"{prompt}\n\nCRITIC: {critique}"

        return prompt, active_hats

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_system_msg(self) -> str:
        """Build once after all tools are registered; cache thereafter."""
        if self._system_msg is None:
            hat_tools = self._hat_engine.get_hat_tool_definitions()
            self._system_msg = _assemble_system_prompt(
                self._base_system_prompt,
                hat_tools,
                self._registry,
                global_skills=self._global_skills,
            )
        return self._system_msg

    def _build_active_system_msg(self, active_hats: list[str]) -> str:
        """
        Return the system message for one LLM call.
        If hats are active, prepend their expert blocks before the base system msg.
        """
        base = self._get_system_msg()
        if not active_hats:
            return base
        builder = getattr(self._hat_engine, "build_expert_block", None)
        if not callable(builder):
            return base
        blocks = []
        for name in active_hats:
            block = builder(name)
            if block:
                blocks.append(block)
        if not blocks:
            return base
        return "\n\n".join(blocks) + "\n\n" + base

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

    def _get_transition_suggestions(
        self,
        active_hats: list[str],
        turn_message: str,
    ) -> list[dict[str, str]]:
        suggester = getattr(self._hat_engine, "get_transition_suggestions", None)
        if not callable(suggester):
            return []
        return list(suggester(active_hats, turn_message))

    def _get_suggested_next_hat(self, name: str) -> str | None:
        suggester = getattr(self._hat_engine, "get_suggested_next_hat", None)
        if not callable(suggester):
            return None
        suggested = suggester(name)
        return suggested if isinstance(suggested, str) and suggested else None

    def _get_coordination_rules(self, name: str) -> dict:
        getter = getattr(self._hat_engine, "get_coordination_rules", None)
        if not callable(getter):
            return {}
        rules = getter(name)
        return rules if isinstance(rules, dict) else {}

    def _hat_activate_event(self, name: str) -> TurnEvent:
        getter = getattr(self._hat_engine, "get_hat_meta", None)
        meta = getter(name) if callable(getter) else {}
        if not isinstance(meta, dict):
            meta = {}
        display_name = str(meta.get("display_name") or name)
        return TurnEvent(
            type="hat_activate",
            message=f"Hat '{display_name}' activated.",
            data={"hat": name, "display_name": display_name},
        )

    def _get_parallel_hats(self, name: str) -> list[str]:
        getter = getattr(self._hat_engine, "get_parallel_hats", None)
        if not callable(getter):
            return []
        hats = getter(name)
        return list(hats) if isinstance(hats, list) else []

    def _get_handoff_message(self, name: str) -> str | None:
        getter = getattr(self._hat_engine, "get_handoff_message", None)
        if not callable(getter):
            return None
        message = getter(name)
        return message if isinstance(message, str) and message else None

    def _build_memory_prefix(
        self,
        active_hats: list[str],
        memory_snapshot: MemorySnapshot | None,
    ) -> str:
        if not active_hats or memory_snapshot is None:
            return ""
        builder = getattr(self._hat_engine, "build_memory_view_block", None)
        if not callable(builder):
            return ""
        view_blocks = []
        for hat_name in active_hats:
            block = builder(hat_name, memory_snapshot)
            if block:
                view_blocks.append(block)
        if not view_blocks:
            return ""
        return "\n\n".join(view_blocks) + "\n\n"


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


def _format_suggested_hats(suggestions: list[dict[str, str]]) -> str:
    hats = ", ".join(
        suggestion["hat"]
        for suggestion in suggestions
        if suggestion.get("hat")
    )
    return f"Suggested hats: {hats}" if hats else "Suggested hats"


def _assemble_system_prompt(
    base: str,
    hat_tools: list[dict],
    registry: ToolRegistry,
    global_skills: list[str] | None = None,
) -> str:
    parts: list[str] = []

    if base:
        parts.append(base.strip())

    # Global skill files (routing guidance, safety rules, etc.)
    for skill in (global_skills or []):
        if skill.strip():
            parts.append(skill.strip())

    # Tool list with inline skill guidance
    tool_entries = []
    for name, spec in registry._tools.items():
        entry = f"- {name}"
        if spec.description:
            entry += f": {spec.description}"
        if spec.skill_guidance:
            entry += f"\n  Guidance: {spec.skill_guidance.strip()[:300]}"
            if len(spec.skill_guidance) > 300:
                entry += "..."
        tool_entries.append(entry)

    if tool_entries:
        parts.append("Available tools:\n" + "\n".join(tool_entries))

    # Hat tool definitions
    if hat_tools:
        hat_names = [t.get("name", "") for t in hat_tools]
        parts.append("Hat tools (expert lenses): " + ", ".join(hat_names))

    # Format instruction (must always be present — see p2a0)
    parts.append(
        'Tool call format (JSON on a single line):\n{"tool": "<name>", "args": {<key>: <value>}}'
    )

    return "\n\n".join(parts)


def _import_symbol(dotted_path: str) -> Any:
    """Import 'package.module:ClassName' or 'package.module:function'."""
    if ":" not in dotted_path:
        raise ValueError(f"handler must be 'module:symbol', got: {dotted_path!r}")
    module_path, symbol = dotted_path.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, symbol)
