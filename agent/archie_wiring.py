"""
archie_wiring.py - wire every OCI tool handler into a Forge instance.

Call build_forge() once per customer session to get a fully configured Forge.
archie_session.py imports build_forge() for the p2i cutover task.
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

# Ordered by specificity — first match wins
_PROSE_GUARD_RULES: list[tuple[list[str], str]] = [
    (["diagram", "draw.io", "drawio", "architecture diagram"], "generate_diagram"),
    (["terraform", "tf file", ".tf"], "generate_terraform"),
    (["waf review", "waf assessment", "waf analysis"], "generate_waf"),
    (["point of view", "pov document", "generate pov"], "generate_pov"),
    (["jep document", "generate jep", "joint execution"], "generate_jep"),
    (["bom", "bill of materials", "bill-of-materials", "pricing", "costed"], "generate_bom"),
]


def _archie_prose_guard(user_message: str, prose_reply: str) -> str | None:
    """
    Return the tool name that should have been called when the LLM wrote prose
    instead. Returns None for genuinely conversational messages.
    """
    text = user_message.lower()
    for keywords, tool in _PROSE_GUARD_RULES:
        if any(kw in text for kw in keywords):
            return tool
    return None

_TOOL_SEQUENCING_RULES = """
## Tool Sequencing Rules

These rules are mandatory. Follow them on every generation request.

### Ordering
1. When the user requests both a BOM and a diagram in the same turn, always call generate_bom FIRST. Pass the BOM result payload to generate_diagram.
2. generate_waf and generate_terraform both require an existing diagram.
   If no diagram exists for the customer, generate one first.
3. generate_pov and generate_jep can be requested in the same turn and may
   be called sequentially in one turn.

### Single-tool requests
4. If the user asks only for a BOM, call generate_bom once and return.
5. If the user asks only for a diagram, call generate_diagram once and return.
6. Do not generate unrequested deliverables.

### Artifact re-use
7. If the user asks for a download link or asks to view an existing artifact,
   return the artifact key from context -- do not re-generate.

### Update requests
8. If the user says "update everything" or "regenerate all", identify which tools have existing artifacts in context and re-run them in this order:
   generate_bom -> generate_diagram -> generate_waf -> generate_terraform ->
   generate_pov -> generate_jep (skip any that were not previously generated).

### Tool-call discipline (mandatory)
9. You MUST output a tool-call JSON line for every generation request. Never
   respond with prose describing what you are about to do. Prose responses are
   ONLY for conversational turns where no tool is needed.
   Correct: {"tool": "generate_bom", "args": {"prompt": "..."}}
   Wrong: "I'll generate a BOM for your web service architecture now."

10. After step3_planning, if the plan identifies a generation action, immediately
    output the tool-call JSON. Do not narrate the plan -- execute it.

11. The tool-call JSON must appear alone on a single line with no surrounding text.
    If you need to say something to the user as well, wait until after the tool
    result is returned -- Forge will give you another turn.
"""


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
    step3_planning: bool = True,
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

    full_prompt = (
        routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
    ).strip()

    forge = Forge(
        base_system_prompt=full_prompt,
        hat_engine=hat_engine,
        memory=memory,
        text_runner=text_runner,
        prompt_enricher=enricher,
        max_iterations=5,
        step3_planning=step3_planning,
        prose_guard=_archie_prose_guard,
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
        requires_hat="oci_bom_expert",
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
        requires_hat="diagram_for_oci",
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
        requires_hat="terraform_for_oci",
    )
    forge.register_tool(
        "generate_pov",
        PovHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
        requires_hat="oci_customer_pov_writer",
    )
    forge.register_tool(
        "generate_jep",
        JepHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
        requires_hat="jep_writer",
    )
    forge.register_tool(
        "generate_waf",
        WafHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
        critique_enabled=True,
        requires_hat="oci_waf_reviewer",
    )

    return forge
