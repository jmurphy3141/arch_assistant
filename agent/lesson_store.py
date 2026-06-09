"""
agent/lesson_store.py
----------------------
LessonStore implementation for Archie.

Persists per-engagement correction lessons from EXPERT_ITERATE decisions.
Implements skillforge.protocols.LessonStore — pure context mutations, no I/O.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agent.context_store import get_archie_state

logger = logging.getLogger(__name__)

_MAX_LESSONS = 20
_DEDUP_PREFIX_LEN = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EngagementLessonStore:
    """Stateless LessonStore; reads/writes archie.lessons in context dict."""

    def record_lesson(
        self, context: dict[str, Any], tool: str, hat: str, concern: str
    ) -> None:
        if not concern:
            return
        archie = get_archie_state(context)
        lessons: list[dict] = archie.setdefault("lessons", [])

        # Dedup: increment count if same tool+hat and concern prefix matches
        prefix = concern[:_DEDUP_PREFIX_LEN]
        for lesson in lessons:
            if (
                lesson.get("tool") == tool
                and lesson.get("hat") == hat
                and lesson.get("concern", "")[:_DEDUP_PREFIX_LEN] == prefix
            ):
                lesson["count"] = lesson.get("count", 1) + 1
                lesson["last_seen"] = _now()
                logger.debug(
                    "[LESSONS] Updated existing lesson (count=%d) tool=%s hat=%s",
                    lesson["count"], tool, hat,
                )
                return

        lessons.append({
            "tool": tool,
            "hat": hat,
            "concern": concern,
            "ts": _now(),
            "last_seen": _now(),
            "count": 1,
        })
        logger.debug("[LESSONS] Recorded new lesson tool=%s hat=%s", tool, hat)

        # FIFO cap
        if len(lessons) > _MAX_LESSONS:
            archie["lessons"] = lessons[-_MAX_LESSONS:]

    def get_lessons(
        self, context: dict[str, Any], tool: str, hat: str
    ) -> list[str]:
        archie = get_archie_state(context)
        lessons: list[dict] = archie.get("lessons", [])
        relevant = [
            l for l in lessons
            if l.get("tool") == tool and l.get("hat") == hat
        ]
        # Return the 3 most recent, formatted for LLM injection
        return [
            f"- Prior correction ({l.get('count', 1)}x): {l['concern']}"
            for l in relevant[-3:]
        ]
