#!/usr/bin/env python3
"""Quick local render test — no OCI/LLM needed.

Runs two tests:
  1. Legacy flat spec (old LR format) — exercises backward-compat path
  2. New hierarchical spec (TB format, single_ad with Fault Domains)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from agent.bom_parser import ServiceItem
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio


# ── Test 1: Legacy flat spec (backward compatibility) ─────────────────────────
print("=== Test 1: Legacy LR spec ===")

legacy_spec = {
    "direction": "LR",
    "page": {"width": 1654, "height": 1169},
    "layers": {
        "external": [
            {"id": "on_prem",          "type": "on premises",     "label": "On-Premises"},
        ],
        "ingress": [
            {"id": "drg_1",            "type": "drg",             "label": "DRG / FastConnect"},
            {"id": "internet_gateway", "type": "internet gateway", "label": "Internet Gateway"},
            {"id": "nat_gateway",      "type": "nat gateway",     "label": "NAT Gateway"},
            {"id": "load_balancer_1",  "type": "load balancer",   "label": "Load Balancer"},
            {"id": "waf",              "type": "waf",             "label": "WAF"},
        ],
        "compute": [
            {"id": "compute_1",        "type": "compute",         "label": "Compute ×1,910 OCPU"},
        ],
        "async": [
            {"id": "queue_1",          "type": "queue",           "label": "Queue"},
        ],
        "data": [
            {"id": "db_1",             "type": "database",        "label": "PostgreSQL DB"},
            {"id": "obj_1",            "type": "object storage",  "label": "Object Storage"},
            {"id": "logging",          "type": "logging",         "label": "Logging Analytics"},
            {"id": "monitoring",       "type": "monitoring",      "label": "Monitoring + APM"},
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

items_by_id = {}
for layer, nodes in legacy_spec["layers"].items():
    for n in nodes:
        items_by_id[n["id"]] = ServiceItem(
            id=n["id"], oci_type=n["type"], label=n["label"], layer=layer
        )

d1 = spec_to_draw_dict(legacy_spec, items_by_id)
generate_drawio(d1, "output_legacy.drawio")
print(f"  nodes={len(d1['nodes'])}  boxes={len(d1.get('boxes',[]))}  edges={len(d1['edges'])}")
print("  Written: output_legacy.drawio")


# ── Test 2: New hierarchical TB spec (single_ad with Fault Domains) ───────────
print("\n=== Test 2: New TB spec — single_ad with Fault Domains ===")

new_spec = {
    "deployment_type": "single_ad",
    "regions": [{
        "id": "region_primary",
        "label": "Oracle Cloud Infrastructure (Region)",
        "regional_subnets": [
            {
                "id": "priv_sub_lb", "label": "Private Subnet", "tier": "ingress",
                "nodes": [{"id": "priv_lb", "type": "load balancer", "label": "Load Balancer"}]
            },
            {
                "id": "pub_sub_bastion", "label": "Public Subnet", "tier": "ingress",
                "nodes": [{"id": "bastion", "type": "compute", "label": "Bastion Host"}]
            },
            {
                "id": "pub_sub_lb", "label": "Public Subnet", "tier": "ingress",
                "nodes": [
                    {"id": "waf",    "type": "waf",          "label": "WAF"},
                    {"id": "pub_lb", "type": "load balancer","label": "Load Balancer"},
                ]
            },
        ],
        "availability_domains": [{
            "id": "ad1", "label": "Availability Domain 1",
            "fault_domains": [
                {
                    "id": "fd1", "label": "Fault Domain 1",
                    "subnets": [{
                        "id": "web_sub_1", "label": "Private Subnet", "tier": "web",
                        "nodes": [{"id": "web_1", "type": "compute", "label": "Web Tier"}]
                    }, {
                        "id": "app_sub_1", "label": "Private Subnet", "tier": "app",
                        "nodes": [
                            {"id": "fdmee_1",   "type": "compute", "label": "FDMEE"},
                            {"id": "hfm_1",     "type": "compute", "label": "HFM"},
                            {"id": "planning_1","type": "compute", "label": "Planning"},
                            {"id": "essbase_1", "type": "compute", "label": "Essbase"},
                        ]
                    }]
                },
                {
                    "id": "fd2", "label": "Fault Domain 2",
                    "subnets": [{
                        "id": "web_sub_2", "label": "Private Subnet", "tier": "web",
                        "nodes": [{"id": "web_2", "type": "compute", "label": "Web Tier"}]
                    }, {
                        "id": "app_sub_2", "label": "Private Subnet", "tier": "app",
                        "nodes": [
                            {"id": "fdmee_2",   "type": "compute", "label": "FDMEE"},
                            {"id": "hfm_2",     "type": "compute", "label": "HFM"},
                            {"id": "planning_2","type": "compute", "label": "Planning"},
                            {"id": "essbase_2", "type": "compute", "label": "Essbase"},
                        ]
                    }]
                },
            ],
            "subnets": [{
                "id": "db_sub", "label": "Private Subnet", "tier": "db",
                "nodes": [
                    {"id": "db_epm",        "type": "database", "label": "EPM Database"},
                    {"id": "db_foundation", "type": "database", "label": "Foundation DB"},
                ]
            }]
        }],
        "gateways": [
            {"id": "igw", "type": "internet gateway", "label": "Internet Gateway", "position": "top"},
            {"id": "drg", "type": "drg",              "label": "DRG",              "position": "left"},
            {"id": "nat", "type": "nat gateway",      "label": "NAT Gateway",      "position": "right"},
            {"id": "sgw", "type": "service gateway",  "label": "Service Gateway",  "position": "right"},
        ],
        "oci_services": [
            {"id": "obj_storage", "type": "object storage", "label": "Object Storage"},
            {"id": "yum_repo",    "type": "compute",        "label": "YUM Repo"},
        ]
    }],
    "external": [
        {"id": "on_prem",  "type": "on premises",   "label": "On-Premises"},
        {"id": "internet", "type": "public internet","label": "Public Internet"},
        {"id": "admins",   "type": "admins",         "label": "Admins"},
    ],
    "edges": [],
}

d2 = spec_to_draw_dict(new_spec, {})
generate_drawio(d2, "output_single_ad.drawio")
print(f"  nodes={len(d2['nodes'])}  boxes={len(d2.get('boxes',[]))}  edges={len(d2['edges'])}")
print("  Written: output_single_ad.drawio")


# ── Test 3: New hierarchical TB spec (multi_ad) ───────────────────────────────
print("\n=== Test 3: New TB spec — multi_ad active-passive ===")

multi_ad_spec = {
    "deployment_type": "multi_ad",
    "regions": [{
        "id": "region_primary",
        "label": "Oracle Cloud Infrastructure (Region)",
        "regional_subnets": [
            {
                "id": "priv_sub_lb", "label": "Private Subnet", "tier": "ingress",
                "nodes": [{"id": "priv_lb", "type": "load balancer", "label": "Load Balancer"}]
            },
            {
                "id": "pub_sub_bastion", "label": "Public Subnet", "tier": "ingress",
                "nodes": [{"id": "bastion", "type": "compute", "label": "Bastion Host"}]
            },
            {
                "id": "pub_sub_lb", "label": "Public Subnet", "tier": "ingress",
                "nodes": [
                    {"id": "waf",    "type": "waf",          "label": "WAF"},
                    {"id": "pub_lb", "type": "load balancer","label": "Load Balancer"},
                ]
            },
        ],
        "availability_domains": [
            {
                "id": "ad1", "label": "Availability Domain 1",
                "subnets": [
                    {"id": "web_sub_ad1", "label": "Private Subnet", "tier": "web",
                     "nodes": [{"id": "web_ad1", "type": "compute", "label": "Web Tier"}]},
                    {"id": "app_sub_ad1", "label": "Private Subnet", "tier": "app",
                     "nodes": [
                         {"id": "fdmee_ad1",   "type": "compute", "label": "FDMEE"},
                         {"id": "hfm_ad1",     "type": "compute", "label": "HFM"},
                         {"id": "planning_ad1","type": "compute", "label": "Planning"},
                     ]},
                    {"id": "db_sub_ad1", "label": "Private Subnet", "tier": "db",
                     "nodes": [{"id": "db_ad1", "type": "database", "label": "EPM DB (Primary)"}]},
                ]
            },
            {
                "id": "ad2", "label": "Availability Domain 2",
                "subnets": [
                    {"id": "web_sub_ad2", "label": "Private Subnet", "tier": "web",
                     "nodes": [{"id": "web_ad2", "type": "compute", "label": "Web Tier"}]},
                    {"id": "app_sub_ad2", "label": "Private Subnet", "tier": "app",
                     "nodes": [
                         {"id": "fdmee_ad2",   "type": "compute", "label": "FDMEE"},
                         {"id": "hfm_ad2",     "type": "compute", "label": "HFM"},
                         {"id": "planning_ad2","type": "compute", "label": "Planning"},
                     ]},
                    {"id": "db_sub_ad2", "label": "Private Subnet", "tier": "db",
                     "nodes": [{"id": "db_ad2", "type": "database", "label": "EPM DB (Standby)"}]},
                ]
            },
        ],
        "gateways": [
            {"id": "igw", "type": "internet gateway", "label": "Internet Gateway", "position": "top"},
            {"id": "drg", "type": "drg",              "label": "DRG",              "position": "left"},
            {"id": "nat", "type": "nat gateway",      "label": "NAT Gateway",      "position": "right"},
            {"id": "sgw", "type": "service gateway",  "label": "Service Gateway",  "position": "right"},
        ],
        "oci_services": [
            {"id": "obj_storage", "type": "object storage", "label": "Object Storage"},
        ]
    }],
    "external": [
        {"id": "on_prem",  "type": "on premises",   "label": "On-Premises"},
        {"id": "internet", "type": "public internet","label": "Public Internet"},
    ],
    "edges": [],
}

d3 = spec_to_draw_dict(multi_ad_spec, {})
generate_drawio(d3, "output_multi_ad.drawio")
print(f"  nodes={len(d3['nodes'])}  boxes={len(d3.get('boxes',[]))}  edges={len(d3['edges'])}")
print("  Written: output_multi_ad.drawio")

print("\nAll tests passed. Open the .drawio files in draw.io or app.diagrams.net")
