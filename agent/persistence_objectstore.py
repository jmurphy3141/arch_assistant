"""
agent/persistence_objectstore.py
----------------------------------
Mockable OCI Object Storage persistence interface.

Production use: swap InMemoryObjectStore for a real OCI ObjectStorageClient wrapper.
Tests use InMemoryObjectStore directly (injectable via app.state.object_store).

Atomicity contract:
  persist_artifacts() uploads all artifact keys first; only on full success does
  it write/update LATEST.json.  If any artifact put() raises, LATEST.json is
  NOT touched and the function returns None.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


# ── Abstract interface ─────────────────────────────────────────────────────────

class ObjectStoreBase(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        ...

    @abstractmethod
    def head(self, key: str) -> bool:
        """Return True if key exists, False otherwise."""
        ...


# ── In-memory fake (used in tests + default when no OCI available) ─────────────

class InMemoryObjectStore(ObjectStoreBase):
    """
    Thread-unsafe in-memory object store.
    Supports failure injection for testing atomicity guarantees.
    """

    def __init__(self):
        self._store: dict[str, bytes] = {}
        self._fail_suffixes: list[str] = []   # keys ending with these raise on put

    # ── Core interface ─────────────────────────────────────────────────────────

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        for suffix in self._fail_suffixes:
            if key.endswith(suffix):
                raise RuntimeError(f"Injected put failure: key ends with '{suffix}'")
        self._store[key] = data

    def get(self, key: str) -> bytes:
        if key not in self._store:
            raise KeyError(f"Object key not found: {key}")
        return self._store[key]

    def head(self, key: str) -> bool:
        return key in self._store

    # ── Test helpers ───────────────────────────────────────────────────────────

    def inject_put_failure(self, key_suffix: str) -> None:
        """
        Cause the next put() whose key ends with key_suffix to raise RuntimeError.
        Example: inject_put_failure("/render_manifest.json")
        """
        self._fail_suffixes.append(key_suffix)

    def list_keys(self) -> list[str]:
        return list(self._store.keys())

    def clear_failures(self) -> None:
        self._fail_suffixes.clear()


# ── Persistence helper ─────────────────────────────────────────────────────────

LATEST_JSON_SCHEMA_VERSION = "1.1"

# Artifacts that may be fetched via the download endpoint
ARTIFACT_ALLOWLIST = frozenset({
    "diagram.drawio",
    "spec.json",
    "draw_dict.json",
    "render_manifest.json",
    "node_to_resource_map.json",
})


def persist_artifacts(
    store: ObjectStoreBase,
    prefix: str,
    client_id: str,
    diagram_name: str,
    artifacts: dict[str, bytes],
) -> Optional[dict]:
    """
    Upload artifacts to:
        {prefix}/{client_id}/{diagram_name}/v{N}/<filename>

    N is auto-incremented by reading the current version from LATEST.json.
    First upload → v1, second → v2, etc.

    Then (only if ALL uploads succeed) write LATEST.json to:
        {prefix}/{client_id}/{diagram_name}/LATEST.json

    LATEST.json schema (schema_version "1.1"):
    {
        "schema_version": "1.1",
        "version": 2,
        "artifacts": {
            "diagram.drawio": "{prefix}/{client_id}/{diagram_name}/v2/diagram.drawio",
            ...
        }
    }

    Returns the LATEST.json dict on success, None if any artifact upload failed.
    """
    latest_key = f"{prefix}/{client_id}/{diagram_name}/LATEST.json"

    # Determine next version number
    version = 1
    if store.head(latest_key):
        try:
            existing = json.loads(store.get(latest_key))
            version = int(existing.get("version", 0)) + 1
        except Exception:
            pass  # corrupt LATEST.json — start at v1

    version_str = f"v{version}"
    base = f"{prefix}/{client_id}/{diagram_name}/{version_str}"
    artifact_keys: dict[str, str] = {}

    try:
        for filename, data in artifacts.items():
            key = f"{base}/{filename}"
            content_type = (
                "text/xml" if filename.endswith(".drawio") else "application/json"
            )
            store.put(key, data, content_type)
            artifact_keys[filename] = key
    except Exception as exc:
        logger.error("persist_artifacts failed uploading to %r: %s", base, exc, exc_info=True)
        return None

    # All artifacts succeeded — atomically update LATEST.json
    latest = {
        "schema_version": LATEST_JSON_SCHEMA_VERSION,
        "version": version,
        "artifacts": artifact_keys,
    }
    store.put(latest_key, json.dumps(latest, indent=2).encode("utf-8"), "application/json")
    return latest
