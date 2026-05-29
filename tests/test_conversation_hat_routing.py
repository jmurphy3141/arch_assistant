from agent.archie_wiring import _TOOL_SEQUENCING_RULES, build_forge
from agent.hat_engine import get_hat_tool_definitions


async def dummy_runner(prompt, system_msg, label):
    return "Done."


def make_forge():
    return build_forge(
        store=None,
        customer_id="c1",
        customer_name="Acme",
        text_runner=dummy_runner,
    )


def test_conversation_hat_routing_section_present():
    assert "## Conversation Hat Routing" in _TOOL_SEQUENCING_RULES


def test_all_four_conversation_hats_mentioned():
    for hat in ("deal_coach", "industry_expert", "architecture_reviewer", "discovery"):
        assert hat in _TOOL_SEQUENCING_RULES, f"Missing hat routing rules for: {hat}"


def test_conversation_hat_tools_registered():
    tools = get_hat_tool_definitions()
    tool_names = {t["function"]["name"] for t in tools}
    for hat in ("deal_coach", "industry_expert", "architecture_reviewer", "discovery"):
        assert f"use_hat_{hat}" in tool_names, f"use_hat_{hat} not in hat tool definitions"
        assert f"drop_hat_{hat}" in tool_names, f"drop_hat_{hat} not in hat tool definitions"
