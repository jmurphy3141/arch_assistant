from __future__ import annotations

import time
import uuid
from typing import Dict


JOB_STORE: Dict[str, dict] = {}
JOB_TTL = 3600


def new_job() -> str:
    """Create a pending job entry and return its ID."""
    jid = str(uuid.uuid4())
    JOB_STORE[jid] = {
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": time.time(),
    }
    cutoff = time.time() - JOB_TTL
    for key in [key for key, value in list(JOB_STORE.items()) if value["created_at"] < cutoff]:
        del JOB_STORE[key]
    return jid


def complete_job(jid: str, result: dict) -> None:
    if jid in JOB_STORE:
        JOB_STORE[jid]["status"] = "complete"
        JOB_STORE[jid]["result"] = result


def fail_job(jid: str, detail: str) -> None:
    if jid in JOB_STORE:
        JOB_STORE[jid]["status"] = "error"
        JOB_STORE[jid]["error"] = detail
