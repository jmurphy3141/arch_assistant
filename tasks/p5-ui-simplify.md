# Task: Phase 5 — UI simplification: chat-only interface
Phase: 5
Status: todo
Depends on: p4-cleanup.md merged to main

## Goal
Remove all specialist form-based UI modes (BOM Advisor, Generate Diagram, POV,
JEP, WAF, Terraform, Notes form). Leave only `ChatInterface.tsx`,
`ArtifactPreviewPanel.tsx`, and `ChatSidebar.tsx`. Every deliverable is now
requested through chat.

---

## Context

The current UI has 8 modes registered in `ui/src/agents/registry.ts`:
- Chat (keep)
- BOM Advisor (remove)
- Generate Diagram (remove)
- Terraform (remove)
- WAF Review (remove)
- JEP (remove)
- POV (remove)
- Notes (remove — notes are saved via chat with `save_notes` tool)

The sidebar (`ChatSidebar.tsx`) and artifact preview panel
(`ArtifactPreviewPanel.tsx`) stay.

---

## Files to modify

### `ui/src/agents/registry.ts`
Remove every entry except the chat entry. The result should export a single
agent/mode: the default chat mode backed by `ChatInterface.tsx`.

### `ui/src/App.tsx`
Remove all conditional branches that render specialist form components
(`BomAdvisor`, `GenerateForm`, `TerraformForm`, `WafForm`, `JepForm`, `PovForm`,
any Notes form). The app should always render `ChatInterface` + sidebar +
artifact panel. Remove any mode-switching logic that is now dead.

### `ui/src/components/ChatInterface.tsx`
No changes unless dead imports from removed components need to be cleaned up.

### `ui/src/components/ChatSidebar.tsx`
Remove any mode-switching buttons or links that reference the removed modes.
Keep the conversation history list and any engagement/session selectors.

---

## Files to delete

Delete each component file **after** confirming it is no longer imported anywhere:

```bash
grep -rn "BomAdvisor\|GenerateForm\|TerraformForm\|WafForm\|JepForm\|PovForm" \
    ui/src --include="*.tsx" --include="*.ts" | grep -v "the file itself"
```

Files to delete once imports are removed:
- `ui/src/components/BomAdvisor.tsx`
- `ui/src/components/GenerateForm.tsx`
- `ui/src/components/TerraformForm.tsx`
- `ui/src/components/WafForm.tsx`
- `ui/src/components/JepForm.tsx`
- `ui/src/components/PovForm.tsx`
- Any Notes form component if it exists as a separate file

---

## Files to NOT touch

- `ui/src/components/ChatInterface.tsx`
- `ui/src/components/ArtifactPreviewPanel.tsx`
- `ui/src/components/ChatSidebar.tsx`
- `ui/src/api/client.ts` — all API calls stay; chat uses them
- Any backend file (`agent/`, `drawing_agent_server.py`, `sub_agents/`)
- Any test file in `tests/` (backend)
- `ui/src/__tests__/` — do not modify frontend tests

---

## What to do

1. Remove the specialist mode entries from `ui/src/agents/registry.ts`.
2. Remove specialist form rendering branches from `ui/src/App.tsx`.
3. Remove mode-switching UI from `ui/src/components/ChatSidebar.tsx`.
4. Remove dead imports from all modified files.
5. Delete the specialist form component files listed above.
6. Run: `cd ui && npm run build` — must exit 0 with no TypeScript errors.
7. Run: `cd ui && npm test -- --run` — must pass (or not regress vs baseline).
8. Open a PR. Do not merge. Add notes against each acceptance criterion.

---

## Acceptance criteria

- `cd ui && npm run build` exits 0 with no TypeScript errors
- `ls ui/src/components/BomAdvisor.tsx` returns "No such file"
- `ls ui/src/components/GenerateForm.tsx` returns "No such file"
- `ls ui/src/components/TerraformForm.tsx` returns "No such file"
- `grep -rn "BomAdvisor\|GenerateForm\|TerraformForm\|WafForm\|JepForm\|PovForm" ui/src` returns nothing
- `ui/src/agents/registry.ts` contains only one mode entry (chat)
- `ui/src/App.tsx` has no conditional branches rendering the removed components
- Backend tests unchanged: `pytest tests/ -v -m "not live"` — 0 failures
