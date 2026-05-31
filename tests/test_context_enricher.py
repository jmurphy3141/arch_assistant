"""
tests/test_context_enricher.py
-------------------------------
Unit tests for agent/context_enricher.py.

All tests are offline — no OCI auth required.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.context_enricher import (
    _is_conversational_turn,
    _worker_case_studies,
    _worker_preflight_risks,
    _worker_ref_arch,
    enrich_turn_context,
)


# ---------------------------------------------------------------------------
# Intent guard
# ---------------------------------------------------------------------------

class TestIsConversationalTurn:
    def test_short_message(self):
        assert _is_conversational_turn("hello") is True

    def test_fewer_than_8_words(self):
        assert _is_conversational_turn("thanks, that looks good") is True

    def test_domain_message(self):
        assert _is_conversational_turn(
            "Design a 3-tier OCI architecture diagram with OKE and GenAI for a banking migration"
        ) is False

    def test_bom_keyword(self):
        assert _is_conversational_turn(
            "Create a BOM estimate for a streaming analytics platform on OCI us-chicago-1"
        ) is False


# ---------------------------------------------------------------------------
# Worker 1 — Reference Architecture Matcher
# ---------------------------------------------------------------------------

class TestWorkerRefArch:
    def test_returns_none_on_low_confidence(self):
        mock_sel = MagicMock()
        mock_sel.reference_family = ""
        mock_sel.reference_confidence = 0.1
        with patch("agent.reference_architecture.select_reference_architecture", return_value=mock_sel):
            result = _worker_ref_arch("hello", {})
        assert result is None

    def test_returns_result_on_high_confidence(self):
        mock_sel = MagicMock()
        mock_sel.reference_family = "single_region_oke_app"
        mock_sel.reference_label = "3-tier OKE App"
        mock_sel.reference_confidence = 0.85
        mock_sel.reference_mode = "matched"
        mock_sel.family_constraints = {
            "required_containers": ["web_subnet", "app_subnet", "data_subnet"],
            "connector_lanes": ["igw", "nlb"],
        }
        with patch("agent.reference_architecture.select_reference_architecture", return_value=mock_sel):
            result = _worker_ref_arch(
                "Design an OKE architecture for a 3-tier web application on OCI", {}
            )
        assert result is not None
        assert result["family"] == "single_region_oke_app"
        assert result["confidence"] == 0.85
        assert "3-tier OKE App" in result["text"]
        assert "web_subnet" in result["text"]

    def test_returns_none_on_exception(self):
        with patch("agent.reference_architecture.select_reference_architecture", side_effect=RuntimeError("oops")):
            result = _worker_ref_arch("some message", {})
        assert result is None


# ---------------------------------------------------------------------------
# Worker 2 — Case Study Scorer
# ---------------------------------------------------------------------------

_SAMPLE_CASE_STUDIES = {
    "case_studies": [
        {
            "id": "cs_001",
            "company": "Acme Corp",
            "industry": "manufacturing",
            "region": "us-chicago-1",
            "use_case_type": "migration",
            "oci_services": ["Compute", "Object Storage", "OKE"],
            "migration_from": "AWS",
            "quantified_outcomes": {"cost_savings_percent": "40%"},
            "summary": "Migrated manufacturing workloads to OCI reducing cost by 40%",
            "source_url": "https://example.com",
        },
        {
            "id": "cs_002",
            "company": "Telecom Inc",
            "industry": "telecom",
            "region": "us-ashburn-1",
            "use_case_type": "genai chatbot",
            "oci_services": ["GenAI", "OKE", "Streaming"],
            "migration_from": None,
            "quantified_outcomes": {"performance": "60% ticket deflection"},
            "summary": "Built an AI ops chatbot using OCI GenAI for telecom ops",
            "source_url": "https://example.com",
        },
    ]
}


class TestWorkerCaseStudies:
    def test_returns_top_matches(self, tmp_path):
        path = tmp_path / "oracle_customer_case_studies.json"
        path.write_text(json.dumps(_SAMPLE_CASE_STUDIES))
        with patch("agent.context_enricher._CASE_STUDIES_PATH", path):
            result = _worker_case_studies(
                "Build a GenAI chatbot on OCI", {"pain_statement": "genai chatbot automation"}
            )
        assert result is not None
        assert len(result["studies"]) >= 1
        assert "Telecom Inc" in result["text"]

    def test_returns_none_on_no_match(self, tmp_path):
        path = tmp_path / "oracle_customer_case_studies.json"
        path.write_text(json.dumps(_SAMPLE_CASE_STUDIES))
        with patch("agent.context_enricher._CASE_STUDIES_PATH", path):
            result = _worker_case_studies("xyz", {})
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path):
        missing = tmp_path / "no_such_file.json"
        with patch("agent.context_enricher._CASE_STUDIES_PATH", missing):
            result = _worker_case_studies("OKE migration", {})
        assert result is None

    def test_handles_exception_gracefully(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json")
        with patch("agent.context_enricher._CASE_STUDIES_PATH", path):
            result = _worker_case_studies("migration", {})
        assert result is None


# ---------------------------------------------------------------------------
# Worker 3 — Pre-Flight Risk Checker
# ---------------------------------------------------------------------------

_HIGH_RISK_DECISION_CONTEXT = {
    "assumptions": [
        {
            "id": "region_default",
            "statement": "Region not specified; assume primary OCI region.",
            "reason": "No explicit OCI region found.",
            "risk": "medium",
        },
        {
            "id": "availability_default",
            "statement": "Availability target assumed at 99.9%.",
            "reason": "No explicit availability objective.",
            "risk": "low",
        },
    ]
}


class TestWorkerPreflightRisks:
    def test_surfaces_medium_risk_assumption(self):
        result = _worker_preflight_risks("design an architecture", {}, _HIGH_RISK_DECISION_CONTEXT)
        assert result is not None
        assert any(r["level"] == "P2" for r in result["risks"])
        assert "Region not specified" in result["text"]

    def test_no_risks_returns_none(self):
        ctx = {"assumptions": [{"id": "x", "statement": "ok", "reason": "r", "risk": "low"}]}
        result = _worker_preflight_risks("hello world", {}, ctx)
        assert result is None

    def test_diagram_intent_with_insufficient_context(self):
        with patch("agent.archie_memory._diagram_has_sufficient_context", return_value=False):
            result = _worker_preflight_risks("draw a diagram", {}, {})
        assert result is not None
        assert any("Diagram" in r["risk"] for r in result["risks"])

    def test_diagram_intent_with_sufficient_context(self):
        with patch("agent.archie_memory._diagram_has_sufficient_context", return_value=True):
            result = _worker_preflight_risks("draw a diagram", {}, {})
        assert result is None

    def test_handles_exception_gracefully(self):
        dc = {"assumptions": "not a list"}
        result = _worker_preflight_risks("design architecture", {}, dc)
        # Should not raise — may return None or a result, but never raises
        assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class TestEnrichTurnContext:
    def test_conversational_message_returns_empty(self):
        result = asyncio.run(enrich_turn_context("hi there", {}, {}))
        assert result == {}

    def test_enrichment_keys_present_on_signal(self, tmp_path):
        cs_path = tmp_path / "oracle_customer_case_studies.json"
        cs_path.write_text(json.dumps(_SAMPLE_CASE_STUDIES))

        mock_sel = MagicMock()
        mock_sel.reference_family = "single_region_oke_app"
        mock_sel.reference_label = "3-tier OKE App"
        mock_sel.reference_confidence = 0.85
        mock_sel.reference_mode = "matched"
        mock_sel.family_constraints = {"required_containers": ["web_subnet"], "connector_lanes": []}

        with (
            patch("agent.reference_architecture.select_reference_architecture", return_value=mock_sel),
            patch("agent.context_enricher._CASE_STUDIES_PATH", cs_path),
        ):
            result = asyncio.run(
                enrich_turn_context(
                    "Design a GenAI migration OKE architecture diagram for banking",
                    {},
                    _HIGH_RISK_DECISION_CONTEXT,
                )
            )
        assert "ref_arch" in result
        assert "case_studies" in result
        assert "preflight_risks" in result

    def test_worker_timeout_is_tolerated(self):
        import time

        def _slow(*_args):
            time.sleep(10)

        with patch("agent.context_enricher._worker_ref_arch", _slow):
            result = asyncio.run(
                enrich_turn_context(
                    "Design a 3-tier OCI architecture with OKE and GenAI for banking",
                    {},
                    {},
                )
            )
        # ref_arch key should be absent (timed out), but turn must not raise
        assert "ref_arch" not in result

    def test_all_workers_failing_returns_empty(self):
        def _fail(*_args):
            raise RuntimeError("fail")

        with (
            patch("agent.context_enricher._worker_ref_arch", _fail),
            patch("agent.context_enricher._worker_case_studies", _fail),
            patch("agent.context_enricher._worker_preflight_risks", _fail),
        ):
            result = asyncio.run(
                enrich_turn_context(
                    "Design an OCI architecture with OKE and GenAI for a banking migration workload",
                    {},
                    {},
                )
            )
        assert result == {}
