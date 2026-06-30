from agent import consistency_contract


SELECTED = {
    "option_name": "Skyline Operational Readiness POC",
    "oci_services": [
        "Flexible Load Balancer",
        "VM.Standard.E5.Flex",
        "PostgreSQL DB System",
        "Object Storage",
        "Block Volume",
        "Site-to-Site VPN",
        "Logging",
        "Monitoring",
    ],
    "grounding": {"region": "uk-london-1"},
}


def context_with_selected_poc() -> dict:
    context: dict = {"archie": {}}
    consistency_contract.record_selected_poc(context, SELECTED, artifact_key="poc/skyline/v1.json")
    return context


def test_selected_poc_writes_canonical_contract_without_collapsing_database() -> None:
    context = context_with_selected_poc()
    contract = consistency_contract.get_contract(context)
    service_ids = {item["service_id"] for item in contract["components"]}

    assert contract["region"] == "uk-london-1"
    assert "database.postgresql" in service_ids
    assert "database.autonomous" not in service_ids
    assert "network.vpn.site_to_site" in service_ids


def test_skyline_bom_scope_fills_every_missing_poc_component_with_assumptions() -> None:
    context = context_with_selected_poc()
    payload = {
        "region": "uk-london-1",
        "line_items": [
            {"sku": "B97384", "description": "Compute - Standard - E5 - OCPU", "quantity": 15},
            {"sku": "B97385", "description": "Compute - Standard - E5 - Memory", "quantity": 120},
            {"sku": "B91961", "description": "Storage - Block Volume - Storage", "quantity": 2048},
            {"sku": "B91628", "description": "Object Storage - Storage", "quantity": 6144},
        ],
    }

    completed = consistency_contract.ensure_bom_scope(payload, context)
    report = consistency_contract.validate_bom(completed, context)
    scope_by_id = {item["canonical_service_id"]: item for item in completed["scope_items"]}

    assert report["verdict"] == "pass"
    assert report["missing_components"] == []
    assert scope_by_id["database.postgresql"]["assumed_sizing"] == {
        "systems": 1,
        "ocpu": 2,
        "memory_gb": 32,
        "storage_gb": 100,
    }
    assert scope_by_id["database.postgresql"]["pricing_status"] == "authoritative_sku_required"
    postgres_rows = [
        row for row in completed["line_items"]
        if row.get("canonical_service_id") == "database.postgresql"
    ]
    assert len(postgres_rows) == 1
    assert postgres_rows[0]["sku"] == ""
    assert postgres_rows[0]["quantity"] == 2
    assert "no substitute SKU" in postgres_rows[0]["notes"]
    assert "network.vpn.site_to_site" in scope_by_id
    assert "observability.logging" in scope_by_id
    assert "observability.monitoring" in scope_by_id


def test_postgresql_cannot_be_replaced_by_autonomous_database() -> None:
    context = context_with_selected_poc()
    payload = consistency_contract.ensure_bom_scope(
        {
            "line_items": [
                {"sku": "B99060", "description": "Oracle Autonomous Database - ECPU", "quantity": 2},
            ]
        },
        context,
    )

    report = consistency_contract.validate_bom(payload, context)

    assert report["verdict"] == "blocked"
    assert "Autonomous Database cannot substitute" in report["contradictions"][0]


def test_conflicting_downstream_database_request_requires_impact_update() -> None:
    conflicts = consistency_contract.request_conflicts(
        "Use Autonomous Database in the BOM instead.",
        context_with_selected_poc(),
    )

    assert conflicts == [{
        "field": "database.service",
        "upstream_value": "PostgreSQL",
        "requested_value": "Autonomous Database",
        "source": "selected_poc",
    }]


def test_bom_validation_rejects_unselected_extra_services() -> None:
    context = context_with_selected_poc()
    payload = consistency_contract.ensure_bom_scope({"line_items": []}, context)
    payload["line_items"].append({
        "sku": "BWAF01",
        "description": "Web Application Firewall Policy",
        "canonical_service_id": "security.waf",
        "quantity": 1,
    })

    report = consistency_contract.validate_bom(payload, context)

    assert report["verdict"] == "blocked"
    assert report["unexpected_components"] == ["security.waf"]


def test_region_change_requires_confirmed_impact_update() -> None:
    conflicts = consistency_contract.request_conflicts(
        "Create the selected POC BOM in eu-frankfurt-1.",
        context_with_selected_poc(),
    )

    assert any(item["field"] == "region" for item in conflicts)


def test_file_storage_requires_a_real_bom_line_item() -> None:
    context: dict = {"archie": {}}
    consistency_contract.record_selected_poc(context, {
        "option_name": "Shared Files POC",
        "oci_services": ["VM.Standard.E5.Flex", "File Storage"],
    })

    payload = consistency_contract.ensure_bom_scope({
        "line_items": [
            {"sku": "B97384", "description": "Compute - Standard - E5 - OCPU", "quantity": 4},
        ],
    }, context)
    report = consistency_contract.validate_bom(payload, context)

    assert consistency_contract.canonical_service_id("FSS NFS") == "storage.file"
    assert consistency_contract.display_name("storage.file") == "File Storage"
    assert report["verdict"] == "blocked"
    assert report["missing_components"] == ["storage.file"]
    assert not any(item.get("canonical_service_id") == "storage.file" for item in payload["scope_items"])
