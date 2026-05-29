from pathlib import Path

from sub_agents.tech_research import server


def test_format_shape_catalog_returns_reference_table():
    text = server._format_shape_catalog(server._SHAPE_CATALOG)

    assert text
    assert "## OCI Compute Shape Catalog Reference" in text
    assert "| Shape | SKUs | Max OCPU | Max RAM GB | Notes |" in text
    assert "VM.Standard.E5.Flex" in text
    assert "Shape selection guide:" in text


def test_format_shape_catalog_handles_missing_file():
    missing = Path("/tmp/archie-missing-compute-shapes.json")

    assert server._format_shape_catalog(missing) == ""


def test_system_message_contains_shape_catalog():
    assert "## OCI Shape/Region Availability Reference" in server._system_message
    assert "## OCI Compute Shape Catalog Reference" in server._system_message
    assert "VM.Standard.E5.Flex" in server._system_message
