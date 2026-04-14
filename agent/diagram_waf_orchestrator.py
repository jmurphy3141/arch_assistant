"""
agent/diagram_waf_orchestrator.py
-----------------------------------
Orchestration loop: Drawing ↔ WAF quality gate.

After the drawing agent produces a diagram, this loop:
  1. Runs the WAF agent in orchestration mode (topology gap analysis).
  2. If WAF finds topology gaps, re-runs the drawing agent with the
     draw_instructions as a refinement request.
  3. Repeats up to max_iterations times.
  4. Stops when WAF is satisfied (empty suggestions) or max_iterations reached.

The loop is called server-side — completely invisible to the SA.
The SA only sees clarification questions (drawing phase) and the final
diagram + WAF result when the loop is done.

Pure Python — no FastAPI imports. Independently testable.
"""
from __future__ import annotations

import logging
import uuid
from typing import Callable, Optional

from agent.waf_agent import generate_waf

logger = logging.getLogger(__name__)


def _build_diagram_context(draw_result: dict) -> dict:
    """
    Build the diagram_context dict for the WAF agent from a run_pipeline ok result.
    """
    node_to_resource_map = draw_result.get("node_to_resource_map") or {}
    spec = draw_result.get("spec") or {}

    node_types = sorted({
        v.get("oci_type", "unknown")
        for v in node_to_resource_map.values()
        if isinstance(v, dict)
    })
    layers = sorted({
        v.get("layer", "")
        for v in node_to_resource_map.values()
        if isinstance(v, dict) and v.get("layer")
    })

    draw_dict  = draw_result.get("draw_dict") or {}
    node_count = len(draw_dict.get("nodes", []))
    edge_count = len(draw_dict.get("edges", []))

    return {
        "deployment_type": spec.get("deployment_type", "single_ad"),
        "node_types":      node_types,
        "node_count":      node_count,
        "edge_count":      edge_count,
        "layers":          layers,
    }


def _build_feedback_prompt(suggestions: list[dict]) -> str:
    """Convert WAF suggestions list to a numbered draw_instruction string."""
    lines = [
        "Apply these architecture improvements to bring the diagram in line "
        "with OCI WAF requirements:"
    ]
    for i, s in enumerate(suggestions, 1):
        instr    = s.get("draw_instruction", "")
        pillar   = s.get("pillar", "")
        priority = s.get("priority", "")
        note     = f" [{pillar}{', ' + priority if priority else ''}]" if pillar else ""
        lines.append(f"{i}. {instr}{note}")
    return "\n".join(lines)


async def run_diagram_waf_loop(
    *,
    items: list,
    base_prompt: str,
    deployment_hints: dict,
    draw_result: dict,
    customer_id: str,
    customer_name: str,
    diagram_name: str,
    client_id: str,
    object_store,
    text_runner: Callable,
    run_pipeline_fn: Callable,
    max_iterations: int = 3,
) -> dict:
    """
    Run the WAF ↔ Drawing quality-gate loop.

    Args:
        items:            ServiceItem list from the initial BOM parse.
        base_prompt:      Original (un-enriched) BOM prompt.
        deployment_hints: Hints dict (preserves multi_region_mode across refines).
        draw_result:      The status=ok result from the initial run_pipeline call.
        customer_id:      Customer identifier for WAF document storage.
        customer_name:    Human-readable customer name.
        diagram_name:     Diagram name for pipeline calls.
        client_id:        Client session ID for pipeline calls.
        object_store:     ObjectStoreBase instance.
        text_runner:      sync callable(prompt, system_message) -> str.
        run_pipeline_fn:  async callable matching run_pipeline signature.
        max_iterations:   Maximum WAF ↔ refine cycles (default 3).

    Returns:
        {
          "draw_result":   final draw_result dict,
          "waf_result":    final waf_result dict,
          "iterations":    int,
          "loop_history":  list of {iteration, waf_rating, applied, draw_instructions},
        }
    """
    import anyio

    loop_history: list[dict] = []
    prev_prev_spec: Optional[str] = None   # stalemate detection (2 back)
    waf_result: dict = {}
    iteration = 1

    for iteration in range(1, max_iterations + 1):
        # ── WAF review ─────────────────────────────────────────────────────────
        diagram_context = _build_diagram_context(draw_result)
        logger.info(
            "WAF loop iteration=%d customer_id=%s node_types=%s",
            iteration, customer_id, diagram_context.get("node_types"),
        )

        def _run_waf(_ctx=diagram_context):
            return generate_waf(
                customer_id,
                customer_name,
                object_store,
                text_runner,
                diagram_context=_ctx,
            )

        waf_result  = await anyio.to_thread.run_sync(_run_waf)
        suggestions = waf_result.get("refinement_suggestions", [])

        loop_history.append({
            "iteration":         iteration,
            "waf_rating":        waf_result.get("overall_rating", "⚠️"),
            "applied":           len(suggestions),
            "draw_instructions": [s.get("draw_instruction", "") for s in suggestions],
        })

        if not suggestions:
            logger.info("WAF loop: no suggestions at iteration=%d — done", iteration)
            break

        if iteration == max_iterations:
            logger.info("WAF loop: max_iterations=%d reached — stopping", max_iterations)
            break

        # ── Refine diagram based on WAF feedback ────────────────────────────────
        feedback_prompt = _build_feedback_prompt(suggestions)
        refine_ctx      = draw_result.get("_refine_context") or {}
        prev_spec_json  = refine_ctx.get("prev_spec")

        available_ids = ", ".join(
            f"{getattr(i, 'id', '')} ({getattr(i, 'oci_type', '')})" for i in items
        )

        if prev_spec_json:
            enriched_prompt = (
                "You are an OCI LayoutIntent editor.\n"
                "Apply ONLY the requested changes to the current LayoutIntent below.\n"
                "Do NOT regenerate from scratch. Modify only what is requested and "
                "keep everything else identical.\n"
                "Output ONLY valid JSON — the complete updated LayoutIntent.\n"
                "\n═══ CURRENT LAYOUT (modify this):\n"
                + prev_spec_json
                + "\n\n═══ AVAILABLE SERVICE IDs (from BOM — use these exact IDs):\n"
                + available_ids
                + "\n\n═══ REQUESTED CHANGES (WAF recommendations):\n"
                + feedback_prompt
                + "\n\nReturn the COMPLETE updated LayoutIntent JSON. Output ONLY valid JSON."
            )
        else:
            enriched_prompt = (
                base_prompt
                + "\n\n═══════════════════════════════════════════════════════\n"
                + "DIAGRAM REFINEMENT REQUEST (WAF recommendations):\n"
                + feedback_prompt
                + "\n\nApply the requested changes. "
                + "Return the COMPLETE updated LayoutIntent JSON. "
                + "Output ONLY valid JSON."
            )

        new_draw_result = await run_pipeline_fn(
            items            = items,
            prompt           = enriched_prompt,
            diagram_name     = diagram_name,
            client_id        = client_id,
            request_id       = str(uuid.uuid4()),
            input_hash       = f"waf_loop_{iteration}",
            deployment_hints = deployment_hints,
        )

        if new_draw_result.get("status") != "ok":
            logger.warning(
                "WAF loop: run_pipeline status=%s at iteration=%d — stopping",
                new_draw_result.get("status"), iteration,
            )
            break

        # Restore original (un-enriched) prompt in _refine_context so that
        # subsequent refinements don't accumulate stacked prompts.
        if "_refine_context" in new_draw_result:
            new_draw_result["_refine_context"]["prompt"] = base_prompt

        # ── Stalemate detection: stop if diagram didn't change ──────────────────
        cur_spec = (new_draw_result.get("_refine_context") or {}).get("prev_spec")
        if cur_spec and cur_spec == prev_prev_spec:
            logger.info("WAF loop: stalemate at iteration=%d — stopping", iteration)
            draw_result = new_draw_result
            break
        prev_prev_spec = (draw_result.get("_refine_context") or {}).get("prev_spec")
        draw_result    = new_draw_result

    return {
        "draw_result":  draw_result,
        "waf_result":   waf_result,
        "iterations":   iteration,
        "loop_history": loop_history,
    }
