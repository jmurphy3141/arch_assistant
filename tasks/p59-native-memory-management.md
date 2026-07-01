# Task: native long-term memory management
Phase: 5
Status: todo

## Goal
Give the native agent scalable long-term memory: a compact working set plus
memory-retrieval tools the model self-invokes — so a real engagement of many
meetings, notes, and corrections stays affordable and accurate, and so cost per
turn stays flat as engagements (and the customer count) grow.

Authorized by PLAN.md "Memory Requirements" + Decision #7 (native tools). The
deterministic storage layer (Customer → Engagement → Session in object storage)
is kept; only assembly and retrieval change, and only on the native path.

## Files to create
- `agent/archie_memory_retrieval.py` — native working-set assembler + memory tools.
- `tests/test_archie_memory_retrieval.py` — the acceptance tests below.

## Files to change
- `agent/archie_native_loop.py` — replace the "dump full session history + full
  context blob" prompt assembly with `assemble_working_set(...)`; register the
  memory tools alongside the existing tools.
- `agent/archie_memory.py` — add rolling session summarization, an engagement-level
  digest, and fact-currency (corrections supersede prior values).
- `config.yaml` — memory tuning under `orchestrator`: `working_set_turns`,
  `session_summary_threshold`, `working_set_char_budget`.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path (its memory assembly is unchanged)
- `sub_agents/**` internals and the composers
- The object-storage key layout (Customer/Engagement/Session)
- The Forge `excluded` set

## What to do
1. **Compact working set.** `assemble_working_set(...)` returns a BOUNDED context:
   the engagement fact-summary/digest + the rolling session summary + only the last
   `working_set_turns` turns — never the full history or a raw context dump. Total
   stays under `working_set_char_budget` regardless of engagement age.
2. **Memory tools** the model self-invokes (registered like any other tool),
   ALL scoped to the active engagement only — never another customer's data:
   - `recall_fact(query)` — return the current authoritative value(s) for a fact.
   - `search_notes(query)` — keyword search across this engagement's notes
     (reuse `search_documents`).
   - `get_decisions()` — decisions recorded on this engagement.
   - `list_artifacts()` — artifacts produced, with keys/links.
   - `get_meeting_summaries()` — per-session summaries across meetings.
3. **Compaction.** When a session exceeds `session_summary_threshold`, roll older
   turns into a session summary. Maintain an engagement digest (state-of-the-deal)
   updated as facts/decisions/artifacts change.
4. **Fact currency.** A correction supersedes the prior value: the latest
   authoritative value is what surfaces in the working set and `recall_fact`;
   superseded values are excluded from both. (Build on the existing relationship
   merge/dedup — do not fork it.)
5. Wire all of the above into `archie_native_loop` only; leave forge untouched.

## Acceptance criteria
- Bounded context: given a synthetic engagement with a very long history, the
  assembled working set stays within `working_set_char_budget` and includes the
  fact digest + rolling summary + last `working_set_turns` turns (not the full
  history). (assert in the new test)
- Cross-session recall: "remind me everything we know" surfaces facts introduced
  across multiple sessions via the working set / memory tools.
- Fact currency: after a fact is corrected, `recall_fact` and the working set
  return ONLY the corrected value; the superseded value never appears.
- Engagement isolation: memory tools called under engagement A never return data
  from engagement B / another customer.
- Native loop no longer injects full session history or a raw context blob — grep
  `agent/archie_native_loop.py` shows the working-set assembler is used instead.
- Forge mode unchanged → `pytest -m "not live"` green.
- New tests green → `pytest tests/test_archie_memory_retrieval.py -m "not live"`.
