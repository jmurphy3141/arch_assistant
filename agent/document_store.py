"""
agent/document_store.py
------------------------
Versioned document and notes storage helpers for writing agents.

Bucket layout (all paths are relative to the root bucket):

  notes/{customer_id}/{note_name}            — individual meeting notes
  notes/{customer_id}/MANIFEST.json          — list of all notes with timestamps

  pov/{customer_id}/v{n}.md                  — POV versions
  pov/{customer_id}/LATEST.md                — latest LLM-generated content
  pov/{customer_id}/MANIFEST.json            — version history
  pov/{customer_id}/v{n}_prompt_log.json     — LLM prompt/response log per version
  pov/{customer_id}/feedback.json            — append-only SA feedback history

  jep/{customer_id}/v{n}.md                  — JEP versions
  jep/{customer_id}/LATEST.md
  jep/{customer_id}/MANIFEST.json
  jep/{customer_id}/v{n}_prompt_log.json
  jep/{customer_id}/feedback.json
  jep/{customer_id}/poc_questions.json       — Q&A from JEP kickoff

  approved/{customer_id}/pov.md              — SA-uploaded approved POV (source of truth)
  approved/{customer_id}/jep.md              — SA-uploaded approved JEP

Atomicity: versioned copy is written first; LATEST.md and MANIFEST.json
are written only after the versioned copy succeeds.
Approved versions are NEVER overwritten by LLM generation.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)


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
    key = f"notes/{customer_id}/{note_name}"
    store.put(key, content, content_type)

    manifest_key = f"notes/{customer_id}/MANIFEST.json"
    try:
        manifest = json.loads(store.get(manifest_key))
    except KeyError:
        manifest = {"notes": []}

    # Upsert by key (re-uploading the same note name replaces the entry)
    manifest["notes"] = [n for n in manifest["notes"] if n["key"] != key]
    manifest["notes"].append({
        "key": key,
        "name": note_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    store.put(
        manifest_key,
        json.dumps(manifest, indent=2).encode("utf-8"),
        "application/json",
    )
    logger.info("Note saved: key=%s", key)
    return key


def list_notes(store: ObjectStoreBase, customer_id: str) -> list[dict]:
    """Return list of note metadata dicts for a customer. Empty list if none."""
    manifest_key = f"notes/{customer_id}/MANIFEST.json"
    try:
        return json.loads(store.get(manifest_key)).get("notes", [])
    except KeyError:
        return []


def get_note(store: ObjectStoreBase, customer_id: str, note_name: str) -> Optional[str]:
    """Read a single note by name. Returns None if not found."""
    key = f"notes/{customer_id}/{note_name}"
    try:
        return store.get(key).decode("utf-8", errors="replace")
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
            content = store.get(note["key"]).decode("utf-8", errors="replace")
            parts.append(f"=== {note['name']} ===\n{content}\n")
        except KeyError:
            logger.warning("Note key not found: %s", note["key"])

    return "\n".join(parts)


# ── Versioned documents (POV / JEP) ──────────────────────────────────────────

def _get_next_version(store: ObjectStoreBase, doc_type: str, customer_id: str) -> int:
    """Return the next version number (1-based)."""
    manifest_key = f"{doc_type}/{customer_id}/MANIFEST.json"
    try:
        manifest = json.loads(store.get(manifest_key))
        return len(manifest.get("versions", [])) + 1
    except KeyError:
        return 1


def get_latest_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> Optional[str]:
    """
    Read the latest version of a document.
    Returns None if no previous version exists.
    """
    key = f"{doc_type}/{customer_id}/LATEST.md"
    try:
        return store.get(key).decode("utf-8", errors="replace")
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

    version_key = f"{doc_type}/{customer_id}/v{version}.md"
    latest_key  = f"{doc_type}/{customer_id}/LATEST.md"
    manifest_key = f"{doc_type}/{customer_id}/MANIFEST.json"

    # 1. Versioned copy
    store.put(version_key, content_bytes, "text/markdown")

    # 2. LATEST pointer
    store.put(latest_key, content_bytes, "text/markdown")

    # 3. Manifest
    try:
        manifest = json.loads(store.get(manifest_key))
    except KeyError:
        manifest = {"versions": []}

    manifest["versions"].append({
        "version":   version,
        "key":       version_key,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata":  metadata or {},
    })
    store.put(
        manifest_key,
        json.dumps(manifest, indent=2).encode("utf-8"),
        "application/json",
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
    manifest_key = f"{doc_type}/{customer_id}/MANIFEST.json"
    try:
        return json.loads(store.get(manifest_key)).get("versions", [])
    except KeyError:
        return []


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
    key = f"approved/{customer_id}/{doc_type}.md"
    store.put(key, content.encode("utf-8"), "text/markdown")
    logger.info("Approved %s saved: key=%s", doc_type, key)
    return key


def get_approved_doc(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> Optional[str]:
    """
    Read the SA-approved version of a document.
    Returns None if no approved version exists yet.
    """
    key = f"approved/{customer_id}/{doc_type}.md"
    try:
        return store.get(key).decode("utf-8", errors="replace")
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
    key = f"{doc_type}/{customer_id}/feedback.json"
    try:
        entries: list = json.loads(store.get(key))
    except KeyError:
        entries = []

    entries.append({
        "timestamp":            datetime.now(timezone.utc).isoformat(),
        "feedback":             feedback_text,
        "resulted_in_version":  resulted_in_version,
    })
    store.put(key, json.dumps(entries, indent=2).encode("utf-8"), "application/json")
    logger.info("Feedback appended: %s customer=%s", doc_type, customer_id)


def get_feedback_history(
    store: ObjectStoreBase,
    doc_type: str,
    customer_id: str,
) -> list[dict]:
    """
    Return all SA feedback entries for a document type. Empty list if none.
    """
    key = f"{doc_type}/{customer_id}/feedback.json"
    try:
        return json.loads(store.get(key))
    except KeyError:
        return []


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
    key = f"{doc_type}/{customer_id}/v{version}_prompt_log.json"
    log.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    store.put(key, json.dumps(log, indent=2).encode("utf-8"), "application/json")
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
    key = f"jep/{customer_id}/poc_questions.json"
    # Merge with existing record if answers are being added separately
    try:
        existing = json.loads(store.get(key))
    except KeyError:
        existing = {}

    record = {
        **existing,
        "questions": questions,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if answers is not None:
        record["answers"] = answers

    store.put(key, json.dumps(record, indent=2).encode("utf-8"), "application/json")
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
    key = f"jep/{customer_id}/poc_questions.json"
    try:
        return json.loads(store.get(key))
    except KeyError:
        return {}


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
    key = f"conversations/{customer_id}/history.json"
    try:
        history: list = json.loads(store.get(key))
    except KeyError:
        history = []
    history.extend(new_turns)
    store.put(key, json.dumps(history, indent=2).encode("utf-8"), "application/json")


def load_conversation_history(
    store: ObjectStoreBase,
    customer_id: str,
    max_turns: int = 30,
) -> list[dict]:
    """
    Return the last max_turns from conversations/{customer_id}/history.json.
    Returns [] if no history exists.
    """
    key = f"conversations/{customer_id}/history.json"
    try:
        history: list = json.loads(store.get(key))
        return history[-max_turns:] if max_turns else history
    except KeyError:
        return []


def clear_conversation_history(
    store: ObjectStoreBase,
    customer_id: str,
) -> None:
    """Overwrite history with an empty list (effectively clears the conversation)."""
    key = f"conversations/{customer_id}/history.json"
    store.put(key, b"[]", "application/json")


def save_conversation_summary(
    store: ObjectStoreBase,
    customer_id: str,
    summary: str,
) -> None:
    """Write a rolling summary of older turns to conversations/{customer_id}/summary.txt."""
    key = f"conversations/{customer_id}/summary.txt"
    store.put(key, summary.encode("utf-8"), "text/plain")


def load_conversation_summary(
    store: ObjectStoreBase,
    customer_id: str,
) -> str:
    """Return the stored rolling summary, or '' if none exists."""
    key = f"conversations/{customer_id}/summary.txt"
    try:
        return store.get(key).decode("utf-8", errors="replace")
    except KeyError:
        return ""
