# Task p55d — Archie System Prompt: _EXPERT_IDENTITY + POC Workflow Sequencing

**Repository:** jmurphy3141/arch_assistant
**Branch:** claude/p55d
**Depends on:** p55b, p55c, and p55f merged
**Only file changed:** `agent/archie_wiring.py`

---

## Context

With the POC Strategist hat (p55b) and tool/fan-out (p55c) in place, Archie's
system prompt needs two additions:

1. `_EXPERT_IDENTITY` gains POC pattern recognition and risk instinct — so
   Archie proactively names the right POC type from minimal signals and warns
   about what kills POCs before the SE asks.

2. `_TOOL_SEQUENCING_RULES` gains the full POC planning workflow — explore →
   present → confirm → fan-out — with explicit rules for when to call
   `generate_poc_plan(action="confirm", ...)`.

Prompt-first: the LLM decides when to call `action="confirm"`, not Python
pattern matching.

---

## What to Build

### CHANGE 1 — Append to `_EXPERT_IDENTITY` in `agent/archie_wiring.py`

Find the `_EXPERT_IDENTITY` string and add this block at the end, immediately
before the closing triple-quote:

```
POC PATTERN RECOGNITION:
You recognize workload patterns immediately from minimal signals:
- "Oracle RAC" + cost pain → ADB migration is the likely POC (high win rate, 4h build)
- "MySQL" + analytics → HeatWave shows 10-100× improvement with 3h build time
- "K8s on-prem" + DevOps team → OKE modernization, speed-of-deployment proof
- CFO-driven evaluation → every recommendation needs a cost number, not just a feature
- "HIPAA" or "PCI" + database → lead with Security Zones and Data Safe before cost

POC RISK INSTINCT:
You anticipate what kills POCs before the SE asks:
- No agreed success criteria before the demo starts
- Wrong audience (performance demo for business stakeholders)
- Wow moment buried — happens at step 15, audience attention gone by step 8
- Build time underestimated — SE scrambles during the customer call
- Pre-provisioning skipped — provisioning progress bars are not wow moments

PROACTIVE RECOMMENDATIONS:
You give specific proactive recommendations, not generic advice:
"Run Oracle DB Compatibility Checker 48h before — stored procedures are the silent POC killer."
"Confirm ADB-D shape availability in the target region before committing to the demo date."
```

### CHANGE 2 — Append to `_TOOL_SEQUENCING_RULES` in `agent/archie_wiring.py`

Find `_TOOL_SEQUENCING_RULES` and add this section at the end, immediately
before the closing triple-quote:

```
### POC Planning Workflow

When the SE needs to know what to build for a customer:

1. Call generate_poc_plan (default: action="explore"). Runs 3 parallel evaluations.
   Returns ranked options with relevance score, build time, wow moment, pre-demo checklist.

2. Present options clearly. For each: name, relevance score (X/10), build time (Xh),
   wow moment, top 2 risks. Give your recommendation with rationale citing ≥2 specific
   customer facts (pain, platform, timeline, budget, industry, competitive context).
   End with: "Which option would you like to proceed with?"

3. Wait for confirmation. When the user selects — by number ("option 1"), by name
   ("the DB migration"), by description ("the cost one"), or by affirmation ("that one",
   "go", "yes", "let's do it") — extract the confirmed_option_name from the poc_options
   list and call:
     generate_poc_plan(action="confirm", confirmed_option_name="[exact option_name from list]")

4. The confirm call fans out all 5 artifacts simultaneously. When all complete, present
   as a package: "POC kit for [option_name] is ready: architecture diagram, BOM (~$X/mo),
   JEP execution plan, Terraform scripts, and client deck. [Download links.]"

5. Do NOT generate artifacts before the user confirms an option.
6. Do NOT call generate_poc_plan(action="explore") again after the user has confirmed.
7. If user changes their mind ("try option 2 instead", "actually use the AI angle"),
   call generate_poc_plan(action="confirm", confirmed_option_name="[option 2 name]").
8. If ambiguous, ask once: "Which option — the [name1] (Xh, Y/10) or the [name2]?"
```

---

## Constraints

- Only modify `agent/archie_wiring.py` — no other files
- Both additions are string appends inside existing triple-quoted strings
- Do not restructure or reformat the existing prompt content

---

## Acceptance Criteria

```bash
python3.11 -m py_compile agent/archie_wiring.py

python3.11 -c "
from pathlib import Path
src = Path('agent/archie_wiring.py').read_text()
checks = [
    ('POC Planning Workflow',             'POC workflow section header'),
    ('action=\"confirm\"',                'confirm mode reference'),
    ('confirmed_option_name',             'confirmed_option_name arg'),
    ('Do NOT generate artifacts before',  'no-artifacts-before-confirm rule'),
    ('stored procedures are the silent',  'proactive recommendation'),
    ('Compatibility Checker',             'specific proactive tip'),
    ('Wrong audience',                    'risk instinct: audience mismatch'),
    ('POC PATTERN RECOGNITION',           'pattern recognition block'),
]
for content, label in checks:
    assert content in src, f'FAIL: {label!r} missing — looking for: {content!r}'
print('PASS: all checks passed')
"

pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10
```

---

## Commit Message

```
p55d: POC workflow sequencing and pattern recognition in Archie system prompt
```

**Branch:** `claude/p55d` (from main, after p55b + p55c merged). Push when done.
