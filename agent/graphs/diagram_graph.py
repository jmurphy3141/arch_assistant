from __future__ import annotations


async def run(
    *,
    args: dict,
    customer_id: str,
    a2a_base_url: str,
) -> tuple[str, str, dict]:
    """
    LangGraph-compatible diagram specialist entrypoint.
    Reuses the orchestrator diagram adapter so legacy and LangGraph modes
    share the same recovery and reply behavior.
    """
    from agent import orchestrator_agent

    return await orchestrator_agent._call_generate_diagram(args, customer_id, a2a_base_url)
