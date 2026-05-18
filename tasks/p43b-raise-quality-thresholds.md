# Task p43b: Raise Expert Quality Thresholds

## Objective

`_EXPERT_THINKING_MIN_CHARS = 300` allows pre-action reasoning as short as
three brief paragraphs. `_EXPERT_REVIEW_MIN_CHARS = 500` allows a similarly
shallow post-review. For expert-level OCI architecture reasoning, these floors
are too low to enforce the depth the quality bar requires.

Raise both constants to enforce longer, more thorough reasoning.

---

## Scope

**Touch:**
- `skillforge/forge.py` — two constant value changes only

**Do NOT touch:** any other file.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "_EXPERT_THINKING_MIN_CHARS\|_EXPERT_REVIEW_MIN_CHARS" skillforge/forge.py
# must show 300 and 500
```

---

## Change

In `skillforge/forge.py`, find and update:

```python
_EXPERT_THINKING_MIN_CHARS = 300
```
→
```python
_EXPERT_THINKING_MIN_CHARS = 600
```

And:

```python
_EXPERT_REVIEW_MIN_CHARS = 500
```
→
```python
_EXPERT_REVIEW_MIN_CHARS = 800
```

That is the entire change. Do not touch any other line.

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Updated values confirmed:
   ```bash
   grep "_EXPERT_THINKING_MIN_CHARS\|_EXPERT_REVIEW_MIN_CHARS" skillforge/forge.py
   # must show 600 and 800
   ```

3. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p43b: raise expert quality thresholds — pre-action 300→600 chars, post-review 500→800 chars
```

Branch: `claude/p43b` (from main, can develop in parallel with p43a). Push when done.
