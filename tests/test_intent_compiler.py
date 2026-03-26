"""
tests/test_intent_compiler.py
------------------------------
Unit tests for agent/layout_intent.py (validator) and
agent/intent_compiler.py (LayoutIntent → legacy flat spec).

All tests are fully offline — no OCI auth, no BOM.xlsx fixture required.

Run: pytest tests/test_intent_compiler.py -v
"""
from __future__ import annotations

import copy
import pytest

from agent.bom_parser import ServiceItem, build_layout_intent_prompt
from agent.layout_intent import (
    LayoutIntent, DeploymentHints, Placement, Assumption,
    validate_layout_intent, LayoutIntentError,
    VALID_LAYERS, VALID_GROUPS,
)
from agent.intent_compiler import compile_intent_to_flat_spec
from agent.layout_engine import spec_to_draw_dict


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _items():
    """Four canonical ServiceItems used across multiple tests."""
    return [
        ServiceItem(id="on_prem",          oci_type="on premises",    label="On-Premises",     layer="external"),
        ServiceItem(id="internet_gateway", oci_type="internet gateway", label="Internet Gateway", layer="ingress"),
        ServiceItem(id="compute_1",        oci_type="compute",         label="Compute",          layer="compute"),
        ServiceItem(id="database_1",       oci_type="database",        label="PostgreSQL DB",    layer="data"),
    ]


def _minimal_intent_data(items=None):
    """Raw dict that represents a valid LayoutIntent for the four items above."""
    target = items or _items()
    return {
        "schema_version": "1.0",
        "deployment_hints": {
            "region_count": 1,
            "availability_domains_per_region": 1,
            "dr_enabled": False,
            "on_prem_connectivity": "fastconnect",
        },
        "placements": [
            {"id": i.id, "oci_type": i.oci_type, "layer": i.layer,
             "group": _default_group(i.oci_type)}
            for i in target
        ],
        "assumptions": [
            {"id": "ha_mode", "statement": "Single AD assumed",
             "reason": "No HA signal", "risk": "low"}
        ],
        "fixed_edges_policy": True,
    }


def _default_group(oci_type: str):
    """Deterministic group assignment matching classification rules."""
    if oci_type in {"compute", "functions", "api gateway", "container engine"}:
        return "app_sub_box"
    if oci_type in {"database", "vault"}:
        return "db_sub_box"
    if oci_type in {"waf", "load balancer", "bastion"}:
        return "pub_sub_box"
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Task 5.1 — Intent compiler: layers, groups, edges
# ══════════════════════════════════════════════════════════════════════════════

class TestIntentCompilerLayersGroupsEdges:
    """T_IC_001 — verify compiler output structure."""

    def _compile(self):
        items = _items()
        intent = validate_layout_intent(_minimal_intent_data(items), items)
        return compile_intent_to_flat_spec(intent, items)

    def test_flat_spec_has_required_keys(self):
        spec = self._compile()
        assert "layers" in spec
        assert "groups" in spec
        assert "edges" in spec

    def test_all_five_layers_present(self):
        spec = self._compile()
        assert set(spec["layers"].keys()) == {"external", "ingress", "compute", "async", "data"}

    def test_on_prem_in_external_layer(self):
        spec = self._compile()
        ids = [n["id"] for n in spec["layers"]["external"]]
        assert "on_prem" in ids

    def test_igw_in_ingress_layer(self):
        spec = self._compile()
        ids = [n["id"] for n in spec["layers"]["ingress"]]
        assert "internet_gateway" in ids

    def test_compute_in_compute_layer(self):
        spec = self._compile()
        ids = [n["id"] for n in spec["layers"]["compute"]]
        assert "compute_1" in ids

    def test_database_in_data_layer(self):
        spec = self._compile()
        ids = [n["id"] for n in spec["layers"]["data"]]
        assert "database_1" in ids

    def test_app_sub_box_group_contains_compute(self):
        spec = self._compile()
        app_group = next((g for g in spec["groups"] if g["id"] == "app_sub_box"), None)
        assert app_group is not None
        assert "compute_1" in app_group["nodes"]

    def test_db_sub_box_group_contains_database(self):
        spec = self._compile()
        db_group = next((g for g in spec["groups"] if g["id"] == "db_sub_box"), None)
        assert db_group is not None
        assert "database_1" in db_group["nodes"]

    def test_igw_vcn_edge_injected(self):
        """internet_gateway → vcn_box edge must be present when IGW exists."""
        spec = self._compile()
        pairs = {(e["source"], e["target"]) for e in spec["edges"]}
        assert ("internet_gateway", "vcn_box") in pairs

    def test_on_prem_vcn_edge_injected(self):
        """on_prem → vcn_box edge must be present when on_prem exists."""
        spec = self._compile()
        pairs = {(e["source"], e["target"]) for e in spec["edges"]}
        assert ("on_prem", "vcn_box") in pairs

    def test_on_prem_vcn_edge_label_fastconnect(self):
        spec = self._compile()
        edge = next(
            (e for e in spec["edges"]
             if e["source"] == "on_prem" and e["target"] == "vcn_box"),
            None,
        )
        assert edge is not None
        assert edge["label"] == "FastConnect"

    def test_app_to_db_edge_injected(self):
        """app_sub_box → db_sub_box edge must be present when both groups exist."""
        spec = self._compile()
        pairs = {(e["source"], e["target"]) for e in spec["edges"]}
        assert ("app_sub_box", "db_sub_box") in pairs

    def test_no_vcn_box_in_layers(self):
        """vcn_box is synthesised by layout engine; must NOT appear in layers."""
        spec = self._compile()
        all_node_ids = [
            n["id"]
            for layer_nodes in spec["layers"].values()
            for n in layer_nodes
        ]
        assert "vcn_box" not in all_node_ids

    def test_no_vcn_box_in_groups(self):
        spec = self._compile()
        group_ids = [g["id"] for g in spec["groups"]]
        assert "vcn_box" not in group_ids

    def test_output_is_deterministic(self):
        """Compiling the same intent twice produces identical output."""
        items = _items()
        data = _minimal_intent_data(items)
        intent_a = validate_layout_intent(copy.deepcopy(data), items)
        intent_b = validate_layout_intent(copy.deepcopy(data), items)
        spec_a = compile_intent_to_flat_spec(intent_a, items)
        spec_b = compile_intent_to_flat_spec(intent_b, items)
        assert spec_a == spec_b

    def test_layout_engine_accepts_compiled_spec(self):
        """The compiled legacy spec must be accepted by spec_to_draw_dict()."""
        items = _items()
        intent = validate_layout_intent(_minimal_intent_data(items), items)
        flat_spec = compile_intent_to_flat_spec(intent, items)
        items_by_id = {i.id: i for i in items}
        draw_dict = spec_to_draw_dict(flat_spec, items_by_id)
        assert "nodes" in draw_dict
        assert "edges" in draw_dict
        node_ids = {n["id"] for n in draw_dict["nodes"]}
        assert "compute_1" in node_ids
        assert "database_1" in node_ids


# ══════════════════════════════════════════════════════════════════════════════
# Task 5.2 — Validation rejects unknown layer
# ══════════════════════════════════════════════════════════════════════════════

class TestLayoutIntentValidation:
    """T_IV_001 — layer validation."""

    def test_rejects_unknown_layer_app(self):
        """layer='app' is an AD-tier name, not a valid flat-spec layer."""
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                {"id": "compute_1", "oci_type": "compute", "layer": "app", "group": None},
            ],
        }
        with pytest.raises(LayoutIntentError, match="Unknown layer"):
            validate_layout_intent(data)

    def test_rejects_unknown_layer_web(self):
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                {"id": "compute_1", "oci_type": "compute", "layer": "web", "group": None},
            ],
        }
        with pytest.raises(LayoutIntentError, match="Unknown layer"):
            validate_layout_intent(data)

    def test_rejects_unknown_group(self):
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                {"id": "compute_1", "oci_type": "compute", "layer": "compute",
                 "group": "mystery_box"},
            ],
        }
        with pytest.raises(LayoutIntentError, match="Unknown group"):
            validate_layout_intent(data)

    def test_rejects_duplicate_placement_id(self):
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                {"id": "compute_1", "oci_type": "compute", "layer": "compute", "group": None},
                {"id": "compute_1", "oci_type": "compute", "layer": "compute", "group": None},
            ],
        }
        with pytest.raises(LayoutIntentError, match="Duplicate"):
            validate_layout_intent(data)

    def test_rejects_missing_item_ids(self):
        """If items list is supplied, all item ids must appear in placements."""
        items = _items()
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                # only on_prem — other three are missing
                {"id": "on_prem", "oci_type": "on premises", "layer": "external", "group": None},
            ],
        }
        with pytest.raises(LayoutIntentError, match="missing from placements"):
            validate_layout_intent(data, items)

    def test_accepts_valid_intent(self):
        items = _items()
        intent = validate_layout_intent(_minimal_intent_data(items), items)
        assert len(intent.placements) == len(items)

    def test_none_group_treated_as_null(self):
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                {"id": "on_prem", "oci_type": "on premises", "layer": "external", "group": "none"},
            ],
        }
        intent = validate_layout_intent(data)
        assert intent.placements[0].group is None

    def test_null_string_group_treated_as_null(self):
        """LLMs sometimes return the string "null" instead of JSON null."""
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                {"id": "on_prem", "oci_type": "on premises", "layer": "external", "group": "null"},
            ],
        }
        intent = validate_layout_intent(data)
        assert intent.placements[0].group is None

    def test_unknown_connectivity_coerced_to_unknown(self):
        data = {
            "schema_version": "1.0",
            "deployment_hints": {"on_prem_connectivity": "express-route"},
            "placements": [],
        }
        intent = validate_layout_intent(data)
        assert intent.deployment_hints.on_prem_connectivity == "unknown"

    @pytest.mark.parametrize("layer", sorted(VALID_LAYERS))
    def test_all_valid_layers_accepted(self, layer):
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [{"id": "svc_1", "oci_type": "compute", "layer": layer, "group": None}],
        }
        intent = validate_layout_intent(data)
        assert intent.placements[0].layer == layer


# ══════════════════════════════════════════════════════════════════════════════
# Task 5.3 — BOM-only scenario: prompt + deterministic compile
# ══════════════════════════════════════════════════════════════════════════════

class TestBomOnlyScenario:
    """T_BS_001 — BOM only (no questionnaire/notes), deterministic pipeline."""

    def _make_bom_items(self):
        """Small set of items that parse_bom would produce from a typical BOM."""
        return [
            ServiceItem(id="on_prem",          oci_type="on premises",     label="On-Premises",       layer="external"),
            ServiceItem(id="internet",          oci_type="internet",        label="Public Internet",   layer="external"),
            ServiceItem(id="internet_gateway",  oci_type="internet gateway",label="Internet Gateway",  layer="ingress"),
            ServiceItem(id="nat_gateway",       oci_type="nat gateway",     label="NAT Gateway",       layer="ingress"),
            ServiceItem(id="drg",               oci_type="drg",             label="DRG",               layer="ingress"),
            ServiceItem(id="compute_1",         oci_type="compute",         label="Compute",           layer="compute"),
            ServiceItem(id="database_1",        oci_type="database",        label="PostgreSQL DB",     layer="data"),
            ServiceItem(id="bastion_1",         oci_type="bastion",         label="Bastion",           layer="ingress"),
        ]

    def _make_intent_data(self, items):
        """Simulate the LayoutIntent the LLM would return for BOM-only input."""
        group_map = {
            "internet gateway": None,
            "nat gateway":      None,
            "drg":              None,
            "compute":          "app_sub_box",
            "database":         "db_sub_box",
            "bastion":          "pub_sub_box",
            "on premises":      None,
            "internet":         None,
        }
        return {
            "schema_version": "1.0",
            "deployment_hints": {
                "region_count": 1,
                "availability_domains_per_region": 1,
                "dr_enabled": False,
                "on_prem_connectivity": "fastconnect",
            },
            "placements": [
                {
                    "id":       i.id,
                    "oci_type": i.oci_type,
                    "layer":    i.layer,
                    "group":    group_map.get(i.oci_type),
                }
                for i in items
            ],
            "assumptions": [
                {
                    "id":        "ha_mode",
                    "statement": "Single AD, no HA signal in BOM",
                    "reason":    "No HA or multi-AD mentioned",
                    "risk":      "low",
                }
            ],
            "fixed_edges_policy": True,
        }

    def test_prompt_contains_all_item_ids(self):
        items = self._make_bom_items()
        prompt = build_layout_intent_prompt(items)
        for item in items:
            assert item.id in prompt, f"Item id {item.id!r} missing from prompt"

    def test_prompt_contains_valid_layer_names(self):
        items = self._make_bom_items()
        prompt = build_layout_intent_prompt(items)
        assert "external" in prompt
        assert "ingress" in prompt
        assert "compute" in prompt

    def test_prompt_contains_group_names(self):
        items = self._make_bom_items()
        prompt = build_layout_intent_prompt(items)
        assert "pub_sub_box" in prompt
        assert "app_sub_box" in prompt
        assert "db_sub_box" in prompt

    def test_compiler_produces_deterministic_spec(self):
        """Compiling the same intent twice gives identical flat specs."""
        items = self._make_bom_items()
        data = self._make_intent_data(items)
        intent_a = validate_layout_intent(copy.deepcopy(data), items)
        intent_b = validate_layout_intent(copy.deepcopy(data), items)
        spec_a = compile_intent_to_flat_spec(intent_a, items)
        spec_b = compile_intent_to_flat_spec(intent_b, items)
        assert spec_a == spec_b

    def test_spec_contains_compute_in_app_group(self):
        items = self._make_bom_items()
        intent = validate_layout_intent(self._make_intent_data(items), items)
        spec = compile_intent_to_flat_spec(intent, items)
        app_group = next((g for g in spec["groups"] if g["id"] == "app_sub_box"), None)
        assert app_group is not None
        assert "compute_1" in app_group["nodes"]

    def test_spec_contains_database_in_db_group(self):
        items = self._make_bom_items()
        intent = validate_layout_intent(self._make_intent_data(items), items)
        spec = compile_intent_to_flat_spec(intent, items)
        db_group = next((g for g in spec["groups"] if g["id"] == "db_sub_box"), None)
        assert db_group is not None
        assert "database_1" in db_group["nodes"]

    def test_spec_accepted_by_layout_engine(self):
        """End-to-end: BOM items → intent → flat spec → draw_dict (no OCI needed)."""
        items = self._make_bom_items()
        intent = validate_layout_intent(self._make_intent_data(items), items)
        flat_spec = compile_intent_to_flat_spec(intent, items)
        items_by_id = {i.id: i for i in items}
        draw_dict = spec_to_draw_dict(flat_spec, items_by_id)
        assert draw_dict["nodes"]
        node_ids = {n["id"] for n in draw_dict["nodes"]}
        assert "compute_1"  in node_ids
        assert "database_1" in node_ids
        assert "on_prem"    in node_ids

    def test_intent_assumptions_not_empty_for_bom_only(self):
        """A BOM-only intent should include at least one assumption."""
        items = self._make_bom_items()
        data = self._make_intent_data(items)
        intent = validate_layout_intent(data, items)
        assert len(intent.assumptions) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# Edge-case tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_no_groups_no_edge_injected(self):
        """If no groups exist (all nodes are ungrouped), no group edges appear."""
        items = [
            ServiceItem(id="on_prem", oci_type="on premises", label="On-Premises", layer="external"),
        ]
        data = {
            "schema_version": "1.0",
            "deployment_hints": {"on_prem_connectivity": "none"},
            "placements": [
                {"id": "on_prem", "oci_type": "on premises", "layer": "external", "group": None},
            ],
        }
        intent = validate_layout_intent(data, items)
        spec = compile_intent_to_flat_spec(intent, items)
        # No group edges
        pairs = {(e["source"], e["target"]) for e in spec["edges"]}
        assert ("pub_sub_box", "app_sub_box") not in pairs
        assert ("app_sub_box", "db_sub_box") not in pairs

    def test_pub_to_app_edge_only_when_both_groups_present(self):
        """pub_sub_box → app_sub_box only when both groups have members."""
        items = [
            ServiceItem(id="bastion_1", oci_type="bastion",  label="Bastion",  layer="ingress"),
            ServiceItem(id="compute_1", oci_type="compute",  label="Compute",  layer="compute"),
        ]
        data = {
            "schema_version": "1.0",
            "deployment_hints": {},
            "placements": [
                {"id": "bastion_1", "oci_type": "bastion",  "layer": "ingress",  "group": "pub_sub_box"},
                {"id": "compute_1", "oci_type": "compute",  "layer": "compute",  "group": "app_sub_box"},
            ],
        }
        intent = validate_layout_intent(data, items)
        spec = compile_intent_to_flat_spec(intent, items)
        pairs = {(e["source"], e["target"]) for e in spec["edges"]}
        assert ("pub_sub_box", "app_sub_box") in pairs
        assert ("app_sub_box", "db_sub_box") not in pairs  # no db group

    def test_deployment_hints_region_count_stored(self):
        data = {
            "schema_version": "1.0",
            "deployment_hints": {"region_count": 2, "dr_enabled": True,
                                 "on_prem_connectivity": "fastconnect"},
            "placements": [],
        }
        intent = validate_layout_intent(data)
        assert intent.deployment_hints.region_count == 2
        assert intent.deployment_hints.dr_enabled is True
        assert intent.deployment_hints.on_prem_connectivity == "fastconnect"

    def test_questionnaire_text_in_prompt(self):
        items = [ServiceItem(id="c1", oci_type="compute", label="C", layer="compute")]
        prompt = build_layout_intent_prompt(items, questionnaire_text="3 ADs, HA mode")
        assert "3 ADs, HA mode" in prompt

    def test_notes_text_in_prompt(self):
        items = [ServiceItem(id="c1", oci_type="compute", label="C", layer="compute")]
        prompt = build_layout_intent_prompt(items, notes_text="Discussed DR requirements.")
        assert "Discussed DR requirements." in prompt
