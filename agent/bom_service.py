from __future__ import annotations

import io
import json
import logging
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from openpyxl import Workbook
from openpyxl.styles import Font

logger = logging.getLogger(__name__)

OCI_PRICE_LIST_API_URL = "https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/"
OCI_SHAPES_DOC_URL = "https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm"
OCI_SERVICES_DOC_URL = "https://docs.oracle.com/en-us/iaas/Content/home.htm"

CPU_SKU_TO_MEM_SKU = {
    "B94176": "B94177",
    "B111129": "B111130",
    "B88317": "B88318",
}

DEFAULT_PRICE_TABLE: dict[str, dict[str, Any]] = {
    "B94176": {"description": "Compute E4 OCPU", "unit_price": 0.05, "category": "compute"},
    "B94177": {"description": "Compute E4 Memory GB", "unit_price": 0.01, "category": "compute"},
    "B111129": {"description": "Compute E6 OCPU", "unit_price": 0.055, "category": "compute"},
    "B111130": {"description": "Compute E6 Memory GB", "unit_price": 0.011, "category": "compute"},
    "B88317": {"description": "Compute A1 OCPU", "unit_price": 0.03, "category": "compute"},
    "B88318": {"description": "Compute A1 Memory GB", "unit_price": 0.005, "category": "compute"},
    "B91961": {"description": "Block Volume Capacity GB", "unit_price": 0.043, "category": "storage"},
    "B93030": {"description": "Load Balancer Flexible Base", "unit_price": 0.025, "category": "network"},
    "B91628": {"description": "Object Storage Capacity GB", "unit_price": 0.026, "category": "storage"},
}


@dataclass
class CacheSnapshot:
    pricing_table: dict[str, dict[str, Any]]
    shapes_text: str
    services_text: str
    refreshed_at: float
    source: str


class BomService:
    """
    Shared v1.7 BOM service used by REST and orchestrator tool execution.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._cache: CacheSnapshot | None = None

    def _fetch_url(self, url: str, timeout: int = 20) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "oci-agent-bom/1.7"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def refresh_data(self) -> dict[str, Any]:
        started = time.perf_counter()
        pricing_table: dict[str, dict[str, Any]] = {}
        source = "live"

        try:
            raw = self._fetch_url(OCI_PRICE_LIST_API_URL)
            payload = json.loads(raw)
            pricing_table = self._parse_pricing_payload(payload)
            if not pricing_table:
                raise ValueError("pricing payload parsed to empty table")
        except Exception as exc:
            logger.warning("BOM pricing refresh fallback: %s", exc)
            source = "fallback"
            pricing_table = dict(DEFAULT_PRICE_TABLE)

        try:
            shapes_text = self._fetch_url(OCI_SHAPES_DOC_URL)
            services_text = self._fetch_url(OCI_SERVICES_DOC_URL)
        except Exception as exc:
            logger.warning("BOM shapes/services refresh fallback: %s", exc)
            source = "fallback"
            shapes_text = "OCI compute shapes catalog unavailable; using fallback guidance."
            services_text = "OCI services catalog unavailable; using fallback guidance."

        with self._lock:
            self._cache = CacheSnapshot(
                pricing_table=pricing_table,
                shapes_text=shapes_text[:60000],
                services_text=services_text[:60000],
                refreshed_at=time.time(),
                source=source,
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ready": True,
            "source": source,
            "pricing_sku_count": len(pricing_table),
            "latency_ms": elapsed_ms,
            "refreshed_at": int(self._cache.refreshed_at) if self._cache else None,
        }

    def _parse_pricing_payload(self, payload: Any) -> dict[str, dict[str, Any]]:
        table: dict[str, dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        if isinstance(payload, list):
            rows = [r for r in payload if isinstance(r, dict)]
        elif isinstance(payload, dict):
            for key in ("items", "products", "data"):
                val = payload.get(key)
                if isinstance(val, list):
                    rows = [r for r in val if isinstance(r, dict)]
                    break

        for row in rows:
            sku = str(row.get("sku") or row.get("partNumber") or "").strip().upper()
            if not sku:
                continue

            description = str(row.get("description") or row.get("displayName") or "").strip()
            unit_cost_raw = row.get("unit_cost")
            if unit_cost_raw is None:
                unit_cost_raw = row.get("unitPrice")
            if unit_cost_raw is None:
                unit_cost_raw = row.get("price")
            try:
                unit_price = float(unit_cost_raw)
            except Exception:
                continue

            if unit_price <= 0:
                continue

            category = "compute" if "compute" in description.lower() else "service"
            table[sku] = {
                "description": description or sku,
                "unit_price": unit_price,
                "category": category,
            }

        # keep required fallback SKUs for deterministic validation
        for sku, row in DEFAULT_PRICE_TABLE.items():
            table.setdefault(sku, dict(row))
        return table

    def health(self) -> dict[str, Any]:
        with self._lock:
            snap = self._cache
        if not snap:
            return {
                "ready": False,
                "source": "none",
                "refreshed_at": None,
                "pricing_sku_count": 0,
            }
        return {
            "ready": True,
            "source": snap.source,
            "refreshed_at": int(snap.refreshed_at),
            "pricing_sku_count": len(snap.pricing_table),
        }

    def config(self, default_model_id: str) -> dict[str, Any]:
        status = self.health()
        return {
            "status": "ok",
            "default_model_id": default_model_id,
            "cache": status,
            "allowed_types": ["normal", "question", "final"],
        }

    def chat(
        self,
        *,
        message: str,
        conversation: list[dict[str, str]] | None = None,
        trace_id: str,
        model_id: str,
        text_runner: Callable[[str, str], str] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        with self._lock:
            snap = self._cache

        if not snap:
            return {
                "type": "normal",
                "reply": "BOM data is not ready. Run /api/bom/refresh-data, then retry.",
                "trace_id": trace_id,
                "trace": {
                    "model_id": model_id,
                    "type": "normal",
                    "repair_attempts": 0,
                    "cache_ready": False,
                    "cache_source": "none",
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
            }

        intent = self._classify_intent(message)

        if intent == "normal":
            reply = (
                "I can build a BOM, validate SKUs/prices, and export XLSX. "
                "Ask for a costed BOM with workload sizing details (OCPU, memory, storage, LB)."
            )
            result_type = "normal"
            payload = None
        elif intent == "question":
            reply = (
                "Before finalizing, please confirm: target region, non-GPU vs GPU compute, "
                "and expected OCPU/memory/storage quantities."
            )
            result_type = "question"
            payload = None
        else:
            raw_payload = self._draft_bom_payload(message, snap.pricing_table)
            repaired_payload, attempts, errors = self._repair_until_valid(raw_payload, snap.pricing_table)
            if errors:
                result_type = "question"
                reply = (
                    "I could not finalize a valid BOM after 3 repair attempts. "
                    "Please provide exact SKU-level sizing inputs."
                )
                payload = None
            else:
                result_type = "final"
                payload = self._normalize_payload(repaired_payload)
                reply = "Final BOM prepared. Review line items, then export JSON or XLSX."

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        trace = {
            "model_id": model_id,
            "type": result_type,
            "repair_attempts": attempts if intent == "final" else 0,
            "cache_ready": True,
            "cache_source": snap.source,
            "latency_ms": elapsed_ms,
        }
        response: dict[str, Any] = {
            "type": result_type,
            "reply": reply,
            "trace_id": trace_id,
            "trace": trace,
        }
        if payload is not None:
            response["json_bom"] = json.dumps(payload, ensure_ascii=False, indent=2)
            response["bom_payload"] = payload
            response["score"] = 1.0
        return response

    def _classify_intent(self, message: str) -> str:
        m = (message or "").strip().lower()
        if not m:
            return "normal"
        final_markers = (
            "final",
            "generate bom",
            "bill of materials",
            "cost estimate",
            "price bom",
            "export bom",
        )
        question_markers = ("how", "what", "?", "clarify", "need info")
        sizing_markers = ("ocpu", "gb", "tb", "gpu", "load balancer", "storage")

        if any(marker in m for marker in final_markers):
            return "final"
        if "bom" in m and any(marker in m for marker in sizing_markers):
            return "final"
        if "bom" in m and any(marker in m for marker in question_markers):
            return "question"
        if "bom" in m or "pricing" in m or "cost" in m:
            return "question"
        return "normal"

    def _draft_bom_payload(self, message: str, price_table: dict[str, dict[str, Any]]) -> dict[str, Any]:
        text = message.lower()
        is_gpu = "gpu" in text

        ocpu = self._extract_number(r"(\d+(?:\.\d+)?)\s*ocpu", text, default=4.0)
        mem_gb = self._extract_number(r"(\d+(?:\.\d+)?)\s*gb\s*(?:memory|ram)?", text, default=ocpu * 16)
        block_tb = self._extract_number(r"(\d+(?:\.\d+)?)\s*tb\s*(?:block|storage)", text, default=1.0)
        block_gb = block_tb * 1024.0

        cpu_sku = "B111129" if "e6" in text else "B94176"
        mem_sku = CPU_SKU_TO_MEM_SKU[cpu_sku]

        line_items: list[dict[str, Any]] = []
        line_items.append(self._build_line(cpu_sku, ocpu, price_table, "compute", "Primary compute OCPU"))

        # non-GPU compute must be split into OCPU + memory rows
        if not is_gpu:
            line_items.append(self._build_line(mem_sku, mem_gb, price_table, "compute", "Primary compute memory"))

        line_items.append(self._build_line("B91961", block_gb, price_table, "storage", "Block storage capacity"))

        if "load balancer" in text or "lb" in text or "ingress" in text:
            line_items.append(self._build_line("B93030", 1, price_table, "network", "Flexible load balancer"))

        if "object storage" in text:
            line_items.append(self._build_line("B91628", max(100.0, block_gb * 0.2), price_table, "storage", "Object storage"))

        return {
            "currency": "USD",
            "line_items": line_items,
            "assumptions": [
                "Pricing is estimate-only and non-binding.",
                "Monthly costs assume steady-state usage.",
            ],
        }

    @staticmethod
    def _extract_number(pattern: str, text: str, default: float) -> float:
        match = re.search(pattern, text)
        if not match:
            return default
        try:
            return float(match.group(1))
        except Exception:
            return default

    @staticmethod
    def _build_line(
        sku: str,
        quantity: float,
        price_table: dict[str, dict[str, Any]],
        category: str,
        notes: str,
    ) -> dict[str, Any]:
        row = price_table.get(sku, {"description": sku, "unit_price": 0.0})
        unit_price = float(row.get("unit_price", 0.0) or 0.0)
        qty = float(quantity)
        return {
            "sku": sku,
            "description": str(row.get("description") or sku),
            "category": category,
            "quantity": qty,
            "unit_price": unit_price,
            "extended_price": round(qty * unit_price, 4),
            "notes": notes,
        }

    def _repair_until_valid(
        self,
        payload: dict[str, Any],
        pricing_table: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any], int, list[str]]:
        attempts = 0
        current = json.loads(json.dumps(payload))

        while attempts < 3:
            errors = self.validate_final_payload(current, pricing_table)
            if not errors:
                return current, attempts, []
            attempts += 1
            current = self.repair_payload(current, pricing_table, errors)

        final_errors = self.validate_final_payload(current, pricing_table)
        return current, attempts, final_errors

    def validate_final_payload(
        self,
        payload: dict[str, Any],
        pricing_table: dict[str, dict[str, Any]],
    ) -> list[str]:
        errors: list[str] = []
        line_items = payload.get("line_items")
        if not isinstance(line_items, list) or not line_items:
            return ["line_items must be a non-empty array"]

        seen_cpu = False
        seen_mem = False

        for idx, row in enumerate(line_items):
            if not isinstance(row, dict):
                errors.append(f"line_items[{idx}] must be object")
                continue
            sku = str(row.get("sku") or "").strip().upper()
            if sku not in pricing_table:
                errors.append(f"line_items[{idx}] unknown SKU: {sku}")
                continue
            try:
                unit_price = float(row.get("unit_price"))
            except Exception:
                unit_price = -1
            if unit_price <= 0:
                errors.append(f"line_items[{idx}] non-positive unit_price for SKU {sku}")
            category = str(row.get("category") or "").lower()
            desc = str(row.get("description") or "").lower()
            if category == "compute" and "gpu" not in desc:
                if sku in CPU_SKU_TO_MEM_SKU:
                    seen_cpu = True
                if sku in CPU_SKU_TO_MEM_SKU.values():
                    seen_mem = True

        # non-GPU compute split rule
        if seen_cpu and not seen_mem:
            errors.append("non-GPU compute rows must include both OCPU and memory SKUs")

        return errors

    def repair_payload(
        self,
        payload: dict[str, Any],
        pricing_table: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> dict[str, Any]:
        fixed = json.loads(json.dumps(payload))
        line_items: list[dict[str, Any]] = [
            row for row in fixed.get("line_items", []) if isinstance(row, dict)
        ]
        output: list[dict[str, Any]] = []

        for row in line_items:
            sku = str(row.get("sku") or "").strip().upper()
            if sku not in pricing_table:
                continue
            ref = pricing_table[sku]
            row["description"] = row.get("description") or ref.get("description") or sku
            row["category"] = row.get("category") or ref.get("category") or "service"
            try:
                quantity = float(row.get("quantity") or 0)
            except Exception:
                quantity = 0.0
            if quantity <= 0:
                quantity = 1.0
            row["quantity"] = quantity
            try:
                unit_price = float(row.get("unit_price") or 0)
            except Exception:
                unit_price = 0.0
            if unit_price <= 0:
                unit_price = float(ref.get("unit_price", 0.0) or 0.0)
            row["unit_price"] = unit_price
            row["extended_price"] = round(quantity * unit_price, 4)
            output.append(row)

        sku_set = {str(row.get("sku") or "").strip().upper() for row in output}
        for cpu_sku, mem_sku in CPU_SKU_TO_MEM_SKU.items():
            if cpu_sku in sku_set and mem_sku not in sku_set:
                cpu_row = next((r for r in output if str(r.get("sku")).upper() == cpu_sku), None)
                qty = float(cpu_row.get("quantity", 1.0) if cpu_row else 1.0)
                mem_ref = pricing_table[mem_sku]
                output.append(
                    {
                        "sku": mem_sku,
                        "description": str(mem_ref.get("description") or mem_sku),
                        "category": "compute",
                        "quantity": max(1.0, qty * 16.0),
                        "unit_price": float(mem_ref.get("unit_price", 0.0) or 0.0),
                        "extended_price": round(max(1.0, qty * 16.0) * float(mem_ref.get("unit_price", 0.0) or 0.0), 4),
                        "notes": "Auto-repair: added memory SKU for non-GPU split rule",
                    }
                )

        fixed["line_items"] = output
        return self._normalize_payload(fixed)

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        norm = json.loads(json.dumps(payload))
        line_items = norm.get("line_items") or []
        total = 0.0
        for row in line_items:
            qty = float(row.get("quantity") or 0)
            price = float(row.get("unit_price") or 0)
            row["quantity"] = round(qty, 4)
            row["unit_price"] = round(price, 6)
            row["extended_price"] = round(qty * price, 4)
            total += float(row["extended_price"])
        norm["currency"] = str(norm.get("currency") or "USD")
        norm["totals"] = {"estimated_monthly_cost": round(total, 4)}
        assumptions = norm.get("assumptions")
        if not isinstance(assumptions, list):
            norm["assumptions"] = []
        return norm

    def generate_xlsx(self, payload: dict[str, Any]) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "BOM"

        headers = [
            "SKU",
            "Description",
            "Category",
            "Quantity",
            "Unit Price (USD)",
            "Extended Price (USD)",
            "Notes",
        ]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)

        line_items = payload.get("line_items") or []
        for item in line_items:
            ws.append(
                [
                    item.get("sku", ""),
                    item.get("description", ""),
                    item.get("category", ""),
                    float(item.get("quantity") or 0),
                    float(item.get("unit_price") or 0),
                    None,  # formula column
                    item.get("notes", ""),
                ]
            )

        start_row = 2
        end_row = max(1, len(line_items) + 1)
        for row_idx in range(start_row, end_row + 1):
            ws.cell(row=row_idx, column=6, value=f"=D{row_idx}*E{row_idx}")

        total_row = end_row + 2
        ws.cell(row=total_row, column=5, value="TOTAL")
        ws.cell(row=total_row, column=6, value=f"=SUM(F{start_row}:F{end_row})")
        ws.cell(row=total_row, column=5).font = Font(bold=True)
        ws.cell(row=total_row, column=6).font = Font(bold=True)

        ws.column_dimensions["A"].width = 14
        ws.column_dimensions["B"].width = 38
        ws.column_dimensions["C"].width = 14
        ws.column_dimensions["D"].width = 12
        ws.column_dimensions["E"].width = 18
        ws.column_dimensions["F"].width = 20
        ws.column_dimensions["G"].width = 42

        output = io.BytesIO()
        wb.save(output)
        return output.getvalue()


_SHARED_SERVICE: BomService | None = None


def get_shared_bom_service() -> BomService:
    global _SHARED_SERVICE
    if _SHARED_SERVICE is None:
        _SHARED_SERVICE = BomService()
    return _SHARED_SERVICE


def new_trace_id() -> str:
    return str(uuid.uuid4())
