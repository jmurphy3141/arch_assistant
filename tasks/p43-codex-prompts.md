# p43 Codex Prompts — Post-Review Sight, Quality Depth, Hat Quality Bars

## Background

p39–p42 built the full expert reasoning loop (pre-action, post-review, hat
gates). Three genuine gaps remain: the post-review is blind to artifact
content (one-liner summaries), quality thresholds are too low, and hat
Quality Bar items aren't checkable against those one-liners.

p43a fixes the summaries. p43b raises the thresholds. p43c updates the hats
to reference the richer data. p43b can be developed in parallel with p43a.
p43c must wait until both are merged.

---

## p43a — Richer Handler Result Summaries

```
Read tasks/p43a-richer-handler-summaries.md carefully end to end before
touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p43a origin/main

Run the prerequisite check first:
  python3.11 -m compileall agent/tools/diagram.py agent/tools/bom.py agent/tools/specialists.py
  grep "_summarise_drawio\|findings_summary\|service_count\|nodes:" \
    agent/tools/diagram.py agent/tools/bom.py agent/tools/specialists.py
  # must be zero matches

Read these files before implementing to understand the exact structure:
  agent/tools/diagram.py  — find the _call_generate_diagram return and
    the ToolResult construction (look for summary= argument)
  agent/tools/bom.py      — find _extract_bom_payload and where
    "BOM generated with structured payload." is set
  agent/tools/specialists.py — find the return ToolResult(...) in
    _SpecialistHandler.__call__ and where summary= is set

Implement in this order:
1. diagram.py — add _summarise_drawio() helper at module level; update
   ToolResult summary to include inventory string
2. bom.py — replace "BOM generated with structured payload." with the
   enriched service count + monthly total + service names summary
3. specialists.py — add findings_summary block gated on
   self._agent_name == "waf"; update ToolResult summary to append it

Run ALL acceptance criteria checks before committing.

Commit message:
p43a: richer handler summaries — node inventory, service list, WAF findings for post-review

Branch: claude/p43a (from main). Push when done.
```

---

## p43b — Raise Expert Quality Thresholds

```
Read tasks/p43b-raise-quality-thresholds.md carefully.

IMPORTANT: Branch from origin/main. This can develop in parallel with p43a
since it only touches skillforge/forge.py.

  git fetch origin
  git checkout -b claude/p43b origin/main

Run the prerequisite check:
  grep "_EXPERT_THINKING_MIN_CHARS\|_EXPERT_REVIEW_MIN_CHARS" skillforge/forge.py
  # must show 300 and 500

Change _EXPERT_THINKING_MIN_CHARS from 300 to 600.
Change _EXPERT_REVIEW_MIN_CHARS from 500 to 800.
That is the entire change. Do not touch any other file or line.

Run ALL acceptance criteria checks before committing.

Commit message:
p43b: raise expert quality thresholds — pre-action 300→600 chars, post-review 500→800 chars

Branch: claude/p43b (from main). Push when done.
```

---

## p43c — Update Hat Quality Bar Items

```
Read tasks/p43c-hat-quality-bar-updates.md carefully end to end before
touching any files.

IMPORTANT: Branch from origin/main AFTER p43a and p43b are both merged.

  git fetch origin
  git checkout -b claude/p43c origin/main

Run the prerequisite check:
  grep "AI/ML services\|nodes:.*×\|service count\|findings.*P1" \
    agent/hats/diagram_for_oci.md agent/hats/oci_bom_expert.md \
    agent/hats/oci_waf_reviewer.md
  # must be zero matches

Read each hat file's ## Quality Bar section before editing it.

Implement:
1. agent/hats/diagram_for_oci.md — update item 9 to reference node inventory
   format; add items 10 (AI/ML services) and 11 (no missing service categories)
2. agent/hats/oci_bom_expert.md — add item referencing enriched BOM summary
   format with service count and monthly total
3. agent/hats/oci_waf_reviewer.md — update item 9 to reference findings count
   and P1 count from enriched WAF summary

Run ALL acceptance criteria checks before committing.

Commit message:
p43c: update hat Quality Bar items — checkable against p43a enriched summaries

Branch: claude/p43c (from main, after p43a and p43b merged). Push when done.
```
