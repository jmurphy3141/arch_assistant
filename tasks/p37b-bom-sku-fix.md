# Task p37b: BOM SKU Mapping — Live Catalog Approach

## Goal

Two problems:
1. `agent/bom_parser.py` `SKU_MAP` mislabels B94176 as "E3/E4 OCPU" (it is X9
   Intel) and is missing the E4.Flex SKUs (B93113 / B93114).
2. `agent/bom_service.py` defaults to B94176 (X9 Intel) whenever the customer
   does not specify a shape — E4.Flex should be the default general-purpose VM.

The pricing API (`OCI_PRICE_LIST_API_URL`) and shapes scrape are already live in
`bom_service.py`. This task wires them correctly: build a `SHAPE_SKU_CATALOG`
string dynamically from the live `price_table` and inject it into every LLM BOM
draft so the model selects SKUs from authoritative data, not hardcoded defaults.

---

## Scope

**Modify:**
- `agent/bom_parser.py` — fix SKU_MAP labels and add E4 entries
- `agent/bom_service.py` — fix default cpu_sku fallback; add `_build_shape_catalog()`; inject catalog into `_draft_bom_payload`

**Do NOT touch:** `sub_agents/bom/system_prompt.md`, diagram pipeline,
terraform, or any handler.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/bom_parser.py agent/bom_service.py
grep "B94176\|B93113" agent/bom_parser.py
grep "cpu_sku\|B94176" agent/bom_service.py | grep -v "DEFAULT_PRICE\|CPU_SKU_TO"
```

---

## What to implement

### 1. Fix `agent/bom_parser.py` SKU_MAP

**Current (wrong):**
```python
"B94176":  ("compute", "compute"),   # E3/E4 OCPU
"B94177":  (None,      None),        # E3/E4 memory — part of compute
```

**Correct:**
```python
"B94176":  ("compute", "compute"),   # X9 (Intel Standard) OCPU
"B94177":  (None,      None),        # X9 (Intel Standard) memory
"B93113":  ("compute", "compute"),   # E4 (AMD Standard) OCPU
"B93114":  (None,      None),        # E4 (AMD Standard) memory
```

(`CPU_SKU_TO_MEM_SKU` in `bom_service.py` already maps B93113 → B93114; no
change needed there.)

### 2. Add `_build_shape_catalog()` to `BomService`

Build a compact shape→SKU reference from the live `price_table`. The table
already contains descriptions like `"Compute - Standard - E4 - OCPU"`.

```python
def _build_shape_catalog(self, price_table: dict[str, dict]) -> str:
    """
    Build a compact shape→SKU reference from the live price table.
    Returns a pipe-delimited string for injection into LLM prompts.
    """
    SHAPE_KEYWORDS = [
        ("e4", "E4.Flex (AMD)", "OCPU"),
        ("e5", "E5.Flex (AMD)", "OCPU"),
        ("e6", "E6.Flex (AMD)", "OCPU"),
        ("x9", "X9 (Intel Standard)", "OCPU"),
        ("a1", "A1.Flex (Ampere)", "OCPU"),
        ("gpu", "GPU shapes", "GPU"),
    ]
    lines = ["Shape | CPU SKU | CPU $/hr | Mem SKU | Mem $/hr"]
    for keyword, label, _ in SHAPE_KEYWORDS:
        for sku, row in price_table.items():
            desc = row.get("description", "").lower()
            metric = row.get("metric", "").lower()
            if keyword in desc and "ocpu" in metric:
                mem_sku = CPU_SKU_TO_MEM_SKU.get(sku, "—")
                mem_row = price_table.get(mem_sku, {})
                lines.append(
                    f"{label} | {sku} | ${row.get('unit_price', 0):.4f} "
                    f"| {mem_sku} | ${mem_row.get('unit_price', 0):.4f}"
                )
                break
    return "\n".join(lines)
```

### 3. Inject catalog into `_draft_bom_payload`

`_draft_bom_payload` has a hardcoded shape-detection block that defaults to
`cpu_sku = "B94176"` (X9). Replace the entire shape detection block:

**Before:**
```python
        if shape_hint == "a1":
            cpu_sku = "B93297"
        elif shape_hint == "e6":
            cpu_sku = "B111129"
        else:
            cpu_sku = "B94176"
```

**After:**
```python
        # Determine CPU SKU from text hints; default to E4 (AMD general-purpose)
        if shape_hint == "a1" or "ampere" in text:
            cpu_sku = "B93297"
        elif "e6" in text or shape_hint == "e6":
            cpu_sku = "B111129"
        elif "e5" in text or shape_hint == "e5":
            cpu_sku = "B97384"
        elif "x9" in text or "intel" in text:
            cpu_sku = "B94176"
        else:
            cpu_sku = "B93113"   # E4.Flex — OCI default general-purpose VM
```

### 4. Fix `_build_compute_from_structured`

Same default correction:

**Before:**
```python
        cpu_sku = "B97384" if is_native else "B94176"
```

**After:**
```python
        cpu_sku = "B97384" if is_native else "B93113"   # E4.Flex default
```

### 5. Inject `_build_shape_catalog()` into the BOM service prompt

In `_draft_bom_payload`, after computing `cpu_sku`, call
`self._build_shape_catalog(price_table)` and store the result. If the method
makes an LLM call, prepend the catalog as a context block in the prompt string.
If it uses only regex extraction (no LLM call), add the catalog string to the
`trace` output under key `"shape_catalog"` so it is visible for diagnostics.
*Do not break the existing fast-path regex extraction.*

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/bom_parser.py agent/bom_service.py` exits 0
2. `grep "B93113" agent/bom_parser.py` — matches
3. `grep "X9" agent/bom_parser.py` — comment on B94176 says "X9", not "E4"
4. `grep "B93113" agent/bom_service.py` — at least 2 matches (default assignment + catalog)
5. `grep "cpu_sku.*B94176\b" agent/bom_service.py | grep -v DEFAULT_PRICE | grep -v "CPU_SKU_TO"` — zero matches (all hardcoded X9 defaults removed)
6. Unit test: instantiate `BomService`, call `_build_shape_catalog(DEFAULT_PRICE_TABLE)` — assert return is a non-empty string containing "E4"
7. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Do NOT Do

- Do not add any new hardcoded price values to `DEFAULT_PRICE_TABLE` (live API
  already fetches them; the table is a fallback only)
- Do not add a static shape→SKU dict in Python — the catalog must derive from
  `price_table` so it stays current when Oracle adds new shapes
- Do not touch `sub_agents/bom/system_prompt.md` or any A2A handler

---

## Commit Message

```
p37b: default to E4.Flex SKU; build shape catalog from live price table
```

Branch: `claude/p37b` (from main after p37a merges). Push when done.
