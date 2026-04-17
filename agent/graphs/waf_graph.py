from __future__ import annotations

import asyncio
from typing import Callable

from agent.persistence_objectstore import ObjectStoreBase
from agent import waf_agent


async def run(
    *,
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
) -> tuple[str, str]:
    """
    LangGraph-compatible WAF specialist entrypoint.
    """
    result = await asyncio.to_thread(
        waf_agent.generate_waf,
        customer_id,
        customer_name,
        store,
        text_runner,
    )
    key = result.get("key", "")
    rating = result.get("overall_rating", "")
    return f"WAF review {rating} saved. Key: {key}", key

