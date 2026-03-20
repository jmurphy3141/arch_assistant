"""
agent/diagram_orchestrator.py
------------------------------
DEPRECATED — legacy pipeline coordinator.

The server (drawing_agent_server.py) now calls the pipeline stages directly:
  bom_parser → layout_engine → drawio_generator

This module predates the layout_engine approach and is kept for reference only.
It will be removed once the new pipeline is confirmed stable in production.
"""
from __future__ import annotations

import logging
import warnings

warnings.warn(
    "diagram_orchestrator is deprecated. Use bom_parser + layout_engine + drawio_generator directly.",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)


def run_pipeline(xlsx_path: str, context: str = "", diagram_name: str = "output") -> dict:
    """
    DEPRECATED. End-to-end pipeline: BOM Excel → draw.io file.

    Prefer calling the individual stages directly or using the FastAPI server.
    """
    warnings.warn("run_pipeline is deprecated.", DeprecationWarning, stacklevel=2)

    from agent.bom_parser import bom_to_llm_input
    from agent.llm_client import run_layout_prompt
    from agent.layout_engine import spec_to_draw_dict
    from agent.drawio_generator import generate_drawio

    items, prompt = bom_to_llm_input(xlsx_path, context=context)
    spec = run_layout_prompt(prompt)

    if spec.get("status") == "need_clarification":
        return spec

    items_by_id = {i.id: i for i in items}
    draw_dict = spec_to_draw_dict(spec, items_by_id)
    output_path = f"/tmp/diagrams/{diagram_name}.drawio"
    generate_drawio(draw_dict, output_path)

    return {
        "status": "ok",
        "drawio_path": output_path,
        "spec": spec,
        "node_count": len(draw_dict.get("nodes", [])),
        "edge_count": len(draw_dict.get("edges", [])),
    }
