# Phase 5 Requirements — Archie Expert UX Polish

**Date:** 2026-05-18  
**Status:** Draft  
**Scope:** Post-p48. Forge orchestration is stable. Native tool use works.
Expert reasoning fires. Focus now shifts to UX quality: Archie should feel
expert and decisive, not bureaucratic and hesitant.

---

## What We Observed (Evidence Base)

After running Archie end-to-end post-p48, four recurring problems appear:

### 1. BOM Expert Blocks Instead of Generates
The `oci_bom_expert` hat's Pre-Action Checklist has three ★-required items:
compute shape, region, and storage sizing. When any of these is unstated,
the expert LLM returns a clarification request rather than calling
`generate_bom`. This fires on nearly every fresh BOM request because users
rarely specify all three upfront.

**Observed behaviour:** User says "2 servers, web service, WAF, block storage"
→ Archie responds with a block of clarification questions instead of generating.

**Correct behaviour:** Generate immediately using E5.Flex / us-ashburn-1 /
500 GB balanced as defaults, document assumptions in the BOM, let the user
correct output rather than pre-interrogate input.

### 2. Thinking Status Not Visible During Tool Execution
The `reasoning_sink` events ("Thinking...", "→ generate bom",
"Running generate bom...") are wired in forge.py (p47) and handled in the
UI (p43e), but the `thinkingStatus` label doesn't visually stand out from the
static "Archie is chewing on it..." message below it. Users can't tell whether
Archie is idle or actively running a tool.

### 3. Upload Notes Button Doesn't Trigger save_notes
When the user clicks the upload button, the UI sends:
`"I've just uploaded my meeting notes (filename). Please save them."`
The step3_planning LLM sometimes interprets this as a generation request
(BOM/diagram from the file) rather than a `save_notes` call. The file is
written to object storage by the API endpoint but not indexed into context.

### 4. BOM Sub-Agent Defaults to E6 Instead of E5
When no compute shape is specified, the sub-agent LLM chooses E6 (newer AMD)
despite the system prompt saying E5 is the default. E5 and E6 have identical
pricing ($0.03/OCPU) but different SKU numbers, which affects downstream
BOM comparison and audit.

---

## Goals for Phase 5

1. **Archie generates first, asks later.** For BOM and diagram requests with
   reasonable OCI defaults available, call the tool immediately. Surface gaps
   as assumptions in the output, not as pre-flight questions.

2. **Forge thinking is visible.** The status area shows which tool is running
   and, where relevant, which expert lens is active. Users know when Archie is
   working vs when it's stuck.

3. **Upload notes always works.** The upload button message reliably triggers
   `save_notes` without the user needing to rephrase.

4. **BOM shape defaults are consistent.** E5.Flex is always the default unless
   the user specifies otherwise, across both the sub-agent and the fallback
   `_draft_bom_payload` path.

---

## Non-Goals

- No Forge core changes (orchestration is stable)
- No new tools or sub-agents
- No database or storage layer changes
- No UI component restructuring

---

## Design Principles

**Prefer defaults over interrogation.** An OCI expert doesn't ask "which region
should I use?" — they say "I assumed us-ashburn-1; let me know if you need
a different region." The BOM hat should mirror that behaviour.

**Visibility without noise.** Thinking status should be prominent enough to
confirm Archie is working, but disappear cleanly once output arrives.

**Forge stays domain-agnostic.** All changes to expert behaviour live in hat
files and the Archie system prompt — not in `skillforge/forge.py`.

---

## Success Criteria

- "2 servers, web service, OCI" → BOM generated in one turn with no questions
- Diagram request → `generate_diagram` called correctly even with BOM history
- Upload notes → `save_notes` called without rephrasing
- BOM XLSX always uses E5 SKUs (B97384/B97385) when no shape is specified
- "Running generate bom..." visible in status area during BOM generation
- All existing tests pass
