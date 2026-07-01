from agent import reference_tools
from agent.bom_service import BomService, CacheSnapshot


def test_lookup_compute_shapes_uses_catalog_ranges_and_architectures():
    result = reference_tools.lookup_compute_shapes()
    shapes = {item["family"]: item for item in result["shapes"]}

    assert shapes["E5"]["architecture"] == "AMD x86"
    assert shapes["E6"]["architecture"] == "AMD x86"
    assert shapes["A1"]["architecture"] == "Ampere/Arm"
    assert shapes["X9"]["architecture"] == "Intel x86"
    for family in ("E5", "E6", "A1", "X9"):
        assert shapes[family]["ocpu_min"] > 0
        assert shapes[family]["ocpu_max"] >= shapes[family]["ocpu_min"]
        assert shapes[family]["memory_max_gb"] >= shapes[family]["memory_min_gb"]


def test_lookup_price_reads_live_cache_and_unknown_is_tbd(monkeypatch):
    service = BomService()
    service._cache = CacheSnapshot(
        pricing_table={
            "B97384": {
                "description": "Compute - Standard - E5 - OCPU",
                "unit_price": 0.03125,
                "metric": "OCPU Per Hour",
                "source": "oracle_price_list_api",
            }
        },
        shapes_text="cached",
        services_text="cached",
        refreshed_at=1234.0,
        source="live",
    )
    monkeypatch.setattr(reference_tools, "get_shared_bom_service", lambda: service)

    known = reference_tools.lookup_price("B97384")
    unknown = reference_tools.lookup_price("NOT-A-REAL-SKU")

    assert known["unit_price"] == 0.03125
    assert known["pricing_status"] == "priced"
    assert known["price_source"] == "oracle_price_list_api"
    assert unknown["unit_price"] == "TBD"
    assert unknown["pricing_status"] == "unpriced/TBD"
    assert unknown["matches"] == []


def test_lookup_price_without_ready_cache_is_tbd(monkeypatch):
    service = BomService()
    monkeypatch.setattr(reference_tools, "get_shared_bom_service", lambda: service)

    result = reference_tools.lookup_price("B97384")

    assert result["unit_price"] == "TBD"
    assert result["pricing_status"] == "unpriced/TBD"
    assert result["cache_source"] == "none"


def test_lookup_reference_architecture_matches_bundle_and_unknown_is_empty():
    known = reference_tools.lookup_reference_architecture(
        "single region OKE app with WAF load balancer database object storage"
    )
    unknown = reference_tools.lookup_reference_architecture(
        "unicorn quantum teleporter"
    )

    assert known["match"]["id"] == "single_region_oke_app"
    assert known["match"]["bundle_id"] == "oracle-oci-reference-bundle"
    assert known["match"]["approved_sources"]
    assert unknown == {
        "query": "unicorn quantum teleporter",
        "match": None,
        "matches": [],
    }


def test_reference_tool_specs_expose_only_the_three_read_only_lookups():
    names = {spec.name for spec in reference_tools.get_reference_tool_specs()}

    assert names == {
        "lookup_compute_shapes",
        "lookup_price",
        "lookup_reference_architecture",
    }
