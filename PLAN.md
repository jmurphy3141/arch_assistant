# Archie — Architecture Plan

**This file is locked.** Codex reads it, never modifies it.
Planning changes go through the human + Claude review cycle first.

---

## How Codex Uses This File

1. Read `PLAN.md` (this file) to understand the target architecture and locked decisions.
2. Read the assigned task file in `tasks/` for the specific unit of work.
3. Implement only what the task file specifies.
4. Do not refactor, generalize, or improve anything outside the task scope.
5. Do not add new abstractions unless the task explicitly requires them.
6. Open a PR. Do not merge.

---

## Vision

Archie is a conversational OCI enterprise architect assistant. He reasons at the
level of a professional solutions architect, asks questions to guide the human to
the right answer, and orchestrates a team of independent specialist sub-agents to
produce deliverables.

Archie does not generate diagrams, BOMs, documents, or Terraform himself.
He reasons, delegates, reviews, and presents.

---

## Locked Architecture Decisions

These decisions are final. Do not re-litigate them in code or comments.

### 1. Archie is thin

`orchestrator_agent.py` must not contain generation logic, specialist repair
loops, or per-agent business rules. Its job is:

- Maintain conversation and context
- Reason about what the user needs
- Choose hats
- Build prompts for sub-agents
- Call sub-agents via A2A
- Review sub-agent output (using the critic hat)
- Present results

### 2. Hats are prompt injections chosen by Archie as tools

- Every hat is a `.md` file in `agent/hats/`
- Archie's tool list includes one tool per hat: `use_hat_{name}`
- When called, the hat's `.md` content is injected at the **start of Archie's next reasoning round**, before the user message and any sub-agent output
- Multiple hats can be active simultaneously — they concatenate in call order
- A hat stays active until the task it was invoked for is complete; Archie judges completion using the hat's own exit criteria
- Hats are Archie's expert lenses. Sub-agents never receive Archie's hats.
- Hats can be refined independently without touching Archie's base system prompt

**Starting hat inventory:**

| Hat file | Purpose |
|----------|---------|
| `agent/hats/critic.md` | Review sub-agent output, identify issues, decide whether to re-prompt |
| `agent/hats/governor.md` | Enforce security, cost, and quality guardrails |
| `agent/hats/diagram_builder.md` | Expert context for scoping and reviewing diagram requests |
| `agent/hats/bom_reviewer.md` | Expert context for scoping and reviewing BOM requests |
| `agent/hats/terraform_reviewer.md` | Expert context for scoping and reviewing Terraform requests |
| `agent/hats/waf_reviewer.md` | Expert context for reviewing WAF outputs |

New hats are added by creating a `.md` file in `agent/hats/`. No Python changes required.

### 3. Sub-agents are independent A2A services

- Each sub-agent runs as its own process with its own system prompt and LLM config
- Sub-agents live in `sub_agents/{name}/`
- Each sub-agent exposes a single A2A endpoint Archie calls via HTTP
- Archie builds the prompt from current context and sends it; the sub-agent does not need Archie's memory or hats
- Sub-agents can use different LLMs (e.g. Terraform agent uses a code-optimised model)
- New sub-agents are registered in `config.yaml` under `sub_agents:` — no orchestrator code changes required to add one

**Sub-agents to migrate from in-process to independent A2A:**

| Name | Current location | Target |
|------|-----------------|--------|
| bom | `agent/bom_service.py` (in-process) | `sub_agents/bom/` |
| diagram | A2A self-call on localhost | `sub_agents/diagram/` |
| pov | `agent/pov_agent.py` (in-process) | `sub_agents/pov/` |
| jep | `agent/jep_agent.py` (in-process) | `sub_agents/jep/` |
| waf | `agent/waf_agent.py` (in-process) | `sub_agents/waf/` |
| terraform | `agent/graphs/terraform_graph.py` (in-process) | `sub_agents/terraform/` |

### 4. Critic and Governor are hats, not Python modules

`agent/critic_agent.py` and `agent/governor_agent.py` are replaced by
`agent/hats/critic.md` and `agent/hats/governor.md`.

Minimal deterministic safety rules (block on missing encryption, hard cost limits)
may remain as a thin Python guard in `agent/safety_rules.py`. This file must not
grow beyond 100 lines. It does not call an LLM.

### 5. Single chat interface

All specialist form UIs (BOM Advisor, Generate Diagram, POV, JEP, WAF, Terraform,
Notes) are removed. Everything flows through `ChatInterface.tsx`.

The artifact preview panel (`ArtifactPreviewPanel.tsx`) stays — outputs appear there.
The conversation sidebar (`ChatSidebar.tsx`) stays.

### 6. Archie understands before acting

Archie asks questions when context is insufficient. "We don't know yet" is a valid
answer that Archie accepts and proceeds with explicit assumptions. This behavior
must not be eroded by routing shortcuts.

---

## Current State vs Target State

| Concern | Current | Target |
|---------|---------|--------|
| Hat system | Skill SKILL.md files injected by routing rules, not agent choice | Hats in `agent/hats/`, called as tools by Archie |
| Sub-agents | All in-process Python imports | Independent A2A services |
| Orchestrator size | 8,400+ lines, one file | Split into bounded modules, each < 500 lines |
| Critic / Governor | Python modules with LLM calls inline | Hats (.md), thin safety guard in Python |
| UI | 8 specialist form modes + chat | Chat only |
| Codex drift | Codex adds to orchestrator_agent.py directly | Codex works only on scoped task files |

---

## Phases

Work proceeds in phase order. Do not start a phase until the previous one is merged to main.

---

### Phase 0 — Stabilize (no feature work)

**Goal:** stop the drift before adding anything new.

**Tasks:**
- `tasks/p0-fix-config.md` — update `config.yaml` `git_push.branch` from `claude/webapp-fastapi-tests-sWH4S` to `main`
- `tasks/p0-delete-deprecated.md` — delete `agent/diagram_orchestrator.py`; confirm no live imports
- `tasks/p0-update-agents-md.md` — update `AGENTS.md` to reference `PLAN.md` as the architecture source; remove instructions that contradict this plan
- `tasks/p0-close-orphan-branch.md` — cherry-pick the UI rename commit from `archie-cross-path-drafting` (`f22fa0d`); do not merge the full branch

**Acceptance:** all tests pass, server starts, `config.yaml` branch is `main`.

---

### Phase 1 — Hat System

**Goal:** Archie chooses expert lenses as tools. This is the highest priority change.

**What to build:**

1. `agent/hats/` directory with initial `.md` files (see hat inventory above)
2. `agent/hat_engine.py` — discovers hats, registers each as a tool, handles injection and stacking
3. Modify `orchestrator_agent.py` `run_turn()` to:
   - Include hat tools in the tool list shown to the LLM
   - When a hat tool is called, load the `.md` content and prepend to the next prompt round
   - Support multiple active hats (concatenate in call order)
   - Clear hat stack when Archie judges the task complete
4. Write `agent/hats/critic.md` — migrates the LLM-based critique logic from `governor_agent.py`
5. Write `agent/hats/governor.md` — migrates the LLM-based governance reasoning from `governor_agent.py`
6. Write `agent/safety_rules.py` — keep only the deterministic block rules from `governor_agent.py` (≤ 100 lines, no LLM calls)
7. Delete `agent/critic_agent.py` (it is currently a one-line re-export; remove the import from orchestrator too)
8. Slim `agent/governor_agent.py` down to a shim that calls `safety_rules.py`; schedule for deletion in Phase 3

**Do not touch in this phase:**
- Sub-agent call paths (bom, diagram, pov, jep, waf, terraform)
- `gstack_skills/` and `agent/orchestrator_skills/` — leave them in place; they become sub-agent system prompts in Phase 3
- UI

**Acceptance:**
- Archie's tool list includes `use_hat_critic`, `use_hat_governor`, `use_hat_diagram_builder`, etc.
- A conversation log shows Archie calling a hat before delegating to a sub-agent
- Hats stack: Archie can call two hat tools in one turn and both are injected
- `agent/critic_agent.py` is deleted
- All existing tests pass

---

### Phase 2 — Split Orchestrator

**Goal:** break `orchestrator_agent.py` into modules Codex can work on without conflict.

**Target module structure:**

| New file | Responsibility | Approx lines |
|----------|---------------|-------------|
| `agent/archie_loop.py` | `run_turn()`, ReAct loop, tool dispatch routing | ~400 |
| `agent/hat_engine.py` | Already created in Phase 1 | ~150 |
| `agent/archie_memory.py` | Conversation history, context assembly, canonical memory snapshot | ~300 |
| `agent/sub_agent_client.py` | A2A HTTP client for calling sub-agents; prompt construction | ~200 |
| `agent/orchestrator_agent.py` | Thin entry point — imports and re-exports `run_turn` only | ~50 |

**Rules for this phase:**
- Behaviour must not change. This is a structural refactor only.
- Each module has one clear responsibility stated at the top of the file.
- No new features, no new abstractions beyond the split.
- Tests must pass before and after with no changes to test files.

**Do not touch in this phase:**
- Sub-agents
- Hat `.md` files
- UI

---

### Phase 3 — Sub-Agent Independence

**Goal:** every sub-agent is an independent A2A service Archie calls over HTTP.

**A2A contract (all sub-agents implement this):**

```
POST /a2a
Content-Type: application/json

{
  "task": "<plain-text prompt Archie constructed>",
  "customer_id": "<str>",
  "trace_id": "<uuid>",
  "llm_config": { ... }   // optional override; sub-agent uses own default if absent
}

→ 200 OK
{
  "result": "<output text or structured payload>",
  "status": "ok" | "needs_input" | "error",
  "trace": { ... }        // sub-agent internal trace for Archie's review
}
```

**Sub-agent directory structure:**

```
sub_agents/
  {name}/
    server.py          # FastAPI app, single POST /a2a endpoint
    system_prompt.md   # The agent's own identity and instructions
    config.yaml        # Default LLM, port, timeouts
    README.md          # What this agent does and how to deploy it
```

**Migration order** (one PR per sub-agent):
1. BOM — highest complexity, validate against existing `bom_service.py` tests
2. Diagram — already has A2A shape, lowest migration risk
3. POV
4. JEP
5. WAF
6. Terraform — update `config.yaml` to point at code-optimised model

**Archie side changes (in `sub_agent_client.py`):**
- Replace all `from agent.bom_service import ...` and similar direct imports with HTTP calls
- Sub-agent URLs come from `config.yaml` `sub_agents:` block
- Archie constructs the prompt, calls the URL, receives the result, optionally wears critic hat to review

**`gstack_skills/` and `agent/orchestrator_skills/`:**
- These become the source material for each sub-agent's `system_prompt.md`
- Migrate content, then delete the originals at end of this phase

**Do not touch in this phase:**
- Hat system
- UI

---

### Phase 4 — Single Chat UI

**Goal:** one interface. No form modes.

**Remove:**
- `ui/src/components/BomAdvisor.tsx`
- `ui/src/components/GenerateForm.tsx`
- `ui/src/components/TerraformForm.tsx`
- `ui/src/components/WafForm.tsx`
- `ui/src/components/JepForm.tsx`
- `ui/src/components/PovForm.tsx`
- `ui/src/components/NoteUpload.tsx`
- `ui/src/components/ClarifyForm.tsx`
- All mode routing in `ui/src/App.tsx` except `chat`
- Dead API client functions in `ui/src/api/client.ts` that only served removed forms

**Keep:**
- `ChatInterface.tsx` — primary surface
- `ArtifactPreviewPanel.tsx` — output display
- `ChatSidebar.tsx` — conversation history
- `DocViewer.tsx` — document viewing
- `HealthIndicator.tsx`

**Backend:** remove API routes that only served removed form UIs. Keep all routes that
`ChatInterface` uses. Do not remove `/upload-bom` until confirmed unused.

**Acceptance:** UI builds, chat works, no broken imports, all UI tests pass or are deleted with the components they tested.

---

### Phase 5 — Cleanup

**Goal:** leave no dead code, no stale config, no orphaned branches.

- Delete `agent/governor_agent.py` (shim from Phase 1, now unused)
- Delete `agent/orchestrator_skill_engine.py` (replaced by hat engine)
- Delete `agent/skill_loader.py` (replaced by hat engine)
- Delete `agent/langgraph_orchestrator.py` and `agent/langgraph_specialists.py` (nominal; real logic is in archie_loop.py)
- Delete `agent/graphs/` directory (thin wrappers with no logic)
- Delete `SESSION_CHECKPOINT.md`
- Remove `archie-cross-path-drafting` branch from remote
- Update `AGENTS.md` to reflect final state
- Update `CLAUDE.md` to reflect final state

---

## Task File Format

Every file in `tasks/` follows this structure. Codex reads the assigned task file and implements exactly what it says.

```markdown
# Task: {short name}
Phase: {0-5}
Status: todo | in-progress | done

## Goal
One sentence.

## Files to change
- `path/to/file.py` — what changes

## Files to create
- `path/to/new.py` — what it contains

## Files to delete
- `path/to/dead.py`

## Do not touch
- Explicit list of files Codex must not modify

## What to do
Step-by-step instructions specific enough that there is only one correct implementation.

## Acceptance criteria
- Testable, binary pass/fail statements
- Include the exact test command to run
```

---

## File Ownership After Phase 3

```
agent/
  archie_loop.py          ← ReAct loop, tool dispatch
  hat_engine.py           ← Hat discovery, injection, stacking
  archie_memory.py        ← Conversation, context, canonical memory
  sub_agent_client.py     ← A2A calls to sub-agents
  safety_rules.py         ← Deterministic safety blocks only (≤100 lines)
  orchestrator_agent.py   ← 50-line entry point, re-exports run_turn
  hats/                   ← .md files only, no Python
  layout_engine.py        ← unchanged
  intent_compiler.py      ← unchanged
  drawio_generator.py     ← unchanged
  bom_parser.py           ← unchanged (used by diagram sub-agent)
  document_store.py       ← unchanged
  context_store.py        ← unchanged
  decision_context.py     ← unchanged
  persistence_objectstore.py ← unchanged
  object_store_oci.py     ← unchanged
  llm_inference_client.py ← unchanged
  runtime_config.py       ← unchanged
  oci_standards.py        ← unchanged (do not edit)
  reference_architecture.py ← unchanged
  external_corpus_scorer.py ← unchanged
  jep_lifecycle.py        ← unchanged
  notifications.py        ← unchanged

sub_agents/
  bom/
  diagram/
  pov/
  jep/
  waf/
  terraform/
```

---

## What Codex Must Never Do

- Modify `PLAN.md`
- Add code to `orchestrator_agent.py` beyond what a task explicitly specifies
- Import a sub-agent module directly into orchestrator code (use `sub_agent_client.py`)
- Add LLM calls to `safety_rules.py`
- Create new `.md` skill files in `gstack_skills/` or `agent/orchestrator_skills/` (use `agent/hats/` instead)
- Merge a PR without human review
