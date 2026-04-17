from __future__ import annotations

import asyncio
from typing import Callable

from agent.persistence_objectstore import ObjectStoreBase
from agent import jep_agent


async def run(
    *,
    args: dict,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
) -> tuple[str, str]:
    """
    LangGraph-compatible JEP specialist entrypoint.
    """
    feedback = args.get("feedback", "")
    result = await asyncio.to_thread(
        jep_agent.generate_jep,
        customer_id,
        customer_name,
        store,
        text_runner,
        feedback=feedback,
    )
    key = result.get("key", "")
    return f"JEP v{result.get('version')} saved. Key: {key}", key

