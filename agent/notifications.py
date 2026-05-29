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

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
from pathlib import Path
from typing import Callable

import httpx
import yaml

logger = logging.getLogger(__name__)
_NOTIFICATION_SINK: ContextVar[Callable[[str, str, str], None] | None] = ContextVar(
    "archie_notification_sink",
    default=None,
)


@contextmanager
def notification_sink(sink: Callable[[str, str, str], None]):
    token = _NOTIFICATION_SINK.set(sink)
    try:
        yield
    finally:
        _NOTIFICATION_SINK.reset(token)


def notify(event: str, customer_id: str, detail: str = "") -> None:
    """
    Fire a notification event.

    Args:
        event:       Short event identifier (e.g. "pov_generated").
        customer_id: Customer this event belongs to.
        detail:      Human-readable description of what happened.
    """
    _send(event, customer_id, detail)
    sink = _NOTIFICATION_SINK.get()
    if sink is not None:
        try:
            sink(event, customer_id, detail)
        except Exception:
            logger.exception("Notification sink failed for event=%s customer_id=%s", event, customer_id)


def _send(event: str, customer_id: str, detail: str) -> None:
    """
    Delivery backend — logs every event and schedules Telegram delivery when enabled.
    """
    logger.info(
        "NOTIFY event=%s customer_id=%s detail=%r",
        event,
        customer_id,
        detail,
    )
    cfg = _load_telegram_config()
    if not cfg.get("enabled", False):
        return

    message = _format_message(event, customer_id, detail)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("Skipping Telegram notification outside an active event loop")
        return
    loop.create_task(_send_telegram(message, cfg))


def _format_message(event: str, customer_id: str, detail: str) -> str:
    lines = [
        f"*Archie*: `{event}`",
        f"*Customer*: `{customer_id}`",
    ]
    if detail:
        lines.append(detail)
    return "\n".join(lines)


def _load_telegram_config() -> dict:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    try:
        with config_path.open() as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        logger.exception("Could not load notification config from %s", config_path)
        return {}

    telegram = cfg.get("telegram")
    if isinstance(telegram, dict):
        return telegram

    legacy = cfg.get("orchestrator", {}).get("telegram")
    return legacy if isinstance(legacy, dict) else {}


async def _send_telegram(message: str, cfg: dict) -> None:
    token_env = cfg.get("bot_token_env", cfg.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN"))
    chat_id_env = cfg.get("chat_id_env", cfg.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID"))
    token = os.environ.get(str(token_env), "")
    chat_id = os.environ.get(str(chat_id_env), "")
    if not token or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            )
    except Exception:
        logger.debug("Telegram notification failed", exc_info=True)
