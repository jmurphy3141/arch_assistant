"""
archie_wiring.py - wire every OCI tool handler into a Forge instance.

Call build_forge() once per customer session to get a fully configured Forge.
archie_loop.py imports build_forge() for the p2i cutover task.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import agent.hat_engine as hat_engine
from agent.archie_memory_impl import ArchieMemory
from agent.persistence_objectstore import ObjectStoreBase
from agent.tools.bom import BomHandler
from agent.tools.diagram import DiagramHandler
from agent.tools.notes import NotesHandlers
from agent.tools.specialists import JepHandler, PovHandler, WafHandler
from agent.tools.terraform import TerraformHandler
from skillforge import Forge
from skillforge.types import MemorySnapshot


_INTENT_ROUTING_SKILL = Path(__file__).parent.parent / "skills" / "intent_routing.md"


class ArchiePromptEnricher:
    """
    Injects per-round OCI context into the prompt before each LLM call.

    Forge calls this before every ReAct round. This keeps OCI-specific
    prompt assembly out of Forge core.

    Injects:
      - decision_context summary (constraints, approved region, sizing)
      - facts_summary (accumulated SA-provided facts)
    """

    def __call__(self, prompt: str, memory: MemorySnapshot) -> str:
        parts: list[str] = []

        facts_summary = str((memory.facts or {}).get("facts_summary") or "").strip()
        if facts_summary:
            parts.append(f"[Archie Facts]\n{facts_summary}\n[/Archie Facts]")

        if memory.constraints:
            import json

            constraints_text = json.dumps(memory.constraints, ensure_ascii=False)
            parts.append(
                f"[Archie Constraints]\n{constraints_text}\n[/Archie Constraints]"
            )

        if not parts:
            return prompt
        return "\n\n".join(parts) + "\n\n" + prompt


def build_forge(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    text_runner: Callable,
    a2a_base_url: str = "",
    base_system_prompt: str = "",
) -> Forge:
    """
    Instantiate and return a Forge wired with all OCI tool handlers.

    Parameters
    ----------
    store              : OCI Object Storage adapter (or in-memory stub for tests)
    customer_id        : Archie customer/engagement ID
    customer_name      : Human-readable customer name (for context hydration)
    text_runner        : async (prompt, system_msg, label) -> str  (LLM call)
    a2a_base_url       : Base URL for A2A sub-agent calls
    base_system_prompt : Archie orchestrator system prompt
    """
    memory = ArchieMemory(store=store)
    enricher = ArchiePromptEnricher()
    routing_guidance = ""
    if _INTENT_ROUTING_SKILL.exists():
        routing_guidance = _INTENT_ROUTING_SKILL.read_text()

    full_prompt = (routing_guidance + "\n\n" + base_system_prompt).strip()

    forge = Forge(
        base_system_prompt=full_prompt,
        hat_engine=hat_engine,
        memory=memory,
        text_runner=text_runner,
        prompt_enricher=enricher,
        max_iterations=5,
        step3_planning=True,
    )

    notes = NotesHandlers(
        store=store, customer_id=customer_id, customer_name=customer_name
    )
    forge.register_tool("save_notes", notes.save_notes, memory_contract=True)
    forge.register_tool("get_summary", notes.get_summary)
    forge.register_tool("get_document", notes.get_document)

    forge.register_tool(
        "generate_bom",
        BomHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        memory_contract=True,
        critique_enabled=True,
    )
    forge.register_tool(
        "generate_diagram",
        DiagramHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        memory_contract=True,
        critique_enabled=True,
    )
    forge.register_tool(
        "generate_terraform",
        TerraformHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        memory_contract=True,
        critique_enabled=True,
    )
    forge.register_tool(
        "generate_pov",
        PovHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
    )
    forge.register_tool(
        "generate_jep",
        JepHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
    )
    forge.register_tool(
        "generate_waf",
        WafHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
        critique_enabled=True,
    )

    return forge
