# tasks/

Each file is a self-contained unit of work for Codex.

## How to use

1. Read `PLAN.md` first. Understand the target architecture.
2. Pick the next task in the current phase.
3. Read the task file completely before writing a single line.
4. Implement only what the task says. Nothing more.
5. Run the acceptance criteria commands. All must pass.
6. Open a PR. Do not merge. Set status to `in-progress` in the task file header.

## Rules

- If the task conflicts with `PLAN.md`, stop. Flag it. Do not improvise.
- Do not modify `PLAN.md` or `AGENTS.md` or `CLAUDE.md` unless the task explicitly says to.
- Do not add error handling, logging, or abstractions not mentioned in the task.
- Do not clean up unrelated code while doing a task.
- One PR per task file.

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| p0 | Config fix | ✅ done |
| p1 | A2A base + sub-agent scaffolding | ✅ done |
| p2 | Archie loop, memory, tools | ✅ done |
| p3 | Hat engine, critic/governor | ✅ done |
| p35 | Declarative registration, skill files, A2ADelegate, parallel tools | ✅ done |
| p36 | Dead route cleanup, extract terraform/chat-stream/specialists | ✅ done |
| p37 | Hat persistence, BOM SKU fix, auto-coordinate, POV interview mode | ✅ done |
| p38 | Hat quality uplift — BOM, diagram, Terraform, WAF, POV, JEP, critic/governor | ✅ done |
| p39 | Manager reasoning loop skill, hat pre/post-action sections, structured loop | ✅ done |
| p40 | Pre-action header validation, iterate context, loop events, step3 planning | ✅ done |
| p41 | Enable step3 planning, iterate correction directive, turn stats | ✅ done |
| p42 | requires_hat gate, pre-action-always, thinking status events | ✅ done |
| p43 | Richer summaries, quality thresholds, hat Quality Bar updates, realtime stream | ✅ done |
| p44 | Enrich Archie system prompt, remove workflow/parallel bypasses, architecture guard | ✅ done |
| p45 | Native tool use (OCI GenAI SDK direct), streaming wiring | ✅ done |
| p46 | BOM server count multiplier, generate_bom prompt arg, cap critique retries | ✅ done |
| p47 | Live Forge status events via reasoning_sink | ✅ done |
| p48 | Approved-tools guard — break after first tool approval | ✅ done |
| p50 | Expert quality depth — pre-action defaults, star-gate clarification | ✅ done |
| **p51** | **Expert reasoning depth — pre-action workload pattern + risk + proactive flag, structured critic, Phase D post-review** | ✅ done |
| **p52** | **Expert identity (Archie wiring), architectural risk in step3, memory enrichment** | ✅ done |

## Current Task Files (p51/p52 — all merged to main)

| File | What it did |
|------|-------------|
| `p51-strategic-plan.md` | Authoritative spec for p51a–p52c — read before implementing anything in this area |
| `p51a` | `agent/hat_engine.py` — inject Pre-Action Checklist + Post-Action Review into expert block |
| `p51b` | `skillforge/forge.py` — pre-action: WORKLOAD PATTERN, GAPS+defaults, RECOMMENDATION, WHY, TOP RISK, PROACTIVE FLAG, SUB-AGENT TASK |
| `p51c` | `skillforge/forge.py` — critic: per-item Quality Bar PASS/FAIL, no rubber-stamping |
| `p51d` | `skillforge/forge.py` — post-review Phase D: GOAL FIT, ANTIPATTERNS, NEXT STEP FLAG; min chars 800→1000 |
| `p52a` | `skillforge/forge.py` — step3 planning: primary architectural risk question in STEP 1 |
| `p52b` | `agent/archie_wiring.py` — Expert Identity: PATTERN RECOGNITION, RISK INSTINCT, SPECIFICITY, ASSUMPTION SURFACING, PROACTIVE GUIDANCE |
| `p52c` | `agent/archie_memory_impl.py` — enrich prompt: infrastructure_profile + resolved_questions injected via ArchiePromptEnricher |
