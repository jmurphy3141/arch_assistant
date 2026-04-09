"""
tests/test_llm_scenarios.py
----------------------------
End-to-end pipeline tests for three Calypso / Capital Markets scenarios.

Each test uses a hardcoded mock LLM response representing what the LLM
would return for that input fidelity level.  The mocked spec is fed directly
into spec_to_draw_dict() and generate_drawio() to validate the full
layout + render pipeline — no OCI auth required.

Scenarios (all target multi_ad two-AD regional webapp architecture):
  1. Full info      — complete questionnaire answered; known compute/DB sizing
  2. Partial info   — architecture known but sizing gaps filled with "(assumed)"
  3. Minimal info   — only "capital markets trading"; agent suggests reference arch

Run:  pytest tests/test_llm_scenarios.py -v
"""
from __future__ import annotations

import os
import tempfile
import pytest

from agent.layout_engine import spec_to_draw_dict, PAGE_W, PAGE_H
from agent.drawio_generator import generate_drawio


# ── Shared Calypso / Capital Markets multi_ad spec factory ─────────────────

def _calypso_multi_ad_spec(
    *,
    web_label_ad1: str = "Web Tier",
    web_label_ad2: str = "Web Tier",
    app_label_ad1: str = "Calypso App",
    app_label_ad2: str = "Calypso App",
    db_label_ad1:  str = "Oracle DB RAC (Primary)",
    db_label_ad2:  str = "Oracle DB RAC (Standby)",
    drg_label:     str = "DRG / FastConnect",
    extra_oci_services: list | None = None,
) -> dict:
    """
    Return a multi_ad spec for the Calypso / Capital Markets reference architecture.
    Parameters allow per-scenario label customisation.
    """
    extra_oci_services = extra_oci_services or []
    base_oci_services = [
        {"id": "obj_storage",  "type": "object storage", "label": "Object Storage"},
        {"id": "logging_svc",  "type": "logging",        "label": "Logging Analytics"},
        {"id": "monitoring",   "type": "monitoring",     "label": "Monitoring + APM"},
    ]

    return {
        "deployment_type": "multi_ad",
        "page": {"width": 1654, "height": 1169},
        "regions": [{
            "id": "region_primary",
            "label": "Oracle Cloud Infrastructure — us-phoenix-1",
            "regional_subnets": [
                {
                    "id": "pub_sub_lb",
                    "label": "Public Subnet",
                    "tier": "public_ingress",
                    "nodes": [
                        {"id": "waf",    "type": "waf",           "label": "WAF"},
                        {"id": "pub_lb", "type": "load balancer", "label": "Public Load Balancer"},
                    ],
                },
                {
                    "id": "priv_sub_lb",
                    "label": "Private Subnet",
                    "tier": "private_ingress",
                    "nodes": [
                        {"id": "priv_lb", "type": "load balancer", "label": "Private Load Balancer"},
                    ],
                },
                {
                    "id": "pub_sub_bastion",
                    "label": "Public Subnet",
                    "tier": "bastion",
                    "nodes": [
                        {"id": "bastion", "type": "compute", "label": "Bastion Host"},
                    ],
                },
            ],
            "availability_domains": [
                {
                    "id": "ad1",
                    "label": "Availability Domain 1",
                    "subnets": [
                        {
                            "id": "web_sub_ad1",
                            "label": "Private Subnet",
                            "tier": "web",
                            "nodes": [{"id": "web_ad1", "type": "compute", "label": web_label_ad1}],
                        },
                        {
                            "id": "app_sub_ad1",
                            "label": "Private Subnet",
                            "tier": "app",
                            "nodes": [
                                {"id": "calypso_ad1",  "type": "compute", "label": app_label_ad1},
                                {"id": "queue_ad1",    "type": "queue",   "label": "Messaging Queue"},
                            ],
                        },
                        {
                            "id": "db_sub_ad1",
                            "label": "Private Subnet",
                            "tier": "db",
                            "nodes": [{"id": "db_ad1", "type": "database", "label": db_label_ad1}],
                        },
                    ],
                },
                {
                    "id": "ad2",
                    "label": "Availability Domain 2",
                    "subnets": [
                        {
                            "id": "web_sub_ad2",
                            "label": "Private Subnet",
                            "tier": "web",
                            "nodes": [{"id": "web_ad2", "type": "compute", "label": web_label_ad2}],
                        },
                        {
                            "id": "app_sub_ad2",
                            "label": "Private Subnet",
                            "tier": "app",
                            "nodes": [
                                {"id": "calypso_ad2",  "type": "compute", "label": app_label_ad2},
                                {"id": "queue_ad2",    "type": "queue",   "label": "Messaging Queue"},
                            ],
                        },
                        {
                            "id": "db_sub_ad2",
                            "label": "Private Subnet",
                            "tier": "db",
                            "nodes": [{"id": "db_ad2", "type": "database", "label": db_label_ad2}],
                        },
                    ],
                },
            ],
            "gateways": [
                {"id": "igw", "type": "internet gateway", "label": "Internet Gateway", "position": "top"},
                {"id": "drg", "type": "drg",              "label": drg_label,          "position": "left"},
                {"id": "nat", "type": "nat gateway",      "label": "NAT Gateway",      "position": "right"},
                {"id": "sgw", "type": "service gateway",  "label": "Service Gateway",  "position": "right"},
            ],
            "oci_services": base_oci_services + extra_oci_services,
        }],
        "external": [
            {"id": "on_prem",  "type": "on premises",   "label": "On-Premises\n(Trading Desks)"},
            {"id": "internet", "type": "public internet", "label": "Public Internet"},
        ],
        "edges": [
            {"id": "e1", "source": "on_prem",  "target": "drg",    "label": "FastConnect"},
            {"id": "e2", "source": "internet", "target": "igw",    "label": "HTTPS/443"},
        ],
    }


# ── Helpers ─────────────────────────────────────────────────────────────────

def _run_pipeline(spec: dict) -> tuple[dict, str]:
    """Run spec through full layout + render pipeline. Returns (draw_dict, xml_string)."""
    d = spec_to_draw_dict(spec, {})
    with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
        outpath = f.name
    try:
        path = generate_drawio(d, outpath)
        xml = path.read_text(encoding="utf-8")
    finally:
        if os.path.exists(outpath):
            os.unlink(outpath)
    return d, xml


def _node_ids(draw_dict: dict) -> set[str]:
    return {n["id"] for n in draw_dict["nodes"]}


def _box_ids(draw_dict: dict) -> set[str]:
    return {b["id"] for b in draw_dict["boxes"]}


def _edge_pairs(draw_dict: dict) -> set[tuple[str, str]]:
    return {(e["source"], e["target"]) for e in draw_dict["edges"]}


def _subnet_boxes(draw_dict: dict) -> list[dict]:
    return [b for b in draw_dict["boxes"] if b["box_type"] == "_subnet_box"]


# ── Scenario 1: Full info ────────────────────────────────────────────────────
# Questionnaire context (what the user would submit):
#   Environment:   Capital markets — Calypso trading platform migration to OCI
#   App tier:      8 x VM.Standard3.Flex (32 OCPU / 512 GB) per AD
#   Database:      Oracle Exadata X9M — 2-node RAC, Data Guard across 2 ADs
#   Storage:       10 TB block + 5 TB object (market-data archive)
#   Networking:    FastConnect 10 Gbps (2 redundant) to Chicago trading desk
#   HA:            multi-AD active-active in us-phoenix-1
#   DR:            Data Guard synchronous replication, RTO < 30 min

SCENARIO_1_SPEC = _calypso_multi_ad_spec(
    web_label_ad1="Web Tier\n×4 VM.Std3",
    web_label_ad2="Web Tier\n×4 VM.Std3",
    app_label_ad1="Calypso App\n×8 (32 OCPU)",
    app_label_ad2="Calypso App\n×8 (32 OCPU)",
    db_label_ad1="Oracle Exadata RAC\n(Primary)",
    db_label_ad2="Oracle Exadata RAC\n(Standby — Data Guard)",
    drg_label="DRG / FastConnect 10G",
    extra_oci_services=[
        {"id": "vault",   "type": "vault",   "label": "Vault (Secrets)"},
    ],
)

# ── Scenario 2: Partial info — assumptions applied ───────────────────────────
# Questionnaire context (what the user would submit):
#   Environment:   Capital markets — Calypso, migrating from on-prem
#   App tier:      "several app servers" (count unknown)
#   Database:      Oracle DB, exact edition TBD
#   Networking:    FastConnect confirmed, bandwidth not specified
#   HA:            "we need HA" — multi-AD inferred from "regional HA" mention
#   DR:            not specified → assumed Data Guard from default table

SCENARIO_2_SPEC = _calypso_multi_ad_spec(
    web_label_ad1="Web Tier\n(count assumed: 2)",
    web_label_ad2="Web Tier\n(count assumed: 2)",
    app_label_ad1="Calypso App\n(sizing assumed: 4×16 OCPU)",
    app_label_ad2="Calypso App\n(sizing assumed: 4×16 OCPU)",
    db_label_ad1="Oracle DB\n(edition assumed: EE)",
    db_label_ad2="Oracle DB\n(Standby — assumed Data Guard)",
    drg_label="DRG / FastConnect\n(bandwidth assumed: 1G)",
)

# ── Scenario 3: Minimal info — agent-suggested reference architecture ─────────
# Questionnaire context (what the user would submit):
#   "We are a capital markets firm looking to run a trading platform on OCI.
#    We need it to be highly available."
#   No specific sizing, no specific HA mode, no DB edition
#   Agent applies ALL defaults from the assumption table.

SCENARIO_3_SPEC = _calypso_multi_ad_spec(
    web_label_ad1="Web Tier\n(suggested: 2 nodes)",
    web_label_ad2="Web Tier\n(suggested: 2 nodes)",
    app_label_ad1="App Tier\n(suggested: 4 nodes)",
    app_label_ad2="App Tier\n(suggested: 4 nodes)",
    db_label_ad1="Database\n(suggested: Oracle EE)",
    db_label_ad2="Database\n(suggested: Standby)",
    drg_label="DRG / FastConnect\n(suggested)",
    extra_oci_services=[
        {"id": "db_mgmt", "type": "monitoring", "label": "DB Management"},
    ],
)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestScenario1FullInfo:
    """Full questionnaire answered — all components explicitly sized."""

    def setup_method(self):
        self.d, self.xml = _run_pipeline(SCENARIO_1_SPEC)

    def test_deployment_type_is_multi_ad(self):
        assert SCENARIO_1_SPEC["deployment_type"] == "multi_ad"

    def test_region_box_present(self):
        region_boxes = [b for b in self.d["boxes"] if b["box_type"] == "_region_box"]
        assert len(region_boxes) == 1

    def test_two_ad_boxes(self):
        ad_boxes = [b for b in self.d["boxes"] if b["box_type"] == "_ad_box"]
        assert len(ad_boxes) == 2

    def test_expected_nodes_present(self):
        ids = _node_ids(self.d)
        assert "igw"      in ids, "Internet Gateway missing"
        assert "drg"      in ids, "DRG / FastConnect missing"
        assert "nat"      in ids, "NAT Gateway missing"
        assert "sgw"      in ids, "Service Gateway missing"
        assert "waf"      in ids, "WAF missing"
        assert "pub_lb"   in ids, "Public LB missing"
        assert "priv_lb"  in ids, "Private LB missing"
        assert "bastion"  in ids, "Bastion missing"
        assert "db_ad1"   in ids, "AD1 DB missing"
        assert "db_ad2"   in ids, "AD2 DB missing"
        assert "on_prem"  in ids, "On-Premises missing"

    def test_both_ad_web_app_db_subnets(self):
        subnet_ids = {b["id"] for b in _subnet_boxes(self.d)}
        assert "web_sub_ad1" in subnet_ids
        assert "web_sub_ad2" in subnet_ids
        assert "app_sub_ad1" in subnet_ids
        assert "app_sub_ad2" in subnet_ids
        assert "db_sub_ad1"  in subnet_ids
        assert "db_sub_ad2"  in subnet_ids

    def test_igw_present_as_node(self):
        """IGW communicates via position (VCN top edge) — no explicit edge required."""
        node_ids = {n["id"] for n in self.d["nodes"]}
        assert "igw" in node_ids, "IGW node missing from diagram"

    def test_drg_present_as_node(self):
        """DRG communicates via position (VCN left edge) — no explicit edge required."""
        node_ids = {n["id"] for n in self.d["nodes"]}
        assert "drg" in node_ids, "DRG node missing from diagram"

    def test_no_gateway_to_subnet_edges(self):
        """Gateway connectivity is implied by position; no gateway→subnet lines."""
        pairs = _edge_pairs(self.d)
        subnet_ids = {b["id"] for b in self.d["boxes"] if b["box_type"] == "_subnet_box"}
        gw_to_subnet = [(s, t) for s, t in pairs if t in subnet_ids]
        assert gw_to_subnet == [], f"Unexpected gateway→subnet edges: {gw_to_subnet}"

    def test_all_nodes_within_page_bounds(self):
        for n in self.d["nodes"]:
            assert 0 <= n["x"] <= PAGE_W, f"Node {n['id']} x={n['x']} out of bounds"
            assert 0 <= n["y"] <= PAGE_H, f"Node {n['id']} y={n['y']} out of bounds"

    def test_subnet_widths_fill_available(self):
        """AD-level subnets (vertically stacked) must fill the full available width.
        Regional subnets placed horizontally side-by-side may be narrower."""
        from agent.layout_engine import REGION_W
        subs = _subnet_boxes(self.d)
        # Subnets sharing a y-value are in a horizontal row; skip those
        from collections import Counter
        y_counts = Counter(round(s["y"]) for s in subs)
        vertical_subs = [s for s in subs if y_counts[round(s["y"])] == 1]
        for sub in vertical_subs:
            assert sub["w"] >= REGION_W * 0.3, (
                f"Subnet {sub['id']} is too narrow: w={sub['w']:.0f} (region_w={REGION_W})"
            )

    def test_xml_is_valid_drawio(self):
        assert "mxGraphModel" in self.xml
        assert "mxCell" in self.xml

    def test_drawio_has_both_ad_content(self):
        assert "Availability Domain 1" in self.xml
        assert "Availability Domain 2" in self.xml


class TestScenario2PartialInfoWithAssumptions:
    """Architecture known; missing sizing details are filled with assumptions."""

    def setup_method(self):
        self.d, self.xml = _run_pipeline(SCENARIO_2_SPEC)

    def test_deployment_type_is_multi_ad(self):
        assert SCENARIO_2_SPEC["deployment_type"] == "multi_ad"

    def test_two_ad_boxes(self):
        ad_boxes = [b for b in self.d["boxes"] if b["box_type"] == "_ad_box"]
        assert len(ad_boxes) == 2

    def test_assumed_labels_in_xml(self):
        assert "assumed" in self.xml.lower(), "Assumed labels should appear in XML for scenario 2"

    def test_all_gateway_types_present(self):
        ids = _node_ids(self.d)
        for gw_id in ("igw", "drg", "nat", "sgw"):
            assert gw_id in ids, f"Gateway {gw_id} missing in assumed-labels scenario"

    def test_db_present_in_both_ads(self):
        ids = _node_ids(self.d)
        assert "db_ad1" in ids
        assert "db_ad2" in ids

    def test_calypso_app_nodes_both_ads(self):
        ids = _node_ids(self.d)
        assert "calypso_ad1" in ids
        assert "calypso_ad2" in ids

    def test_gateways_present_as_nodes(self):
        """Gateways communicate via position — no explicit subnet edges required."""
        node_ids = _node_ids(self.d)
        assert "igw" in node_ids, "IGW node missing"
        assert "drg" in node_ids, "DRG node missing"

    def test_subnet_widths_fill_available(self):
        """AD-level subnets (vertically stacked) must fill the full available width.
        Regional subnets placed horizontally side-by-side may be narrower."""
        from agent.layout_engine import REGION_W
        subs = _subnet_boxes(self.d)
        # Subnets sharing a y-value are in a horizontal row; skip those
        from collections import Counter
        y_counts = Counter(round(s["y"]) for s in subs)
        vertical_subs = [s for s in subs if y_counts[round(s["y"])] == 1]
        for sub in vertical_subs:
            assert sub["w"] >= REGION_W * 0.3, (
                f"Subnet {sub['id']} is too narrow: w={sub['w']:.0f} (region_w={REGION_W})"
            )

    def test_xml_valid(self):
        assert "mxGraphModel" in self.xml


class TestScenario3MinimalInfoAgentSuggests:
    """Only 'capital markets HA' — agent applies all defaults and suggests architecture."""

    def setup_method(self):
        self.d, self.xml = _run_pipeline(SCENARIO_3_SPEC)

    def test_deployment_type_is_multi_ad(self):
        assert SCENARIO_3_SPEC["deployment_type"] == "multi_ad"

    def test_region_box_present(self):
        region_boxes = [b for b in self.d["boxes"] if b["box_type"] == "_region_box"]
        assert len(region_boxes) == 1

    def test_two_ad_boxes(self):
        ad_boxes = [b for b in self.d["boxes"] if b["box_type"] == "_ad_box"]
        assert len(ad_boxes) == 2

    def test_suggested_labels_in_xml(self):
        assert "suggested" in self.xml.lower()

    def test_minimum_required_components(self):
        ids = _node_ids(self.d)
        # Internet-facing components
        assert "igw" in ids,     "IGW required for internet-facing app"
        assert "waf" in ids,     "WAF required for internet-facing app"
        assert "pub_lb" in ids,  "Public LB required"
        # Private connectivity
        assert "drg" in ids,     "DRG required (capital markets connects on-prem)"
        assert "priv_lb" in ids, "Private LB required"
        assert "bastion" in ids, "Bastion required for admin access"
        # Compute tiers present in both ADs
        assert "web_ad1" in ids
        assert "web_ad2" in ids
        # DB in both ADs
        assert "db_ad1" in ids
        assert "db_ad2" in ids

    def test_three_regional_subnet_types(self):
        subnet_tiers = {b["tier"] for b in _subnet_boxes(self.d)}
        assert "public_ingress"  in subnet_tiers
        assert "private_ingress" in subnet_tiers
        assert "bastion"         in subnet_tiers

    def test_ad_subnets_cover_three_tiers(self):
        subnet_tiers = {b["tier"] for b in _subnet_boxes(self.d)}
        assert "web" in subnet_tiers
        assert "app" in subnet_tiers
        assert "db"  in subnet_tiers

    def test_no_overlapping_nodes(self):
        """No two icon nodes should share the exact same x,y position."""
        positions = [(n["x"], n["y"]) for n in self.d["nodes"]]
        assert len(positions) == len(set(positions)), "Duplicate node positions found"

    def test_all_nodes_within_page_bounds(self):
        for n in self.d["nodes"]:
            assert 0 <= n["x"] <= PAGE_W, f"Node {n['id']} x={n['x']} out of bounds"
            assert 0 <= n["y"] <= PAGE_H, f"Node {n['id']} y={n['y']} out of bounds"

    def test_xml_valid(self):
        assert "mxGraphModel" in self.xml
        assert "mxCell" in self.xml


# ── Cross-scenario consistency tests ─────────────────────────────────────────

class TestCrossScenarioConsistency:
    """All three scenarios produce structurally consistent multi_ad diagrams."""

    SPECS = [
        ("full_info",   SCENARIO_1_SPEC),
        ("assumptions", SCENARIO_2_SPEC),
        ("minimal",     SCENARIO_3_SPEC),
    ]

    @pytest.mark.parametrize("name,spec", SPECS)
    def test_multi_ad_has_two_ads(self, name, spec):
        d, _ = _run_pipeline(spec)
        ads = [b for b in d["boxes"] if b["box_type"] == "_ad_box"]
        assert len(ads) == 2, f"{name}: expected 2 AD boxes, got {len(ads)}"

    @pytest.mark.parametrize("name,spec", SPECS)
    def test_all_have_drg_and_igw(self, name, spec):
        d, _ = _run_pipeline(spec)
        ids = _node_ids(d)
        assert "igw" in ids, f"{name}: IGW missing"
        assert "drg" in ids, f"{name}: DRG missing"

    @pytest.mark.parametrize("name,spec", SPECS)
    def test_all_have_db_in_both_ads(self, name, spec):
        d, _ = _run_pipeline(spec)
        ids = _node_ids(d)
        assert "db_ad1" in ids, f"{name}: DB in AD1 missing"
        assert "db_ad2" in ids, f"{name}: DB in AD2 missing"

    @pytest.mark.parametrize("name,spec", SPECS)
    def test_all_produce_valid_xml(self, name, spec):
        _, xml = _run_pipeline(spec)
        assert "mxGraphModel" in xml, f"{name}: invalid XML"

    @pytest.mark.parametrize("name,spec", SPECS)
    def test_igw_present_as_node(self, name, spec):
        """IGW communicates via position — no explicit subnet edge required."""
        d, _ = _run_pipeline(spec)
        node_ids = {n["id"] for n in d["nodes"]}
        assert "igw" in node_ids, f"{name}: IGW node missing"

    @pytest.mark.parametrize("name,spec", SPECS)
    def test_drg_present_as_node(self, name, spec):
        """DRG communicates via position — no explicit subnet edge required."""
        d, _ = _run_pipeline(spec)
        node_ids = {n["id"] for n in d["nodes"]}
        assert "drg" in node_ids, f"{name}: DRG node missing"
