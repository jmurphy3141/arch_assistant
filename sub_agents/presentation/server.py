from __future__ import annotations

import base64
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard
from sub_agents.presentation.scripts import render_oci_powerpoint


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"


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


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))


card = AgentCard(
    name="presentation",
    description="Generates a 7-slide Oracle-standard PowerPoint POC deck.",
    inputs={
        "required": ["task", "customer_name"],
        "optional": ["poc_name", "oci_services", "bom_summary", "jep_phases", "engagement_context", "trace_id"],
    },
    output="Base64-encoded PPTX bytes",
    llm_model_id=_model_id,
)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


async def handle(req: A2ARequest) -> A2AResponse:
    ec = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    spec = {
        "poc_name": ec.get("poc_name", "OCI POC"),
        "customer_name": ec.get("customer_name", "Customer"),
        "pain_statement": ec.get("pain_statement", ""),
        "oci_services": _as_list(ec.get("oci_services", [])),
        "bom_summary": ec.get("bom_summary", ""),
        "bom_rows": _as_list(ec.get("bom_rows", [])),
        "jep_phases": _as_list(ec.get("jep_phases", [])),
        "date": datetime.today().strftime("%B %d, %Y"),
    }
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        output_path = f.name
    try:
        render_oci_powerpoint.render(spec, output_path)
        with open(output_path, "rb") as f:
            pptx_bytes = f.read()
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
    return A2AResponse(
        result=base64.b64encode(pptx_bytes).decode("ascii"),
        status="ok",
        trace={"agent": card.name, "trace_id": req.trace_id},
    )


app = make_agent_app(card, handle)
