"""
server/routes/briefing.py
--------------------------
Briefing and engagement status endpoints.
"""
from __future__ import annotations

import functools

import anyio
from fastapi import APIRouter, HTTPException

from agent.context_store import read_context, get_archie_state
from agent.document_store import get_se_engagements
from agent.meeting_prep import build_meeting_prep


def create_briefing_router(deps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/briefing/{customer_id}")
    async def get_engagement_briefing(customer_id: str, customer_name: str = ""):
        store = deps.require_object_store()
        try:
            context = await anyio.to_thread.run_sync(
                functools.partial(read_context, store, customer_id, customer_name)
            )
            archie = get_archie_state(context)
            mission = archie.get("mission") or context.get("agents", {}).get("mission") or {}
            relationship = archie.get("relationship") or {}
            open_actions = [
                a for a in (relationship.get("action_items") or [])
                if isinstance(a, dict) and a.get("status") != "done"
            ]
            open_objections = [
                o for o in (relationship.get("objections") or [])
                if isinstance(o, dict) and o.get("status") != "addressed"
            ]
            open_commitments = [
                c for c in (relationship.get("commitments") or [])
                if isinstance(c, dict) and c.get("status") != "done"
            ]
            pending_debrief = context.get("pending_debrief")
            return {
                "status": "ok",
                "customer_id": customer_id,
                "customer_name": customer_name or context.get("customer_name", ""),
                "mission": mission,
                "stakeholders": relationship.get("stakeholders", []),
                "competitive": relationship.get("competitive", {}),
                "open_objections": open_objections,
                "open_commitments": open_commitments,
                "open_action_items": open_actions,
                "pending_debrief": pending_debrief,
            }
        except Exception as exc:
            deps.logger.error("Error in /briefing/%s: %s", customer_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/briefing/{customer_id}/prep")
    async def get_meeting_prep(customer_id: str, customer_name: str = ""):
        store = deps.require_object_store()
        try:
            context = await anyio.to_thread.run_sync(
                functools.partial(read_context, store, customer_id, customer_name)
            )
            name = customer_name or context.get("customer_name", "")
            prep_text = build_meeting_prep(context, customer_name=name)
            return {
                "status": "ok",
                "customer_id": customer_id,
                "customer_name": name,
                "prep": prep_text,
            }
        except Exception as exc:
            deps.logger.error("Error in /briefing/%s/prep: %s", customer_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/se/{se_id}/accounts")
    async def get_se_accounts(se_id: str):
        store = deps.require_object_store()
        try:
            index = await anyio.to_thread.run_sync(
                functools.partial(get_se_engagements, store, se_id)
            )
            accounts = list(index.values())
            accounts.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            return {
                "status": "ok",
                "se_id": se_id,
                "accounts": accounts,
                "total": len(accounts),
            }
        except Exception as exc:
            deps.logger.error("Error in /se/%s/accounts: %s", se_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    return router
