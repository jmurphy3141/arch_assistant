# Task p41a: Enable Step 3 Planning in Archie

## Objective

`step3_planning=False` is the Forge default so existing callers are unaffected.
But Archie should benefit from the Step 3 hat-selection reasoning added in p40d.
Wire `step3_planning=True` into `build_forge()` in `agent/archie_wiring.py`, and
expose it as a `build_forge()` parameter so callers can override.

---

## Scope

**Touch:**
- `agent/archie_wiring.py` — add `step3_planning` param to `build_forge()` and
  pass it to `Forge(...)`

**Do NOT touch:** `skillforge/forge.py`, hat files, skill files, tests, other
Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/archie_wiring.py
grep "step3_planning" agent/archie_wiring.py  # must be zero
```

---

## Change

### 1. Add parameter to `build_forge()`

```python
def build_forge(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    text_runner: Callable,
    a2a_base_url: str = "",
    base_system_prompt: str = "",
    step3_planning: bool = True,   # ← new
) -> Forge:
```

### 2. Pass it to `Forge(...)`

```python
    forge = Forge(
        base_system_prompt=full_prompt,
        hat_engine=hat_engine,
        memory=memory,
        text_runner=text_runner,
        prompt_enricher=enricher,
        max_iterations=5,
        step3_planning=step3_planning,   # ← new
    )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall agent/archie_wiring.py
   ```

2. Parameter is wired:
   ```bash
   grep "step3_planning" agent/archie_wiring.py | wc -l
   # must be ≥ 2 (signature + Forge call)
   ```

3. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5
   ```

---

## Commit Message

```
p41a: enable step3_planning in build_forge() — Archie now reasons through Steps 1–3 before the loop
```

Branch: `claude/p41a` (from main). Push when done.
