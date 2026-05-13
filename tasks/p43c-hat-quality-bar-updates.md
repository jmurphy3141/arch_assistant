# Task p43c: Update Hat Quality Bar Items

## Objective

The hat Quality Bar items ask the post-review LLM to check things like
"All BOM services represented" — but before p43a, the reviewer had only a
one-liner summary to check against. Now that p43a enriches handler summaries
with node inventory, service lists, and findings counts, the Quality Bar items
can be made specific and directly checkable.

Update three hats to reference the new summary fields, and add AI/ML
completeness checks to the diagram hat.

---

## Scope

**Touch:**
- `agent/hats/diagram_for_oci.md`
- `agent/hats/oci_bom_expert.md`
- `agent/hats/oci_waf_reviewer.md`

**Do NOT touch:** `skillforge/`, Python files, other hat files.

**Prerequisite:** p43a must be merged before this task runs.

---

## Prerequisite Check

```bash
grep "AI/ML services\|nodes:.*×\|service count\|findings.*P1" \
  agent/hats/diagram_for_oci.md agent/hats/oci_bom_expert.md agent/hats/oci_waf_reviewer.md
# must be zero matches
```

---

## Changes

### 1. `agent/hats/diagram_for_oci.md` — Quality Bar

Find the `## Quality Bar` section. Make these edits:

**Update item 9** (currently about `node_count`) to:
```
9. The result summary contains a node inventory in the format
   "N nodes: category×count, ..." — verify N is plausible for the requested
   architecture (a 3-tier HA web app should have ≥ 8 nodes).
```

**Add two new items after item 9:**
```
10. AI/ML services are present in the node inventory whenever the user
    requested an AI diagram, LLM endpoint, RAG pipeline, or GenAI feature
    (look for `generativeai`, `aiservice`, `datasciencenotebook`, or similar
    categories in the inventory string).
11. No obviously required service category is missing given the request
    (e.g. a "secure web app" must have a load balancer and WAF node; a
    "database tier" must have a database node).
```

### 2. `agent/hats/oci_bom_expert.md` — Quality Bar

Find the `## Quality Bar` section. Add a new item after the existing
`artifact_key` check:

```
6. The result summary is in the enriched format:
   "BOM generated (N services, $X/mo): service1, service2, ..."
   Verify N matches the number of line_items in the BOM payload and that
   the named services correspond to what the user requested.
```

Renumber subsequent items if needed.

### 3. `agent/hats/oci_waf_reviewer.md` — Quality Bar

Find the `## Quality Bar` section. Update the existing `artifact_key` item
(currently item 9) to:

```
9. The result summary is in the enriched format:
   "WAF vN saved. M findings (K P1)."
   Verify total findings count is non-zero (a review with 0 findings is a
   red flag — confirm the review covered all 6 pillars).
   Verify P1 findings are present if the architecture has public-facing
   services (LB, WAF, API Gateway) or unencrypted storage.
```

---

## Acceptance Criteria

1. New items present in all three hats:
   ```bash
   grep "AI/ML services\|node inventory\|enriched format\|M findings" \
     agent/hats/diagram_for_oci.md agent/hats/oci_bom_expert.md \
     agent/hats/oci_waf_reviewer.md | wc -l
   # must be ≥ 3
   ```

2. No broken markdown (hats are valid UTF-8 text files):
   ```bash
   python3.11 -c "
   for f in ['agent/hats/diagram_for_oci.md','agent/hats/oci_bom_expert.md','agent/hats/oci_waf_reviewer.md']:
       open(f).read()
   print('hats readable OK')
   "
   ```

3. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p43c: update hat Quality Bar items — checkable against p43a enriched summaries
```

Branch: `claude/p43c` (from main, after p43a and p43b merged). Push when done.
