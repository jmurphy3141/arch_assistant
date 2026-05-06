"""
SkillForge — domain-agnostic polymath orchestrator framework.

Public API surface:

    from skillforge import Forge, ToolResult, TurnResult, MemorySnapshot

    forge = Forge(system_prompt=..., hat_engine=..., memory=..., store=...)
    forge.register_tool("generate_bom", bom_handler, memory_contract=True)
    result = await forge.run_turn(user_message=..., session_id=..., ...)
"""

from skillforge.types import MemorySnapshot, ToolResult, TurnResult, ToolCall
from skillforge.forge import Forge

__all__ = ["Forge", "MemorySnapshot", "ToolResult", "TurnResult", "ToolCall"]
