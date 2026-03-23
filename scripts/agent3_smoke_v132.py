#!/usr/bin/env python3
"""
OCI Drawing Agent v1.3.2 — end-to-end smoke test.

Usage
-----
    python scripts/agent3_smoke_v132.py [--host http://localhost:8080] [--out ./evidence_dir]

Exit codes
----------
    0  all checks passed
    1  one or more checks failed
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AGENT_VERSION = "1.3.2"

REQUIRED_ENVELOPE_FIELDS = [
    "status",
    "request_id",
    "input_hash",
    "drawio_xml",
    "spec",
    "draw_dict",
    "render_manifest",
    "node_to_resource_map",
    "errors",
]

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)

# Minimal /generate payload — uses oci_type fields + questionnaire + notes
GENERATE_PAYLOAD = {
    "resources": [
        {"id": "igw_1",     "oci_type": "internet_gateway",   "label": "Internet Gateway"},
        {"id": "lb_1",      "oci_type": "load_balancer",      "label": "Load Balancer"},
        {"id": "compute_1", "oci_type": "compute",            "label": "App Server"},
        {"id": "db_1",      "oci_type": "database",           "label": "Autonomous DB"},
    ],
    "questionnaire": "Single region. Active-passive HA. No DR required.",
    "notes": "Smoke test run by agent3_smoke_v132.py. No real BOM.",
    "diagram_name": "smoke_test",
    "client_id": "smoke_client",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(method: str, url: str, body: bytes | None = None,
             headers: dict | None = None) -> tuple[int, bytes]:
    """Execute an HTTP request; return (status_code, body_bytes)."""
    req = urllib.request.Request(url, data=body, method=method,
                                  headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _save(evidence_dir: Path, filename: str, data: bytes | str) -> Path:
    path = evidence_dir / filename
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)
    return path


def _check(name: str, condition: bool, details: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    msg = f"  [{mark}] {name}"
    if details:
        msg += f" — {details}"
    print(msg)
    return condition


# ---------------------------------------------------------------------------
# Individual test steps
# ---------------------------------------------------------------------------

def check_health(host: str, evidence_dir: Path) -> bool:
    print("\n--- GET /health ---")
    url = f"{host}/health"
    code, body = _request("GET", url)
    _save(evidence_dir, "health.json", body)

    ok = True
    ok &= _check("HTTP 200", code == 200, f"got {code}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        _check("valid JSON", False, str(exc))
        return False

    ok &= _check("status=ok", data.get("status") == "ok", repr(data.get("status")))
    ok &= _check("agent_version present", "agent_version" in data)
    return ok


def call_generate(host: str, payload: dict, evidence_dir: Path,
                  out_filename: str) -> tuple[bool, dict]:
    """POST /generate; return (ok, parsed_json)."""
    body = json.dumps(payload).encode()
    code, resp_bytes = _request(
        "POST", f"{host}/generate", body=body,
        headers={"Content-Type": "application/json"},
    )
    _save(evidence_dir, out_filename, resp_bytes)

    if code not in (200, 202):
        _check("HTTP 2xx", False, f"got {code}")
        try:
            err = json.loads(resp_bytes)
            print(f"    response: {json.dumps(err, indent=2)[:400]}")
        except Exception:
            pass
        return False, {}

    try:
        data = json.loads(resp_bytes)
    except json.JSONDecodeError as exc:
        _check("valid JSON", False, str(exc))
        return False, {}

    return True, data


def check_generate_envelope(data: dict) -> bool:
    """Validate v1.3.2 envelope fields on a /generate response."""
    ok = True
    ok &= _check("status=ok", data.get("status") == "ok",
                 f"got {data.get('status')!r}")

    for field in REQUIRED_ENVELOPE_FIELDS:
        ok &= _check(f"field '{field}' present", field in data)

    # Validate download sub-object
    download = data.get("download", {})
    ok &= _check("download.url present",
                 bool(download.get("url")), repr(download.get("url")))
    ok &= _check("download.object_storage_latest present",
                 bool(download.get("object_storage_latest")),
                 repr(download.get("object_storage_latest")))

    # Validate UUID v4
    rid = data.get("request_id", "")
    ok &= _check("request_id is UUIDv4", bool(UUID4_RE.match(rid)), repr(rid))

    # Validate sha256
    ih = data.get("input_hash", "")
    ok &= _check("input_hash is sha256", bool(SHA256_RE.match(ih)), repr(ih))

    return ok


def check_download(host: str, download_url: str, evidence_dir: Path) -> bool:
    """Download the diagram via the URL returned in the envelope."""
    print(f"\n--- GET {download_url} ---")
    full_url = f"{host}{download_url}" if download_url.startswith("/") else download_url
    code, body = _request("GET", full_url)
    _save(evidence_dir, "diagram.drawio", body)

    ok = True
    ok &= _check("HTTP 200", code == 200, f"got {code}")
    ok &= _check("non-empty body", len(body) > 0, f"{len(body)} bytes")
    if code == 200:
        ok &= _check("looks like draw.io XML",
                     b"<mxGraphModel" in body or b"<mxfile" in body,
                     f"first 120 bytes: {body[:120]!r}")
    return ok


def check_negative_download(host: str, evidence_dir: Path) -> bool:
    """GET /download/diagram.drawio with no scope params must return 400."""
    print("\n--- Negative: GET /download/diagram.drawio (no scope) ---")
    url = f"{host}/download/diagram.drawio"
    code, body = _request("GET", url)
    _save(evidence_dir, "neg_download_no_scope.txt", body)
    return _check("HTTP 400 without scope params", code == 400, f"got {code}")


def check_idempotency(gen1: dict, gen2: dict) -> bool:
    """Assert request_id and input_hash are unchanged on identical payload."""
    print("\n--- Idempotency check ---")
    ok = True
    rid1, rid2 = gen1.get("request_id"), gen2.get("request_id")
    ih1, ih2 = gen1.get("input_hash"), gen2.get("input_hash")

    ok &= _check("request_id unchanged", rid1 == rid2,
                 f"{rid1!r} vs {rid2!r}")
    ok &= _check("input_hash unchanged", ih1 == ih2,
                 f"{ih1!r} vs {ih2!r}")
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_summary(data: dict) -> None:
    """Print a brief summary without dumping the full drawio_xml."""
    summary = {k: v for k, v in data.items() if k != "drawio_xml"}
    xml_bytes = len((data.get("drawio_xml") or "").encode())
    summary["drawio_xml"] = f"<{xml_bytes} bytes — omitted>"
    print(json.dumps(summary, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agent 3 v1.3.2 end-to-end smoke test"
    )
    parser.add_argument("--host", default="http://localhost:8080",
                        help="Base URL of the running service (default: http://localhost:8080)")
    parser.add_argument("--out", default=None,
                        help="Evidence output directory (default: ./evidence_agent3_v132_<epoch>/)")
    args = parser.parse_args()

    host = args.host.rstrip("/")
    epoch = int(time.time())
    evidence_dir = Path(args.out) if args.out else Path(f"evidence_agent3_v132_{epoch}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    print(f"Evidence directory: {evidence_dir.resolve()}")

    # Save the generate payload for reference
    _save(evidence_dir, "req_generate.json",
          json.dumps(GENERATE_PAYLOAD, indent=2))

    results: list[bool] = []

    # -----------------------------------------------------------------------
    # 1. Health check
    # -----------------------------------------------------------------------
    results.append(check_health(host, evidence_dir))

    # -----------------------------------------------------------------------
    # 2. First POST /generate
    # -----------------------------------------------------------------------
    print("\n--- POST /generate (run 1) ---")
    ok1, gen1 = call_generate(host, GENERATE_PAYLOAD, evidence_dir, "gen1.json")
    results.append(ok1)

    if ok1 and gen1:
        results.append(check_generate_envelope(gen1))

        # Print summary (no huge XML)
        print("\n  Summary:")
        _print_summary(gen1)

        # -----------------------------------------------------------------------
        # 3. Download via returned URL
        # -----------------------------------------------------------------------
        download_url = (gen1.get("download") or {}).get("url", "")
        if download_url:
            results.append(check_download(host, download_url, evidence_dir))
        else:
            results.append(_check("download.url non-empty", False))
    else:
        # Pad results so overall count is consistent
        results.extend([False, False])

    # -----------------------------------------------------------------------
    # 4. Negative test — no scope params
    # -----------------------------------------------------------------------
    results.append(check_negative_download(host, evidence_dir))

    # -----------------------------------------------------------------------
    # 5. Idempotency — repeat identical payload
    # -----------------------------------------------------------------------
    print("\n--- POST /generate (run 2, idempotency) ---")
    ok2, gen2 = call_generate(host, GENERATE_PAYLOAD, evidence_dir, "gen2.json")
    results.append(ok2)

    if ok1 and ok2 and gen1 and gen2:
        results.append(check_idempotency(gen1, gen2))
    else:
        results.append(_check("idempotency skipped (earlier failure)", False))

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    passed = sum(results)
    total = len(results)
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} checks passed")
    if passed == total:
        print("SMOKE TEST PASSED")
        return 0
    else:
        print("SMOKE TEST FAILED")
        print(f"Evidence saved to: {evidence_dir.resolve()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
