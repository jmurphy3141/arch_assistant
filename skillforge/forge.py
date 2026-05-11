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
        auto_coordinate: bool = True,
    ) -> None:
        self._base_system_prompt = base_system_prompt
        self._hat_engine = hat_engine
        self._memory = memory
        self._text_runner = text_runner
        self._prompt_enricher = prompt_enricher
        self._max_iterations = max_iterations
        self._history_window = history_window
        self._auto_coordinate = auto_coordinate
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
            args_schema=args_schema,
            memory_contract=memory_contract,
            safety_checker=safety_checker,
            skill_guidance=skill_guidance,
            parallel_safe=parallel_safe,
            retry_on_needs_input=retry_on_needs_input,
            critique_enabled=critique_enabled,
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
            memory_prefix = self._build_memory_prefix(active_hats, memory_snapshot)
            # MEMORY VIEW blocks stay in the user prompt; expert blocks stay in system.
            prompt_for_llm = memory_prefix + self._hat_engine.inject_hats(
                enriched, active_hats
            )
            system_msg = self._build_active_system_msg(active_hats)

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

            # Inject skill_guidance into the task/prompt arg before dispatch.
            if spec.skill_guidance:
                task_key = "prompt" if "prompt" in tool_args else "task"
                existing = str(tool_args.get(task_key) or "")
                tool_args = {
                    **tool_args,
                    task_key: f"{spec.skill_guidance}\n\n{existing}".strip(),
                }

            # Step 4: expert pre-action thinking (fires for critique-enabled tools)
            if spec.critique_enabled:
                prompt = await self._run_expert_pre_action(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    active_hats=active_hats,
                    session_id=session_id,
                )

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

            # Post-tool critic pass
            if spec.critique_enabled and result.status == "ok":
                prompt, active_hats = await self._run_critique_pass(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                )

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

    async def _run_expert_pre_action(
        self,
        *,
        prompt: str,
        tool_name: str,
        tool_args: dict,
        active_hats: list[str],
        session_id: str,
    ) -> str:
        """
        Step 4 of the manager reasoning loop: expert pre-action thinking.

        The manager thinks as the active expert before calling a sub-agent.
        Covers: known facts, gaps, approach, and instructions for the sub-agent.
        Output is appended to prompt as EXPERT_THINKING and logged at INFO.
        No-op when no expert hat is active.
        """
        expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
        if not expert_hats:
            return prompt

        hat_label = ", ".join(expert_hats)
        pre_action_prompt = (
            f"{prompt}\n\n[STEP 4 — EXPERT PRE-ACTION THINKING]\n"
            f"You are wearing the {hat_label} hat. Before calling '{tool_name}', "
            "think deeply as the expert. Cover:\n"
            "1. Known facts: what has been confirmed in this session?\n"
            "2. Gaps: does this hat's Pre-Action Checklist have any unmet items?\n"
            "3. Approach: as the expert, what is the right solution?\n"
            "4. Instructions: what precise task will you give the sub-agent?\n"
            "Output your reasoning as plain text. Do NOT call a tool here."
        )
        system_msg = self._build_active_system_msg(active_hats)

        try:
            raw = await self._text_runner(pre_action_prompt, system_msg, "expert_pre_action")
        except Exception:
            logger.exception(
                "Expert pre-action call failed session=%s tool=%s", session_id, tool_name
            )
            return prompt

        reasoning = raw.strip()
        if reasoning:
            logger.info("Expert pre-action [%s] for tool '%s' session=%s:\n%s",
                hat_label,
                tool_name,
                session_id,
                reasoning,
            )
            prompt = f"{prompt}\n\nEXPERT_THINKING:\n{reasoning}"
        return prompt

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
            f"{prompt}\n\n[CRITIC REVIEW REQUEST]\n"
            f"Review the result of '{tool_name}' above.\n"
            f"If the result is acceptable, call: {{\"tool\": \"critic_approve\", \"args\": {{}}}}\n"
            f"If you have concerns, describe them as plain text."
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
