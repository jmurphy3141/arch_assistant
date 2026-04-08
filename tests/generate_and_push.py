#!/usr/bin/env python3.11
"""
tests/generate_and_push.py
--------------------------
Generate sample .drawio diagrams from built-in test fixtures (no BOM, no LLM,
no OCI auth needed) and push them to git for visual review.

Usage (on OCI server):
    cd ~/drawing-agent
    python3.11 tests/generate_and_push.py

Generates:
    tests/fixtures/outputs/sample_hpc.drawio      — HPC / OKE topology
    tests/fixtures/outputs/sample_3tier.drawio    — Classic 3-tier web app
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Ensure repo root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.bom_parser import ServiceItem
from agent.layout_intent import validate_layout_intent
from agent.intent_compiler import compile_intent_to_flat_spec
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio

OUT_DIR = ROOT / "tests" / "fixtures" / "outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Sample 1: HPC / OKE topology ──────────────────────────────────────────────

HPC_ITEMS = [
    ServiceItem(id="bastion_1",   oci_type="bastion",          label="Bastion",           layer="ingress"),
    ServiceItem(id="nat_gateway", oci_type="nat gateway",      label="NAT Gateway",       layer="ingress"),
    ServiceItem(id="oke_1",       oci_type="container engine", label="OKE Cluster",       layer="compute"),
    ServiceItem(id="hpc_1",       oci_type="bare metal",       label="BM.Optimized3.36",  layer="compute"),
    ServiceItem(id="hpc_2",       oci_type="bare metal",       label="BM.Optimized3.36",  layer="compute"),
    ServiceItem(id="hpc_3",       oci_type="bare metal",       label="BM.Optimized3.36",  layer="compute"),
    ServiceItem(id="fss_1",       oci_type="file storage",     label="FSS",               layer="data"),
]

HPC_INTENT = {
    "schema_version": "1.0",
    "deployment_hints": {
        "region_count": 1,
        "availability_domains_per_region": 1,
        "dr_enabled": False,
        "on_prem_connectivity": "none",
    },
    "groups": [
        {"id": "bas_sub_box",    "label": "Bastion Subnet (Public)",  "order": 0},
        {"id": "cp_sub_box",     "label": "Control Plane Subnet",     "order": 1},
        {"id": "worker_sub_box", "label": "Worker Subnet (Private)",  "order": 2},
        {"id": "storage_sub_box","label": "Storage Subnet",           "order": 3},
    ],
    "placements": [
        {"id": "bastion_1",   "oci_type": "bastion",          "layer": "ingress", "group": "bas_sub_box"},
        {"id": "nat_gateway", "oci_type": "nat gateway",      "layer": "ingress", "group": None},
        {"id": "oke_1",       "oci_type": "container engine", "layer": "compute", "group": "cp_sub_box"},
        {"id": "hpc_1",       "oci_type": "bare metal",       "layer": "compute", "group": "worker_sub_box"},
        {"id": "hpc_2",       "oci_type": "bare metal",       "layer": "compute", "group": "worker_sub_box"},
        {"id": "hpc_3",       "oci_type": "bare metal",       "layer": "compute", "group": "worker_sub_box"},
        {"id": "fss_1",       "oci_type": "file storage",     "layer": "data",    "group": "storage_sub_box"},
    ],
    "assumptions": [],
    "fixed_edges_policy": True,
}

# ── Sample 2: Classic 3-tier web app ──────────────────────────────────────────

TIER3_ITEMS = [
    ServiceItem(id="on_prem",          oci_type="on premises",     label="On-Premises",     layer="external"),
    ServiceItem(id="internet",         oci_type="internet",        label="Internet",         layer="external"),
    ServiceItem(id="internet_gateway", oci_type="internet gateway",label="Internet Gateway", layer="ingress"),
    ServiceItem(id="nat_gateway",      oci_type="nat gateway",     label="NAT Gateway",      layer="ingress"),
    ServiceItem(id="drg",              oci_type="drg",             label="DRG",              layer="ingress"),
    ServiceItem(id="load_balancer_1",  oci_type="load balancer",   label="Load Balancer",    layer="ingress"),
    ServiceItem(id="compute_1",        oci_type="compute",         label="App Server",       layer="compute"),
    ServiceItem(id="compute_2",        oci_type="compute",         label="App Server",       layer="compute"),
    ServiceItem(id="database_1",       oci_type="database",        label="PostgreSQL DB",    layer="data"),
    ServiceItem(id="bastion_1",        oci_type="bastion",         label="Bastion",          layer="ingress"),
]

TIER3_INTENT = {
    "schema_version": "1.0",
    "deployment_hints": {
        "region_count": 1,
        "availability_domains_per_region": 1,
        "dr_enabled": False,
        "on_prem_connectivity": "fastconnect",
    },
    "groups": [
        {"id": "pub_sub_box", "label": "Public Subnet",  "order": 0},
        {"id": "app_sub_box", "label": "App Subnet",     "order": 1},
        {"id": "db_sub_box",  "label": "DB Subnet",      "order": 2},
    ],
    "placements": [
        {"id": "on_prem",          "oci_type": "on premises",     "layer": "external", "group": None},
        {"id": "internet",         "oci_type": "internet",        "layer": "external", "group": None},
        {"id": "internet_gateway", "oci_type": "internet gateway","layer": "ingress",  "group": None},
        {"id": "nat_gateway",      "oci_type": "nat gateway",     "layer": "ingress",  "group": None},
        {"id": "drg",              "oci_type": "drg",             "layer": "ingress",  "group": None},
        {"id": "load_balancer_1",  "oci_type": "load balancer",   "layer": "ingress",  "group": "pub_sub_box"},
        {"id": "bastion_1",        "oci_type": "bastion",         "layer": "ingress",  "group": "pub_sub_box"},
        {"id": "compute_1",        "oci_type": "compute",         "layer": "compute",  "group": "app_sub_box"},
        {"id": "compute_2",        "oci_type": "compute",         "layer": "compute",  "group": "app_sub_box"},
        {"id": "database_1",       "oci_type": "database",        "layer": "data",     "group": "db_sub_box"},
    ],
    "assumptions": [],
    "fixed_edges_policy": True,
}


def generate(name: str, intent_data: dict, items: list[ServiceItem]) -> Path:
    intent      = validate_layout_intent(intent_data, items)
    spec        = compile_intent_to_flat_spec(intent, items)
    items_by_id = {i.id: i for i in items}
    draw_dict   = spec_to_draw_dict(spec, items_by_id)
    out_path    = OUT_DIR / f"{name}.drawio"
    generate_drawio(draw_dict, str(out_path))
    print(f"  wrote {out_path.relative_to(ROOT)}")
    return out_path


def git_push(files: list[Path]) -> None:
    rel = [str(f.relative_to(ROOT)) for f in files]
    subprocess.run(["git", "-C", str(ROOT), "add"] + rel, check=True)
    result = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if result.returncode == 0:
        print("  nothing changed — skipping commit")
        return
    subprocess.run(
        ["git", "-C", str(ROOT), "commit", "-m", "test: regenerate sample diagrams"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(ROOT), "push", "origin", "HEAD"],
        check=True,
    )
    print("  pushed to git")


if __name__ == "__main__":
    print("Generating diagrams...")
    files = [
        generate("sample_hpc",   HPC_INTENT,   HPC_ITEMS),
        generate("sample_3tier", TIER3_INTENT, TIER3_ITEMS),
    ]
    print("Pushing to git...")
    git_push(files)
    print("Done.")
