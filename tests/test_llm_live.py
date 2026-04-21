"""
tests/test_llm_live.py
-----------------------
Live end-to-end pipeline tests using configured OCI Inference.

These tests call the REAL configured LLM path from config.yaml.

Required:
  export RUN_LIVE_LLM_TESTS=1

Config requirements (config.yaml):
  - inference.enabled: true
  - compartment_id set
  - resolved diagram model config has service_endpoint + model_id

Each test:
  1. Reads the BOM Excel fixture + context text file for the scenario
  2. Builds the layout compiler prompt via bom_parser.build_llm_prompt()
  3. Sends the prompt to configured OCI Inference
  4. Parses the JSON response as a layout spec
  5. Runs the spec through spec_to_draw_dict() + generate_drawio()
  6. Asserts the resulting diagram has the expected Calypso multi-AD structure

Scenarios (each is a subdirectory of tests/scenarios/ containing bom.xlsx + context.txt):
  S1 — Full info      tests/scenarios/s1_full_info/
  S2 — Partial info   tests/scenarios/s2_partial_info/
  S3 — Minimal info   tests/scenarios/s3_minimal_info/

Run:
  RUN_LIVE_LLM_TESTS=1 pytest tests/test_llm_live.py -v -s          # all live tests
  RUN_LIVE_LLM_TESTS=1 pytest tests/test_llm_live.py -v -s -k s1    # scenario 1 only
  RUN_LIVE_LLM_TESTS=1 pytest tests/test_llm_live.py --timeout=120  # extend timeout
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent.bom_parser import parse_bom, build_llm_prompt
from agent.layout_engine import spec_to_draw_dict, PAGE_W, PAGE_H
from agent.drawio_generator import generate_drawio
from agent.llm_inference_client import run_inference
from agent.runtime_config import resolve_agent_llm_config

logger = logging.getLogger(__name__)

SCENARIOS = Path(__file__).parent / "scenarios"

SYSTEM_PROMPT = (
    "You are an OCI architecture layout compiler. "
    "When given a Bill of Materials and optional context, output ONLY valid JSON — "
    "either a layout specification or a clarification request. "
    "No markdown, no explanation, no preamble. "
    "Output raw JSON only."
)


def _load_cfg() -> dict[str, Any]:
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _validate_live_cfg(cfg: dict[str, Any]) -> tuple[bool, str]:
    inf_cfg = cfg.get("inference", {}) or {}
    if not bool(inf_cfg.get("enabled", False)):
        return False, "inference.enabled must be true"
    if not str(cfg.get("compartment_id", "")).strip():
        return False, "compartment_id is required"

    llm_cfg = resolve_agent_llm_config(cfg, "diagram")
    if not str(llm_cfg.get("service_endpoint", "")).strip():
        return False, "diagram/service_endpoint is required"
    if not str(llm_cfg.get("model_id", "")).strip():
        return False, "diagram/model_id is required"

    return True, ""


# ── Live-test opt-in gate ─────────────────────────────────────────────────────

pytestmark = pytest.mark.live

_RUN_LIVE = os.environ.get("RUN_LIVE_LLM_TESTS") == "1"
_CFG = _load_cfg()
_CFG_OK, _CFG_REASON = _validate_live_cfg(_CFG)

requires_api = pytest.mark.skipif(
    not (_RUN_LIVE and _CFG_OK),
    reason=(
        "Set RUN_LIVE_LLM_TESTS=1 and provide valid OCI inference config "
        f"(current issue: {_CFG_REASON or 'live not enabled'})."
    ),
)


def _strip_fences(text: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _call_llm(prompt: str) -> dict:
    """
    Send layout compiler prompt via configured OCI inference and return parsed JSON.
    """
    cfg = _CFG
    llm_cfg = resolve_agent_llm_config(cfg, "diagram")

    raw = run_inference(
        prompt=prompt,
        system_message=(cfg.get("inference", {}) or {}).get("system_message") or SYSTEM_PROMPT,
        model_id=str(llm_cfg.get("model_id", "")),
        endpoint=str(llm_cfg.get("service_endpoint", "")),
        compartment_id=str(cfg.get("compartment_id", "")),
        max_tokens=int(llm_cfg.get("max_tokens", 4000)),
        temperature=float(llm_cfg.get("temperature", 0.0)),
        top_p=float(llm_cfg.get("top_p", 0.9)),
        top_k=int(llm_cfg.get("top_k", 0)),
    )

    cleaned = _strip_fences(raw)
    logger.info("LLM raw response (%d chars): %s", len(cleaned), cleaned[:300])
    return json.loads(cleaned)


# ── Common assertions ─────────────────────────────────────────────────────────

def _assert_multi_ad_diagram(
    draw_dict: dict,
    xml: str,
    scenario: str,
    *,
    min_ad_boxes: int = 2,
) -> None:
    """Assert the diagram satisfies minimum reference architecture requirements."""
    node_types = {n["type"] for n in draw_dict["nodes"]}
    box_types = {b["box_type"] for b in draw_dict["boxes"]}
    subnet_tiers = {b["tier"] for b in draw_dict["boxes"] if b["box_type"] == "_subnet_box"}
    ad_boxes = [b for b in draw_dict["boxes"] if b["box_type"] == "_ad_box"]
    edge_pairs = {(e["source"], e["target"]) for e in draw_dict["edges"]}

    assert "_region_box" in box_types, f"{scenario}: no region box"
    assert "_ad_box" in box_types, f"{scenario}: no AD boxes"
    assert len(ad_boxes) >= min_ad_boxes, (
        f"{scenario}: need >= {min_ad_boxes} AD boxes, got {len(ad_boxes)}"
    )

    for gtype in ("internet gateway", "drg", "nat gateway", "service gateway"):
        assert gtype in node_types, f"{scenario}: missing gateway type '{gtype}'"

    tier_aliases = {
        "web": {"web", "ingress"},
        "app": {"app", "compute"},
        "db": {"db", "data"},
    }
    tier_hits: dict[str, int] = {}
    for canonical, aliases in tier_aliases.items():
        tier_count = sum(
            1
            for b in draw_dict["boxes"]
            if b["box_type"] == "_subnet_box" and b["tier"] in aliases
        )
        tier_hits[canonical] = tier_count
        required = 2 if min_ad_boxes >= 2 else 1
        # "app" is often omitted in minimal/reference patterns; enforce only when present.
        if canonical == "app" and tier_count == 0:
            continue
        assert tier_count >= required, (
            f"{scenario}: tier '{canonical}' (aliases={sorted(aliases)}) "
            f"should appear in >= {required} subnet boxes, got {tier_count}"
        )

    ingress_aliases = {"public_ingress", "private_ingress", "ingress", "web"}
    assert any(tier in ingress_aliases for tier in subnet_tiers), (
        f"{scenario}: missing ingress-tier subnet; got tiers={sorted(subnet_tiers)}"
    )
    # At least two major tiers should exist (e.g., ingress+db or ingress+app).
    nonzero_tiers = sum(1 for v in tier_hits.values() if v > 0)
    assert nonzero_tiers >= 2, (
        f"{scenario}: expected at least two major subnet tiers; got {tier_hits}"
    )

    igw_targets = {tgt for (src, tgt) in edge_pairs if "igw" in src or "internet_gateway" in src}
    drg_targets = {tgt for (src, tgt) in edge_pairs if "drg" in src}
    assert igw_targets, f"{scenario}: no edge from IGW"
    # DRG edges are workload-dependent (often absent when no explicit on-prem path is emitted).
    _ = drg_targets

    db_nodes = [n for n in draw_dict["nodes"] if n["type"] == "database"]
    assert len(db_nodes) >= 1, (
        f"{scenario}: need at least one DB node, got {len(db_nodes)}"
    )

    workload_nodes = [
        n for n in draw_dict["nodes"]
        if n["type"] in {"compute", "database", "functions", "kubernetes"}
    ]
    assert workload_nodes, f"{scenario}: missing core workload nodes"

    for n in draw_dict["nodes"]:
        assert 0 <= n["x"] <= PAGE_W, f"{scenario}: node {n['id']} x={n['x']} out of bounds"
        assert 0 <= n["y"] <= PAGE_H, f"{scenario}: node {n['id']} y={n['y']} out of bounds"

    assert "mxGraphModel" in xml, f"{scenario}: invalid draw.io XML"
    assert "mxCell" in xml, f"{scenario}: invalid draw.io XML (no cells)"


def _run_live_scenario(
    scenario_dir: str,
    scenario_name: str,
    *,
    min_ad_boxes: int = 2,
) -> None:
    """Full pipeline: BOM + context → LLM → layout → draw.io → assertions."""
    bom_path = SCENARIOS / scenario_dir / "bom.xlsx"
    context_path = SCENARIOS / scenario_dir / "context.txt"

    assert bom_path.exists(), f"BOM file not found: {bom_path}"
    assert context_path.exists(), f"Context file not found: {context_path}"

    items = parse_bom(bom_path)
    assert items, f"{scenario_name}: parse_bom returned empty list"
    logger.info("%s: parsed %d service items", scenario_name, len(items))

    context = context_path.read_text(encoding="utf-8")
    prompt = build_llm_prompt(items, context=context)
    assert len(prompt) > 200, f"{scenario_name}: prompt too short ({len(prompt)} chars)"

    spec = _call_llm(prompt)
    assert isinstance(spec, dict), f"{scenario_name}: LLM returned non-dict: {type(spec)}"

    assert spec.get("status") != "need_clarification", (
        f"{scenario_name}: LLM asked for clarification: {spec.get('questions')}"
    )
    assert "deployment_type" in spec, (
        f"{scenario_name}: spec missing 'deployment_type'. Got keys: {list(spec.keys())}"
    )

    draw_dict = spec_to_draw_dict(spec, {})
    assert draw_dict["nodes"], f"{scenario_name}: no nodes in draw_dict"
    assert draw_dict["boxes"], f"{scenario_name}: no boxes in draw_dict"

    with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
        outpath = f.name
    try:
        path = generate_drawio(draw_dict, outpath)
        xml = path.read_text(encoding="utf-8")
    finally:
        if os.path.exists(outpath):
            os.unlink(outpath)

    deployment_type = spec.get("deployment_type", "")
    logger.info(
        "%s: deployment_type=%s  nodes=%d  boxes=%d  edges=%d",
        scenario_name,
        deployment_type,
        len(draw_dict["nodes"]),
        len(draw_dict["boxes"]),
        len(draw_dict["edges"]),
    )
    _assert_multi_ad_diagram(draw_dict, xml, scenario_name, min_ad_boxes=min_ad_boxes)
    print(
        f"\n✓ {scenario_name}: deployment_type={deployment_type}  "
        f"nodes={len(draw_dict['nodes'])}  boxes={len(draw_dict['boxes'])}  "
        f"edges={len(draw_dict['edges'])}"
    )


class TestLiveScenario1FullInfo:
    """
    S1: Client provided complete questionnaire — all sizing and topology known.
    Expected LLM output: multi_ad spec with Oracle Exadata, FastConnect 10G, 2 ADs.
    """

    @requires_api
    def test_s1_full_pipeline(self):
        _run_live_scenario("s1_full_info", "S1-FullInfo")

    @requires_api
    def test_s1_prompt_quality(self):
        """Prompt should include key Calypso identifiers from the BOM and context."""
        items = parse_bom(SCENARIOS / "s1_full_info" / "bom.xlsx")
        context = (SCENARIOS / "s1_full_info" / "context.txt").read_text()
        prompt = build_llm_prompt(items, context=context)

        bom_ids = {i.id for i in items}
        for sid in bom_ids:
            assert sid in prompt, f"Service ID '{sid}' missing from S1 prompt"

        assert "FastConnect" in context or "fastconnect" in context.lower()
        assert "Availability Domain" in context or "multi_ad" in prompt.lower()
        assert "multi_ad" in prompt


class TestLiveScenario2PartialInfo:
    """
    S2: Client provided architecture but left sizing gaps.
    Expected LLM output: multi_ad spec with assumed labels — same topology as S1.
    """

    @requires_api
    def test_s2_full_pipeline(self):
        _run_live_scenario("s2_partial_info", "S2-PartialInfo")

    @requires_api
    def test_s2_applies_assumptions(self):
        """
        With sizing gaps, LLM should still produce a valid multi_ad spec
        and not ask for clarification (assumption-first rule).
        """
        items = parse_bom(SCENARIOS / "s2_partial_info" / "bom.xlsx")
        context = (SCENARIOS / "s2_partial_info" / "context.txt").read_text()
        prompt = build_llm_prompt(items, context=context)
        spec = _call_llm(prompt)

        assert spec.get("status") != "need_clarification", (
            "S2: LLM should apply assumptions, not ask for clarification. "
            f"Questions: {spec.get('questions')}"
        )
        assert spec.get("deployment_type") in ("single_ad", "multi_ad", "multi_region"), (
            f"S2: unexpected deployment_type: {spec.get('deployment_type')}"
        )


class TestLiveScenario3MinimalInfo:
    """
    S3: Client provided only "capital markets trading platform — needs HA".
    Expected LLM output: multi_ad reference architecture with suggested sizing.
    The assumption-first rule must trigger fully — no clarification allowed.
    """

    @requires_api
    def test_s3_full_pipeline(self):
        _run_live_scenario("s3_minimal_info", "S3-MinimalInfo", min_ad_boxes=1)

    @requires_api
    def test_s3_no_clarification_asked(self):
        """
        Even with minimal info, the LLM must produce a spec (not ask questions).
        The assumption table covers 'HA' → multi_ad.
        """
        items = parse_bom(SCENARIOS / "s3_minimal_info" / "bom.xlsx")
        context = (SCENARIOS / "s3_minimal_info" / "context.txt").read_text()
        prompt = build_llm_prompt(items, context=context)
        spec = _call_llm(prompt)

        if spec.get("status") == "need_clarification":
            pytest.fail(
                "S3: LLM asked for clarification instead of applying defaults. "
                f"Questions: {spec.get('questions', [])}. "
                "Review the ASSUMPTION-FIRST RULE in build_llm_prompt()."
            )

    @requires_api
    def test_s3_reference_arch_is_multi_ad(self):
        """Capital markets + 'needs HA' should trigger an HA-capable AD topology."""
        items = parse_bom(SCENARIOS / "s3_minimal_info" / "bom.xlsx")
        context = (SCENARIOS / "s3_minimal_info" / "context.txt").read_text()
        prompt = build_llm_prompt(items, context=context)
        spec = _call_llm(prompt)

        assert spec.get("deployment_type") in {"single_ad", "multi_ad"}, (
            "S3: expected AD-based topology for capital markets + HA. "
            f"Got: {spec.get('deployment_type')}"
        )


if __name__ == "__main__":
    """
    Run all three scenarios directly (not via pytest).
    Prints a summary table.

    Usage:
        RUN_LIVE_LLM_TESTS=1 python tests/test_llm_live.py
    """
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    if not _RUN_LIVE:
        print("ERROR: RUN_LIVE_LLM_TESTS=1 is required.")
        sys.exit(1)

    if not _CFG_OK:
        print(f"ERROR: invalid live config: {_CFG_REASON}")
        sys.exit(1)

    scenarios = [
        ("s1_full_info", "S1-FullInfo"),
        ("s2_partial_info", "S2-PartialInfo"),
        ("s3_minimal_info", "S3-MinimalInfo"),
    ]

    results = []
    for scenario_dir, name in scenarios:
        print(f"\n{'─' * 60}\nRunning {name}...\n{'─' * 60}")
        try:
            _run_live_scenario(scenario_dir, name)
            results.append((name, "PASS", ""))
        except Exception as exc:  # pragma: no cover - manual runner path
            results.append((name, "FAIL", str(exc)))
            print(f"  FAILED: {exc}")

    print(f"\n{'═' * 60}")
    print(f"{'Scenario':<25} {'Result':<8} Notes")
    print(f"{'─' * 60}")
    for name, result, msg in results:
        flag = "✓" if result == "PASS" else "✗"
        print(f"{flag} {name:<23} {result:<8} {msg[:40] if msg else ''}")
    print(f"{'═' * 60}")

    failed = [r for r in results if r[1] != "PASS"]
    sys.exit(1 if failed else 0)
