"""
agent/document_store.py
------------------------
Versioned document and notes storage helpers for writing agents.

Primary bucket layout (all paths are relative to the root bucket):

  customers/{customer_id}/notes/{note_name}            — individual meeting notes
  customers/{customer_id}/notes/MANIFEST.json          — list of all notes with timestamps

  customers/{customer_id}/pov/v{n}.md                  — POV versions
  customers/{customer_id}/pov/LATEST.md                — latest LLM-generated content
  customers/{customer_id}/pov/MANIFEST.json            — version history
  customers/{customer_id}/pov/v{n}_prompt_log.json     — LLM prompt/response log per version
  customers/{customer_id}/pov/feedback.json            — append-only SA feedback history

  customers/{customer_id}/jep/v{n}.md                  — JEP versions
  customers/{customer_id}/jep/LATEST.md
  customers/{customer_id}/jep/MANIFEST.json
  customers/{customer_id}/jep/v{n}_prompt_log.json
  customers/{customer_id}/jep/feedback.json
  customers/{customer_id}/jep/poc_questions.json       — Q&A from JEP kickoff

  customers/{customer_id}/approved/pov.md              — SA-uploaded approved POV (source of truth)
  customers/{customer_id}/approved/jep.md              — SA-uploaded approved JEP

Backward compatibility:
  Legacy keys under notes/{customer_id}/..., pov/{customer_id}/..., etc.
  are still written/read for compatibility during migration.

Atomicity: versioned copy is written first; LATEST.md and MANIFEST.json
are written only after the versioned copy succeeds.
Approved versions are NEVER overwritten by LLM generation.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)


# ── Key layout helpers ───────────────────────────────────────────────────────

def _customer_prefix(customer_id: str) -> str:
    return f"customers/{customer_id}"


def _notes_key(customer_id: str, note_name: str, *, customer_first: bool) -> str:
    if customer_first:
        return f"{_customer_prefix(customer_id)}/notes/{note_name}"
    return f"notes/{customer_id}/{note_name}"


def _notes_manifest_key(customer_id: str, *, customer_first: bool) -> str:
    if customer_first:
        return f"{_customer_prefix(customer_id)}/notes/MANIFEST.json"
    return f"notes/{customer_id}/MANIFEST.json"


def _doc_key(doc_type: str, customer_id: str, tail: str, *, customer_first: bool) -> str:
    if customer_first:
        return f"{_customer_prefix(customer_id)}/{doc_type}/{tail}"
    return f"{doc_type}/{customer_id}/{tail}"


def _approved_key(doc_type: str, customer_id: str, *, customer_first: bool) -> str:
    if customer_first:
        return f"{_customer_prefix(customer_id)}/approved/{doc_type}.md"
    return f"approved/{customer_id}/{doc_type}.md"


def _conversation_key(customer_id: str, filename: str, *, customer_first: bool) -> str:
    if customer_first:
        return f"{_customer_prefix(customer_id)}/conversations/{filename}"
    return f"conversations/{customer_id}/{filename}"


def _project_key(project_id: str, tail: str) -> str:
    return f"projects/{project_id}/{tail}"


def _put_dual(
    store: ObjectStoreBase,
    *,
    customer_key: str,
    legacy_key: str,
    content: bytes,
    content_type: str,
) -> None:
    store.put(customer_key, content, content_type)
    if legacy_key != customer_key:
        store.put(legacy_key, content, content_type)


def _get_first_bytes(store: ObjectStoreBase, keys: list[str]) -> bytes:
    last: KeyError | None = None
    for key in keys:
        try:
            return store.get(key)
        except KeyError as exc:
            last = exc
            continue
    raise last or KeyError(keys[-1] if keys else "")


def _get_first_json(store: ObjectStoreBase, keys: list[str], default):
    for key in keys:
        try:
            return json.loads(store.get(key))
        except KeyError:
            continue
    return default


# ── Notes ─────────────────────────────────────────────────────────────────────

def save_note(
    store: ObjectStoreBase,
    customer_id: str,
    note_name: str,
    content: bytes,
    content_type: str = "text/plain",
) -> str:
    """
    Save a note file and update the notes manifest.
    Returns the object key.
    """
    legacy_key = _notes_key(customer_id, note_name, customer_first=False)
    customer_key = _notes_key(customer_id, note_name, customer_first=True)
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=legacy_key,
        content=content,
        content_type=content_type,
    )

    manifest_customer_key = _notes_manifest_key(customer_id, customer_first=True)
    manifest_legacy_key = _notes_manifest_key(customer_id, customer_first=False)
    manifest = _get_first_json(
        store,
        [manifest_customer_key, manifest_legacy_key],
        {"notes": []},
    )

    # Upsert by key (re-uploading the same note name replaces the entry)
    manifest["notes"] = [n for n in manifest["notes"] if n["key"] != legacy_key]
    manifest["notes"].append({
        "key": legacy_key,  # keep legacy shape for backward compatibility
        "name": note_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=manifest_customer_key,
        legacy_key=manifest_legacy_key,
        content=manifest_bytes,
        content_type="application/json",
    )
    logger.info("Note saved: key=%s customer_key=%s", legacy_key, customer_key)
    return legacy_key


def list_notes(store: ObjectStoreBase, customer_id: str) -> list[dict]:
    """Return list of note metadata dicts for a customer. Empty list if none."""
    manifest = _get_first_json(
        store,
        [
            _notes_manifest_key(customer_id, customer_first=True),
            _notes_manifest_key(customer_id, customer_first=False),
        ],
        {"notes": []},
    )
    return manifest.get("notes", [])


def get_note(store: ObjectStoreBase, customer_id: str, note_name: str) -> Optional[str]:
    """Read a single note by name. Returns None if not found."""
    try:
        content = _get_first_bytes(
            store,
            [
                _notes_key(customer_id, note_name, customer_first=True),
                _notes_key(customer_id, note_name, customer_first=False),
            ],
        )
        return content.decode("utf-8", errors="replace")
    except KeyError:
        return None


def get_all_notes_text(store: ObjectStoreBase, customer_id: str) -> str:
    """
    Read and concatenate all notes for a customer into a single string.
    Each note is separated by a header line.
    Returns empty string if no notes exist.
    """
    notes = list_notes(store, customer_id)
    if not notes:
        return ""

    parts: list[str] = []
    for note in notes:
        try:
            content = _get_first_bytes(
                store,
                [
                    note["key"],
                    _notes_key(customer_id, note["name"], customer_first=True),
                ],
            ).decode("utf-8", errors="replace")
            parts.append(f"=== {note['name']} ===\n{content}\n")
        except KeyError:
            logger.warning("Note key not found: %s", note["key"])

    return "\n".join(parts)


# ── Versioned documents (POV / JEP) ──────────────────────────────────────────

def _get_next_version(store: ObjectStoreBase, doc_type: str, customer_id: str) -> int:
    """Return the next version number (1-based)."""
    manifest = _get_first_json(
        store,
        [
            _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=True),
            _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=False),
        ],
        {"versions": []},
    )
    return len(manifest.get("versions", [])) + 1


def get_latest_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> Optional[str]:
    """
    Read the latest version of a document.
    Returns None if no previous version exists.
    """
    keys = [
        _doc_key(doc_type, customer_id, "LATEST.md", customer_first=True),
        _doc_key(doc_type, customer_id, "LATEST.md", customer_first=False),
    ]
    try:
        return _get_first_bytes(store, keys).decode("utf-8", errors="replace")
    except KeyError:
        return None


def save_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
    content: str,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Save a document as a new version.

    Writes in order:
      1. {doc_type}/{customer_id}/v{n}.md   (versioned copy — fails safe if store is down)
      2. {doc_type}/{customer_id}/LATEST.md  (pointer to latest)
      3. {doc_type}/{customer_id}/MANIFEST.json (version history)

    Returns dict: {version, key, latest_key}
    """
    version = _get_next_version(store, doc_type, customer_id)
    content_bytes = content.encode("utf-8")

    version_key = _doc_key(doc_type, customer_id, f"v{version}.md", customer_first=False)
    version_customer_key = _doc_key(doc_type, customer_id, f"v{version}.md", customer_first=True)
    latest_key = _doc_key(doc_type, customer_id, "LATEST.md", customer_first=False)
    latest_customer_key = _doc_key(doc_type, customer_id, "LATEST.md", customer_first=True)
    manifest_key = _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=False)
    manifest_customer_key = _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=True)

    # 1. Versioned copy
    _put_dual(
        store,
        customer_key=version_customer_key,
        legacy_key=version_key,
        content=content_bytes,
        content_type="text/markdown",
    )

    # 2. LATEST pointer
    _put_dual(
        store,
        customer_key=latest_customer_key,
        legacy_key=latest_key,
        content=content_bytes,
        content_type="text/markdown",
    )

    # 3. Manifest
    manifest = _get_first_json(
        store,
        [manifest_customer_key, manifest_key],
        {"versions": []},
    )

    manifest["versions"].append({
        "version":   version,
        "key":       version_key,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata":  metadata or {},
    })
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=manifest_customer_key,
        legacy_key=manifest_key,
        content=manifest_bytes,
        content_type="application/json",
    )

    logger.info("%s saved: key=%s version=%d", doc_type, version_key, version)
    return {
        "version":    version,
        "key":        version_key,
        "latest_key": latest_key,
    }


def list_versions(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> list[dict]:
    """Return version history for a document type. Empty list if none."""
    manifest = _get_first_json(
        store,
        [
            _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=True),
            _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=False),
        ],
        {"versions": []},
    )
    return manifest.get("versions", [])


def merge_latest_doc_metadata(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
    metadata: Optional[dict] = None,
) -> bool:
    """
    Merge metadata into the latest version entry in {doc_type}/{customer_id}/MANIFEST.json.
    Returns True when a merge was applied, False otherwise.
    """
    if not metadata:
        return False
    manifest_key = _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=False)
    manifest_customer_key = _doc_key(doc_type, customer_id, "MANIFEST.json", customer_first=True)
    manifest = _get_first_json(store, [manifest_customer_key, manifest_key], None)
    if not manifest:
        return False
    versions = manifest.get("versions", [])
    if not versions:
        return False
    latest = versions[-1]
    existing = latest.get("metadata", {})
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(existing)
    merged.update(metadata)
    latest["metadata"] = merged
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=manifest_customer_key,
        legacy_key=manifest_key,
        content=manifest_bytes,
        content_type="application/json",
    )
    return True


# ── Approved versions ─────────────────────────────────────────────────────────

def save_approved_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
    content: str,
) -> str:
    """
    Save the SA-approved version of a document.

    Writes to: approved/{customer_id}/{doc_type}.md
    This file is NEVER overwritten by LLM generation — only by explicit SA upload.
    Returns the object key.
    """
    legacy_key = _approved_key(doc_type, customer_id, customer_first=False)
    customer_key = _approved_key(doc_type, customer_id, customer_first=True)
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=legacy_key,
        content=content.encode("utf-8"),
        content_type="text/markdown",
    )
    logger.info("Approved %s saved: key=%s customer_key=%s", doc_type, legacy_key, customer_key)
    return legacy_key


def get_approved_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> Optional[str]:
    """
    Read the SA-approved version of a document.
    Returns None if no approved version exists yet.
    """
    keys = [
        _approved_key(doc_type, customer_id, customer_first=True),
        _approved_key(doc_type, customer_id, customer_first=False),
    ]
    try:
        return _get_first_bytes(store, keys).decode("utf-8", errors="replace")
    except KeyError:
        return None


def get_best_base_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> Optional[str]:
    """
    Return the best base document for the next LLM generation run.

    Priority:
      1. approved/{customer_id}/{doc_type}.md  (SA-edited ground truth)
      2. {doc_type}/{customer_id}/LATEST.md    (last LLM-generated version)
      3. None                                  (first run)
    """
    approved = get_approved_doc(store, doc_type, customer_id)
    if approved is not None:
        logger.debug("Using approved %s as base for customer=%s", doc_type, customer_id)
        return approved
    return get_latest_doc(store, doc_type, customer_id)


# ── Feedback history ──────────────────────────────────────────────────────────

def append_feedback(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
    feedback_text: str,
    resulted_in_version: Optional[int] = None,
) -> None:
    """
    Append an SA feedback entry to the permanent feedback log.

    Stored at: {doc_type}/{customer_id}/feedback.json
    Format: [{"timestamp": ..., "feedback": ..., "resulted_in_version": N}, ...]
    """
    key = _doc_key(doc_type, customer_id, "feedback.json", customer_first=False)
    customer_key = _doc_key(doc_type, customer_id, "feedback.json", customer_first=True)
    entries = _get_first_json(store, [customer_key, key], [])

    entries.append({
        "timestamp":            datetime.now(timezone.utc).isoformat(),
        "feedback":             feedback_text,
        "resulted_in_version":  resulted_in_version,
    })
    entries_bytes = json.dumps(entries, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=key,
        content=entries_bytes,
        content_type="application/json",
    )
    logger.info("Feedback appended: %s customer=%s", doc_type, customer_id)


def get_feedback_history(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> list[dict]:
    """
    Return all SA feedback entries for a document type. Empty list if none.
    """
    return _get_first_json(
        store,
        [
            _doc_key(doc_type, customer_id, "feedback.json", customer_first=True),
            _doc_key(doc_type, customer_id, "feedback.json", customer_first=False),
        ],
        [],
    )


# ── Prompt logging ────────────────────────────────────────────────────────────

def save_prompt_log(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
    version: int,
    log: dict,
) -> None:
    """
    Save the LLM prompt and response metadata alongside a generated version.

    Stored at: {doc_type}/{customer_id}/v{n}_prompt_log.json
    Recommended log keys: timestamp, system_message, prompt, model_id,
                          max_tokens, temperature, response_length_chars.
    """
    key = _doc_key(doc_type, customer_id, f"v{version}_prompt_log.json", customer_first=False)
    customer_key = _doc_key(doc_type, customer_id, f"v{version}_prompt_log.json", customer_first=True)
    log.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    payload = json.dumps(log, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=key,
        content=payload,
        content_type="application/json",
    )
    logger.debug("Prompt log saved: %s", key)


# ── JEP POC questions ─────────────────────────────────────────────────────────

def save_jep_questions(
    store: ObjectStoreBase,
    customer_id: str,
    questions: list[dict],
    answers: Optional[dict] = None,
) -> str:
    """
    Save the POC clarifying questions (and optionally their answers).

    Stored at: jep/{customer_id}/poc_questions.json
    Format: {"questions": [...], "answers": {...}, "timestamp": ...}
    Returns the object key.
    """
    key = _doc_key("jep", customer_id, "poc_questions.json", customer_first=False)
    customer_key = _doc_key("jep", customer_id, "poc_questions.json", customer_first=True)
    # Merge with existing record if answers are being added separately
    try:
        existing = json.loads(_get_first_bytes(store, [customer_key, key]))
    except KeyError:
        existing = {}

    record = {
        **existing,
        "questions": questions,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if answers is not None:
        record["answers"] = answers

    record_bytes = json.dumps(record, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=key,
        content=record_bytes,
        content_type="application/json",
    )
    logger.info("JEP POC questions saved: customer=%s", customer_id)
    return key


def get_jep_questions(
    store: ObjectStoreBase,
    customer_id: str,
) -> dict:
    """
    Return the stored POC questions and answers for a customer.
    Returns {} if none exist yet.
    """
    return _get_first_json(
        store,
        [
            _doc_key("jep", customer_id, "poc_questions.json", customer_first=True),
            _doc_key("jep", customer_id, "poc_questions.json", customer_first=False),
        ],
        {},
    )


# ── Conversation history (orchestrator) ──────────────────────────────────────

def save_conversation_turns(
    store: ObjectStoreBase,
    customer_id: str,
    new_turns: list[dict],
) -> None:
    """
    Append new_turns to conversations/{customer_id}/history.json.
    Creates the file if it doesn't exist.
    """
    key = _conversation_key(customer_id, "history.json", customer_first=False)
    customer_key = _conversation_key(customer_id, "history.json", customer_first=True)
    history = _get_first_json(store, [customer_key, key], [])
    history.extend(new_turns)
    payload = json.dumps(history, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=key,
        content=payload,
        content_type="application/json",
    )


def load_conversation_history(
    store: ObjectStoreBase,
    customer_id: str,
    max_turns: int = 30,
) -> list[dict]:
    """
    Return the last max_turns from conversations/{customer_id}/history.json.
    Returns [] if no history exists.
    """
    history = _get_first_json(
        store,
        [
            _conversation_key(customer_id, "history.json", customer_first=True),
            _conversation_key(customer_id, "history.json", customer_first=False),
        ],
        [],
    )
    return history[-max_turns:] if max_turns else history


def clear_conversation_history(
    store: ObjectStoreBase,
    customer_id: str,
) -> None:
    """Overwrite history with an empty list (effectively clears the conversation)."""
    key = _conversation_key(customer_id, "history.json", customer_first=False)
    customer_key = _conversation_key(customer_id, "history.json", customer_first=True)
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=key,
        content=b"[]",
        content_type="application/json",
    )


def save_conversation_summary(
    store: ObjectStoreBase,
    customer_id: str,
    summary: str,
) -> None:
    """Write a rolling summary of older turns to conversations/{customer_id}/summary.txt."""
    key = _conversation_key(customer_id, "summary.txt", customer_first=False)
    customer_key = _conversation_key(customer_id, "summary.txt", customer_first=True)
    _put_dual(
        store,
        customer_key=customer_key,
        legacy_key=key,
        content=summary.encode("utf-8"),
        content_type="text/plain",
    )


def load_conversation_summary(
    store: ObjectStoreBase,
    customer_id: str,
) -> str:
    """Return the stored rolling summary, or '' if none exists."""
    try:
        return _get_first_bytes(
            store,
            [
                _conversation_key(customer_id, "summary.txt", customer_first=True),
                _conversation_key(customer_id, "summary.txt", customer_first=False),
            ],
        ).decode("utf-8", errors="replace")
    except KeyError:
        return ""


# ── Projects / engagement index ───────────────────────────────────────────────

def normalize_project_id(project_name: str, fallback_customer_id: str = "") -> str:
    """
    Return a stable, URL/path-safe project id derived from a project name.
    Falls back to the customer/engagement id when the name has no slug content.
    """
    source = (project_name or "").strip() or (fallback_customer_id or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    return slug or (fallback_customer_id or "project").strip() or "project"


def _load_project_engagement_index(store: ObjectStoreBase) -> dict[str, dict]:
    """
    Return project engagement metadata keyed by existing customer_id.
    """
    by_customer: dict[str, dict] = {}
    for key in store.list("projects/"):
        if "/engagements/" not in key or not key.endswith(".json"):
            continue
        try:
            record = json.loads(store.get(key))
        except Exception:
            logger.warning("Skipping invalid project engagement metadata: %s", key)
            continue
        if not isinstance(record, dict):
            continue
        customer_id = str(record.get("customer_id") or record.get("engagement_id") or "").strip()
        project_id = str(record.get("project_id") or "").strip()
        if not customer_id or not project_id:
            continue
        by_customer[customer_id] = record
    return by_customer


def save_project_engagement(
    store: ObjectStoreBase,
    *,
    customer_id: str,
    customer_name: str = "",
    project_id: str = "",
    project_name: str = "",
) -> dict:
    """
    Save lightweight membership metadata for an engagement without moving artifacts.

    A project is the customer/account. The existing customer_id remains the
    engagement/thread id used by all current APIs.
    """
    customer_id = (customer_id or "").strip()
    customer_name = (customer_name or "").strip()
    resolved_project_name = (project_name or "").strip() or customer_name or customer_id
    resolved_project_id = normalize_project_id(project_id or resolved_project_name, customer_id)
    now = datetime.now(timezone.utc).isoformat()

    project_key = _project_key(resolved_project_id, "project.json")
    project_doc = _get_first_json(store, [project_key], {})
    if not isinstance(project_doc, dict):
        project_doc = {}
    project_doc.update({
        "project_id": resolved_project_id,
        "project_name": resolved_project_name,
        "updated_at": now,
    })
    project_doc.setdefault("created_at", now)
    store.put(
        project_key,
        json.dumps(project_doc, indent=2).encode("utf-8"),
        "application/json",
    )

    engagement_key = _project_key(resolved_project_id, f"engagements/{customer_id}.json")
    engagement_doc = _get_first_json(store, [engagement_key], {})
    if not isinstance(engagement_doc, dict):
        engagement_doc = {}
    engagement_doc.update({
        "project_id": resolved_project_id,
        "project_name": resolved_project_name,
        "engagement_id": customer_id,
        "customer_id": customer_id,
        "customer_name": customer_name or resolved_project_name,
        "updated_at": now,
    })
    engagement_doc.setdefault("created_at", now)
    store.put(
        engagement_key,
        json.dumps(engagement_doc, indent=2).encode("utf-8"),
        "application/json",
    )
    return engagement_doc


# ── Terraform bundles ──────────────────────────────────────────────────────────

def save_terraform_bundle(
    store: ObjectStoreBase,
    customer_id: str,
    files: dict[str, str],
    metadata: Optional[dict] = None,
) -> dict:
    """
    Save a versioned Terraform bundle.

    Writes:
      terraform/{customer_id}/v{n}/{filename}
      terraform/{customer_id}/v{n}/manifest.json
      terraform/{customer_id}/LATEST.json
      terraform/{customer_id}/MANIFEST.json
    """
    version = _get_next_version(store, "terraform", customer_id)
    base = _doc_key("terraform", customer_id, f"v{version}", customer_first=False)
    base_customer = _doc_key("terraform", customer_id, f"v{version}", customer_first=True)
    file_keys: dict[str, str] = {}

    for filename, content in files.items():
        key = f"{base}/{filename}"
        customer_key = f"{base_customer}/{filename}"
        _put_dual(
            store,
            customer_key=customer_key,
            legacy_key=key,
            content=content.encode("utf-8"),
            content_type="text/plain",
        )
        file_keys[filename] = key  # keep legacy key shape for compatibility

    manifest_doc = {
        "version": version,
        "files": file_keys,
        "metadata": metadata or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    version_manifest_key = f"{base}/manifest.json"
    store.put(
        version_manifest_key,
        json.dumps(manifest_doc, indent=2).encode("utf-8"),
        "application/json",
    )

    latest_key = _doc_key("terraform", customer_id, "LATEST.json", customer_first=False)
    latest_customer_key = _doc_key("terraform", customer_id, "LATEST.json", customer_first=True)
    manifest_doc_bytes = json.dumps(manifest_doc, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=latest_customer_key,
        legacy_key=latest_key,
        content=manifest_doc_bytes,
        content_type="application/json",
    )

    manifest_key = _doc_key("terraform", customer_id, "MANIFEST.json", customer_first=False)
    manifest_customer_key = _doc_key("terraform", customer_id, "MANIFEST.json", customer_first=True)
    root_manifest = _get_first_json(store, [manifest_customer_key, manifest_key], {"versions": []})
    root_manifest["versions"].append(
        {
            "version": version,
            "key": version_manifest_key,
            "timestamp": manifest_doc["timestamp"],
            "metadata": metadata or {},
        }
    )
    root_manifest_bytes = json.dumps(root_manifest, indent=2).encode("utf-8")
    _put_dual(
        store,
        customer_key=manifest_customer_key,
        legacy_key=manifest_key,
        content=root_manifest_bytes,
        content_type="application/json",
    )

    return {
        "version": version,
        "key": version_manifest_key,
        "latest_key": latest_key,
        "files": file_keys,
    }


def get_latest_terraform_bundle(store: ObjectStoreBase, customer_id: str) -> Optional[dict]:
    data = _get_first_json(
        store,
        [
            _doc_key("terraform", customer_id, "LATEST.json", customer_first=True),
            _doc_key("terraform", customer_id, "LATEST.json", customer_first=False),
        ],
        None,
    )
    return data if isinstance(data, dict) else None


def list_terraform_versions(store: ObjectStoreBase, customer_id: str) -> list[dict]:
    return list_versions(store, "terraform", customer_id)


def get_terraform_file(store: ObjectStoreBase, customer_id: str, filename: str) -> Optional[bytes]:
    latest = get_latest_terraform_bundle(store, customer_id)
    if not latest:
        return None
    key = latest.get("files", {}).get(filename)
    if not key:
        return None
    try:
        return store.get(key)
    except KeyError:
        return None


def merge_latest_terraform_metadata(
    store: ObjectStoreBase,
    customer_id: str,
    metadata: Optional[dict] = None,
) -> bool:
    """
    Merge metadata into latest Terraform records:
      - terraform/{customer_id}/LATEST.json
      - terraform/{customer_id}/MANIFEST.json latest version entry
    Returns True when at least one merge succeeded.
    """
    if not metadata:
        return False
    merged_any = False

    latest_key = _doc_key("terraform", customer_id, "LATEST.json", customer_first=False)
    latest_customer_key = _doc_key("terraform", customer_id, "LATEST.json", customer_first=True)
    try:
        latest = json.loads(_get_first_bytes(store, [latest_customer_key, latest_key]))
        latest_meta = latest.get("metadata", {})
        if not isinstance(latest_meta, dict):
            latest_meta = {}
        new_meta = dict(latest_meta)
        new_meta.update(metadata)
        latest["metadata"] = new_meta
        latest_bytes = json.dumps(latest, indent=2).encode("utf-8")
        _put_dual(
            store,
            customer_key=latest_customer_key,
            legacy_key=latest_key,
            content=latest_bytes,
            content_type="application/json",
        )
        merged_any = True
    except KeyError:
        pass

    manifest_key = _doc_key("terraform", customer_id, "MANIFEST.json", customer_first=False)
    manifest_customer_key = _doc_key("terraform", customer_id, "MANIFEST.json", customer_first=True)
    try:
        manifest = json.loads(_get_first_bytes(store, [manifest_customer_key, manifest_key]))
        versions = manifest.get("versions", [])
        if versions:
            latest_version = versions[-1]
            existing = latest_version.get("metadata", {})
            if not isinstance(existing, dict):
                existing = {}
            updated = dict(existing)
            updated.update(metadata)
            latest_version["metadata"] = updated
            manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
            _put_dual(
                store,
                customer_key=manifest_customer_key,
                legacy_key=manifest_key,
                content=manifest_bytes,
                content_type="application/json",
            )
            merged_any = True
    except KeyError:
        pass

    return merged_any


def list_conversation_customers(store: ObjectStoreBase) -> list[str]:
    """
    Return sorted customer_ids that have persisted conversation history.
    """
    keys = store.list("conversations/") + store.list("customers/")
    customer_ids: set[str] = set()
    for key in keys:
        if not key.endswith("/history.json"):
            continue
        parts = key.split("/")
        # conversations/{customer_id}/history.json
        if len(parts) >= 3 and parts[0] == "conversations":
            customer_ids.add(parts[1])
        # customers/{customer_id}/conversations/history.json
        if len(parts) >= 4 and parts[0] == "customers" and parts[2] == "conversations":
            customer_ids.add(parts[1])
    return sorted(customer_ids)


def _build_conversation_summary(
    store: ObjectStoreBase,
    customer_id: str,
    project_index: Optional[dict[str, dict]] = None,
) -> dict | None:
    history = load_conversation_history(store, customer_id, max_turns=0)
    if not history:
        return None

    last_turn = history[-1]
    last_ts = last_turn.get("timestamp", "")
    customer_name = ""
    last_preview = ""
    status = "In Progress"
    project_id = ""
    project_name = ""

    for turn in reversed(history):
        if not customer_name and turn.get("role") == "user":
            customer_name = turn.get("customer_name", "") or ""
        if not project_id:
            project_id = str(turn.get("project_id", "") or "").strip()
        if not project_name:
            project_name = str(turn.get("project_name", "") or "").strip()
        if not last_preview and turn.get("content"):
            text = str(turn.get("content", "")).strip()
            if text:
                last_preview = text.replace("\n", " ")[:160]
        if turn.get("tool") == "generate_terraform":
            summary = str(turn.get("result_summary", "")).lower()
            if "blocked" in summary or "clarification" in summary:
                status = "Terraform Needs Input"
            else:
                status = "Completed with Terraform"
        if customer_name and project_id and project_name and last_preview and status != "In Progress":
            break

    project_meta = (project_index or {}).get(customer_id, {})
    if project_meta:
        project_id = str(project_meta.get("project_id") or project_id or "").strip()
        project_name = str(project_meta.get("project_name") or project_name or "").strip()
        customer_name = str(project_meta.get("customer_name") or customer_name or "").strip()

    customer_name = customer_name or customer_id
    project_name = project_name or customer_name or customer_id
    project_id = project_id or normalize_project_id(project_name, customer_id)

    return {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "engagement_id": customer_id,
        "project_id": project_id,
        "project_name": project_name,
        "last_message_preview": last_preview,
        "last_activity_timestamp": last_ts,
        "status": status,
    }


def list_conversation_summaries(
    store: ObjectStoreBase,
    *,
    search: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    Return paginated conversation summaries across customers.
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20

    search_lc = search.strip().lower()
    summaries: list[dict] = []
    project_index = _load_project_engagement_index(store)

    for customer_id in list_conversation_customers(store):
        summary = _build_conversation_summary(store, customer_id, project_index)
        if not summary:
            continue

        haystack = " ".join([
            str(summary.get("customer_id", "")),
            str(summary.get("customer_name", "")),
            str(summary.get("engagement_id", "")),
            str(summary.get("project_id", "")),
            str(summary.get("project_name", "")),
            str(summary.get("last_message_preview", "")),
        ]).lower()
        if search_lc and search_lc not in haystack:
            continue

        summaries.append(summary)

    summaries.sort(key=lambda item: item.get("last_activity_timestamp", ""), reverse=True)
    total = len(summaries)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": summaries[start:end],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_next": end < total,
        },
    }


def list_project_summaries(
    store: ObjectStoreBase,
    *,
    search: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    Return projects grouped from conversation history and project metadata.
    Legacy histories without project metadata are grouped by customer_name,
    falling back to customer_id.
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20

    search_lc = search.strip().lower()
    project_index = _load_project_engagement_index(store)
    projects: dict[str, dict] = {}

    for customer_id in list_conversation_customers(store):
        engagement = _build_conversation_summary(store, customer_id, project_index)
        if not engagement:
            continue
        project_id = str(engagement["project_id"])
        project = projects.setdefault(
            project_id,
            {
                "project_id": project_id,
                "project_name": engagement["project_name"],
                "last_activity_timestamp": "",
                "engagement_count": 0,
                "engagements": [],
            },
        )
        if len(str(engagement.get("project_name", ""))) > len(str(project.get("project_name", ""))):
            project["project_name"] = engagement["project_name"]
        project["engagements"].append(engagement)
        project["engagement_count"] = len(project["engagements"])
        if str(engagement.get("last_activity_timestamp", "")) > str(project.get("last_activity_timestamp", "")):
            project["last_activity_timestamp"] = engagement.get("last_activity_timestamp", "")

    for project in projects.values():
        project["engagements"].sort(
            key=lambda item: item.get("last_activity_timestamp", ""),
            reverse=True,
        )
        first = project["engagements"][0] if project["engagements"] else {}
        project["last_message_preview"] = first.get("last_message_preview", "")
        project["status"] = first.get("status", "In Progress")

    project_list = list(projects.values())
    if search_lc:
        filtered: list[dict] = []
        for project in project_list:
            engagement_haystack = " ".join(
                " ".join([
                    str(item.get("customer_id", "")),
                    str(item.get("customer_name", "")),
                    str(item.get("engagement_id", "")),
                    str(item.get("last_message_preview", "")),
                ])
                for item in project.get("engagements", [])
            )
            haystack = " ".join([
                str(project.get("project_id", "")),
                str(project.get("project_name", "")),
                str(project.get("last_message_preview", "")),
                engagement_haystack,
            ]).lower()
            if search_lc in haystack:
                filtered.append(project)
        project_list = filtered

    project_list.sort(key=lambda item: item.get("last_activity_timestamp", ""), reverse=True)
    total = len(project_list)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": project_list[start:end],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_next": end < total,
        },
    }
