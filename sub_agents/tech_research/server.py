from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import yaml

from agent.llm_inference_client import run_inference
from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


import json

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"
_SYSTEM_PROMPT = _HERE / "system_prompt.md"
_SHAPE_AVAILABILITY = _ROOT / "agent" / "standards" / "oci_shape_region_availability.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _format_shape_availability(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    regions = data.get("regions", {})
    shapes = data.get("shapes", {})
    shape_keys = list(shapes.keys())
    short = {k: k.replace("VM.Standard.", "").replace("BM.GPU.", "GPU.").replace("BM.HPC2.36", "HPC2.36") for k in shape_keys}
    header = "| Region | ADs | " + " | ".join(short[k] for k in shape_keys) + " |"
    sep = "|--------|-----|" + "|".join("---" for _ in shape_keys) + "|"
    lines = [
        "\n\n## OCI Shape/Region Availability Reference",
        "Verify with `oci compute shape list` before committing to a design. GPU shapes may need capacity reservations.\n",
        header, sep,
    ]
    for region_id, region in regions.items():
        ads = region.get("availability_domains", "?")
        cells = []
        for sk in shape_keys:
            status = shapes[sk].get("availability", {}).get(region_id, "—")
            cells.append("GA" if status == "GA" else status[:3].title() if isinstance(status, str) and status else "—")
        lines.append(f"| {region_id} | {ads} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "GA=generally available, Sel=selective (may need quota increase), N/A=not available",
        "3-AD regions: us-ashburn-1, us-phoenix-1, us-chicago-1, eu-frankfurt-1, uk-london-1",
        "All other listed regions have 1 AD — use Fault Domains for HA.",
    ]
    return "\n".join(lines)


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = _SYSTEM_PROMPT.read_text(encoding="utf-8") + _format_shape_availability(_SHAPE_AVAILABILITY)


card = AgentCard(
    name="tech_research",
    description="OCI infrastructure technology research and evaluation specialist.",
    inputs={
        "required": ["task"],
        "optional": ["customer_name", "engagement_context", "trace_id"],
    },
    output="structured technology assessment with OCI service recommendations and sizing hints",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    parts = [req.task]
    architect_brief = context.get("architect_brief") or {}
    if architect_brief:
        parts.append(f"Architect brief:\n{architect_brief}")
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


async def handle(req: A2ARequest) -> A2AResponse:
    prompt = _build_prompt(req)
    text = await anyio.to_thread.run_sync(
        lambda: run_inference(
            prompt,
            endpoint=str(_main_inference.get("service_endpoint") or ""),
            model_id=_model_id,
            compartment_id=str(_main_config.get("compartment_id") or ""),
            max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=6000)),
            temperature=float(_first_present(_agent_llm.get("temperature"), _main_inference.get("temperature"), default=0.5)),
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
    )
    return A2AResponse(result=text, status="ok", trace={"agent": card.name, "trace_id": req.trace_id})


app = make_agent_app(card, handle)
