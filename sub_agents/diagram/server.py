from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from agent.bom_parser import freeform_arch_text_to_llm_input

try:
    from agent.intent_compiler import compile_intent
except ImportError:
    from agent.intent_compiler import compile_intent_to_flat_spec as compile_intent
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio
from agent.llm_inference_client import run_inference
from agent.layout_intent import validate_layout_intent
from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"
_SYSTEM_PROMPT = _HERE / "system_prompt.md"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _clean_json(raw: str) -> str:
    text = str(raw or "").strip()
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        return fenced.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


_diagram_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_diagram_llm = _diagram_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_diagram_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = (
    _SYSTEM_PROMPT.read_text(encoding="utf-8") if _SYSTEM_PROMPT.exists() else ""
)


card = AgentCard(
    name="diagram",
    description="Generates an OCI architecture draw.io diagram from a workload description.",
    inputs={
        "required": ["task"],
        "optional": ["diagram_name", "customer_id", "trace_id"],
    },
    output="draw.io XML string",
    llm_model_id=_model_id,
)


async def handle(req: A2ARequest) -> A2AResponse:
    items, prompt = freeform_arch_text_to_llm_input(req.task)

    raw = await asyncio.to_thread(
        run_inference,
        prompt,
        endpoint=str(_main_inference.get("service_endpoint") or ""),
        model_id=_model_id,
        compartment_id=str(_main_config.get("compartment_id") or ""),
        max_tokens=int(
            _first_present(
                _diagram_llm.get("max_tokens"),
                _main_inference.get("max_tokens"),
                default=4000,
            )
        ),
        temperature=float(
            _first_present(
                _diagram_llm.get("temperature"),
                _main_inference.get("temperature"),
                default=0.0,
            )
        ),
        top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
        top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
        system_message=_system_message,
    )
    spec = json.loads(_clean_json(raw))

    if spec.get("status") == "need_clarification":
        questions_json = json.dumps(spec.get("questions", []), ensure_ascii=False)
        return A2AResponse(
            result=questions_json,
            status="needs_input",
            trace={
                "agent": card.name,
                "trace_id": req.trace_id,
                "llm_status": "need_clarification",
            },
        )

    if "placements" in spec:
        intent = validate_layout_intent(spec, items)
        spec = compile_intent(intent, items)

    items_by_id = {item.id: item for item in items}
    draw_dict = await asyncio.to_thread(spec_to_draw_dict, spec, items_by_id)

    diagram_name = "diagram"
    context_name = (
        req.engagement_context.get("diagram_name")
        if isinstance(req.engagement_context, dict)
        else None
    )
    if context_name:
        diagram_name = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(context_name)).strip("._")
            or "diagram"
        )

    with tempfile.TemporaryDirectory(prefix="diagram-sub-agent-") as tmpdir:
        drawio_path = Path(tmpdir) / f"{diagram_name}.drawio"
        await asyncio.to_thread(generate_drawio, draw_dict, drawio_path)
        drawio_xml = await asyncio.to_thread(drawio_path.read_text, encoding="utf-8")

    return A2AResponse(
        result=drawio_xml,
        status="ok",
        trace={
            "agent": card.name,
            "trace_id": req.trace_id,
            "node_count": len(draw_dict.get("nodes", [])),
            "edge_count": len(draw_dict.get("edges", [])),
        },
    )


app = make_agent_app(card, handle)
