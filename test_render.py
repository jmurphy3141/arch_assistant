#!/usr/bin/env python3
"""Quick local render test — no OCI/LLM needed."""
from agent.bom_parser import ServiceItem
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio

mock_spec = {
    "direction": "LR",
    "page": {"width": 1654, "height": 1169},
    "layers": {
        "external": [
            {"id": "on_prem",          "type": "on premises",    "label": "On-Premises"},
        ],
        "ingress": [
            {"id": "drg_1",            "type": "drg",            "label": "DRG / FastConnect"},
            {"id": "internet_gateway", "type": "internet gateway","label": "Internet Gateway"},
            {"id": "nat_gateway",      "type": "nat gateway",    "label": "NAT Gateway"},
            {"id": "load_balancer_1",  "type": "load balancer",  "label": "Load Balancer"},
            {"id": "waf",              "type": "waf",            "label": "WAF"},
        ],
        "compute": [
            {"id": "compute_1",        "type": "compute",        "label": "Compute ×1,910 OCPU"},
        ],
        "async": [
            {"id": "queue_1",          "type": "queue",          "label": "Queue"},
        ],
        "data": [
            {"id": "db_1",             "type": "database",       "label": "PostgreSQL DB"},
            {"id": "obj_1",            "type": "object storage", "label": "Object Storage"},
            {"id": "logging",          "type": "logging",        "label": "Logging Analytics"},
            {"id": "monitoring",       "type": "monitoring",     "label": "Monitoring + APM"},
        ],
    },
    "groups": [
        {"id": "pub_sub_box", "label": "Public Subnet",
         "nodes": ["drg_1", "internet_gateway", "nat_gateway", "load_balancer_1", "waf"]},
        {"id": "app_sub_box", "label": "App Subnet",
         "nodes": ["compute_1", "queue_1"]},
        {"id": "db_sub_box",  "label": "DB Subnet",
         "nodes": ["db_1"]},
        {"id": "region_box",  "label": "OCI Region Services",
         "nodes": ["obj_1", "logging", "monitoring"]},
    ],
    "edges": [],
}

# Build a minimal items_by_id so the layout engine has type info
items_by_id = {}
for layer, nodes in mock_spec["layers"].items():
    for n in nodes:
        items_by_id[n["id"]] = ServiceItem(
            id=n["id"], oci_type=n["type"], label=n["label"], layer=layer
        )

draw_dict = spec_to_draw_dict(mock_spec, items_by_id)
generate_drawio(draw_dict, "output.drawio")
print(f"Done — {len(draw_dict['nodes'])} nodes, {len(draw_dict['edges'])} edges")
print("Open output.drawio in draw.io desktop or app.diagrams.net")
