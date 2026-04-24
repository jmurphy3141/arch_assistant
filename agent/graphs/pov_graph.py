from __future__ import annotations

import asyncio
from typing import Callable

from agent.persistence_objectstore import ObjectStoreBase
from agent import pov_agent


async def run(
    *,
    args: dict,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
) -> tuple[str, str]:
    """
    LangGraph-compatible POV specialist entrypoint.
    """
    feedback = args.get("feedback", "")
    result = await asyncio.to_thread(
        pov_agent.generate_pov,
        customer_id,
        customer_name,
        store,
        text_runner,
        feedback=feedback,
        architect_brief=dict(args.get("_architect_brief", {}) or {}),
    )
    key = result.get("key", "")
    return f"POV v{result.get('version')} saved. Key: {key}", key
