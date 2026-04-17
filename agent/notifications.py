"""
agent/notifications.py
-----------------------
Notification stub for the OCI Architecture Assistant fleet.

Currently logs events to the server logger.
Replace the body of _send() with real Telegram/webhook delivery when ready —
no other code needs to change.

Usage:
    from agent.notifications import notify
    notify("pov_generated", "acme", "POV v2 generated for ACME Corp")

Events emitted by the system:
    pov_generated       — new POV version written to bucket
    pov_approved        — SA uploaded an approved POV
    jep_generated       — new JEP version written to bucket
    jep_approved        — SA uploaded an approved JEP
    jep_kickoff         — JEP kickoff questions generated
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify(event: str, customer_id: str, detail: str = "") -> None:
    """
    Fire a notification event.

    Args:
        event:       Short event identifier (e.g. "pov_generated").
        customer_id: Customer this event belongs to.
        detail:      Human-readable description of what happened.
    """
    _send(event, customer_id, detail)


def _send(event: str, customer_id: str, detail: str) -> None:
    """
    Delivery backend — currently a structured log line.
    Replace this function body with Telegram bot API call, webhook POST, etc.
    """
    logger.info(
        "NOTIFY event=%s customer_id=%s detail=%r",
        event,
        customer_id,
        detail,
    )
    # TODO: Telegram integration
    # import httpx
    # httpx.post(TELEGRAM_WEBHOOK_URL, json={"event": event, "customer_id": customer_id, "detail": detail})
