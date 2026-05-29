from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import yaml

from agent.llm_inference_client import run_inference
from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
_CONFIG = _HERE / "config.yaml"
_MAIN_CONFIG = _ROOT / "config.yaml"
_SYSTEM_PROMPT = _HERE / "system_prompt.md"
_CIS_CONTROLS = _HERE / "cis_controls.json"
_PCI_DSS = _ROOT / "agent" / "standards" / "pci_dss_v4.json"
_HIPAA = _ROOT / "agent" / "standards" / "hipaa_security_rule.json"
_FEDRAMP = _ROOT / "agent" / "standards" / "fedramp_moderate_controls.json"


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


def _format_cis_reference(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [
        f"\n\n## {data['benchmark']} v{data['version']} — Control Reference",
        "Cite the control ID (e.g. CIS 2.3) in every finding that maps to one of these controls.\n",
    ]
    for sec_id, section in data.get("sections", {}).items():
        lines.append(f"\n### {sec_id}. {section['title']}")
        for c in section.get("controls", []):
            lines.append(f"- {c['id']} ({c['level']}) {c['title']}")
        for sub in section.get("subsections", {}).values():
            lines.append(f"\n#### {sub['title']}")
            for c in sub.get("controls", []):
                lines.append(f"- {c['id']} ({c['level']}) {c['title']}")
    return "\n".join(lines)


def _format_pci_reference(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [
        f"\n\n## {data['standard']} — Requirement Reference",
        "Cite as [PCI X] in findings that map to these requirements. Apply when customer is in financial services, retail, or processes payment card data.\n",
    ]
    for req in data.get("requirements", []):
        lines.append(f"- Req {req['id']}: {req['title']}")
    return "\n".join(lines)


def _format_hipaa_reference(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [
        f"\n\n## {data['standard']} — Safeguard Reference",
        "Cite as [HIPAA §164.XXX] in findings. Apply when customer is in healthcare, life sciences, or handles ePHI.\n",
    ]
    for section in data.get("safeguards", {}).values():
        lines.append(f"\n### {section['title']} ({section['section']})")
        for std in section.get("standards", []):
            for spec in std.get("implementation_specifications", []):
                lines.append(f"- {spec['id']} [{spec['status']}] {spec['title']}")
            if not std.get("implementation_specifications"):
                lines.append(f"- {std['id']} [Required] {std['title']}")
    return "\n".join(lines)


def _format_fedramp_reference(path: Path) -> str:
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [
        "\n\n## FedRAMP Moderate Baseline — Control Reference",
        "Cite as [FedRAMP AC-2] in findings. Apply when customer is US public sector or processes federal data.\n",
    ]
    current_family = None
    for c in data.get("controls", []):
        if c["family"] != current_family:
            current_family = c["family"]
            lines.append(f"\n### {current_family}")
        lines.append(f"- {c['id']}: {c['title']} — {c['oci_relevance']}")
    return "\n".join(lines)


_agent_config = _load_yaml(_CONFIG)
_main_config = _load_yaml(_MAIN_CONFIG)
_agent_llm = _agent_config.get("llm") or {}
_main_inference = _main_config.get("inference") or {}
_model_id = str(_first_present(_agent_llm.get("model_id"), _main_inference.get("model_id"), default=""))
_system_message = (
    _SYSTEM_PROMPT.read_text(encoding="utf-8")
    + _format_cis_reference(_CIS_CONTROLS)
    + _format_pci_reference(_PCI_DSS)
    + _format_hipaa_reference(_HIPAA)
    + _format_fedramp_reference(_FEDRAMP)
)


card = AgentCard(
    name="waf",
    description="Performs an OCI Well-Architected Framework review of an architecture.",
    inputs={
        "required": ["task"],
        "optional": [
            "architecture_summary",
            "diagram_context",
            "engagement_context",
            "feedback",
            "trace_id",
        ],
    },
    output="WAF review in Markdown covering the six OCI WAF pillars",
    llm_model_id=_model_id,
)


def _build_prompt(req: A2ARequest) -> str:
    context = req.engagement_context if isinstance(req.engagement_context, dict) else {}
    parts = [req.task]
    if context.get("architecture_summary"):
        parts.append(f"Architecture summary:\n{context['architecture_summary']}")
    if context.get("diagram_context"):
        parts.append(f"Diagram context:\n{context['diagram_context']}")
    if context.get("feedback"):
        parts.append(f"Review feedback or focus areas:\n{context['feedback']}")
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


async def handle(req: A2ARequest) -> A2AResponse:
    prompt = _build_prompt(req)
    text = await anyio.to_thread.run_sync(
        lambda: run_inference(
            prompt,
            endpoint=str(_main_inference.get("service_endpoint") or ""),
            model_id=_model_id,
            compartment_id=str(_main_config.get("compartment_id") or ""),
            max_tokens=int(_first_present(_agent_llm.get("max_tokens"), _main_inference.get("max_tokens"), default=4000)),
            temperature=float(_first_present(_agent_llm.get("temperature"), _main_inference.get("temperature"), default=0.5)),
            top_p=float(_first_present(_main_inference.get("top_p"), default=0.9)),
            top_k=int(_first_present(_main_inference.get("top_k"), default=0)),
            system_message=_system_message,
        )
    )
    return A2AResponse(result=text, status="ok", trace={"agent": card.name, "trace_id": req.trace_id})


app = make_agent_app(card, handle)
