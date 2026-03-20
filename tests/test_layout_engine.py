"""
tests/test_layout_engine.py
----------------------------
Unit tests for agent.layout_engine.

Run: pytest tests/test_layout_engine.py
"""
import pytest
from agent.layout_engine import compute_positions, spec_to_draw_dict, PAGE_W, PAGE_H

MINIMAL_SPEC = {
    "direction": "LR",
    "page": {"width": PAGE_W, "height": PAGE_H},
    "layers": {
        "external": [{"id": "on_prem", "type": "on premises", "label": "On-Premises"}],
        "ingress":  [{"id": "drg_1",   "type": "drg",         "label": "DRG"}],
        "compute":  [{"id": "comp_1",  "type": "compute",      "label": "Compute"}],
        "async":    [],
        "data":     [{"id": "db_1",    "type": "database",     "label": "PostgreSQL DB"}],
    },
    "groups": [
        {"id": "pub_sub_box", "label": "Public Subnet", "nodes": ["drg_1"]},
        {"id": "app_sub_box", "label": "App Subnet",    "nodes": ["comp_1"]},
        {"id": "db_sub_box",  "label": "DB Subnet",     "nodes": ["db_1"]},
    ],
    "edges": [],
}


class TestComputePositions:
    def test_returns_tuple(self):
        nodes, groups = compute_positions(MINIMAL_SPEC)
        assert isinstance(nodes, list)
        assert isinstance(groups, list)

    def test_all_nodes_positioned(self):
        nodes, _ = compute_positions(MINIMAL_SPEC)
        ids = {n.id for n in nodes}
        assert "on_prem" in ids
        assert "drg_1" in ids
        assert "comp_1" in ids

    def test_nodes_within_page_bounds(self):
        nodes, _ = compute_positions(MINIMAL_SPEC)
        for n in nodes:
            assert 0 <= n.x <= PAGE_W, f"Node {n.id} x={n.x} out of bounds"
            assert 0 <= n.y <= PAGE_H, f"Node {n.id} y={n.y} out of bounds"

    def test_vcn_box_inserted(self):
        _, groups = compute_positions(MINIMAL_SPEC)
        ids = [g.id for g in groups]
        assert "vcn_box" in ids

    def test_vcn_box_first(self):
        _, groups = compute_positions(MINIMAL_SPEC)
        assert groups[0].id == "vcn_box"

    def test_group_boxes_have_positive_dimensions(self):
        _, groups = compute_positions(MINIMAL_SPEC)
        for g in groups:
            assert g.w > 0, f"Group {g.id} has zero width"
            assert g.h > 0, f"Group {g.id} has zero height"

    def test_accepts_json_string(self):
        import json
        nodes, groups = compute_positions(json.dumps(MINIMAL_SPEC))
        assert len(nodes) > 0


class TestSpecToDrawDict:
    def _make_items_by_id(self):
        from agent.bom_parser import ServiceItem
        return {
            "on_prem": ServiceItem(id="on_prem", oci_type="on premises", label="On-Premises", layer="external"),
            "drg_1":   ServiceItem(id="drg_1",   oci_type="drg",         label="DRG",         layer="ingress"),
            "comp_1":  ServiceItem(id="comp_1",  oci_type="compute",     label="Compute",     layer="compute"),
            "db_1":    ServiceItem(id="db_1",    oci_type="database",    label="DB",          layer="data"),
        }

    def test_returns_nodes_and_edges(self):
        draw_dict = spec_to_draw_dict(MINIMAL_SPEC, self._make_items_by_id())
        assert "nodes" in draw_dict
        assert "edges" in draw_dict

    def test_group_boxes_before_icons(self):
        draw_dict = spec_to_draw_dict(MINIMAL_SPEC, self._make_items_by_id())
        nodes = draw_dict["nodes"]
        first_group = next(i for i, n in enumerate(nodes) if n["type"] == "_group_box")
        first_icon  = next(i for i, n in enumerate(nodes) if n["type"] not in ("_group_box",))
        assert first_group < first_icon

    def test_fixed_edges_present(self):
        draw_dict = spec_to_draw_dict(MINIMAL_SPEC, self._make_items_by_id())
        edges = draw_dict["edges"]
        src_tgt = {(e["source"], e["target"]) for e in edges}
        assert ("on_prem", "vcn_box") in src_tgt
