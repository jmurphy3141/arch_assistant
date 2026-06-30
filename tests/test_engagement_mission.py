from agent.engagement_mission import EngagementMission


def test_next_step_uses_full_strategic_technical_approach_name() -> None:
    context = {
        "archie": {
            "mission": {
                "phase": "Discover",
                "completed_artifacts": [],
                "last_updated": "2026-06-30T00:00:00+00:00",
            },
            "client_facts": {"economic_buyer": "CIO", "platform": "on-premises"},
        }
    }

    nudge = EngagementMission().suggest_next_step(context, [])

    assert nudge is not None
    assert "Strategic Technical Approach" in nudge
    assert "generate the sta" not in nudge.lower()
