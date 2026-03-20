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


@pytest.mark.skipif(not SAMPLE_BOM.exists(), reason="sample_bom.xlsx fixture not present")
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

    def test_all_items_have_required_fields(self):
        items = parse_bom(SAMPLE_BOM)
        for item in items:
            assert item.id
            assert item.oci_type
            assert item.label
            assert item.layer in ("external", "ingress", "compute", "async", "data")
