from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from agent import document_store
from agent.persistence_objectstore import ObjectStoreBase

REQUIRED_FIELDS = (
    "duration",
    "scope_in",
    "scope_out",
    "success_criteria",
    "owners",
    "milestones",
)


STATE_NEXT_STEP = {
    "not_started": "upload_notes_or_context",
    "kickoff_ready": "run_kickoff_questions",
    "questions_pending": "answer_kickoff_questions",
    "ready_to_generate": "generate_jep",
    "generated": "approve_or_regenerate",
    "approved": "request_revision",
    "revision_requested": "generate_jep",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lifecycle_key(customer_id: str) -> str:
    return f"jep/{customer_id}/lifecycle.json"


def _is_tbd(value: str) -> bool:
    return "[tbd]" in value.lower()


def _compact_snippet(value: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _load_lifecycle_record(store: ObjectStoreBase, customer_id: str) -> dict[str, Any]:
    key = _lifecycle_key(customer_id)
    try:
        raw = store.get(key).decode("utf-8", errors="replace")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except KeyError:
        pass
    except Exception:
        pass
    return {
        "state": "not_started",
        "is_locked": False,
        "required_next_step": STATE_NEXT_STEP["not_started"],
        "created_at": "",
        "updated_at": "",
        "last_generated_at": "",
        "last_approved_at": "",
        "last_revision_request": {},
    }


def _save_lifecycle_record(store: ObjectStoreBase, customer_id: str, record: dict[str, Any]) -> None:
    key = _lifecycle_key(customer_id)
    store.put(key, json.dumps(record, indent=2).encode("utf-8"), "application/json")


def _required_unanswered_question_ids(questions_data: dict[str, Any]) -> list[str]:
    questions = questions_data.get("questions", []) if isinstance(questions_data, dict) else []
    answers = questions_data.get("answers", {}) if isinstance(questions_data, dict) else {}
    if not isinstance(questions, list) or not isinstance(answers, dict):
        return []

    pending: list[str] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id", "")).strip()
        if not qid:
            continue
        known = str(item.get("known_value", "") or "").strip()
        answer = str(answers.get(qid, "") or "").strip()
        if (answer and not _is_tbd(answer)) or (known and not _is_tbd(known)):
            continue
        pending.append(qid)
    return pending


def _has_non_tbd_answer(qa_answers: dict[str, Any], patterns: tuple[str, ...]) -> bool:
    for k, v in qa_answers.items():
        key = str(k or "").lower()
        value = str(v or "").strip()
        if not value or _is_tbd(value):
            continue
        if any(pat in key or pat in value.lower() for pat in patterns):
            return True
    return False


def _field_present(doc: str, keyword_patterns: tuple[str, ...], content_patterns: tuple[str, ...]) -> tuple[bool, bool]:
    lower = doc.lower()
    has_keyword = any(re.search(pattern, lower) for pattern in keyword_patterns)
    if not has_keyword:
        return False, False
    has_tbd = False
    for pattern in keyword_patterns:
        for m in re.finditer(pattern, lower):
            start = max(0, m.start() - 120)
            end = min(len(doc), m.end() + 280)
            window = doc[start:end]
            if _is_tbd(window):
                has_tbd = True
    has_content = any(re.search(pattern, lower) for pattern in content_patterns)
    return has_content, has_tbd


def extract_missing_fields(content: str, qa_answers: dict[str, Any] | None = None) -> list[str]:
    doc = content or ""
    answers = qa_answers or {}
    missing: list[str] = []

    duration_present, duration_tbd = _field_present(
        doc,
        (r"\bduration\b", r"\btimeline\b", r"\bphase\b"),
        (r"\b\d+\s*(day|days|week|weeks|month|months)\b",),
    )
    if (not duration_present or duration_tbd) and not _has_non_tbd_answer(answers, ("duration", "timeline", "week", "day")):
        missing.append("duration")

    scope_in_present, scope_in_tbd = _field_present(
        doc,
        (r"\bin scope\b", r"\bscope in\b", r"\bscope\b"),
        (r"\bin scope\b", r"\bscope in\b", r"\bscope includes\b"),
    )
    if (not scope_in_present or scope_in_tbd) and not _has_non_tbd_answer(answers, ("scope_in", "in scope", "scope")):
        missing.append("scope_in")

    scope_out_present, scope_out_tbd = _field_present(
        doc,
        (r"\bout of scope\b", r"\bscope out\b", r"\bexcluded\b"),
        (r"\bout of scope\b", r"\bscope out\b", r"\bexcluded\b"),
    )
    if (not scope_out_present or scope_out_tbd) and not _has_non_tbd_answer(answers, ("scope_out", "out of scope", "exclude")):
        missing.append("scope_out")

    success_present, success_tbd = _field_present(
        doc,
        (r"\bsuccess criteria\b", r"\bacceptance criteria\b", r"\bsuccess metric"),
        (r"\bsuccess criteria\b", r"\bacceptance criteria\b", r"\bmetric\b"),
    )
    if (not success_present or success_tbd) and not _has_non_tbd_answer(answers, ("success", "acceptance", "criteria", "metric")):
        missing.append("success_criteria")

    owners_present, owners_tbd = _field_present(
        doc,
        (r"\bowner\b", r"\bowners\b", r"\bstakeholder\b", r"\braci\b"),
        (r"\bowner\b", r"\bstakeholder\b", r"\braci\b", r"\bteam\b"),
    )
    if (not owners_present or owners_tbd) and not _has_non_tbd_answer(answers, ("owner", "stakeholder", "raci", "team")):
        missing.append("owners")

    milestones_present, milestones_tbd = _field_present(
        doc,
        (r"\bmilestone\b", r"\btimeline\b", r"\bphase\b"),
        (r"\bmilestone\b", r"\bphase\b", r"\bweek\b", r"\bday\b"),
    )
    if (not milestones_present or milestones_tbd) and not _has_non_tbd_answer(answers, ("milestone", "timeline", "phase", "week")):
        missing.append("milestones")

    return missing


def build_source_context(store: ObjectStoreBase, customer_id: str) -> dict[str, Any]:
    references: dict[str, Any] = {}
    snippets: list[dict[str, str]] = []

    approved_key = f"approved/{customer_id}/jep.md"
    latest_key = f"jep/{customer_id}/LATEST.md"
    kickoff_key = f"jep/{customer_id}/poc_questions.json"

    approved_content = ""
    latest_content = ""

    if store.head(approved_key):
        references["approved_doc_key"] = approved_key
        try:
            approved_content = store.get(approved_key).decode("utf-8", errors="replace")
            snippets.append({
                "source": "approved_jep",
                "key": approved_key,
                "text": _compact_snippet(approved_content),
            })
        except Exception:
            pass

    if store.head(latest_key):
        references["latest_doc_key"] = latest_key
        try:
            latest_content = store.get(latest_key).decode("utf-8", errors="replace")
            snippets.append({
                "source": "latest_jep",
                "key": latest_key,
                "text": _compact_snippet(latest_content),
            })
        except Exception:
            pass

    notes = document_store.list_notes(store, customer_id)
    note_keys = [item.get("key", "") for item in notes if isinstance(item, dict) and item.get("key")]
    if note_keys:
        references["notes_keys"] = note_keys
        for key in note_keys[:3]:
            try:
                text = store.get(key).decode("utf-8", errors="replace")
                snippets.append({
                    "source": "note",
                    "key": key,
                    "text": _compact_snippet(text, limit=160),
                })
            except Exception:
                continue

    if store.head(kickoff_key):
        references["kickoff_qa_key"] = kickoff_key
        try:
            qa = json.loads(store.get(kickoff_key).decode("utf-8", errors="replace"))
            answers = qa.get("answers", {}) if isinstance(qa, dict) else {}
            if isinstance(answers, dict):
                idx = 0
                for qid, answer in answers.items():
                    value = str(answer or "").strip()
                    if not value:
                        continue
                    snippets.append({
                        "source": "kickoff_answer",
                        "key": kickoff_key,
                        "text": _compact_snippet(f"{qid}: {value}", limit=180),
                    })
                    idx += 1
                    if idx >= 3:
                        break
        except Exception:
            pass

    references["lifecycle_key"] = _lifecycle_key(customer_id)
    return {
        "references": references,
        "snippets": snippets[:8],
        "latest_content": latest_content,
        "approved_content": approved_content,
    }


def sync_jep_state(store: ObjectStoreBase, customer_id: str) -> dict[str, Any]:
    record = _load_lifecycle_record(store, customer_id)
    created_at = str(record.get("created_at") or "")
    now = _utc_now()

    latest_key = f"jep/{customer_id}/LATEST.md"
    approved_key = f"approved/{customer_id}/jep.md"
    kickoff_key = f"jep/{customer_id}/poc_questions.json"
    context_key = f"context/{customer_id}/context.json"

    has_latest = store.head(latest_key)
    has_approved = store.head(approved_key)
    has_kickoff = store.head(kickoff_key)
    has_context = store.head(context_key)
    has_notes = len(document_store.list_notes(store, customer_id)) > 0

    questions_data = document_store.get_jep_questions(store, customer_id) if has_kickoff else {}
    pending_qids = _required_unanswered_question_ids(questions_data)

    last_revision = record.get("last_revision_request", {})
    if not isinstance(last_revision, dict):
        last_revision = {}
    last_revision_ts = str(last_revision.get("requested_at") or "")
    last_generated_ts = str(record.get("last_generated_at") or "")
    last_approved_ts = str(record.get("last_approved_at") or "")

    revision_pending = bool(
        has_approved
        and last_revision_ts
        and (not last_generated_ts or last_revision_ts > last_generated_ts)
    )

    if has_approved and revision_pending:
        state = "revision_requested"
    elif has_approved and has_latest and last_generated_ts and (not last_approved_ts or last_generated_ts > last_approved_ts):
        state = "generated"
    elif has_approved:
        state = "approved"
    elif has_latest:
        state = "generated"
    elif has_kickoff and pending_qids:
        state = "questions_pending"
    elif has_kickoff and not pending_qids:
        state = "ready_to_generate"
    elif has_notes or has_context:
        state = "kickoff_ready"
    else:
        state = "not_started"

    source_context = build_source_context(store, customer_id)
    qa_answers = questions_data.get("answers", {}) if isinstance(questions_data, dict) else {}
    if not isinstance(qa_answers, dict):
        qa_answers = {}

    content_for_gaps = source_context.get("latest_content") or source_context.get("approved_content") or ""
    missing_fields = extract_missing_fields(content_for_gaps, qa_answers) if content_for_gaps else list(REQUIRED_FIELDS)

    jep_state = {
        "state": state,
        "is_locked": state == "approved",
        "missing_fields": missing_fields,
        "required_next_step": STATE_NEXT_STEP[state],
        "source_context": {
            "references": source_context.get("references", {}),
            "snippets": source_context.get("snippets", []),
        },
    }

    updated_record = {
        **record,
        **jep_state,
        "created_at": created_at or now,
        "updated_at": now,
        "last_revision_request": last_revision,
    }
    _save_lifecycle_record(store, customer_id, updated_record)
    return jep_state


def mark_generated(store: ObjectStoreBase, customer_id: str) -> dict[str, Any]:
    record = _load_lifecycle_record(store, customer_id)
    record["last_generated_at"] = _utc_now()
    _save_lifecycle_record(store, customer_id, record)
    return sync_jep_state(store, customer_id)


def mark_approved(store: ObjectStoreBase, customer_id: str) -> dict[str, Any]:
    record = _load_lifecycle_record(store, customer_id)
    record["last_approved_at"] = _utc_now()
    _save_lifecycle_record(store, customer_id, record)
    return sync_jep_state(store, customer_id)


def request_revision(store: ObjectStoreBase, customer_id: str, reason: str = "", requested_by: str = "") -> dict[str, Any]:
    current = sync_jep_state(store, customer_id)
    if current["state"] != "approved":
        raise ValueError("revision_request_requires_approved_state")

    record = _load_lifecycle_record(store, customer_id)
    now = _utc_now()
    record["last_revision_request"] = {
        "reason": reason.strip(),
        "requested_at": now,
        "requested_by": requested_by.strip(),
    }
    _save_lifecycle_record(store, customer_id, record)
    return sync_jep_state(store, customer_id)


def generate_policy_block_payload(store: ObjectStoreBase, customer_id: str) -> dict[str, Any] | None:
    jep_state = sync_jep_state(store, customer_id)
    if jep_state["state"] != "approved":
        return None
    return {
        "status": "policy_block",
        "customer_id": customer_id,
        "doc_type": "jep",
        "reason_codes": ["JEP_APPROVED_LOCKED"],
        "missing_fields": list(jep_state.get("missing_fields", [])),
        "required_next_step": jep_state.get("required_next_step", "request_revision"),
        "retry_instructions": [
            "Call POST /api/jep/revision-request with customer_id (and optional reason).",
            "Retry POST /api/jep/generate after revision request is accepted.",
        ],
        "jep_state": jep_state,
        "lock_outcome": "blocked",
    }
