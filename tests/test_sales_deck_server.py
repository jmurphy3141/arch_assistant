from pathlib import Path

from sub_agents.sales_deck import server


def test_format_case_studies_returns_reference_table():
    text = server._format_case_studies(server._CASE_STUDIES)

    assert text
    assert "## Oracle Customer Outcomes Reference" in text
    assert "| Company | Industry | Use Case | Quantified Outcome |" in text
    assert "Do not invent metrics" in text


def test_format_sla_reference_returns_reference_table():
    text = server._format_sla_reference(server._SLA_REFERENCE)

    assert text
    assert "## OCI SLA Reference" in text
    assert "| Service | SLA | Multi-AD Note |" in text
    assert "verify against current Oracle Pillar Document" in text


def test_reference_formatters_handle_missing_files():
    missing = Path("/tmp/archie-missing-reference.json")

    assert server._format_case_studies(missing) == ""
    assert server._format_sla_reference(missing) == ""


def test_system_message_contains_sales_deck_reference_blocks():
    assert "## Reference Data" in server._system_message
    assert "## Oracle Customer Outcomes Reference" in server._system_message
    assert "## OCI SLA Reference" in server._system_message
    assert "Do not invent metrics" in server._system_message
    assert "verify against current Oracle Pillar Document" in server._system_message
