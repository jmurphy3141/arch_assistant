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
