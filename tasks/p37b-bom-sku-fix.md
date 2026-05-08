# Task p37b: BOM SKU Mapping Fix — E4.Flex vs X9 Intel

## Goal

The BOM pipeline incorrectly labels SKU B94176 as "E3/E4 OCPU" in
`agent/bom_parser.py` when it is actually the X9 (Intel) SKU. The E4 (AMD)
SKUs — B93113 (OCPU) and B93114 (memory) — are missing from `SKU_MAP`
entirely. Additionally, the BOM generation sub-agent (`sub_agents/bom/`)
needs its prompt updated so the LLM selects E4 (AMD) SKUs when the customer
requests E4.Flex shapes.

---

## Scope

**Modify:**
- `agent/bom_parser.py` — fix SKU_MAP comments and add E4 entries
- `agent/bom_service.py` — verify E4 entries exist (read-only check; add if missing)
- `sub_agents/bom/` — update system prompt / SKU guidance so LLM chooses E4 SKUs

**Do NOT touch:** diagram pipeline, terraform, or any handler.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/bom_parser.py agent/bom_service.py
grep "B93113\|B94176" agent/bom_parser.py
grep "B93113\|B94176" agent/bom_service.py
```

---

## What to implement

### 1. Fix `agent/bom_parser.py` SKU_MAP

**Current (wrong):**
```python
"B94176": ("compute", "compute"),  # E3/E4 OCPU
"B94177": (None,      None),       # E3/E4 memory — part of compute
```

**Correct:**
```python
"B94176": ("compute", "compute"),  # X9 (Intel Standard) OCPU
"B94177": (None,      None),       # X9 (Intel Standard) memory
"B93113": ("compute", "compute"),  # E4 (AMD Standard) OCPU
"B93114": (None,      None),       # E4 (AMD Standard) memory
"B88514": ("compute", "compute"),  # A1 (Ampere) OCPU
"B88515": (None,      None),       # A1 (Ampere) memory
```

Add A1 entries while here — they are similarly missing.

### 2. Verify `agent/bom_service.py`

Check that `bom_service.py`'s SKU catalogue includes E4 entries with correct
pricing. Expected:
```python
"B93113": {"description": "Compute - Standard - E4 - OCPU",   "unit_price": 0.025, ...},
"B93114": {"description": "Compute - Standard - E4 - Memory",  "unit_price": 0.0015, ...},
```

If absent, add them. Do not change existing entries.

### 3. Update BOM sub-agent prompt

Read the system prompt in `sub_agents/bom/`. Find the section that instructs
the LLM on SKU selection. Add or update the shape → SKU mapping guidance:

```
OCI Compute SKU mapping (use these exact SKU codes):
  E4.Flex (AMD)    → OCPU: B93113, Memory: B93114  ($0.025/OCPU, $0.0015/GB)
  E3.Flex (Intel)  → OCPU: B91961, Memory: B91962
  X9 Standard      → OCPU: B94176, Memory: B94177  ($0.04/OCPU)
  A1.Flex (Ampere) → OCPU: B88514, Memory: B88515  ($0.01/OCPU)
  BM.GPU4.8        → GPU SKU per shape — always request explicit confirmation

When a customer requests E4.Flex, use B93113/B93114. Do NOT use B94176 for E4.
```

Find the exact file and location in `sub_agents/bom/` (likely a `.py`, `.md`,
or `.txt` system prompt file) and insert this guidance.

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/bom_parser.py agent/bom_service.py` exits 0
2. `grep "B93113" agent/bom_parser.py` — matches
3. `grep "B94176" agent/bom_parser.py` — matches with correct comment (X9, not E4)
4. `grep "B93113" agent/bom_service.py` — matches with correct unit_price (0.025)
5. BOM sub-agent prompt contains "E4.Flex" → "B93113" guidance
6. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Commit Message

```
p37b: fix E4.Flex SKU mapping — B93113/B93114 not B94176/B94177
```

Branch: `claude/p37b` (from main after p37a merges). Push when done.
