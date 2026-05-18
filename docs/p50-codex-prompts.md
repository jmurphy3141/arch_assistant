# Phase 5 Codex Prompts

Based on `docs/requirements-phase5.md`. Four observed production problems;
four targeted fixes. Each prompt touches one file or a tightly related pair.
Run order: p50a and p50b are independent — run them in parallel.
p50c and p50d are also independent. All four can merge in any order.

---

## Prompt 1 — p50a: Remove ★ blocking from BOM Pre-Action Checklist

```
You are making a targeted change to one hat file only.

Context: The oci_bom_expert hat Pre-Action Checklist marks three items with ★
and instructs the expert LLM to ask the user before calling generate_bom when
any ★ item is unconfirmed. This fires on nearly every fresh BOM request because
users never specify compute shape, region, and storage upfront. The fix is to
remove the ★ blocking rule and replace it with documented defaults, so the expert
generates immediately and documents assumptions in the BOM output.

File to edit: agent/hats/oci_bom_expert.md

Current Pre-Action Checklist (lines 192–197):

  If any item marked with ★ is unconfirmed, ask the user before calling the sub-agent:
  ★ Compute shape or family
  ★ Region
  ★ Storage sizing

  Unstarred items may be defaulted — document the assumption.

Replace that entire block (the four lines starting with "If any item..." through
"Unstarred items may be defaulted...") with:

  All items may be defaulted when the user has not specified them. Document every
  assumption in the BOM output — do not ask pre-flight questions.

  Default values to use when unspecified:
  - Compute shape: E5.Flex (B97384 OCPU / B97385 memory, $0.03/OCPU)
  - Region: us-chicago-1
  - Storage: 500 GB Block Volume balanced tier

  Only block on missing information when it is structurally impossible to produce
  a valid BOM without it (e.g. no workload description at all).

Do NOT touch any other section of the file — not Core Principles, not Quality Bar,
not Post-Action Review, not the YAML front matter.

Verification:
  grep "★" agent/hats/oci_bom_expert.md
  # must return no output (all ★ removed)

  grep -A5 "Default values" agent/hats/oci_bom_expert.md
  # must show the three default lines

  python3.11 -c "import yaml; yaml.safe_load(open('agent/hats/oci_bom_expert.md').read().split('---')[1])"
  # must not error — YAML front matter still valid

Commit message: p50a: remove ★ blocking from BOM Pre-Action Checklist — generate with defaults instead of interrogating

Branch: claude/explore-repo-Os53i
Push when done.
```

---

## Prompt 2 — p50b: Strengthen E5 default in BOM sub-agent system prompt

```
You are making a targeted change to one sub-agent system prompt only.

Context: The BOM sub-agent LLM sometimes chooses E6.Flex (B111129/B111130)
even though the system prompt says E5 is the default. E5 and E6 have identical
pricing ($0.03/OCPU, $0.002/GB-memory) but different SKU numbers, which breaks
BOM comparison and audit. The fix is to make the E5 default instruction
unambiguous and add an explicit exclusion rule for E6.

File to edit: sub_agents/bom/system_prompt.md

Current SKU mapping block (around line 30):
  E5.Flex (AMD)    → OCPU: B97384, Memory: B97385  ($0.03/OCPU, $0.002/GB)  ← DEFAULT
  ...
  E6.Flex (AMD)    → OCPU: B111129, Memory: B111130  ($0.03/OCPU, $0.002/GB)

And the rule two lines below the table:
  Use E5.Flex (B97384/B97385) as the default compute shape unless the customer
  explicitly requests a different shape. E4.Flex is legacy — only use it when
  the customer or memory block explicitly requests E4.

Replace that trailing rule paragraph with:

  SHAPE SELECTION RULE (mandatory):
  - When no compute shape is specified by the user or the memory block, use E5.Flex
    (B97384 OCPU / B97385 memory) exclusively. Never substitute E6 as a default.
  - E6.Flex (B111129/B111130) is only valid when the user or memory block
    explicitly names "E6" or "B111129" or "B111130". Do not choose it because
    it is newer or has the same price.
  - E4.Flex (B93113/B93114) is legacy — only use when explicitly requested.

Do NOT change any other section of the file.

Verification:
  grep -A3 "SHAPE SELECTION RULE" sub_agents/bom/system_prompt.md
  # must print the three bullet lines

  grep "Never substitute E6" sub_agents/bom/system_prompt.md
  # must return the line

Commit message: p50b: strengthen E5 default in BOM sub-agent — explicit exclusion rule for E6 as default

Branch: claude/explore-repo-Os53i
Push when done.
```

---

## Prompt 3 — p50c: Route upload-notes message to save_notes in Archie system prompt

```
You are making a targeted change to one constant in agent/archie_wiring.py only.

Context: When the user clicks the "Upload Notes" button in the UI, the frontend
sends a message of the form:
  "I've just uploaded my meeting notes (filename.txt). Please save them."
The Archie orchestrator sometimes interprets this as a diagram or BOM generation
request rather than a save_notes tool call. The fix is to add an explicit
routing rule to _TOOL_SEQUENCING_RULES so the planning LLM always dispatches
save_notes for this pattern.

File to edit: agent/archie_wiring.py

Find _TOOL_SEQUENCING_RULES (the multiline string constant). It ends with rule 11.
Add rule 12 immediately before the closing triple-quote:

12. When the user message contains "uploaded my meeting notes" or "Please save them"
    or "uploaded a file" in combination with a filename or file reference, call
    save_notes immediately. Do not call generate_bom, generate_diagram, or any
    other tool. The file content is already stored — save_notes indexes it into
    context so Archie can reference it.

Do NOT touch any other part of archie_wiring.py. Do not change build_forge(),
the imports, ArchiePromptEnricher, or any tool registration.

Verification:
  python3.11 -m compileall agent/archie_wiring.py
  # must succeed with no errors

  grep "uploaded my meeting notes" agent/archie_wiring.py
  # must return the new rule line

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -10
  # must show same pass count as before (no regressions)

Commit message: p50c: add save_notes routing rule to Archie system prompt — upload notes button always dispatches save_notes

Branch: claude/explore-repo-Os53i
Push when done.
```

---

## Prompt 4 — p50d: Make thinking status visually distinct during tool execution

```
You are making a targeted styling change to one component in the React UI.

Context: The UI has a thinkingStatus label (data-testid="chat-thinking-status")
that shows Forge reasoning events like "Thinking...", "→ generate bom",
"Running generate bom...". It renders in color #8b93a8 at 0.74rem — visually
indistinguishable from ambient UI chrome. Users cannot tell whether Archie is
idle or actively running a tool. The fix is two style changes:
1. When thinkingStatus contains "Running" (tool execution in progress), render
   it in a more prominent accent colour (#61dafb or similar cool-blue) with a
   subtle pulse animation, so users can see Archie is working.
2. For all other thinkingStatus values (Thinking..., → tool name, Reviewing...),
   use a slightly brighter foreground (#a8b4cc instead of #8b93a8) so it reads
   above the background.

File to edit: ui/src/components/ChatInterface.tsx

Find the thinkingStatus rendering block (around line 1097):
  {thinkingStatus && (
    <div
      data-testid="chat-thinking-status"
      style={{
        color: '#8b93a8',
        fontSize: '0.74rem',
        alignSelf: 'flex-start',
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      {thinkingStatus}
    </div>
  )}

Replace it with:

  {thinkingStatus && (
    <div
      data-testid="chat-thinking-status"
      style={{
        color: thinkingStatus.startsWith('Running') ? '#61dafb' : '#a8b4cc',
        fontSize: '0.78rem',
        alignSelf: 'flex-start',
        fontFamily: "'JetBrains Mono', monospace",
        fontWeight: thinkingStatus.startsWith('Running') ? 600 : 400,
        letterSpacing: thinkingStatus.startsWith('Running') ? '0.02em' : undefined,
      }}
    >
      {thinkingStatus}
    </div>
  )}

No animation keyframes needed — the colour and weight change is sufficient signal.
Do NOT change any other styling, state logic, or event handling in this file.

Verification:
  cd ui && npm run build 2>&1 | tail -10
  # must complete with no TypeScript errors

  grep "startsWith('Running')" ui/src/components/ChatInterface.tsx
  # must return two matches (color and fontWeight lines)

  grep "0.78rem" ui/src/components/ChatInterface.tsx
  # must return the updated fontSize line

  cd .. && pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5
  # backend tests unaffected

Commit message: p50d: make Forge thinking status visually distinct — Running state in accent blue, brighter text for all phases

Branch: claude/explore-repo-Os53i
Push when done.
```

---

## Run Order and Merge Sequence

All four prompts are independent (different files, no shared state).

| Task | File | Risk | Can run with |
|------|------|------|-------------|
| p50a | `agent/hats/oci_bom_expert.md` | Low — hat text only | p50b, p50c, p50d |
| p50b | `sub_agents/bom/system_prompt.md` | Low — sub-agent prompt only | p50a, p50c, p50d |
| p50c | `agent/archie_wiring.py` | Medium — check tests pass | p50a, p50b, p50d |
| p50d | `ui/src/components/ChatInterface.tsx` | Low — style only | p50a, p50b, p50c |

Merge order: p50a + p50b first (no code risk), then p50c (verify tests), then p50d (verify UI build).

After all four merge and the server resets, success criteria from `docs/requirements-phase5.md`:
- "2 servers, web service, OCI" → BOM generated in one turn, no questions
- Upload notes → save_notes called without rephrasing
- BOM XLSX always uses E5 SKUs (B97384/B97385) when no shape specified
- "Running generate bom..." visible and distinctly styled during generation
