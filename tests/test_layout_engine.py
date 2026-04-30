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

    def test_no_duplicate_vcn_box_when_already_in_spec(self):
        """If the spec's groups list already contains a vcn_box, compute_positions
        must not insert a second one."""
        spec_with_vcn = {
            "direction": "LR",
            "page": {"width": PAGE_W, "height": PAGE_H},
            "layers": {
                "external": [{"id": "on_prem", "type": "on premises", "label": "On-Premises"}],
                "ingress":  [{"id": "drg_1",   "type": "drg",         "label": "DRG"}],
                "compute":  [{"id": "comp_1",  "type": "compute",     "label": "Compute"}],
                "async":    [],
                "data":     [],
            },
            "groups": [
                {"id": "pub_sub_box", "label": "Public Subnet", "nodes": ["drg_1"]},
                {"id": "app_sub_box", "label": "App Subnet",    "nodes": ["comp_1"]},
                # vcn_box explicitly present in the spec (should not be duplicated)
                {"id": "vcn_box",     "label": "VCN",           "nodes": ["on_prem"]},
            ],
            "edges": [],
        }
        _, groups = compute_positions(spec_with_vcn)
        vcn_boxes = [g for g in groups if g.id == "vcn_box"]
        assert len(vcn_boxes) == 1, (
            f"Expected exactly 1 vcn_box, got {len(vcn_boxes)}: {[g.id for g in groups]}"
        )

    def test_accepts_json_string(self):
        import json
        nodes, groups = compute_positions(json.dumps(MINIMAL_SPEC))
        assert len(nodes) > 0

    def test_single_ad_renders_fault_domain_local_subnets(self):
        spec = {
            "deployment_type": "single_ad",
            "regions": [
                {
                    "id": "region_1",
                    "label": "us-ashburn-1",
                    "availability_domains": [
                        {
                            "id": "ad_1",
                            "label": "AD 1",
                            "fault_domains": [
                                {
                                    "id": "fd_1",
                                    "label": "FD 1",
                                    "subnets": [
                                        {
                                            "id": "fd_1_bm_subnet",
                                            "label": "FD 1 BM Subnet",
                                            "tier": "app",
                                            "nodes": [
                                                {
                                                    "id": "bm_fd_1",
                                                    "type": "bare metal",
                                                    "label": "BM.Standard.X9.64 FD1",
                                                }
                                            ],
                                        }
                                    ],
                                },
                                {
                                    "id": "fd_2",
                                    "label": "FD 2",
                                    "subnets": [
                                        {
                                            "id": "fd_2_bm_subnet",
                                            "label": "FD 2 BM Subnet",
                                            "tier": "app",
                                            "nodes": [
                                                {
                                                    "id": "bm_fd_2",
                                                    "type": "bare metal",
                                                    "label": "BM.Standard.X9.64 FD2",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
            "external": [],
            "edges": [],
        }

        nodes, boxes = compute_positions(spec)
        nodes_by_id = {node.id: node for node in nodes}
        boxes_by_id = {box.id: box for box in boxes}

        assert {"bm_fd_1", "bm_fd_2"}.issubset(nodes_by_id)
        assert {"fd_1", "fd_2", "fd_1_bm_subnet", "fd_2_bm_subnet"}.issubset(boxes_by_id)

        for fd_id, subnet_id, node_id in [
            ("fd_1", "fd_1_bm_subnet", "bm_fd_1"),
            ("fd_2", "fd_2_bm_subnet", "bm_fd_2"),
        ]:
            fd_box = boxes_by_id[fd_id]
            subnet_box = boxes_by_id[subnet_id]
            node = nodes_by_id[node_id]
            assert fd_box.x < subnet_box.x
            assert subnet_box.x + subnet_box.w < fd_box.x + fd_box.w
            assert fd_box.x < node.x
            assert node.x + node.w < fd_box.x + fd_box.w

        assert boxes_by_id["fd_1"].x < boxes_by_id["fd_2"].x


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

    def test_group_boxes_in_boxes_not_nodes(self):
        """Group boxes must appear in draw_dict['boxes'], not duplicated in 'nodes'."""
        draw_dict = spec_to_draw_dict(MINIMAL_SPEC, self._make_items_by_id())
        # No _group_box entries should bleed into the nodes list
        assert not any(n.get("type") == "_group_box" for n in draw_dict["nodes"])
        # Boxes must be present in the dedicated 'boxes' list
        assert len(draw_dict.get("boxes", [])) > 0

    def test_fixed_edges_present(self):
        draw_dict = spec_to_draw_dict(MINIMAL_SPEC, self._make_items_by_id())
        edges = draw_dict["edges"]
        src_tgt = {(e["source"], e["target"]) for e in edges}
        assert ("on_prem", "vcn_box") in src_tgt
