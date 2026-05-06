"""
skillforge/registry.py
-----------------------
ToolRegistry: maps tool names to handlers and their configuration.

This is the "register a domain tool" API that archie_loop.py currently
replaces with hardcoded dicts (_ARCHITECTURE_TOOLS, _MANDATORY_SKILL_FALLBACKS,
_MEMORY_CONTRACT_TOOLS). The registry makes all of that explicit and extensible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from skillforge.protocols import SafetyChecker, ToolHandler


@dataclass
class ToolSpec:
    """Everything Forge needs to know about one registered domain tool."""
    name: str
    handler: ToolHandler
    description: str = ""           # injected into system prompt tool-contract block
    args_schema: dict = field(default_factory=dict)  # {"key": "description"} pairs
    memory_contract: bool = False   # inject MemorySnapshot before calling handler
    safety_checker: SafetyChecker | None = None
    skill_guidance: str = ""        # markdown prepended to the specialist task string
    parallel_safe: bool = False     # may run concurrently with other parallel_safe tools


class ToolRegistry:
    """
    Lightweight dict-backed registry. Not thread-safe (single-process, async).

    Usage:
        registry = ToolRegistry()
        registry.register(
            "generate_bom",
            handler=bom_handler,
            description='{"prompt": "<workload sizing>"}',
            memory_contract=True,
            safety_checker=bom_safety_check,
            skill_guidance=BOM_SKILL_MD,
        )
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
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
        if name in self._tools:
            raise ValueError(f"Tool {name!r} is already registered")
        self._tools[name] = ToolSpec(
            name=name,
            handler=handler,
            description=description,
            args_schema=args_schema or {},
            memory_contract=memory_contract,
            safety_checker=safety_checker,
            skill_guidance=skill_guidance,
            parallel_safe=parallel_safe,
        )

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def requires_memory(self, name: str) -> bool:
        spec = self._tools.get(name)
        return spec is not None and spec.memory_contract

    def tool_contract_block(self) -> str:
        """
        Returns the tool-contract section to append to the system prompt.
        Format matches what archie_loop.py already uses:
            - tool_name {"arg": "description"}
        """
        lines: list[str] = []
        for spec in self._tools.values():
            if spec.description:
                lines.append(f"- {spec.name} {spec.description}")
            else:
                schema = " ".join(f'"{k}": "<{v}>"' for k, v in spec.args_schema.items())
                lines.append(f"- {spec.name} {{{schema}}}")
        return "\n".join(lines)

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
