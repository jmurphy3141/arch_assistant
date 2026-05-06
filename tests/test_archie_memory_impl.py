from agent.archie_memory_impl import ArchieMemory
from skillforge.protocols import Memory
from skillforge.types import MemorySnapshot, ToolResult


def test_assemble_empty_context():
    snapshot = ArchieMemory(store=None).assemble(
        session_id="s1", context={}, user_message="hello"
    )

    assert isinstance(snapshot, MemorySnapshot)
    assert snapshot.session_id == "s1"
    assert snapshot.facts == {}
    assert snapshot.constraints == {}
    assert snapshot.prior_artifacts == {}
    assert snapshot.decision_context == {}


def test_assemble_with_facts():
    context = {
        "archie": {
            "facts_summary": "3-tier web app",
            "latest_approved_constraints": {"region": "us-chicago-1"},
        }
    }

    snapshot = ArchieMemory(store=None).assemble(
        session_id="s1", context=context, user_message="hello"
    )

    assert snapshot.facts["facts_summary"] == "3-tier web app"
    assert snapshot.constraints["region"] == "us-chicago-1"


def test_assemble_prior_artifacts():
    context = {"agents": {"diagram": {"diagram_key": "diagrams/foo.drawio"}}}

    snapshot = ArchieMemory(store=None).assemble(
        session_id="s1", context=context, user_message="hello"
    )

    assert snapshot.prior_artifacts["generate_diagram"] == "diagrams/foo.drawio"


def test_update_returns_context_unchanged():
    context = {"x": 1}

    updated = ArchieMemory(store=None).update(
        session_id="s",
        tool_name="generate_bom",
        result=ToolResult(summary="ok", status="ok"),
        context=context,
    )

    assert updated == {"x": 1}


def test_implements_memory_protocol():
    assert isinstance(ArchieMemory(store=None), Memory)
