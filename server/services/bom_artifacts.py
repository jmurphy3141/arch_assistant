from __future__ import annotations

import functools
import json
import re
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable

import anyio
from fastapi import HTTPException

from agent.context_store import attach_bom_xlsx_to_latest, read_context, record_agent_run, write_context


BOM_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BOM_XLSX_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.xlsx$")
BOM_XLSX_METADATA_SUFFIX = ".metadata.json"


def bom_xlsx_key(customer_id: str, filename: str) -> str:
    return f"customers/{customer_id}/bom/xlsx/{filename}"


def bom_xlsx_metadata_key(xlsx_key: str) -> str:
    return f"{xlsx_key}{BOM_XLSX_METADATA_SUFFIX}"


def validate_bom_xlsx_filename(filename: str) -> str:
    safe_name = Path(str(filename or "")).name
    if safe_name != filename or not BOM_XLSX_FILENAME_RE.fullmatch(safe_name):
        raise HTTPException(status_code=400, detail="Invalid BOM XLSX filename")
    return safe_name


def build_artifact_manifest(customer_id: str, result: dict) -> dict:
    """Build UI-friendly artifact metadata from orchestrator result."""
    manifest: dict[str, list[dict]] = {"downloads": []}
    seen_diagram_keys: set[str] = set()
    seen_bom_keys: set[str] = set()

    def _append_diagram_download(artifact_key: str, *, tool_name: str, scenario_label: str = "") -> None:
        if not artifact_key or artifact_key in seen_diagram_keys:
            return
        seen_diagram_keys.add(artifact_key)
        parts = [part for part in str(artifact_key or "").split("/") if part]
        version_index = next((idx for idx, part in enumerate(parts) if re.fullmatch(r"v\d+", part)), -1)
        artifact_filename = parts[-1] if parts else "diagram.drawio"
        artifact_client_id = parts[version_index - 2] if version_index >= 2 else customer_id
        diagram_name = parts[version_index - 1] if version_index >= 1 else "oci_architecture"
        item = {
            "type": "diagram",
            "tool": tool_name,
            "key": artifact_key,
            "filename": artifact_filename,
            "download_url": (
                f"/api/download/{urllib.parse.quote(artifact_filename)}"
                f"?client_id={urllib.parse.quote(artifact_client_id)}"
                f"&diagram_name={urllib.parse.quote(diagram_name)}"
            ),
        }
        if scenario_label:
            item["label"] = scenario_label
        manifest["downloads"].append(item)

    for tool_call in result.get("tool_calls", []) or []:
        if tool_call.get("tool") != "generate_diagram":
            continue
        _append_diagram_download(
            str(tool_call.get("artifact_key", "") or ""),
            tool_name="generate_diagram",
            scenario_label=str(tool_call.get("scenario_label", "") or ""),
        )

    artifacts = result.get("artifacts", {}) or {}
    for tool_name, artifact_key in artifacts.items():
        if tool_name == "generate_diagram":
            _append_diagram_download(str(artifact_key or ""), tool_name=tool_name)

    for tool_call in result.get("tool_calls", []) or []:
        if tool_call.get("tool") != "generate_bom":
            continue
        result_data = tool_call.get("result_data", {}) or {}
        if not isinstance(result_data, dict):
            continue
        if not bom_result_is_exportable(result_data) or not result_has_bom_xlsx_metadata(result_data):
            continue
        xlsx = result_data.get("bom_xlsx") if isinstance(result_data.get("bom_xlsx"), dict) else {}
        artifact_key = str(result_data.get("xlsx_artifact_key") or xlsx.get("key") or "").strip()
        filename = str(result_data.get("xlsx_filename") or xlsx.get("filename") or "").strip()
        if not artifact_key or not filename or artifact_key in seen_bom_keys:
            continue
        seen_bom_keys.add(artifact_key)
        manifest["downloads"].append(
            {
                "type": "bom",
                "tool": "generate_bom",
                "key": artifact_key,
                "filename": filename,
                "download_url": (
                    f"/api/bom/{urllib.parse.quote(customer_id)}/download/"
                    f"{urllib.parse.quote(filename)}"
                ),
            }
        )

    for tool_call in result.get("tool_calls", []) or []:
        if tool_call.get("tool") != "generate_terraform":
            continue
        result_data = tool_call.get("result_data", {}) or {}
        bundle = result_data.get("bundle")
        if bundle and isinstance(bundle.get("files"), dict):
            for filename in sorted(bundle["files"].keys()):
                manifest["downloads"].append(
                    {
                        "type": "terraform",
                        "tool": "generate_terraform",
                        "filename": filename,
                        "download_url": f"/api/terraform/{customer_id}/download/{filename}",
                    }
                )
    return manifest


def attach_artifact_delivery_to_reply(result: dict, artifact_manifest: dict) -> dict:
    """
    Add a concise delivery sentence after server-side artifact persistence.

    The orchestrator can finish before the server creates downloadable BOM XLSX
    files. This keeps the UI and saved chat history honest without exposing
    internal artifact bookkeeping.
    """
    if not isinstance(result, dict):
        return result
    downloads = (
        artifact_manifest.get("downloads", [])
        if isinstance(artifact_manifest, dict)
        else []
    )
    if not isinstance(downloads, list) or not downloads:
        return result
    reply = str(result.get("reply", "") or "").strip()
    unseen = [
        item
        for item in downloads
        if isinstance(item, dict)
        and str(item.get("download_url", "") or "").strip()
        and str(item.get("download_url", "") or "").strip() not in reply
        and str(item.get("key", "") or "").strip() not in reply
    ]
    if not unseen:
        return result
    sentence = _artifact_delivery_sentence(unseen)
    if not sentence:
        return result
    result["reply"] = f"{reply}\n\n{sentence}".strip() if reply else sentence
    return result


def _artifact_delivery_sentence(downloads: list[dict]) -> str:
    links: list[str] = []
    for item in downloads[:4]:
        artifact_type = str(item.get("type", "") or "artifact").strip().lower()
        filename = str(item.get("filename", "") or artifact_type or "artifact").strip()
        url = str(item.get("download_url", "") or "").strip()
        if not url:
            continue
        label = {
            "bom": "BOM workbook",
            "diagram": "diagram",
            "terraform": "Terraform file",
        }.get(artifact_type, artifact_type or "artifact")
        links.append(f"{label}: [{filename}]({url})")
    if not links:
        return ""
    if len(links) == 1:
        return f"File is ready — {links[0]}."
    return "Files are ready — " + "; ".join(links) + "."


def bom_result_is_exportable(result_data: dict) -> bool:
    result_type = str(result_data.get("type", "") or "").strip().lower()
    if result_type and result_type != "final":
        return False
    payload = result_data.get("bom_payload") if isinstance(result_data.get("bom_payload"), dict) else {}
    line_items = payload.get("line_items") if isinstance(payload.get("line_items"), list) else []
    if not line_items:
        return False
    if isinstance(result_data.get("archie_question_bundle"), dict):
        return False
    if result_data.get("checkpoint_required") is True:
        return False
    for governor in (
        result_data.get("governor", {}) if isinstance(result_data.get("governor"), dict) else {},
        (result_data.get("trace", {}) or {}).get("governor", {}) if isinstance(result_data.get("trace"), dict) else {},
    ):
        status = str((governor or {}).get("overall_status", "") or "").strip().lower()
        if status in {"checkpoint_required", "blocked"}:
            return False
    review = result_data.get("archie_expert_review")
    if not isinstance(review, dict):
        trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
        review = trace.get("archie_expert_review") if isinstance(trace.get("archie_expert_review"), dict) else {}
    verdict = str((review or {}).get("verdict", "") or "").strip().lower()
    if verdict and verdict != "pass":
        return False
    trace_verdict = str(
        ((result_data.get("trace", {}) or {}).get("review_verdict", "") if isinstance(result_data.get("trace"), dict) else "")
        or ""
    ).strip().lower()
    if trace_verdict and trace_verdict != "pass":
        return False
    if structured_bom_result_uses_default_sizing(result_data):
        return False
    return True


def structured_bom_result_uses_default_sizing(result_data: dict) -> bool:
    structured = result_data.get("structured_inputs")
    if not isinstance(structured, dict):
        structured = result_data.get("inputs") if isinstance(result_data.get("inputs"), dict) else {}
    if not structured:
        return False
    payload = result_data.get("bom_payload") if isinstance(result_data.get("bom_payload"), dict) else {}
    produced = bom_payload_sizing(payload)
    compute = structured.get("compute", {}) if isinstance(structured.get("compute"), dict) else {}
    memory = structured.get("memory", {}) if isinstance(structured.get("memory"), dict) else {}
    storage = structured.get("storage", {}) if isinstance(structured.get("storage"), dict) else {}
    required = {
        "ocpu": positive_float(compute.get("ocpu")),
        "ram_gb": positive_float(memory.get("gb")),
        "storage_gb": (positive_float(storage.get("block_tb")) or 0.0) * 1024.0
        if positive_float(storage.get("block_tb"))
        else None,
    }
    for key, value in required.items():
        if value is None:
            continue
        if float(produced.get(key, 0.0) or 0.0) + 0.0001 < value:
            return True
    return False


def positive_float(value: Any) -> float | None:
    if value in (None, "", [], {}):
        return None
    try:
        parsed = float(value)
    except Exception:
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            return None
        parsed = float(match.group(0))
    return parsed if parsed > 0 else None


def bom_payload_sizing(payload: dict) -> dict[str, float]:
    cpu_skus = {"B93113", "B97384", "B111129", "B94176", "B93297"}
    mem_skus = {"B93114", "B97385", "B111130", "B94177", "B93298"}
    produced = {"ocpu": 0.0, "ram_gb": 0.0, "storage_gb": 0.0}
    for row in payload.get("line_items", []) or []:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku", "") or "").upper()
        desc = str(row.get("description", "") or "").lower()
        category = str(row.get("category", "") or "").lower()
        qty = positive_float(row.get("quantity")) or 0.0
        if sku in cpu_skus or ("ocpu" in desc and category == "compute"):
            produced["ocpu"] += qty
        elif sku in mem_skus or ("memory" in desc and category == "compute"):
            produced["ram_gb"] += qty
        elif category == "storage" or "storage" in desc or "volume" in desc:
            produced["storage_gb"] += qty
    return produced


def bom_xlsx_metadata(filename: str, key: str, result_data: dict | None = None) -> dict:
    result_data = result_data or {}
    review = result_data.get("archie_expert_review")
    if not isinstance(review, dict):
        trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
        review = trace.get("archie_expert_review") if isinstance(trace.get("archie_expert_review"), dict) else {}
    payload = result_data.get("bom_payload") if isinstance(result_data.get("bom_payload"), dict) else {}
    resolved_inputs = payload.get("resolved_inputs") if isinstance(payload.get("resolved_inputs"), list) else []
    trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
    context_source = str(trace.get("bom_context_source", result_data.get("bom_context_source", "")) or "")
    return {
        "schema_version": "1.0",
        "tool": "generate_bom",
        "status": "approved",
        "checkpoint_required": False,
        "filename": filename,
        "key": key,
        "archie_review_verdict": str((review or {}).get("verdict", "") or "pass"),
        "resolved_input_count": len(resolved_inputs),
        "context_source": context_source,
        "grounding": "revision-grounded"
        if "revision" in context_source
        else ("context-grounded" if context_source and context_source != "direct_request" else "generic"),
    }


def bom_xlsx_download_is_valid(store, key: str) -> bool:
    meta_key = bom_xlsx_metadata_key(key)
    if not key.lower().endswith(".xlsx") or not store.head(key) or not store.head(meta_key):
        return False
    try:
        metadata = json.loads(store.get(meta_key).decode("utf-8"))
    except Exception:
        return False
    if not isinstance(metadata, dict):
        return False
    if metadata.get("tool") != "generate_bom":
        return False
    if str(metadata.get("status", "") or "").lower() not in {"approved", "final"}:
        return False
    if metadata.get("checkpoint_required") is True:
        return False
    if str(metadata.get("archie_review_verdict", "pass") or "pass").lower() != "pass":
        return False
    return True


def result_has_bom_xlsx_metadata(result_data: dict) -> bool:
    xlsx = result_data.get("bom_xlsx") if isinstance(result_data.get("bom_xlsx"), dict) else {}
    metadata = result_data.get("xlsx_metadata")
    if not isinstance(metadata, dict):
        metadata = xlsx.get("metadata") if isinstance(xlsx.get("metadata"), dict) else {}
    return (
        isinstance(metadata, dict)
        and metadata.get("tool") == "generate_bom"
        and str(metadata.get("status", "") or "").lower() in {"approved", "final"}
        and metadata.get("checkpoint_required") is not True
        and str(metadata.get("archie_review_verdict", "pass") or "pass").lower() == "pass"
    )


async def persist_bom_xlsx_downloads(
    customer_id: str,
    store,
    result: dict,
    *,
    bom_service_factory: Callable[[], Any],
    logger,
) -> dict:
    """
    Persist downloadable XLSX workbooks for successful BOM tool calls.
    Mutates and returns result so artifact manifests can expose the links.
    """
    service = bom_service_factory()
    for index, tool_call in enumerate(result.get("tool_calls", []) or []):
        if tool_call.get("tool") != "generate_bom":
            continue
        result_data = tool_call.get("result_data")
        if not isinstance(result_data, dict):
            continue
        if not bom_result_is_exportable(result_data):
            continue
        if result_data.get("xlsx_artifact_key") or isinstance(result_data.get("bom_xlsx"), dict):
            continue
        payload = result_data.get("bom_payload")
        if not isinstance(payload, dict) or not payload:
            continue
        if not isinstance(payload.get("line_items"), list) or not payload.get("line_items"):
            continue
        workbook_bytes = await anyio.to_thread.run_sync(functools.partial(service.generate_xlsx, payload))
        filename = f"oci-bom-{time.strftime('%Y%m%d-%H%M%S')}-{index + 1}-{uuid.uuid4().hex[:8]}.xlsx"
        key = bom_xlsx_key(customer_id, filename)
        metadata = bom_xlsx_metadata(filename, key, result_data)
        store.put(key, workbook_bytes, BOM_XLSX_CONTENT_TYPE)
        store.put(
            bom_xlsx_metadata_key(key),
            json.dumps(metadata, sort_keys=True).encode("utf-8"),
            "application/json",
        )
        result_data["xlsx_artifact_key"] = key
        result_data["xlsx_filename"] = filename
        result_data["xlsx_metadata"] = metadata
        result_data["bom_xlsx"] = {"key": key, "filename": filename, "metadata": metadata}
        trace = result_data.get("trace", {}) if isinstance(result_data.get("trace"), dict) else {}
        stages = list(trace.get("bom_trace_stages", []) or [])
        if "XLSX persisted" not in stages:
            stages.append("XLSX persisted")
        trace["bom_trace_stages"] = stages
        result_data["trace"] = trace
        try:
            context = read_context(store, customer_id)
            record_agent_run(
                context,
                "bom",
                [],
                {
                    "xlsx_artifact_key": key,
                    "xlsx_filename": filename,
                    "xlsx_metadata": metadata,
                    "bom_xlsx": {"key": key, "filename": filename, "metadata": metadata},
                },
            )
            attach_bom_xlsx_to_latest(context, {"key": key, "filename": filename, "metadata": metadata})
            write_context(store, customer_id, context)
        except Exception as exc:
            logger.warning("Could not record BOM XLSX artifact in context: %s", exc)
    return result
