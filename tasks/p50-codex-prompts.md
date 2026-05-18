# p50 Codex Prompts — Expert Quality Depth

## Background

Deep-dive analysis of `origin/main` revealed three genuine quality problems
that p43–p49 did not address:

1. **BOM generation blocks instead of generates.** The `oci_bom_expert` hat
   Pre-Action Checklist has three ★ items (shape, region, storage) that require
   confirmation before calling `generate_bom`. On every fresh BOM request these
   are unknown, so Archie interrogates instead of producing output. An expert
   that always asks questions isn't an expert.

   Additionally, the Core Principles text lists "E5/E6.Flex for higher-core-
   density needs" as if E6 is a valid default. BomService already defaults to
   E5 in code, but the ambiguous hat text can cause the pre-action output to
   mention E6 — which BomService then picks up via regex extraction. The hat
   must be unambiguous: E5 is the default, E6 only when the user names it.

   The pre-action output must also end with a structured `[SUB-AGENT
   INSTRUCTIONS]` block containing concrete sizing numbers in a parseable
   format. The BOM sub-agent is a deterministic Python pipeline (not an LLM)
   that extracts sizing via regex from the task text. Expert hat quality only
   reaches the sub-agent if it writes parseable numbers — abstract analysis is
   silently ignored.

2. **Upload notes button routes to the wrong tool.** The UI's "Upload Notes"
   button sends: `"I've just uploaded my meeting notes (<filename>). Please save
   them."` Archie's `_TOOL_SEQUENCING_RULES` has no explicit rule for this
   pattern, so step3_planning sometimes interprets it as a generation request
   (BOM or diagram from the file) rather than `save_notes`. One routing rule
   fixes this permanently.

3. **Thinking status is invisible at all status phases.** The `thinkingStatus`
   div renders at `#8b93a8` / `0.74rem` for all states — idle ("Thinking..."),
   active ("Running generate bom..."), and review ("Reviewing result...").
   Users cannot tell whether Archie is idle-thinking or actively executing a
   tool. The "Running" state should stand out visually.

Run order: p50a → p50c → p50d. All three are independent of each other.
p50a is the highest priority (blocks BOM quality end-to-end).

---

## p50a — BOM Hat: defaults over interrogation + concrete sizing output

```
Context: agent/hats/oci_bom_expert.md has two quality problems:

1. Pre-Action Checklist has three ★ blocking items that ask the user
   pre-flight questions before generate_bom fires. This must become
   defaults-over-interrogation: document assumptions and generate immediately.

2. Core Principles mentions "E5/E6.Flex for higher-core-density needs"
   as if E6 is a valid default alongside E5. The sub-agent (BomService)
   defaults to E5 in code, but the ambiguous hat text causes pre-action
   output to mention E6, which BomService picks up via regex. The hat must
   make the hierarchy unambiguous.

3. Pre-Action output must end with a parseable [SUB-AGENT INSTRUCTIONS]
   block. The BOM sub-agent is a deterministic Python pipeline that
   extracts OCPU/memory/storage via regex from the task text. Expert
   quality only reaches the sub-agent if the pre-action output writes
   concrete numbers, not abstract analysis.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p50a origin/main

Read agent/hats/oci_bom_expert.md completely before editing.

Make exactly three changes to agent/hats/oci_bom_expert.md:

Change 1 — Core Principles, Shape selection hierarchy (lines 62–66)

Replace:
  - **Shape selection hierarchy:** Default to E5.Flex (AMD, B97384 OCPU / B97385
    memory) unless the customer specifies otherwise. Use A1.Flex (B93297/B93298)
    for Ampere workloads, E5/E6.Flex for higher-core-density needs, X9 (B94176/
    B94177) only when Intel compatibility is explicitly required, and BM.GPU4.8 or
    BM.GPU.A10 shapes only after explicit GPU confirmation.

With:
  - **Shape selection hierarchy:** Default to E5.Flex (AMD, B97384 OCPU / B97385
    memory) unless the customer specifies otherwise. Use A1.Flex (B93297/B93298)
    for Ampere workloads, E6.Flex (B111129/B111130) only when the customer
    explicitly requests it by name, X9 (B94176/B94177) only when Intel
    compatibility is explicitly required, and BM.GPU4.8 or BM.GPU.A10 shapes
    only after explicit GPU confirmation. E6 is NOT a default — always start
    with E5.Flex unless the customer explicitly names E6.

Change 2 — Pre-Action Checklist: remove ★ blocking, add defaults rule and
sizing table format

Replace the entire block beginning with:
  If any item marked with ★ is unconfirmed, ask the user before calling the sub-agent:
  ★ Compute shape or family
  ★ Region
  ★ Storage sizing

  Unstarred items may be defaulted — document the assumption.

With:
  **Do NOT ask the user pre-flight questions.** All items may be defaulted.
  Document every assumption. An expert produces output immediately; the user
  can revise later.

  Defaults when not stated by the customer:
  - Compute shape: E5.Flex (AMD, B97384/B97385)
  - OCPU per server: 4 OCPU
  - Memory per server: 32 GB (8 GB/OCPU)
  - Region: us-chicago-1
  - Block Volume: 500 GB Balanced tier
  - HA mode: single-AD (do not double compute unless customer says HA)

  End your pre-action output with a concrete sizing table in this exact format
  so the BOM sub-agent (a deterministic regex pipeline) can extract the numbers:

  ```
  [SUB-AGENT INSTRUCTIONS]
  Compute: <N> OCPU E5.Flex × <M> servers = <total_ocpu> OCPU total, <mem_gb> GB memory
  Storage: <X> GB Block Volume Balanced tier
  Region: us-chicago-1
  Managed services: <list or "none">
  Assumptions: <comma-separated list of every defaulted value>
  ```

  Fill in the angle-bracket placeholders with concrete numbers before calling
  generate_bom. The sub-agent reads the sizing from this table.

Change 3 — Activation & Drop section

Replace:
  Before calling the BOM sub-agent I confirm: compute shape or family known,
  OCPU count + memory sizing present or defaulted with justification, region
  confirmed, storage sizing present, and optional managed services scoped. I drop

With:
  Before calling the BOM sub-agent I document all defaults and emit the
  [SUB-AGENT INSTRUCTIONS] sizing table. I drop

Run ALL acceptance criteria:

  grep "★" agent/hats/oci_bom_expert.md
  # must return nothing — all ★ items removed

  grep "E6 is NOT a default" agent/hats/oci_bom_expert.md
  # must match

  grep "\[SUB-AGENT INSTRUCTIONS\]" agent/hats/oci_bom_expert.md
  # must match

  grep "Do NOT ask the user pre-flight questions" agent/hats/oci_bom_expert.md
  # must match

  python3.11 -c "import yaml; yaml.safe_load(open('agent/hats/oci_bom_expert.md').read().split('---')[1])"
  # must not raise (YAML front-matter still valid)

Commit message:
p50a: BOM hat — defaults over interrogation, E5 clarity, parseable sizing table

Branch: claude/p50a (from main). Push when done.
```

---

## p50c — Upload notes routing rule

```
Context: The UI's "Upload Notes" button sends the message:
  "I've just uploaded my meeting notes (<filename>). Please save them."

Archie's _TOOL_SEQUENCING_RULES in agent/archie_wiring.py ends at rule 11.
Without an explicit rule for the upload pattern, step3_planning sometimes
interprets the message as a BOM or diagram generation request. The file is
already in object storage after the upload. Archie only needs to call
save_notes to index it.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p50c origin/main

Read agent/archie_wiring.py lines 26–65 (the _TOOL_SEQUENCING_RULES string)
before editing.

Make exactly one change to agent/archie_wiring.py:

In the _TOOL_SEQUENCING_RULES string, after rule 11 and before the closing
triple-quote ("""), append:

12. When the user message contains "uploaded my meeting notes", "uploaded notes",
    or contains "Please save them" together with a filename reference, call
    save_notes immediately with the message text as the notes argument.
    Do NOT call generate_bom or generate_diagram. The file is already in object
    storage — save_notes indexes it and confirms to the user.

Run ALL acceptance criteria:

  python3.11 -m compileall agent/archie_wiring.py -q
  # must be clean

  grep "uploaded my meeting notes" agent/archie_wiring.py
  # must match

  grep -c "^[0-9][0-9]*\." agent/archie_wiring.py || \
  grep -c "[0-9][0-9]*\. " agent/archie_wiring.py
  # rule 12 exists — at least 12 numbered rules

  pytest tests/test_archie_forge_wiring.py -v --tb=short
  # all existing tests must pass

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p50c: add rule 12 — save_notes for upload message pattern, prevent mis-routing

Branch: claude/p50c (from main). Push when done.
```

---

## p50d — Thinking status visual distinction

```
Context: The thinkingStatus div in ui/src/components/ChatInterface.tsx renders
all states (idle, active, review) at the same color (#8b93a8) and size
(0.74rem). Users cannot tell whether Archie is idle-thinking or actively
executing a tool call.

The Forge reasoning_sink emits these label prefixes:
  "Thinking..."            — idle orchestrator thinking (step3_planning)
  "Planning approach..."   — step3_planning in progress
  "Expert pre-action..."   — pre-action hat analysis
  "→ <tool_name>"          — tool selected (e.g. "→ generate bom")
  "Running <tool_name>..."  — tool executing
  "Reviewing result..."    — critic/post-action review

Labels starting with "Running" are the highest-signal state — Archie is
actively calling a sub-agent. They should be visually distinct.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p50d origin/main

Read ui/src/components/ChatInterface.tsx lines 1097–1109 (the thinkingStatus
div) before editing.

Make exactly one change to ui/src/components/ChatInterface.tsx:

Find the thinkingStatus div (data-testid="chat-thinking-status"). Replace its
style object:

FROM:
  style={{
    color: '#8b93a8',
    fontSize: '0.74rem',
    alignSelf: 'flex-start',
    fontFamily: "'JetBrains Mono', monospace",
  }}

TO:
  style={{
    color: thinkingStatus.startsWith('Running') ? '#61dafb' : '#a8b4cc',
    fontWeight: thinkingStatus.startsWith('Running') ? 600 : 400,
    fontSize: '0.78rem',
    alignSelf: 'flex-start',
    fontFamily: "'JetBrains Mono', monospace",
  }}

Colors:
  #61dafb — React blue; bright enough to read on dark backgrounds, signals activity
  #a8b4cc — Brighter version of the existing #8b93a8 for all other states

Do NOT change any other part of the file. Do NOT change test files.

Run ALL acceptance criteria:

  cd ui && npm run build 2>&1 | tail -5
  # must succeed with no errors (warnings ok)

  grep "startsWith('Running')" ui/src/components/ChatInterface.tsx | wc -l
  # must output 2 (color and fontWeight both use it)

  grep "#61dafb" ui/src/components/ChatInterface.tsx
  # must match

  grep "0\.78rem" ui/src/components/ChatInterface.tsx | grep thinkingStatus -A5
  # 0.78rem in the thinkingStatus block

  cd ui && npm test -- --run 2>&1 | tail -10
  # no new failures vs baseline

Commit message:
p50d: thinking status — Running state in accent blue with bold weight

Branch: claude/p50d (from main). Push when done.
```
