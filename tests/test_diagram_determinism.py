import xml.etree.ElementTree as ET

import pytest

from agent.drawio_generator import (
    DrawioValidationError,
    generate_drawio,
    validate_parent_model,
)
from agent.layout_engine import (
    ICON_W,
    PositionedBox,
    PositionedNode,
    enforce_gateway_placement,
    spec_to_draw_dict,
)
from agent.layout_intent import LayoutIntentValidationError, validate_layout_intent


def _standard_three_tier_spec() -> dict:
    return {
        "deployment_type": "single_ad",
        "regions": [
            {
                "id": "region_box",
                "label": "OCI Region",
                "availability_domains": [
                    {
                        "id": "ad_1",
                        "label": "Availability Domain 1",
                        "fault_domains": [
                            {"id": "fd_1", "label": "Fault Domain 1", "subnets": []},
                            {"id": "fd_2", "label": "Fault Domain 2", "subnets": []},
                            {"id": "fd_3", "label": "Fault Domain 3", "subnets": []},
                        ],
                        "subnets": [
                            {
                                "id": "public_subnet",
                                "label": "Public Subnet",
                                "tier": "ingress",
                                "nodes": [
                                    {"id": "waf", "type": "waf", "label": "WAF"},
                                    {"id": "lb", "type": "load balancer", "label": "Public LB"},
                                ],
                            },
                            {
                                "id": "app_subnet",
                                "label": "App Subnet",
                                "tier": "app",
                                "nodes": [
                                    {"id": "app", "type": "compute", "label": "App"}
                                ],
                            },
                            {
                                "id": "data_subnet",
                                "label": "Data Subnet",
                                "tier": "db",
                                "nodes": [
                                    {"id": "db", "type": "database", "label": "Database"}
                                ],
                            },
                        ],
                    }
                ],
                "gateways": [
                    {"id": "igw", "type": "internet gateway", "label": "IGW"},
                    {"id": "nat", "type": "nat gateway", "label": "NAT"},
                    {"id": "drg", "type": "drg", "label": "DRG"},
                    {"id": "sgw", "type": "service gateway", "label": "SGW"},
                ],
                "regional_subnets": [],
                "oci_services": [],
            }
        ],
        "external": [],
        "edges": [],
    }


def _gateway_positions(draw_dict: dict) -> dict[str, float]:
    return {
        node["id"]: node["x"]
        for node in draw_dict["nodes"]
        if node["id"] in {"igw", "nat", "drg", "sgw"}
    }


def test_standard_three_tier_diagram_is_repeatable_and_flat(tmp_path):
    outputs = []

    for index in range(3):
        draw_dict = spec_to_draw_dict(_standard_three_tier_spec(), {})
        output_path = tmp_path / f"diagram_{index}.drawio"
        generate_drawio(draw_dict, output_path)
        xml = output_path.read_text(encoding="utf-8")
        validate_parent_model(xml)
        outputs.append(
            {
                "node_count": len(draw_dict["nodes"]),
                "gateway_positions": _gateway_positions(draw_dict),
                "xml": xml,
            }
        )

    assert outputs[0]["node_count"] == outputs[1]["node_count"] == outputs[2]["node_count"]
    assert (
        outputs[0]["gateway_positions"]
        == outputs[1]["gateway_positions"]
        == outputs[2]["gateway_positions"]
    )

    root = ET.fromstring(outputs[0]["xml"])
    bad_parents = [
        (cell.get("id"), cell.get("parent"))
        for cell in root.iter("mxCell")
        if cell.get("id") != "0" and cell.get("parent") not in ("0", "1")
    ]
    assert bad_parents == []


def test_invalid_subnet_tier_is_rejected_with_correction():
    intent = {
        "schema_version": "1.0",
        "deployment_hints": {},
        "placements": [
            {
                "id": "app",
                "oci_type": "compute",
                "layer": "compute",
                "group": "app_sub_box",
                "subnet_tier": "Generic",
            }
        ],
    }

    with pytest.raises(LayoutIntentValidationError) as excinfo:
        validate_layout_intent(intent)

    message = str(excinfo.value)
    assert "Generic" in message
    assert "Correction: reassign to Public, Private, Data, or Management." in message


def test_validate_parent_model_rejects_nested_cells():
    xml = """
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="subnet" vertex="1" parent="1"/>
        <mxCell id="nested_icon" vertex="1" parent="subnet"/>
      </root>
    </mxGraphModel>
    """

    with pytest.raises(DrawioValidationError) as excinfo:
        validate_parent_model(xml)

    assert "nested_icon" in str(excinfo.value)
    assert "parent=subnet" in str(excinfo.value)


def test_gateway_placement_override_corrects_wrong_x_position():
    vcn = PositionedBox(
        id="region_vcn",
        label="VCN",
        box_type="_vcn_box",
        x=200,
        y=100,
        w=600,
        h=300,
    )
    nodes = [
        PositionedNode(id="igw", label="IGW", oci_type="internet gateway", x=500, y=120),
        PositionedNode(id="nat", label="NAT", oci_type="nat gateway", x=500, y=200),
        PositionedNode(id="drg", label="DRG", oci_type="drg", x=500, y=280),
        PositionedNode(id="sgw", label="SGW", oci_type="service gateway", x=100, y=120),
    ]

    enforce_gateway_placement(nodes, [vcn])
    positions = {node.id: node.x for node in nodes}

    assert positions["igw"] == vcn.x - ICON_W / 2
    assert positions["nat"] == vcn.x - ICON_W / 2
    assert positions["drg"] == vcn.x - ICON_W / 2
    assert positions["sgw"] == vcn.x + vcn.w - ICON_W / 2
