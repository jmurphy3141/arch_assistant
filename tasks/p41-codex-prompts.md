# p41 Codex Prompts — Reasoning Loop Completeness

Run in order: p41a → p41b → p41c. Each task merges to main before the next starts.

---

## p41a — Enable Step 3 Planning in Archie

```
Read tasks/p41a-enable-step3-planning.md carefully.

Run the prerequisite check:
  python3.11 -m compileall agent/archie_wiring.py
  grep "step3_planning" agent/archie_wiring.py

Then implement exactly as specified:
- Add step3_planning: bool = True to the build_forge() signature
- Pass step3_planning=step3_planning to the Forge(...) constructor call

That is the entire change. Do not touch any other file.

Run ALL acceptance criteria checks from the spec before committing.

Commit message: p41a: enable step3_planning in build_forge() — Archie now reasons through Steps 1–3 before the loop
Branch: claude/p41a (from main). Push when done.
```

---

## p41b — Iterate Correction Directive

```
Read tasks/p41b-iterate-correction-directive.md carefully.

Prerequisites: p41a merged to main. Start from main.

Run the prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  grep "CORRECTION REQUIRED\|Re-call" skillforge/forge.py

Implement exactly as specified:
- In run_turn(), find the block: if review_decision == "iterate": continue
- Replace the bare continue with the CORRECTION REQUIRED directive block
  that extracts the concern from the prompt and appends an explicit
  re-call instruction naming the tool and the concern
- The continue remains at the end of the new block

Run ALL acceptance criteria checks from the spec before committing.

Commit message: p41b: iterate correction directive — explicit re-call instruction after expert review rejects
Branch: claude/p41b (from main, after p41a merged). Push when done.
```

---

## p41c — Turn Stats

```
Read tasks/p41c-turn-stats.md carefully.

Prerequisites: p41a, p41b merged to main. Start from main.

Run the prerequisite check:
  python3.11 -m compileall skillforge/types.py skillforge/forge.py
  grep "stats" skillforge/types.py skillforge/forge.py

Implement exactly as specified:
- Add stats: dict = field(default_factory=dict) to TurnResult in skillforge/types.py
- In run_turn(), immediately before return TurnResult(...), build turn_stats
  from the events list (event_counts, review decisions, iterations, etc.)
- Log it at INFO with marker [TURN_STATS]
- Pass stats=turn_stats to TurnResult(...)

Run ALL acceptance criteria checks including the smoke test that asserts
result.stats contains 'iterations' and 'event_counts'.

Commit message: p41c: TurnResult.stats + TURN_STATS log — per-turn reasoning loop observability
Branch: claude/p41c (from main, after p41a–p41b merged). Push when done.
```
