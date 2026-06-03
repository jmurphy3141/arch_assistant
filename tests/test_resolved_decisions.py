from __future__ import annotations

from agent import context_store
from agent.archie_memory_impl import ArchieMemory
from agent.archie_wiring import ArchiePromptEnricher
from skillforge.types import MemorySnapshot


def test_set_resolved_decisions_stores_topology_under_archie_state() -> None:
    context: dict = {}

    context_store.set_resolved_decisions(
        context,
        topology={
            "ha_mode": "active-active",
            "subnet_tiers": ["public", "private", "data"],
            "diagram_key": "diagrams/acme/v1.drawio",
        },
    )

    assert context["archie"]["resolved_decisions"]["topology"] == {
        "ha_mode": "active-active",
        "subnet_tiers": ["public", "private", "data"],
        "diagram_key": "diagrams/acme/v1.drawio",
    }


def test_set_resolved_decisions_merges_overlapping_keys_without_dropping_other_values() -> None:
    context: dict = {}

    context_store.set_resolved_decisions(
        context,
        topology={
            "ha_mode": "single-AD",
            "subnet_tiers": ["private"],
            "diagram_key": "diagrams/acme/v1.drawio",
        },
    )
    context_store.set_resolved_decisions(
        context,
        topology={
            "ha_mode": "active-active",
            "gateways": ["DRG", "NAT", "SGW"],
        },
    )

    topology = context["archie"]["resolved_decisions"]["topology"]
    assert topology["ha_mode"] == "active-active"
    assert topology["subnet_tiers"] == ["private"]
    assert topology["diagram_key"] == "diagrams/acme/v1.drawio"
    assert topology["gateways"] == ["DRG", "NAT", "SGW"]


def test_archie_memory_assemble_surfaces_resolved_decisions_in_facts() -> None:
    context: dict = {}
    context_store.set_resolved_decisions(
        context,
        waf={
            "security_score": 3,
            "p1_count": 1,
            "compliance_framework": ["PCI DSS"],
            "waf_key": "waf/acme/v1.md",
        },
    )

    snapshot = ArchieMemory(store=None).assemble(
        session_id="session-1",
        context=context,
        user_message="Generate the POV next.",
    )

    assert snapshot.facts["resolved_decisions"]["waf"] == {
        "security_score": 3,
        "p1_count": 1,
        "compliance_framework": ["PCI DSS"],
        "waf_key": "waf/acme/v1.md",
    }


def test_archie_prompt_enricher_injects_resolved_topology_and_waf_block() -> None:
    memory = MemorySnapshot(
        session_id="session-1",
        facts={
            "resolved_decisions": {
                "topology": {
                    "ha_mode": "active-active",
                    "subnet_tiers": ["public", "private", "data"],
                    "gateways": ["DRG", "NAT", "SGW"],
                },
                "waf": {
                    "security_score": 2,
                    "p1_count": 2,
                    "compliance_framework": ["PCI DSS"],
                },
            }
        },
    )

    enriched = ArchiePromptEnricher()("Original prompt", memory)

    assert "[Resolved Decisions]" in enriched
    assert "Topology: ha_mode=active-active" in enriched
    assert "tiers=['public', 'private', 'data']" in enriched
    assert "gateways=['DRG', 'NAT', 'SGW']" in enriched
    assert "WAF: security=2/5" in enriched
    assert "p1_count=2" in enriched
    assert "[/Resolved Decisions]" in enriched
    assert enriched.endswith("Original prompt")
