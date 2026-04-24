from __future__ import annotations

from agent.bom_service import BomService, DEFAULT_PRICE_TABLE


def test_bom_validation_enforces_unknown_sku_and_positive_price() -> None:
    svc = BomService()
    payload = {
        "currency": "USD",
        "line_items": [
            {
                "sku": "UNKNOWN",
                "description": "Bad row",
                "category": "compute",
                "quantity": 1,
                "unit_price": 10,
            },
            {
                "sku": "B94176",
                "description": "Compute",
                "category": "compute",
                "quantity": 1,
                "unit_price": 0,
            },
        ],
    }

    errors = svc.validate_final_payload(payload, dict(DEFAULT_PRICE_TABLE))
    assert any("unknown SKU" in err for err in errors)
    assert any("non-positive unit_price" in err for err in errors)


def test_bom_repair_adds_non_gpu_memory_split() -> None:
    svc = BomService()
    payload = {
        "currency": "USD",
        "line_items": [
            {
                "sku": "B94176",
                "description": "Compute E4 OCPU",
                "category": "compute",
                "quantity": 4,
                "unit_price": 0.05,
                "extended_price": 0.2,
            }
        ],
    }

    repaired, attempts, errors = svc._repair_until_valid(payload, dict(DEFAULT_PRICE_TABLE))
    assert attempts >= 1
    assert errors == []
    skus = {row["sku"] for row in repaired["line_items"]}
    assert "B94176" in skus
    assert "B94177" in skus


def test_bom_fast_path_normalizes_non_oci_provider_mentions() -> None:
    svc = BomService()
    payload = svc._draft_bom_payload(
        "Generate BOM for 8 OCPU, 128 GB RAM, 1 TB block storage with Hetzner load balancer and AWS object storage",
        dict(DEFAULT_PRICE_TABLE),
    )

    descriptions = " ".join(str(row["description"]) for row in payload["line_items"]).lower()
    notes = " ".join(str(row.get("notes") or "") for row in payload["line_items"]).lower()
    assert "hetzner" not in descriptions
    assert "aws" not in descriptions
    assert "hetzner" not in notes
    assert "aws" not in notes
    assert any(
        "oci-only bom enforced" in assumption.lower()
        for assumption in payload.get("assumptions", [])
    )


def test_bom_validation_rejects_non_oci_provider_references_in_line_items() -> None:
    svc = BomService()
    payload = {
        "currency": "USD",
        "line_items": [
            {
                "sku": "B93030",
                "description": "Hetzner load balancer",
                "category": "network",
                "quantity": 1,
                "unit_price": 0.025,
                "notes": "Migrated from Hetzner",
            }
        ],
    }

    errors = svc.validate_final_payload(payload, dict(DEFAULT_PRICE_TABLE))
    assert any("non-oci provider" in err.lower() for err in errors)


def test_bom_fast_path_uses_markdown_table_quantities() -> None:
    svc = BomService()
    payload = svc._draft_bom_payload(
        """
| Category | Component | Specs/Details | Quantity |
|----------|-----------|---------------|----------|
| Compute (App Servers) | Ampere A1 Flex (Instance Pool/ASG) | 4 OCPU ARM, 24GB RAM, 200GB Block Vol, auto-scale min=3 | 3 |
| Load Balancer | Flexible Load Balancer (Standard Shape) | 10Mbps, L7 HTTP/S/HTTPS, path routing, health checks, WAF | 1 |
| Storage | Object Storage (Standard) | 250GB, 10TB egress free/yr | 1 |
        """,
        dict(DEFAULT_PRICE_TABLE),
    )

    by_sku = {row["sku"]: row for row in payload["line_items"]}
    assert by_sku["B88317"]["quantity"] == 12.0
    assert by_sku["B88318"]["quantity"] == 72.0
    assert by_sku["B91961"]["quantity"] == 600.0
    assert by_sku["B93030"]["quantity"] == 1.0
    assert by_sku["B91628"]["quantity"] == 250.0
