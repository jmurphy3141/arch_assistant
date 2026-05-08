"""
SkillForge — domain-agnostic polymath agent orchestration framework.

Public API:

    from skillforge import Forge, ToolResult, TurnResult, MemorySnapshot, ToolStatus
    from skillforge.protocols import ToolHandler, Memory, SafetyChecker, HatEngine, PromptEnricher

    forge = Forge(
        base_system_prompt=MY_SYSTEM_PROMPT,
        hat_engine=hat_engine,
        memory=MyMemory(),
        text_runner=my_async_llm_call,
    )
    forge.register_tool("my_tool", my_handler, memory_contract=True,
                        description='{"arg": "<description>"}')
    result = await forge.run_turn(session_id=..., user_message=..., context={})
"""

from skillforge.types import (
    MemorySnapshot,
    ToolCall,
    ToolResult,
    ToolStatus,
    TurnEvent,
    TurnResult,
)
from skillforge.forge import Forge
from skillforge.memory import SimpleMemory
from skillforge.delegate import A2ADelegate

__all__ = [
    "A2ADelegate",
    "Forge",
    "MemorySnapshot",
    "SimpleMemory",
    "ToolCall",
    "ToolResult",
    "ToolStatus",
    "TurnEvent",
    "TurnResult",
]
