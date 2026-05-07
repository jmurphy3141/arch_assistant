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

## Memory Requirements

### Hierarchy

```
Customer
  └── Engagement  (independent requirements, independent architecture)
        └── Session  (conversation thread within an engagement)
```

A customer may have many engagements running in parallel. Each engagement has its
own requirements, assumptions, and artifact set. Sessions within an engagement
share engagement context but have independent message histories.

### What Archie Maintains

**Per engagement** (persisted to OCI Object Storage):
- `facts` — gathered workload facts: region, scale, HA requirements, constraints
- `assumptions` — explicit working assumptions with dates (used when facts are missing)
- `decisions` — architecture decisions made in this engagement
- `artifacts` — references to the latest version of each artifact type (BOM, diagram, POV, JEP, WAF, Terraform)
- `notes` — uploaded or dictated notes attached to this engagement

**Per session** (persisted, linked to engagement):
- Conversation turns, capped at `history_max_turns` (config)
- Session summary generated when turn count exceeds the cap

**Storage key structure:**
```
{customer_id}/{engagement_id}/context.json
{customer_id}/{engagement_id}/notes/
{customer_id}/{engagement_id}/artifacts/{type}/latest.json
{customer_id}/{engagement_id}/artifacts/{type}/v{n}.json
{customer_id}/{engagement_id}/sessions/{session_id}/history.json
```

### What Gets Passed to Sub-Agents

Archie passes only what a sub-agent's A2A card declares it needs.
Sub-agents do not receive raw conversation history.
Archie constructs a targeted prompt from engagement facts, current task
requirements, and any relevant prior artifact — then sends it.

---

## Phases

Work proceeds in phase order. Do not start a phase until the previous one is merged to main.

---

### Phase 0 — Stabilize (no feature work)

**Goal:** stop the drift before adding anything new.

**Tasks:**
- `tasks/p0-fix-config.md` — update `config.yaml` `git_push.branch` from `claude/webapp-fastapi-tests-sWH4S` to `main`
- `tasks/p0-delete-deprecated.md` — delete `agent/diagram_orchestrator.py`; confirm no live imports
- `tasks/p0-update-agents-md.md` — already done; keep current state
- `tasks/p0-close-orphan-branch.md` — cherry-pick the UI rename commit from `archie-cross-path-drafting` (`f22fa0d`); do not merge the full branch

**Acceptance:** all tests pass, server starts, `config.yaml` branch is `main`.

---

### Phase 1 — Sub-Agent Independence

**Goal:** every sub-agent is an independent process Archie calls over HTTP.
This is the highest priority change — it breaks the monolith and dramatically
reduces the surface Archie's orchestrator needs to cover.

**Sub-agents are independent processes.** Each runs its own FastAPI/uvicorn server
on its own port. On one machine, ports are assigned in `config.yaml`. In production,
each can be a separate container or VM. Archie only needs the URL.

### A2A Agent Card

Every sub-agent publishes an agent card at `GET /a2a/card`. Archie reads this card
to know exactly what inputs to provide. The card is the contract.

```json
{
  "name": "bom",
  "description": "Produces a priced OCI Bill of Materials from workload inputs.",
  "inputs": {
    "required": ["task"],
    "optional": ["engagement_context", "trace_id"]
  },
  "output": "Structured BOM JSON + human-readable summary",
  "llm": "ocid1.generativeaimodel..."
}
```

### A2A Call Contract

```
POST /a2a
Content-Type: application/json

{
  "task": "<prompt Archie constructed from engagement context>",
  "engagement_context": { ... },   // only fields the card declares as needed
  "trace_id": "<uuid>"
}

→ 200 OK
{
  "result": "<output>",
  "status": "ok" | "needs_input" | "error",
  "trace": { ... }
}
```

**Sub-agent directory structure:**

```
sub_agents/
  {name}/
    server.py          # FastAPI app — GET /a2a/card, POST /a2a
    system_prompt.md   # The agent's own identity and instructions
    config.yaml        # LLM model_id, port, timeouts
    README.md          # What this agent does, how to run it, how to deploy it
```

**Migration order** (one PR per sub-agent):
1. Diagram — already has A2A shape, lowest migration risk, validates the pattern
2. BOM — high complexity; validate against existing `bom_service.py` tests
3. POV
4. JEP
5. WAF
6. Terraform — set `config.yaml` model_id to code-optimised LLM

**Archie side (`sub_agent_client.py`):**
- One function: `call_sub_agent(name, task, engagement_context, trace_id) → result`
- Reads URL from `config.yaml` `sub_agents:` block
- Fetches and caches the agent card on first call
- Constructs the request from only the fields the card requires
- Returns the raw result to the caller (Archie's loop decides what to do with it)

**`gstack_skills/` and `agent/orchestrator_skills/`:**
- These become source material for each sub-agent's `system_prompt.md`
- Migrate content when building each sub-agent, then delete the originals at phase end

**Do not touch in this phase:**
- Hat system (build it on a smaller orchestrator in Phase 3)
- UI

**Acceptance:**
- Each sub-agent starts independently: `python3.11 -m uvicorn sub_agents.{name}.server:app`
- `GET /a2a/card` returns a valid card for each sub-agent
- Archie calls sub-agents via `sub_agent_client.py`; no direct imports of sub-agent modules remain in orchestrator code
- All existing integration tests pass

---

### Phase 2 — Split Orchestrator

**Goal:** break `orchestrator_agent.py` into modules Codex can work on without conflict.
By this point sub-agent logic has moved out; the remaining orchestrator code
should be much smaller and split cleanly.

**Target module structure:**

| New file | Responsibility | Approx lines |
|----------|---------------|-------------|
| `agent/archie_loop.py` | `run_turn()`, ReAct loop, tool dispatch routing | ~300 |
| `agent/archie_memory.py` | Customer/engagement/session hierarchy, context assembly | ~300 |
| `agent/sub_agent_client.py` | A2A HTTP client, agent card cache, prompt construction | ~200 |
| `agent/orchestrator_agent.py` | Thin entry point — imports and re-exports `run_turn` only | ~30 |

**Rules for this phase:**
- Behaviour must not change. This is a structural refactor only.
- Each module has one clear responsibility stated at the top of the file.
- No new features, no new abstractions beyond the split.
- Tests must pass before and after with no changes to test files.

**Do not touch in this phase:**
- Sub-agents (already independent from Phase 1)
- UI

---

### Phase 3 — Hat System

**Goal:** Archie chooses expert lenses as tools. By this phase the orchestrator
is small enough that the hat system integrates cleanly.

**What to build:**

1. `agent/hats/` directory with initial `.md` files (see hat inventory above)
2. `agent/hat_engine.py` — discovers hats at startup, registers each as a tool, handles injection and stacking
3. Modify `agent/archie_loop.py` `run_turn()` to:
   - Include hat tools in the tool list shown to the LLM
   - When a hat tool is called, load the `.md` content and prepend to the next prompt round
   - Support stacked hats (concatenate in call order)
   - Clear hat stack when Archie judges the current task complete
4. Write `agent/hats/critic.md` — the LLM-based critique reasoning
5. Write `agent/hats/governor.md` — the LLM-based governance reasoning
6. Write `agent/safety_rules.py` — deterministic block rules only (≤100 lines, no LLM calls)
7. Delete `agent/critic_agent.py` (currently a one-line re-export)
8. Delete `agent/governor_agent.py` (LLM logic now in hats; deterministic logic in safety_rules.py)

**Do not touch in this phase:**
- Sub-agents
- UI

**Acceptance:**
- Archie's tool list includes `use_hat_critic`, `use_hat_governor`, `use_hat_diagram_builder`, etc.
- A conversation log shows Archie calling a hat before delegating to a sub-agent
- Two hats can be active simultaneously; both are injected at round start
- `agent/critic_agent.py` and `agent/governor_agent.py` are deleted
- All existing tests pass

---

### Phase 4 — Cleanup

**Goal:** leave no dead code, no stale config, no orphaned branches.

- Delete `agent/orchestrator_skill_engine.py` (replaced by hat engine)
- Delete `agent/skill_loader.py` (replaced by hat engine)
- Delete `agent/langgraph_orchestrator.py` and `agent/langgraph_specialists.py`
- Delete `agent/graphs/` directory (thin wrappers with no logic)
- Delete `SESSION_CHECKPOINT.md`
- Remove `archie-cross-path-drafting` branch from remote
- Update `AGENTS.md` to reflect final state
- Update `CLAUDE.md` to reflect final state

### UI — Deferred, Low Priority

The existing UI works and is not blocking anything. Form-based modes (BOM Advisor,
Generate Diagram, etc.) stay alongside chat until there is a specific reason to
remove them. No UI work is planned in the current phase sequence.

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

## File Ownership After Phase 4

```
agent/
  archie_loop.py            ← ReAct loop, tool dispatch
  hat_engine.py             ← Hat discovery, injection, stacking
  archie_memory.py          ← Customer/engagement/session hierarchy, context
  sub_agent_client.py       ← A2A calls, agent card cache, prompt construction
  safety_rules.py           ← Deterministic safety blocks only (≤100 lines)
  orchestrator_agent.py     ← ~30-line entry point, re-exports run_turn
  hats/                     ← .md files only, no Python
  layout_engine.py          ← unchanged (used by diagram sub-agent)
  intent_compiler.py        ← unchanged (used by diagram sub-agent)
  drawio_generator.py       ← unchanged (used by diagram sub-agent)
  bom_parser.py             ← unchanged (used by bom sub-agent)
  document_store.py         ← unchanged
  context_store.py          ← unchanged (superseded by archie_memory.py over time)
  decision_context.py       ← unchanged
  persistence_objectstore.py ← unchanged
  object_store_oci.py       ← unchanged
  llm_inference_client.py   ← unchanged
  runtime_config.py         ← unchanged
  oci_standards.py          ← unchanged (do not edit)
  reference_architecture.py ← unchanged
  external_corpus_scorer.py ← unchanged
  jep_lifecycle.py          ← unchanged
  notifications.py          ← unchanged

sub_agents/
  bom/
    server.py
    system_prompt.md
    config.yaml
    README.md
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
