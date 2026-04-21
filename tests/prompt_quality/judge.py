from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.llm_inference_client import run_inference
from agent.runtime_config import resolve_agent_llm_config


@dataclass
class JudgeResult:
    score: float
    status: str
    evidence: str


def _load_cfg() -> dict[str, Any]:
    cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _strip_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.removeprefix("```json").removeprefix("```").strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    return s


def _parse_result(raw: str) -> JudgeResult:
    data = json.loads(_strip_fences(raw))
    score = float(data.get("score", 0.0))
    status = str(data.get("status", "fail")).lower()
    evidence = str(data.get("evidence", ""))
    if status not in {"pass", "fail"}:
        status = "pass" if score >= 0.75 else "fail"
    score = max(0.0, min(1.0, score))
    return JudgeResult(score=score, status=status, evidence=evidence)


def _judge_prompt_template(*, path_id: str, stage: str, prompt_text: str, chain_context: str) -> str:
    return f"""\
Evaluate prompt quality for the OCI Architecture Assistant.

PATH: {path_id}
STAGE: {stage}

PROMPT UNDER REVIEW:
{prompt_text}

RECURSIVE CHAIN CONTEXT (downstream/related prompts):
{chain_context}

Score quality on:
1) Contract clarity and machine-readability constraints
2) Context propagation correctness
3) Anti-hallucination / scope control
4) Downstream coherence and handoff consistency

Return ONLY valid JSON with this exact schema:
{{
  "score": 0.0,
  "status": "pass",
  "evidence": "short explanation"
}}

Rules:
- score is between 0.0 and 1.0
- status must be "pass" if score >= 0.75 else "fail"
- evidence must cite specific strengths/risks from the prompt text
"""


def judge_prompt_with_llm(*, path_id: str, stage: str, prompt_text: str, chain_context: str = "") -> JudgeResult:
    cfg = _load_cfg()
    inf_cfg = cfg.get("inference", {}) or {}
    if not bool(inf_cfg.get("enabled", False)):
        raise RuntimeError("inference.enabled is false; cannot run LLM judge")

    agent_name = os.environ.get("PROMPT_JUDGE_AGENT", "orchestrator")
    llm_cfg = resolve_agent_llm_config(cfg, agent_name)
    system_message = (
        "You are a strict prompt-quality judge for OCI solutioning agents. "
        "Return only JSON using the required schema."
    )

    raw = run_inference(
        prompt=_judge_prompt_template(
            path_id=path_id,
            stage=stage,
            prompt_text=prompt_text,
            chain_context=chain_context,
        ),
        system_message=system_message,
        model_id=llm_cfg.get("model_id", ""),
        endpoint=llm_cfg.get("service_endpoint", ""),
        compartment_id=str(cfg.get("compartment_id", "")),
        max_tokens=int(os.environ.get("PROMPT_JUDGE_MAX_TOKENS", llm_cfg.get("max_tokens", 1200))),
        temperature=float(os.environ.get("PROMPT_JUDGE_TEMPERATURE", 0.0)),
        top_p=float(llm_cfg.get("top_p", 0.9)),
        top_k=int(llm_cfg.get("top_k", 0)),
    )
    return _parse_result(raw)
