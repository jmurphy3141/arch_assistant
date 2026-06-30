#!/usr/bin/env python3
"""Safely inventory and purge non-allowlisted Archie engagement objects.

The command is a dry-run unless both destructive acknowledgement flags are
present.  It deliberately recognizes only documented, segment-based customer
key layouts; everything else is reported for manual review and never deleted.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.object_store_oci import ObjectSummary, OciObjectStore


LEGACY_CUSTOMER_PREFIXES = frozenset(
    {
        "approved",
        "bom",
        "context",
        "conversations",
        "jep",
        "notes",
        "poc_plan",
        "pov",
        "terraform",
        "waf",
    }
)


@dataclass(frozen=True)
class ClassifiedObject:
    key: str
    size: int
    disposition: str
    customer_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class StoreConfig:
    region: str
    namespace: str
    bucket_name: str
    diagram_prefix: str


@dataclass
class Inventory:
    keep: list[ClassifiedObject]
    purge: list[ClassifiedObject]
    manual: list[ClassifiedObject]
    indexes: list[ObjectSummary]
    index_customers: set[str]
    index_entries: dict[str, set[str]]


def load_store_config(path: Path) -> StoreConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    persistence = raw.get("persistence") or {}
    if not persistence.get("enabled"):
        raise ValueError("persistence.enabled must be true")
    if persistence.get("backend") != "oci_object_storage":
        raise ValueError("persistence.backend must be 'oci_object_storage'")
    required = ("region", "namespace", "bucket_name", "prefix")
    missing = [name for name in required if not str(persistence.get(name) or "").strip()]
    if missing:
        raise ValueError(f"config.yaml persistence is missing: {', '.join(missing)}")
    return StoreConfig(
        region=str(persistence["region"]).strip(),
        namespace=str(persistence["namespace"]).strip(),
        bucket_name=str(persistence["bucket_name"]).strip(),
        diagram_prefix=str(persistence["prefix"]).strip().strip("/"),
    )


def _valid_customer_id(value: str) -> bool:
    return bool(value and value not in {".", ".."} and "/" not in value)


def customer_id_from_key(key: str, diagram_prefix: str) -> tuple[str | None, str]:
    """Return a confident customer ID, or a reason the key is not attributable."""
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == "customers" and _valid_customer_id(parts[1]):
        return parts[1], "customers layout"
    if len(parts) >= 3 and parts[0] == diagram_prefix and _valid_customer_id(parts[1]):
        return parts[1], "diagram layout"
    if len(parts) >= 3 and parts[0] in LEGACY_CUSTOMER_PREFIXES and _valid_customer_id(parts[1]):
        return parts[1], f"legacy {parts[0]} layout"
    return None, "key does not match a documented engagement layout"


def is_se_index(key: str) -> bool:
    parts = key.split("/")
    return len(parts) == 3 and parts[0] == "se" and bool(parts[1]) and parts[2] == "engagements.json"


def build_inventory(
    store: OciObjectStore,
    objects: Iterable[ObjectSummary],
    keep_ids: set[str],
    diagram_prefix: str,
) -> Inventory:
    inventory = Inventory(
        keep=[],
        purge=[],
        manual=[],
        indexes=[],
        index_customers=set(),
        index_entries={},
    )
    for obj in sorted(objects, key=lambda item: item.key):
        if is_se_index(obj.key):
            inventory.indexes.append(obj)
            try:
                payload = json.loads(store.get(obj.key).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("index root is not an object")
                for index_key, value in payload.items():
                    embedded = value.get("customer_id") if isinstance(value, dict) else None
                    if embedded and str(embedded) != str(index_key):
                        raise ValueError(f"entry {index_key!r} has mismatched customer_id {embedded!r}")
                    if not _valid_customer_id(str(index_key)):
                        raise ValueError(f"entry has invalid customer id {index_key!r}")
                    inventory.index_customers.add(str(index_key))
                    inventory.index_entries.setdefault(str(index_key), set()).add(obj.key)
            except Exception as exc:
                inventory.manual.append(
                    ClassifiedObject(obj.key, obj.size, "manual", reason=f"SE index cannot be safely parsed: {exc}")
                )
            continue

        customer_id, reason = customer_id_from_key(obj.key, diagram_prefix)
        if customer_id is None:
            inventory.manual.append(ClassifiedObject(obj.key, obj.size, "manual", reason=reason))
        elif customer_id in keep_ids:
            inventory.keep.append(ClassifiedObject(obj.key, obj.size, "keep", customer_id, reason))
        else:
            inventory.purge.append(ClassifiedObject(obj.key, obj.size, "purge", customer_id, reason))
    return inventory


def _write_grouped(path: Path, rows: list[ClassifiedObject]) -> None:
    grouped: dict[str, list[ClassifiedObject]] = defaultdict(list)
    for row in rows:
        grouped[row.customer_id or "MANUAL-REVIEW"].append(row)
    lines: list[str] = []
    for customer_id in sorted(grouped):
        items = grouped[customer_id]
        lines.append(f"[{customer_id}] count={len(items)} bytes={sum(item.size for item in items)}")
        lines.extend(f"{item.size}\t{item.key}\t{item.reason}" for item in items)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_index_plan(path: Path, inventory: Inventory, keep_ids: set[str], *, keep: bool) -> None:
    lines: list[str] = []
    for customer_id in sorted(inventory.index_entries):
        if (customer_id in keep_ids) != keep:
            continue
        keys = sorted(inventory.index_entries[customer_id])
        action = "retain entry" if keep else "drop entry after object deletion"
        lines.append(f"[{customer_id}] se_index_entries={len(keys)}")
        lines.extend(f"0\t{key}#customer_id={customer_id}\t{action}" for key in keys)
        lines.append("")
    if lines:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n[SE INDEX EDIT PLAN]\n")
            handle.write("\n".join(lines))


def write_reports(output_dir: Path, inventory: Inventory, keep_ids: set[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_grouped(output_dir / "keep.txt", inventory.keep)
    _write_grouped(output_dir / "purge.txt", inventory.purge)
    _write_grouped(output_dir / "manual_review.txt", inventory.manual)
    _append_index_plan(output_dir / "keep.txt", inventory, keep_ids, keep=True)
    _append_index_plan(output_dir / "purge.txt", inventory, keep_ids, keep=False)


def rewrite_se_indexes(store: OciObjectStore, indexes: Iterable[ObjectSummary], keep_ids: set[str]) -> None:
    """Rewrite valid SE indexes after object deletion, never delete an index key."""
    for obj in indexes:
        payload = json.loads(store.get(obj.key).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Refusing to rewrite malformed SE index: {obj.key}")
        filtered = {key: value for key, value in payload.items() if key in keep_ids}
        store.put(obj.key, json.dumps(filtered, indent=2).encode("utf-8"), "application/json")


def _assert_target(args: argparse.Namespace, config: StoreConfig) -> None:
    expected = (args.expected_bucket, args.expected_namespace, args.expected_region)
    if not all(expected):
        raise ValueError(
            "destructive mode requires --expected-bucket, --expected-namespace, and --expected-region "
            "copied from the reviewed dry-run"
        )
    actual = (config.bucket_name, config.namespace, config.region)
    if expected != actual:
        raise ValueError(f"target confirmation mismatch: expected={expected!r}, configured={actual!r}")


def _reviewed_objects(path: Path) -> dict[str, int]:
    """Read the concrete object rows from a previously reviewed purge report."""
    reviewed: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3 or not fields[0].isdigit():
            continue
        key = fields[1]
        if "#customer_id=" in key:  # SE index edit plan, not an object deletion
            continue
        reviewed[key] = int(fields[0])
    return reviewed


def assert_matches_reviewed_purge(path: Path, inventory: Inventory) -> None:
    reviewed = _reviewed_objects(path)
    current = {row.key: row.size for row in inventory.purge}
    if reviewed != current:
        added = sorted(set(current) - set(reviewed))
        missing = sorted(set(reviewed) - set(current))
        changed = sorted(key for key in set(current) & set(reviewed) if current[key] != reviewed[key])
        raise ValueError(
            "current purge inventory differs from reviewed purge.txt; refusing deletion "
            f"(added={added[:5]!r}, missing={missing[:5]!r}, size_changed={changed[:5]!r})"
        )


def _remaining_customer_ids(store: OciObjectStore, config: StoreConfig) -> set[str]:
    remaining: set[str] = set()
    for obj in store.list_objects(""):
        customer_id, _ = customer_id_from_key(obj.key, config.diagram_prefix)
        if customer_id:
            remaining.add(customer_id)
        elif is_se_index(obj.key):
            payload = json.loads(store.get(obj.key).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Malformed SE index after purge: {obj.key}")
            remaining.update(str(key) for key in payload)
    return remaining


def execute_purge(
    store: OciObjectStore,
    config: StoreConfig,
    inventory: Inventory,
    keep_ids: set[str],
    output_dir: Path,
    *,
    archive: bool,
) -> None:
    if any(item.key in {row.key for row in inventory.manual} for item in inventory.indexes):
        raise ValueError("one or more SE indexes require manual review; refusing destructive mode")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bucket": config.bucket_name,
        "namespace": config.namespace,
        "region": config.region,
        "keep_customer_ids": sorted(keep_ids),
        "archive_prefix": f"archive/{timestamp}/" if archive else None,
        "objects": [{"key": row.key, "size": row.size} for row in inventory.purge],
    }
    manifest_path = output_dir / "deleted_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for row in inventory.purge:
        if row.customer_id in keep_ids:
            raise RuntimeError(f"internal safety check blocked KEEP object: {row.key}")
        if archive:
            store.put(f"archive/{timestamp}/{row.key}", store.get(row.key))
        store.delete_object(row.key)

    rewrite_se_indexes(store, inventory.indexes, keep_ids)
    remaining = _remaining_customer_ids(store, config)
    if remaining != keep_ids:
        raise RuntimeError(
            f"post-delete verification failed: remaining={sorted(remaining)!r}, expected={sorted(keep_ids)!r}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--keep-customer-id", action="append", default=[], dest="keep_customer_ids")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--i-understand-this-is-permanent", action="store_true")
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--expected-bucket")
    parser.add_argument("--expected-namespace")
    parser.add_argument("--expected-region")
    parser.add_argument("--reviewed-purge-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    keep_ids = {value.strip() for value in args.keep_customer_ids if value.strip()}
    if not keep_ids:
        print("ERROR: supply at least one exact --keep-customer-id; no IDs are inferred", file=sys.stderr)
        return 2
    if any(not _valid_customer_id(value) for value in keep_ids):
        print("ERROR: invalid customer ID in allowlist", file=sys.stderr)
        return 2

    try:
        config = load_store_config(args.config)
        destructive = args.confirm or args.i_understand_this_is_permanent
        if destructive and not (args.confirm and args.i_understand_this_is_permanent):
            raise ValueError("both --confirm and --i-understand-this-is-permanent are required")
        if destructive:
            _assert_target(args, config)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = args.output_dir or Path("/tmp") / f"archie-engagement-purge-{timestamp}"
        store = OciObjectStore(config.region, config.namespace, config.bucket_name)
        inventory = build_inventory(store, store.list_objects(""), keep_ids, config.diagram_prefix)
        write_reports(output_dir, inventory, keep_ids)

        if destructive:
            if args.reviewed_purge_file is None:
                raise ValueError("destructive mode requires --reviewed-purge-file from the approved dry-run")
            assert_matches_reviewed_purge(args.reviewed_purge_file, inventory)
            represented_keep_ids = {row.customer_id for row in inventory.keep} | (
                inventory.index_customers & keep_ids
            )
            if represented_keep_ids != keep_ids:
                raise ValueError(
                    f"not every KEEP customer is present before deletion: found={sorted(represented_keep_ids)!r}"
                )

        kept_customers = {row.customer_id for row in inventory.keep} | (inventory.index_customers & keep_ids)
        purged_customers = {row.customer_id for row in inventory.purge} | (inventory.index_customers - keep_ids)
        print(f"Bucket:    {config.bucket_name}")
        print(f"Namespace: {config.namespace}")
        print(f"Region:    {config.region}")
        print(f"Reports:   {output_dir}")
        print(
            f"Summary: {len(kept_customers)} customers kept, {len(purged_customers)} customers purged, "
            f"{len(inventory.purge)} objects to delete, {len(inventory.manual)} unattributable/manual-review objects"
        )

        if not destructive:
            print("DRY RUN ONLY — DELETE NOTHING. Review purge.txt and confirm bucket, namespace, and region.")
            return 0

        execute_purge(store, config, inventory, keep_ids, output_dir, archive=args.archive)
        print(f"DELETE COMPLETE — {len(inventory.purge)} objects deleted; remaining customer IDs match allowlist")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
