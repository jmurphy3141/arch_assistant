from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "external_oci_arch_corpus_manifest.json"
FIXTURE_ROOT = ROOT / "tests" / "external_fixtures" / "oci_arch_skill"
INDEX_PATH = FIXTURE_ROOT / "index.json"


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _mxcell_count(drawio_path: Path) -> int:
    tree = ET.parse(drawio_path)
    return sum(1 for _ in tree.iter("mxCell"))


def _drawio_root_tag(drawio_path: Path) -> str:
    return ET.parse(drawio_path).getroot().tag


def _diagram_count(drawio_path: Path) -> int:
    tree = ET.parse(drawio_path)
    return sum(1 for _ in tree.iter("diagram"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _load_json(MANIFEST_PATH)  # type: ignore[return-value]


@pytest.fixture(scope="module")
def fetched_index() -> dict:
    if not INDEX_PATH.exists():
        pytest.skip(
            "external OCI architecture corpus not fetched; run "
            "`python3 scripts/fetch_external_oci_arch_skill_fixtures.py` first"
        )
    return _load_json(INDEX_PATH)  # type: ignore[return-value]


def test_fetched_index_matches_manifest_commit(manifest: dict, fetched_index: dict) -> None:
    assert fetched_index["repo"] == manifest["repo"]
    assert fetched_index["commit"] == manifest["commit"]
    assert fetched_index["file_count"] == len(fetched_index["files"])


def test_example_triples_are_coherent(manifest: dict, fetched_index: dict) -> None:
    _ = fetched_index

    for example in manifest["examples"]:
        spec_path = FIXTURE_ROOT / manifest["skill_root"] / "assets" / "examples" / "specs" / f"{example}.json"
        drawio_path = FIXTURE_ROOT / manifest["skill_root"] / "assets" / "examples" / "output" / f"{example}.drawio"
        report_path = FIXTURE_ROOT / manifest["skill_root"] / "assets" / "examples" / "output" / f"{example}.report.json"

        spec = _load_json(spec_path)
        report = _load_json(report_path)
        drawio_xml = drawio_path.read_text(encoding="utf-8")

        assert spec["pages"], f"{example} should contain at least one page"
        assert any(page.get("page_type") == "physical" for page in spec["pages"])

        element_count = sum(len(page.get("elements", [])) for page in spec["pages"])
        assert len(report) == element_count, f"{example} report/spec element count mismatch"
        assert any(item.get("kind") == "edge" for item in report), f"{example} report has no edges"
        assert any(item.get("source") == "oci-library.xml" for item in report if item.get("kind") == "library")

        assert _drawio_root_tag(drawio_path) == "mxfile", f"{example} is not a draw.io mxfile"
        assert _diagram_count(drawio_path) >= 1, f"{example} drawio output has no diagram pages"
        assert (
            "mxGraphModel" in drawio_xml or _mxcell_count(drawio_path) == 0 or _mxcell_count(drawio_path) > 20
        ), f"{example} drawio output looks malformed"


def test_reference_and_asset_files_exist_and_parse(manifest: dict, fetched_index: dict) -> None:
    _ = fetched_index

    for rel_path in manifest["documents"] + manifest["assets"] + manifest["references"]:
        file_path = FIXTURE_ROOT / manifest["skill_root"] / rel_path
        assert file_path.exists(), f"missing fetched file: {rel_path}"
        assert file_path.stat().st_size > 0, f"empty fetched file: {rel_path}"

    style_guide = (
        FIXTURE_ROOT / manifest["skill_root"] / "references" / "style-guide.md"
    ).read_text(encoding="utf-8")
    icon_library = (
        FIXTURE_ROOT / manifest["skill_root"] / "assets" / "drawio" / "oci-library.xml"
    ).read_text(encoding="utf-8")
    reference_drawio = (
        FIXTURE_ROOT
        / manifest["skill_root"]
        / "assets"
        / "reference-architectures"
        / "oracle"
        / "oke-architecture-diagram.drawio"
    ).read_text(encoding="utf-8")

    assert "physical diagrams" in style_guide.lower()
    assert "Container Engine for Kubernetes" in icon_library
    assert "mxGraphModel" in reference_drawio
