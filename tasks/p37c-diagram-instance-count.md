# Task p37c: Diagram Instance Count — Preserve Quantity Through Pipeline

## Goal

When a BOM contains "2 × E4.Flex 2 OCPU each" (4 OCPU total), the diagram
renders a single node. The `ServiceItem.quantity` field exists in
`agent/bom_parser.py` but the instance count is lost before it reaches the
diagram sub-agent.

Fix: add `instance_count` as a distinct field in `ServiceItem`, propagate it
through the layout intent prompt, and update the diagram label to show
"2 × E4.Flex" rather than a single unlabelled node.

---

## Scope

**Modify:**
- `agent/bom_parser.py` — add `instance_count` field to `ServiceItem`, populate it
- `agent/intent_compiler.py` — if it re-labels nodes, preserve instance count
- The layout intent prompt construction (wherever `build_layout_intent_prompt`
  or equivalent assembles the prompt for the LLM) — include instance_count

**Do NOT touch:** `agent/layout_engine.py`, `agent/drawio_generator.py`,
`sub_agents/diagram/`, or any handler.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/bom_parser.py agent/intent_compiler.py
grep "ServiceItem\|quantity\|instance" agent/bom_parser.py | head -20
```

---

## What to implement

### 1. Add `instance_count` to `ServiceItem`

In `agent/bom_parser.py`, update the `ServiceItem` dataclass:

```python
@dataclass
class ServiceItem:
    id:             str
    oci_type:       str
    label:          str
    layer:          str
    quantity:       Optional[float] = None   # total units (OCPUs, GB, etc.)
    instance_count: Optional[int]   = None   # number of discrete instances
    notes:          str = ""
```

### 2. Populate `instance_count` during BOM parsing

When parsing BOM rows (from XLSX or from inline text), extract the instance
count separately from total OCPUs:

- For a row "2 × E4.Flex 2 OCPU" → `instance_count=2`, `quantity=4.0`
- For "4 OCPU E4.Flex" (ambiguous) → `instance_count=None`, `quantity=4.0`
- For "1 × E4.Flex 8 OCPU" → `instance_count=1`, `quantity=8.0`

Look for patterns like `N ×`, `N x`, `N instances`, `qty: N` in the BOM row text.

### 3. Inject instance_count into layout intent prompt

Wherever the layout intent prompt is assembled (search for
`build_layout_intent_prompt` or the function that constructs the LLM prompt
for diagram generation), include `instance_count` in the node description:

```python
# Current
f"{item.label} ({item.oci_type})"

# Updated  
count_str = f"{item.instance_count} × " if item.instance_count and item.instance_count > 1 else ""
f"{count_str}{item.label} ({item.oci_type})"
```

The LLM layout intent output will then label the node as "2 × E4.Flex" and
the diagram generator uses that label verbatim.

### 4. No change to draw.io output structure

Do NOT change how the drawio_generator renders nodes. A "2 × E4.Flex" label
on a single node is acceptable and accurate — it represents a group. Creating
2 separate nodes would require layout changes that are out of scope here.

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/bom_parser.py agent/intent_compiler.py` exits 0
2. `grep "instance_count" agent/bom_parser.py` — at least 2 matches (field + population)
3. Unit test: parse a BOM row containing "2 × E4.Flex 2 OCPU" and assert
   `item.instance_count == 2` and `item.quantity == 4.0`
4. The layout intent prompt string for a 2-instance item contains "2 ×"
5. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Commit Message

```
p37c: add instance_count to ServiceItem and propagate through layout intent prompt
```

Branch: `claude/p37c` (from main after p37a merges). Push when done.
