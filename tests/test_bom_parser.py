"""
tests/test_bom_parser.py
------------------------
Unit tests for agent.bom_parser.

Run: pytest tests/test_bom_parser.py
"""
import pytest
from pathlib import Path
from agent.bom_parser import parse_bom, build_llm_prompt, bom_to_llm_input, ServiceItem

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_BOM = FIXTURES / "sample_bom.xlsx"


def _build_sample_bom(path: Path) -> None:
    """Generate a minimal BOM workbook covering key SKU_MAP entries."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    # Headers: SKU | Description | Quantity
    ws.append(["SKU", "Description", "Quantity"])
    ws.append(["B94176", "Compute VM Standard E4 Flex",    4])   # → compute / compute
    ws.append(["B99060", "Oracle Autonomous Database OCPU", 2])  # → database / data
    ws.append(["B93030", "Flexible Load Balancer",          1])  # → load balancer / ingress
    ws.append(["B88325", "Dynamic Routing Gateway",         2])  # → drg / ingress
    wb.save(path)


# Generate the fixture at collection time if it is absent
if not SAMPLE_BOM.exists():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    _build_sample_bom(SAMPLE_BOM)


class TestBuildLlmPrompt:
    def test_returns_string(self):
        items = [
            ServiceItem(id="compute_1", oci_type="compute", label="Compute", layer="compute"),
            ServiceItem(id="on_prem",   oci_type="on premises", label="On-Premises", layer="external"),
        ]
        prompt = build_llm_prompt(items)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_contains_service_ids(self):
        items = [ServiceItem(id="my_service", oci_type="compute", label="Compute", layer="compute")]
        prompt = build_llm_prompt(items)
        assert "my_service" in prompt

    def test_context_injected(self):
        items = [ServiceItem(id="c1", oci_type="compute", label="C", layer="compute")]
        prompt = build_llm_prompt(items, context="6 regions, active-passive HA")
        assert "6 regions" in prompt

    def test_no_context_no_injection(self):
        items = [ServiceItem(id="c1", oci_type="compute", label="C", layer="compute")]
        prompt = build_llm_prompt(items, context="")
        assert "ADDITIONAL CONTEXT" not in prompt

    def test_clarification_rule_present(self):
        items = [ServiceItem(id="c1", oci_type="compute", label="C", layer="compute")]
        prompt = build_llm_prompt(items)
        assert "need_clarification" in prompt


class TestParseBom:
    def test_returns_list(self):
        items = parse_bom(SAMPLE_BOM)
        assert isinstance(items, list)
        assert len(items) > 0

    def test_on_prem_always_present(self):
        items = parse_bom(SAMPLE_BOM)
        types = [i.oci_type for i in items]
        assert "on premises" in types

    def test_best_practice_services_added(self):
        items = parse_bom(SAMPLE_BOM)
        types = {i.oci_type for i in items}
        assert "internet gateway" in types
        assert "nat gateway" in types
        assert "service gateway" in types
        # Baseline injections
        assert "internet" in types
        assert "bastion" in types

    def test_internet_injected_deterministically(self):
        items = parse_bom(SAMPLE_BOM)
        ids = {i.id for i in items}
        types = {i.oci_type for i in items}
        assert "internet" in ids
        assert "internet" in types
        internet = next(i for i in items if i.id == "internet")
        assert internet.layer == "external"
        assert internet.notes == "injected_baseline"

    def test_bastion_injected_deterministically(self):
        items = parse_bom(SAMPLE_BOM)
        ids = {i.id for i in items}
        types = {i.oci_type for i in items}
        assert "bastion_1" in ids
        assert "bastion" in types
        bastion = next(i for i in items if i.id == "bastion_1")
        assert bastion.layer == "ingress"
        assert bastion.notes == "injected_baseline"

    def test_suppress_internet_via_context(self):
        items = parse_bom(SAMPLE_BOM, context="NO_INTERNET_ENDPOINT=true")
        types = {i.oci_type for i in items}
        assert "internet" not in types

    def test_suppress_bastion_via_context(self):
        items = parse_bom(SAMPLE_BOM, context="NO_BASTION=true")
        types = {i.oci_type for i in items}
        assert "bastion" not in types

    def test_no_duplicate_internet_injection(self):
        items = parse_bom(SAMPLE_BOM)
        internet_items = [i for i in items if i.oci_type == "internet"]
        assert len(internet_items) == 1

    def test_no_duplicate_bastion_injection(self):
        items = parse_bom(SAMPLE_BOM)
        bastion_items = [i for i in items if i.oci_type == "bastion"]
        assert len(bastion_items) == 1

    def test_all_items_have_required_fields(self):
        items = parse_bom(SAMPLE_BOM)
        for item in items:
            assert item.id
            assert item.oci_type
            assert item.label
            assert item.layer in ("external", "ingress", "compute", "async", "data")

    def test_non_bom_sheet_name_accepted(self, tmp_path):
        """parse_bom must work when the sheet is not named 'BOM'."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Services"          # not "BOM"
        ws.append(["SKU", "Description", "Quantity"])
        ws.append(["B94176", "Compute VM Standard E4 Flex", 2])
        ws.append(["B99060", "Oracle Autonomous Database",  1])
        path = tmp_path / "custom_name.xlsx"
        wb.save(path)

        items = parse_bom(path)
        types = {i.oci_type for i in items}
        assert "compute" in types
        assert "database" in types
