from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.object_store_oci import ObjectSummary
from scripts.purge_engagements import (
    StoreConfig,
    _assert_target,
    assert_matches_reviewed_purge,
    build_inventory,
    customer_id_from_key,
    execute_purge,
    write_reports,
)


class FakeStore:
    def __init__(self, values: dict[str, bytes]):
        self.values = dict(values)
        self.deleted: list[str] = []

    def list_objects(self, prefix: str = "") -> list[ObjectSummary]:
        return [
            ObjectSummary(key, len(value))
            for key, value in sorted(self.values.items())
            if key.startswith(prefix)
        ]

    def get(self, key: str) -> bytes:
        return self.values[key]

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.values[key] = data

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)


def _config() -> StoreConfig:
    return StoreConfig("region-1", "namespace-1", "bucket-1", "agent3")


def test_customer_id_parser_uses_only_documented_segment_layouts() -> None:
    assert customer_id_from_key("customers/real/context/context.json", "agent3")[0] == "real"
    assert customer_id_from_key("agent3/real/diagram/v1/diagram.drawio", "agent3")[0] == "real"
    assert customer_id_from_key("poc_plan/synthetic/v1.json", "agent3")[0] == "synthetic"
    assert customer_id_from_key("conversations/real/history.json", "agent3")[0] == "real"
    assert customer_id_from_key("archive/20260630/customers/synthetic/context.json", "agent3")[0] is None
    assert customer_id_from_key("unrelated/synthetic/file.json", "agent3")[0] is None


def test_inventory_keeps_allowlist_purges_attributed_and_manual_reviews_unknown() -> None:
    index = {
        "real": {"customer_id": "real"},
        "synthetic": {"customer_id": "synthetic"},
    }
    store = FakeStore(
        {
            "customers/real/context/context.json": b"keep",
            "notes/synthetic/note.txt": b"purge",
            "agent3/synthetic/diagram/v1/diagram.drawio": b"purge",
            "se/seller/engagements.json": json.dumps(index).encode(),
            "shared/templates/base.json": b"manual",
        }
    )

    inventory = build_inventory(store, store.list_objects(), {"real"}, "agent3")

    assert [row.key for row in inventory.keep] == ["customers/real/context/context.json"]
    assert {row.key for row in inventory.purge} == {
        "notes/synthetic/note.txt",
        "agent3/synthetic/diagram/v1/diagram.drawio",
    }
    assert [row.key for row in inventory.manual] == ["shared/templates/base.json"]
    assert [obj.key for obj in inventory.indexes] == ["se/seller/engagements.json"]
    assert inventory.index_customers == {"real", "synthetic"}


def test_reports_contain_full_keys_and_per_customer_counts(tmp_path: Path) -> None:
    store = FakeStore(
        {
            "customers/real/context/context.json": b"123",
            "notes/fake/a.txt": b"1",
            "notes/fake/b.txt": b"22",
            "other/key.txt": b"x",
        }
    )
    inventory = build_inventory(store, store.list_objects(), {"real"}, "agent3")

    write_reports(tmp_path, inventory, {"real"})

    assert "[real] count=1 bytes=3" in (tmp_path / "keep.txt").read_text()
    purge = (tmp_path / "purge.txt").read_text()
    assert "[fake] count=2 bytes=3" in purge
    assert "notes/fake/a.txt" in purge and "notes/fake/b.txt" in purge
    assert "other/key.txt" in (tmp_path / "manual_review.txt").read_text()


def test_execute_archives_deletes_rewrites_indexes_and_verifies(tmp_path: Path) -> None:
    store = FakeStore(
        {
            "customers/real/context/context.json": b"keep",
            "customers/fake/context/context.json": b"purge",
            "se/seller/engagements.json": json.dumps(
                {"real": {"customer_id": "real"}, "fake": {"customer_id": "fake"}}
            ).encode(),
        }
    )
    config = _config()
    inventory = build_inventory(store, store.list_objects(), {"real"}, config.diagram_prefix)

    execute_purge(store, config, inventory, {"real"}, tmp_path, archive=True)

    assert store.deleted == ["customers/fake/context/context.json"]
    assert "customers/real/context/context.json" in store.values
    assert any(key.endswith("/customers/fake/context/context.json") for key in store.values if key.startswith("archive/"))
    assert json.loads(store.values["se/seller/engagements.json"]) == {"real": {"customer_id": "real"}}
    manifest = json.loads((tmp_path / "deleted_manifest.json").read_text())
    assert manifest["objects"] == [{"key": "customers/fake/context/context.json", "size": 5}]


def test_malformed_index_is_manual_review_and_blocks_delete(tmp_path: Path) -> None:
    store = FakeStore(
        {
            "customers/fake/context/context.json": b"purge",
            "se/seller/engagements.json": b"not-json",
        }
    )
    config = _config()
    inventory = build_inventory(store, store.list_objects(), {"real"}, config.diagram_prefix)

    assert any(row.key == "se/seller/engagements.json" for row in inventory.manual)
    with pytest.raises(ValueError, match="manual review"):
        execute_purge(store, config, inventory, {"real"}, tmp_path, archive=False)
    assert store.deleted == []


def test_destructive_target_must_match_reviewed_dry_run() -> None:
    class Args:
        expected_bucket = "wrong"
        expected_namespace = "namespace-1"
        expected_region = "region-1"

    with pytest.raises(ValueError, match="target confirmation mismatch"):
        _assert_target(Args(), _config())


def test_current_inventory_must_match_reviewed_purge(tmp_path: Path) -> None:
    store = FakeStore({"customers/fake/context/context.json": b"purge"})
    inventory = build_inventory(store, store.list_objects(), {"real"}, "agent3")
    reviewed = tmp_path / "purge.txt"
    reviewed.write_text(
        "[fake] count=1 bytes=5\n5\tcustomers/fake/context/context.json\tcustomers layout\n"
    )
    assert_matches_reviewed_purge(reviewed, inventory)

    store.put("customers/new/context/context.json", b"new")
    changed = build_inventory(store, store.list_objects(), {"real"}, "agent3")
    with pytest.raises(ValueError, match="differs from reviewed"):
        assert_matches_reviewed_purge(reviewed, changed)
