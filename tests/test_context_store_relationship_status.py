from __future__ import annotations

from agent import context_store


def _context_with_relationship():
    context = {"customer_id": "acme"}
    context_store.merge_archie_relationship_facts(
        context,
        {
            "objections": [{"concern": "Cost is too high", "status": "open"}],
            "commitments": [{"who": "SE", "what": "Send pricing by Friday", "status": "open"}],
            "action_items": [{"owner": "SE", "task": "Schedule security review", "status": "open"}],
        },
    )
    # build_context_summary only renders when other archie state is present.
    context_store.set_archie_engagement_summary(context, "ACME cloud migration engagement.")
    return context


def test_update_relationship_item_status_single_match():
    context = _context_with_relationship()
    context_store.update_relationship_item_status(
        context, category="objections", match_text="cost", new_status="addressed"
    )
    rel = context_store.get_archie_state(context)["relationship"]
    assert rel["objections"][0]["status"] == "addressed"


def test_update_relationship_item_status_case_insensitive():
    context = _context_with_relationship()
    context_store.update_relationship_item_status(
        context, category="commitments", match_text="PRICING", new_status="done"
    )
    rel = context_store.get_archie_state(context)["relationship"]
    assert rel["commitments"][0]["status"] == "done"


def test_update_relationship_item_status_no_match_is_noop():
    context = _context_with_relationship()
    context_store.update_relationship_item_status(
        context, category="objections", match_text="nonexistent", new_status="addressed"
    )
    rel = context_store.get_archie_state(context)["relationship"]
    assert rel["objections"][0]["status"] == "open"


def test_update_relationship_item_status_with_note():
    context = _context_with_relationship()
    context_store.update_relationship_item_status(
        context,
        category="action_items",
        match_text="security",
        new_status="done",
        note="Closed during call",
    )
    rel = context_store.get_archie_state(context)["relationship"]
    assert rel["action_items"][0]["status"] == "done"
    assert rel["action_items"][0]["resolution_note"] == "Closed during call"


def test_get_open_relationship_items_filters_resolved():
    context = _context_with_relationship()
    context_store.update_relationship_item_status(
        context, category="objections", match_text="cost", new_status="addressed"
    )
    open_items = context_store.get_open_relationship_items(context)
    assert open_items["objections"] == []
    assert len(open_items["commitments"]) == 1
    assert len(open_items["action_items"]) == 1


def test_get_open_relationship_items_matches_build_context_summary():
    context = _context_with_relationship()
    summary = context_store.build_context_summary(context)
    assert "Cost is too high" in summary
    assert "Send pricing by Friday" in summary
    assert "Schedule security review" in summary

    context_store.update_relationship_item_status(
        context, category="objections", match_text="cost", new_status="addressed"
    )
    summary_after = context_store.build_context_summary(context)
    assert "Cost is too high" not in summary_after
