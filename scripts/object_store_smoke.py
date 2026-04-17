#!/usr/bin/env python3
"""
scripts/object_store_smoke.py
------------------------------
Manual smoke test for the OCI Object Storage backend.

Run on OCI Compute with an Instance Principal attached to the correct
dynamic group (read/write policy on the target bucket):

    python scripts/object_store_smoke.py

Exit codes:
  0 — all operations succeeded
  1 — any failure
"""
import sys
import datetime

# Add repo root to path when invoked directly
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

import yaml
from agent.object_store_oci import OciObjectStore

_CFG_PATH = __import__("pathlib").Path(__file__).parent.parent / "config.yaml"


def main() -> int:
    with open(_CFG_PATH) as f:
        cfg = yaml.safe_load(f)

    per = cfg.get("persistence", {})
    if not per.get("enabled"):
        print("SKIP: persistence.enabled is false in config.yaml")
        return 0
    if per.get("backend") != "oci_object_storage":
        print(f"SKIP: persistence.backend={per.get('backend')!r} (expected 'oci_object_storage')")
        return 0

    store = OciObjectStore(
        region=per["region"],
        namespace=per["namespace"],
        bucket_name=per["bucket_name"],
    )
    print(f"Store: {store!r}")

    prefix    = per.get("prefix", "agent3")
    ts        = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    test_key  = f"{prefix}/smoke/{ts}/hello.txt"
    test_data = f"smoke test at {ts}\n".encode()

    # ── Write ────────────────────────────────────────────────────────────────
    print(f"PUT  {test_key} ({len(test_data)} bytes) … ", end="", flush=True)
    try:
        store.put(test_key, test_data, "text/plain")
        print("ok")
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1

    # ── Head ─────────────────────────────────────────────────────────────────
    print(f"HEAD {test_key} … ", end="", flush=True)
    try:
        exists = store.head(test_key)
        if not exists:
            print("FAILED: head returned False after put")
            return 1
        print("ok")
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1

    # ── Read back ────────────────────────────────────────────────────────────
    print(f"GET  {test_key} … ", end="", flush=True)
    try:
        got = store.get(test_key)
        if got != test_data:
            print(f"FAILED: content mismatch — got {got!r}")
            return 1
        print("ok")
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1

    print("\nObject Storage smoke test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
