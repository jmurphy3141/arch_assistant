#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "external_oci_arch_corpus_manifest.json"
DEFAULT_DEST = ROOT / "tests" / "external_fixtures" / "oci_arch_skill"


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _build_file_list(manifest: dict) -> list[str]:
    skill_root = str(manifest["skill_root"]).strip("/")
    files: list[str] = []

    for rel_path in manifest.get("documents", []):
        files.append(f"{skill_root}/{rel_path}")
    for rel_path in manifest.get("assets", []):
        files.append(f"{skill_root}/{rel_path}")
    for rel_path in manifest.get("references", []):
        files.append(f"{skill_root}/{rel_path}")
    for example in manifest.get("examples", []):
        files.append(f"{skill_root}/assets/examples/specs/{example}.json")
        files.append(f"{skill_root}/assets/examples/output/{example}.drawio")
        files.append(f"{skill_root}/assets/examples/output/{example}.report.json")

    return files


def _raw_url(repo: str, commit: str, rel_path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{commit}/{rel_path}"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url) as response:
        return response.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch pinned external OCI architecture fixtures into the local test workspace."
    )
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help="Destination directory for fetched fixtures.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete the destination and refetch all files.",
    )
    args = parser.parse_args(argv)

    manifest = _load_manifest()
    repo = str(manifest["repo"])
    commit = str(manifest["commit"])
    dest = Path(args.dest).resolve()

    if args.force and dest.exists():
        shutil.rmtree(dest)

    dest.mkdir(parents=True, exist_ok=True)

    fetched_files: list[dict[str, object]] = []
    for rel_path in _build_file_list(manifest):
        url = _raw_url(repo, commit, rel_path)
        try:
            payload = _download(url)
        except urllib.error.URLError as exc:
            print(f"failed to fetch {url}: {exc}", file=sys.stderr)
            return 1

        output_path = dest / rel_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)
        fetched_files.append(
            {
                "path": rel_path,
                "bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
        print(f"fetched {rel_path}")

    index = {
        "repo": repo,
        "commit": commit,
        "file_count": len(fetched_files),
        "files": fetched_files,
    }
    (dest / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {dest / 'index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
