"""
tests/test_llm_live.py
-----------------------
Live end-to-end pipeline tests using the Anthropic Claude API.

These tests call the REAL LLM — they require:
  export ANTHROPIC_API_KEY=<your key>

Each test:
  1. Reads the BOM Excel fixture + context text file for the scenario
  2. Builds the layout compiler prompt via bom_parser.build_llm_prompt()
  3. Sends the prompt to Claude Opus 4.6 (streaming, adaptive thinking)
  4. Parses the JSON response as a layout spec
  5. Runs the spec through spec_to_draw_dict() + generate_drawio()
  6. Asserts the resulting diagram has the expected Calypso multi-AD structure

Scenarios (each is a subdirectory of tests/scenarios/ containing bom.xlsx + context.txt):
  S1 — Full info      tests/scenarios/s1_full_info/
  S2 — Partial info   tests/scenarios/s2_partial_info/
  S3 — Minimal info   tests/scenarios/s3_minimal_info/

Run:
  pytest tests/test_llm_live.py -v -s               # all live tests
  pytest tests/test_llm_live.py -v -s -k s1         # scenario 1 only
  pytest tests/test_llm_live.py --timeout=120        # extend timeout

Skip (when no API key available):
  pytest tests/ --ignore=tests/test_llm_live.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

import pytest

from agent.bom_parser import parse_bom, build_llm_prompt
from agent.layout_engine import spec_to_draw_dict, PAGE_W, PAGE_H
from agent.drawio_generator import generate_drawio

logger = logging.getLogger(__name__)

SCENARIOS = Path(__file__).parent / "scenarios"

# ── Live-test opt-in gate ─────────────────────────────────────────────────────
# Tests in this module only run when the caller explicitly opts in via env vars.
# Default `pytest -q` runs are fully offline — no anthropic import, no API calls.

pytestmark = pytest.mark.live

_RUN_LIVE = (
    os.environ.get("RUN_LIVE_LLM_TESTS") == "1"
    and bool(os.environ.get("ANTHROPIC_API_KEY"))
)
requires_api = pytest.mark.skipif(
    not _RUN_LIVE,
    reason="Set RUN_LIVE_LLM_TESTS=1 and ANTHROPIC_API_KEY to run live LLM tests",
)


# ── LLM call via Anthropic API ────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an OCI architecture layout compiler. "
    "When given a Bill of Materials and optional context, output ONLY valid JSON — "
    "either a layout specification or a clarification request. "
    "No markdown, no explanation, no preamble. "
    "Output raw JSON only."
)

def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def _call_llm(prompt: str) -> dict:
    """
    Send the layout compiler prompt to Claude Opus 4.6 and return parsed JSON.
    Uses streaming + adaptive thinking for reliability on long prompts.
    """
    import anthropic  # imported here so offline runs never load the package
    client = anthropic.Anthropic(api_key=_api_key())

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        final = stream.get_final_message()

    # Extract text block (thinking blocks are separate)
    raw = next(
        (b.text for b in final.content if b.type == "text"),
        "",
    ).strip()

    logger.info("LLM raw response (%d chars): %s", len(raw), raw[:300])

    # Strip markdown code fences if the model accidentally added them
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw)


# ── Common assertions ─────────────────────────────────────────────────────────

def _assert_multi_ad_diagram(draw_dict: dict, xml: str, scenario: str) -> None:
    """Assert the diagram satisfies minimum Calypso multi-AD requirements."""
    node_ids  = {n["id"] for n in draw_dict["nodes"]}
    node_types = {n["type"] for n in draw_dict["nodes"]}
    box_types  = {b["box_type"] for b in draw_dict["boxes"]}
    subnet_tiers = {b["tier"] for b in draw_dict["boxes"] if b["box_type"] == "_subnet_box"}
    ad_boxes = [b for b in draw_dict["boxes"] if b["box_type"] == "_ad_box"]
    edge_srcs = {e["source"] for e in draw_dict["edges"]}
    edge_pairs = {(e["source"], e["target"]) for e in draw_dict["edges"]}

    # Structure
    assert "_region_box" in box_types, f"{scenario}: no region box"
    assert "_ad_box"     in box_types, f"{scenario}: no AD boxes"
    assert len(ad_boxes) >= 2,         f"{scenario}: need >= 2 AD boxes, got {len(ad_boxes)}"

    # Gateways
    for gtype in ("internet gateway", "drg", "nat gateway", "service gateway"):
        assert gtype in node_types, f"{scenario}: missing gateway type '{gtype}'"

    # Subnet tiers
    for tier in ("web", "app", "db"):
        tier_count = sum(1 for b in draw_dict["boxes"]
                         if b["box_type"] == "_subnet_box" and b["tier"] == tier)
        assert tier_count >= 2, (
            f"{scenario}: tier '{tier}' should appear in both ADs, got {tier_count}"
        )

    # Ingress subnets
    assert "public_ingress"  in subnet_tiers, f"{scenario}: missing public_ingress subnet"
    assert "private_ingress" in subnet_tiers, f"{scenario}: missing private_ingress subnet"

    # Key auto-injected edges
    igw_targets = {tgt for (src, tgt) in edge_pairs
                   if "igw" in src or "internet_gateway" in src}
    drg_targets = {tgt for (src, tgt) in edge_pairs if "drg" in src}
    assert igw_targets, f"{scenario}: no edge from IGW"
    assert drg_targets, f"{scenario}: no edge from DRG"

    # DB nodes present
    db_nodes = [n for n in draw_dict["nodes"] if n["type"] == "database"]
    assert len(db_nodes) >= 2, f"{scenario}: need DB nodes in both ADs, got {len(db_nodes)}"

    # Compute nodes present
    compute_nodes = [n for n in draw_dict["nodes"] if n["type"] == "compute"]
    assert compute_nodes, f"{scenario}: no compute nodes"

    # Page bounds
    for n in draw_dict["nodes"]:
        assert 0 <= n["x"] <= PAGE_W, f"{scenario}: node {n['id']} x={n['x']} out of bounds"
        assert 0 <= n["y"] <= PAGE_H, f"{scenario}: node {n['id']} y={n['y']} out of bounds"

    # Valid XML
    assert "mxGraphModel" in xml, f"{scenario}: invalid draw.io XML"
    assert "mxCell"       in xml, f"{scenario}: invalid draw.io XML (no cells)"


def _run_live_scenario(scenario_dir: str, scenario_name: str) -> None:
    """Full pipeline: BOM + context → LLM → layout → draw.io → assertions."""
    bom_path     = SCENARIOS / scenario_dir / "bom.xlsx"
    context_path = SCENARIOS / scenario_dir / "context.txt"

    assert bom_path.exists(),     f"BOM file not found: {bom_path}"
    assert context_path.exists(), f"Context file not found: {context_path}"

    # 1. Parse BOM
    items = parse_bom(bom_path)
    assert items, f"{scenario_name}: parse_bom returned empty list"
    logger.info("%s: parsed %d service items", scenario_name, len(items))

    # 2. Build LLM prompt
    context = context_path.read_text(encoding="utf-8")
    prompt = build_llm_prompt(items, context=context)
    assert len(prompt) > 200, f"{scenario_name}: prompt too short ({len(prompt)} chars)"

    # 3. Call LLM
    spec = _call_llm(prompt)
    assert isinstance(spec, dict), f"{scenario_name}: LLM returned non-dict: {type(spec)}"

    # Handle clarification response — assert it does NOT ask for clarification
    # (all three scenarios should have enough info for the assumption-first LLM)
    assert spec.get("status") != "need_clarification", (
        f"{scenario_name}: LLM asked for clarification: {spec.get('questions')}"
    )
    assert "deployment_type" in spec, (
        f"{scenario_name}: spec missing 'deployment_type'. Got keys: {list(spec.keys())}"
    )

    # 4. Layout engine
    draw_dict = spec_to_draw_dict(spec, {})
    assert draw_dict["nodes"], f"{scenario_name}: no nodes in draw_dict"
    assert draw_dict["boxes"], f"{scenario_name}: no boxes in draw_dict"

    # 5. Generate draw.io XML
    with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
        outpath = f.name
    try:
        path = generate_drawio(draw_dict, outpath)
        xml = path.read_text(encoding="utf-8")
    finally:
        if os.path.exists(outpath):
            os.unlink(outpath)

    # 6. Assert structure
    deployment_type = spec.get("deployment_type", "")
    logger.info(
        "%s: deployment_type=%s  nodes=%d  boxes=%d  edges=%d",
        scenario_name, deployment_type,
        len(draw_dict["nodes"]), len(draw_dict["boxes"]), len(draw_dict["edges"]),
    )
    _assert_multi_ad_diagram(draw_dict, xml, scenario_name)
    print(f"\n✓ {scenario_name}: deployment_type={deployment_type}  "
          f"nodes={len(draw_dict['nodes'])}  boxes={len(draw_dict['boxes'])}  "
          f"edges={len(draw_dict['edges'])}")


# ── Test classes ───────────────────────────────────────────────────────────────

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
        items   = parse_bom(SCENARIOS / "s1_full_info" / "bom.xlsx")
        context = (SCENARIOS / "s1_full_info" / "context.txt").read_text()
        prompt  = build_llm_prompt(items, context=context)

        # BOM service IDs appear
        bom_ids = {i.id for i in items}
        for sid in bom_ids:
            assert sid in prompt, f"Service ID '{sid}' missing from S1 prompt"

        # Key context signals present
        assert "FastConnect" in context or "fastconnect" in context.lower()
        assert "Availability Domain" in context or "multi_ad" in prompt.lower()
        assert "multi_ad" in prompt   # default assumption table must reference it


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
        items   = parse_bom(SCENARIOS / "s2_partial_info" / "bom.xlsx")
        context = (SCENARIOS / "s2_partial_info" / "context.txt").read_text()
        prompt  = build_llm_prompt(items, context=context)
        spec    = _call_llm(prompt)

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
        _run_live_scenario("s3_minimal_info", "S3-MinimalInfo")

    @requires_api
    def test_s3_no_clarification_asked(self):
        """
        Even with minimal info, the LLM must produce a spec (not ask questions).
        The assumption table covers 'HA' → multi_ad.
        """
        items   = parse_bom(SCENARIOS / "s3_minimal_info" / "bom.xlsx")
        context = (SCENARIOS / "s3_minimal_info" / "context.txt").read_text()
        prompt  = build_llm_prompt(items, context=context)
        spec    = _call_llm(prompt)

        if spec.get("status") == "need_clarification":
            pytest.fail(
                "S3: LLM asked for clarification instead of applying defaults. "
                f"Questions: {spec.get('questions', [])}. "
                "Review the ASSUMPTION-FIRST RULE in build_llm_prompt()."
            )

    @requires_api
    def test_s3_reference_arch_is_multi_ad(self):
        """Capital markets + 'needs HA' should trigger multi_ad via assumption table."""
        items   = parse_bom(SCENARIOS / "s3_minimal_info" / "bom.xlsx")
        context = (SCENARIOS / "s3_minimal_info" / "context.txt").read_text()
        prompt  = build_llm_prompt(items, context=context)
        spec    = _call_llm(prompt)

        assert spec.get("deployment_type") == "multi_ad", (
            f"S3: 'capital markets + HA' should produce multi_ad. "
            f"Got: {spec.get('deployment_type')}"
        )


# ── Standalone runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Run all three scenarios directly (not via pytest).
    Prints a summary table.

    Usage:
        ANTHROPIC_API_KEY=sk-ant-... python tests/test_llm_live.py
    """
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    if not _api_key():
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    scenarios = [
        ("s1_full_info",   "S1-FullInfo"),
        ("s2_partial_info", "S2-PartialInfo"),
        ("s3_minimal_info", "S3-MinimalInfo"),
    ]

    results = []
    for scenario_dir, name in scenarios:
        print(f"\n{'─'*60}\nRunning {name}...\n{'─'*60}")
        try:
            _run_live_scenario(scenario_dir, name)
            results.append((name, "PASS", ""))
        except Exception as exc:
            results.append((name, "FAIL", str(exc)))
            print(f"  FAILED: {exc}")

    print(f"\n{'═'*60}")
    print(f"{'Scenario':<25} {'Result':<8} Notes")
    print(f"{'─'*60}")
    for name, result, msg in results:
        flag = "✓" if result == "PASS" else "✗"
        print(f"{flag} {name:<23} {result:<8} {msg[:40] if msg else ''}")
    print(f"{'═'*60}")

    failed = [r for r in results if r[1] != "PASS"]
    sys.exit(1 if failed else 0)
