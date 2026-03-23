"""
OCI Drawing Agent — public API
"""
from agent.bom_parser import (
    parse_bom, build_llm_prompt, build_layout_intent_prompt,
    bom_to_llm_input, ServiceItem,
)
from agent.layout_engine import compute_positions, spec_to_draw_dict
from agent.drawio_generator import generate_drawio
from agent.layout_intent import (
    LayoutIntent, DeploymentHints, Placement, Assumption,
    validate_layout_intent, LayoutIntentError,
)
from agent.intent_compiler import compile_intent_to_flat_spec

__all__ = [
    # BOM parsing
    "parse_bom",
    "build_llm_prompt",
    "build_layout_intent_prompt",
    "bom_to_llm_input",
    "ServiceItem",
    # Layout engine
    "compute_positions",
    "spec_to_draw_dict",
    # Draw.io generator
    "generate_drawio",
    # LayoutIntent
    "LayoutIntent",
    "DeploymentHints",
    "Placement",
    "Assumption",
    "validate_layout_intent",
    "LayoutIntentError",
    # Compiler
    "compile_intent_to_flat_spec",
]
