from __future__ import annotations

from pathlib import Path

import pytest

from agent.external_corpus_scorer import (
    extract_internal_traits,
    load_external_example_profiles,
    rank_reference_profiles,
)
from agent.layout_engine import spec_to_draw_dict


pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = (
    ROOT
    / "tests"
    / "external_fixtures"
    / "oci_arch_skill"
    / ".agents"
    / "skills"
    / "oci-architecture-generator"
)


@pytest.fixture(scope="module")
def external_profiles() -> dict[str, dict]:
    if not SKILL_ROOT.exists():
        pytest.skip(
            "external OCI architecture corpus not fetched; run "
            "`python3 scripts/fetch_external_oci_arch_skill_fixtures.py` first"
        )
    return load_external_example_profiles(SKILL_ROOT)


def _single_region_oke_rag_like_spec() -> dict:
    return {
        "deployment_type": "single_ad",
        "page": {"width": 1654, "height": 1169},
        "regions": [
            {
                "id": "region_primary",
                "label": "OCI Region — us-ashburn-1",
                "regional_subnets": [
                    {
                        "id": "public_ingress",
                        "label": "Public Subnet",
                        "tier": "public_ingress",
                        "nodes": [
                            {"id": "waf_1", "type": "waf", "label": "WAF"},
                            {"id": "lb_1", "type": "load balancer", "label": "Public LB"},
                            {"id": "bastion_1", "type": "bastion", "label": "Bastion"},
                        ],
                    },
                    {
                        "id": "private_app",
                        "label": "Private App Subnet",
                        "tier": "app",
                        "nodes": [
                            {"id": "oke_1", "type": "container engine", "label": "OKE Cluster"},
                        ],
                    },
                    {
                        "id": "private_data",
                        "label": "Private Data Subnet",
                        "tier": "db",
                        "nodes": [
                            {"id": "db_1", "type": "database", "label": "PostgreSQL"},
                        ],
                    },
                ],
                "availability_domains": [],
                "gateways": [
                    {"id": "igw_1", "type": "internet gateway", "label": "IGW"},
                    {"id": "nat_1", "type": "nat gateway", "label": "NAT"},
                    {"id": "sgw_1", "type": "service gateway", "label": "SGW"},
                ],
                "oci_services": [
                    {"id": "obj_1", "type": "object storage", "label": "Object Storage"},
                ],
            }
        ],
        "external": [{"id": "internet", "type": "public internet", "label": "Internet"}],
        "edges": [],
    }


def _multi_region_oke_saas_like_spec() -> dict:
    return {
        "deployment_type": "multi_region",
        "page": {"width": 1654, "height": 1169},
        "regions": [
            {
                "id": "region_primary",
                "label": "OCI Region — us-phoenix-1",
                "regional_subnets": [
                    {
                        "id": "pub_sub_a",
                        "label": "Public Subnet",
                        "tier": "public_ingress",
                        "nodes": [
                            {"id": "lb_a", "type": "load balancer", "label": "LB"},
                        ],
                    },
                    {
                        "id": "app_sub_a",
                        "label": "Private App Subnet",
                        "tier": "app",
                        "nodes": [
                            {"id": "oke_a", "type": "container engine", "label": "OKE"},
                        ],
                    },
                    {
                        "id": "data_sub_a",
                        "label": "Private Data Subnet",
                        "tier": "db",
                        "nodes": [
                            {"id": "db_a", "type": "database", "label": "ATP"},
                        ],
                    },
                ],
                "availability_domains": [],
                "gateways": [
                    {"id": "igw_a", "type": "internet gateway", "label": "IGW"},
                ],
                "oci_services": [
                    {"id": "obj_a", "type": "object storage", "label": "Object Storage"},
                ],
            },
            {
                "id": "region_secondary",
                "label": "OCI Region — us-ashburn-1",
                "regional_subnets": [
                    {
                        "id": "pub_sub_b",
                        "label": "Public Subnet",
                        "tier": "public_ingress",
                        "nodes": [
                            {"id": "lb_b", "type": "load balancer", "label": "LB"},
                        ],
                    },
                    {
                        "id": "app_sub_b",
                        "label": "Private App Subnet",
                        "tier": "app",
                        "nodes": [
                            {"id": "oke_b", "type": "container engine", "label": "OKE"},
                        ],
                    },
                    {
                        "id": "data_sub_b",
                        "label": "Private Data Subnet",
                        "tier": "db",
                        "nodes": [
                            {"id": "db_b", "type": "database", "label": "ATP"},
                        ],
                    },
                ],
                "availability_domains": [],
                "gateways": [
                    {"id": "igw_b", "type": "internet gateway", "label": "IGW"},
                ],
                "oci_services": [
                    {"id": "obj_b", "type": "object storage", "label": "Object Storage"},
                ],
            },
        ],
        "external": [{"id": "internet", "type": "public internet", "label": "Internet"}],
        "edges": [],
    }


def test_external_profile_extracts_expected_rag_traits(external_profiles: dict[str, dict]) -> None:
    traits = external_profiles["oke-genai-rag"]
    assert traits["region_count"] == 1
    assert traits["subnet_count"] == 3
    assert traits["public_subnet_count"] == 1
    assert traits["private_subnet_count"] == 2
    assert {
        "dns",
        "waf",
        "load_balancer",
        "bastion",
        "oke",
        "database",
        "object_storage",
        "generative_ai",
    }.issubset(set(traits["service_tags"]))


def test_ranking_prefers_rag_reference_for_single_region_oke_candidate(
    external_profiles: dict[str, dict]
) -> None:
    draw_dict = spec_to_draw_dict(_single_region_oke_rag_like_spec(), {})
    candidate_traits = extract_internal_traits(draw_dict)
    ranked = rank_reference_profiles(candidate_traits, external_profiles)

    assert ranked[0]["name"] == "oke-genai-rag"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert {"waf", "load_balancer", "bastion", "oke", "database", "object_storage"}.issubset(
        set(ranked[0]["matched_service_tags"])
    )


def test_ranking_prefers_multi_region_reference_for_multi_region_candidate(
    external_profiles: dict[str, dict]
) -> None:
    draw_dict = spec_to_draw_dict(_multi_region_oke_saas_like_spec(), {})
    candidate_traits = extract_internal_traits(draw_dict)
    ranked = rank_reference_profiles(candidate_traits, external_profiles)

    assert ranked[0]["name"] == "multi-region-oke-saas"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert ranked[0]["components"]["multi_region"] == 1.0
