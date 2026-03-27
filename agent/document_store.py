"""
agent/document_store.py
------------------------
Versioned document and notes storage helpers for writing agents.

Bucket layout (all paths are relative to the root bucket):

  notes/{customer_id}/{note_name}        — individual meeting notes
  notes/{customer_id}/MANIFEST.json      — list of all notes with timestamps

  pov/{customer_id}/v{n}.md              — POV versions
  pov/{customer_id}/LATEST.md            — latest POV content
  pov/{customer_id}/MANIFEST.json        — version history

  jep/{customer_id}/v{n}.md              — JEP versions
  jep/{customer_id}/LATEST.md            — latest JEP content
  jep/{customer_id}/MANIFEST.json        — version history

Atomicity: versioned copy is written first; LATEST.md and MANIFEST.json
are written only after the versioned copy succeeds.
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
