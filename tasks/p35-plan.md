# Phase 3.5 — Polish for Reusability & Prompt-First Architecture

## Objectives

SkillForge must become a reusable multi-agent framework that any team — OCI,
AWS, Kubernetes, Salesforce — can adopt quickly with minimal code. Archie is
both the reference implementation and the proof case: it is already a
multi-agent system (six specialist sub-agents, parallel generation paths,
complex pre-routing logic), and its active pain points define the primitives
SkillForge must provide. Simultaneously, future Archie evolution should happen
primarily through edits to system prompts and skill files, not Python code.
Phase 3.5 delivers the multi-agent coordination primitives first, then the
prompt-first authoring layer on top, so both goals reinforce each other.

---

## Guiding Principles

1. **Multi-agent coordination is a framework concern.** Delegation to
   sub-agents and parallel execution are first-class Forge primitives — not
   patterns teams implement themselves in handler code.

2. **Skill files are the truth.** Routing rules, tool guidance, behavioral
   constraints, and orchestrator behavior belong in `.md` files. If a rule is
   expressed in Python, ask whether it could live in a skill file instead.

3. **Tool registration is declarative.** A team should be able to wire an
   entire tool set from a YAML config file. Python wiring code is optional
   scaffolding, not the default path.

4. **The system prompt assembles itself.** Registered tools, active skill
   files, and hat definitions contribute to the LLM's instruction set
   automatically. No manual string concatenation in application code.

5. **Memory adapters have a minimal contract.** `assemble()` and `update()`
   are the entire interface. A team that doesn't need persistence uses
   `SimpleMemory` without writing a class.

6. **Changes stay safe and additive.** No existing Archie behavior is broken.
   Every Phase 3.5 change is either a pure addition to `skillforge/` or a
   reduction of Python logic verified by the 45 routing tests.

---

## Task Index

| ID | Title | Priority | Depends On |
|----|-------|----------|-----------|
| p35g | A2ADelegate — sub-agent HTTP wrapper | High | — |
| p35h | Parallel tool groups | High | — |
| p35c | SimpleMemory — zero-config Memory | Medium | — |
| p35a | Declarative YAML tool registration | Medium | — |
| p35b | Skill files — file-path guidance + base prompt | Medium | — |
| p35i | Dynamic system prompt assembly | Medium | p35b |
| p35d | AWS quickstart example | Medium | p35g, p35h, p35c |
| p35e | Pre-routing reduction — first increment | Large | p35b, p35i |

p35g, p35h, p35c, and p35a are independent and can run in parallel.
p35b → p35i → p35e is the prompt-first chain.
p35d is the integration proof — run last among the framework tasks.

---

## Task Summaries

### p35g — A2ADelegate (High)
**Why:** Archie's six sub-agent handlers are all the same pattern — HTTP POST,
deserialize, return ToolResult. That pattern belongs in the framework, not
six handler classes. A2ADelegate reduces each to a 3-line registration.
Directly reduces ~300 lines of bespoke Archie handler code.

**Files:** `skillforge/delegate.py` (new), `skillforge/__init__.py`
**Acceptance:** `A2ADelegate(base_url, endpoint)` works as a drop-in
ToolHandler; 6 tests pass; no Archie handler files touched yet.

---

### p35h — Parallel Tool Groups (High)
**Why:** The hardcoded `asyncio.gather()` blocks in `archie_loop.py` exist
because Forge has no way to say "run these two tools concurrently." A
`status="parallel"` result type and `ParallelToolCall` dataclass give the
LLM a clean way to declare parallel work that Forge executes natively.

**Files:** `skillforge/types.py`, `skillforge/forge.py`
**Acceptance:** Handler can return `ToolResult(status="parallel", parallel_tools=[...])`;
Forge dispatches them with `asyncio.gather()`; 5 tests pass; existing tests clean.

---

### p35c — SimpleMemory (Medium)
**Why:** Writing a `Memory` implementation is the first barrier new teams hit.
`SimpleMemory` removes it — use it out of the box, subclass when you have real
persistence needs.

**Files:** `skillforge/memory.py` (new), `skillforge/__init__.py`
**Acceptance:** `Forge(..., memory=SimpleMemory())` works end-to-end; 7 tests pass.

---

### p35a — Declarative YAML Registration (Medium)
**Why:** Tool wiring should be config, not code. `register_tools_from_config()`
lets teams describe their full tool set in a YAML file — handler import path,
flags, skill guidance file. Archie's wiring becomes a readable, diffable config.

**Files:** `skillforge/forge.py`
**Acceptance:** `forge.register_tools_from_config("tools.yaml")` registers all
tools; skill_guidance paths resolved from files; 6 tests pass.

---

### p35b — Skill Files: File-Path Guidance + Base Prompt (Medium)
**Why:** Guidance strings and the base system prompt living in Python strings
block prompt-first development. Moving them to `.md` files means product
changes are file edits, not PRs.

**Files:** `skillforge/forge.py`, `skills/README.md` (new)
**Acceptance:** `forge.set_base_prompt_file(path)` works; `register_tool(...,
skill_guidance="skills/tool.md")` reads from file; 5 tests pass.

---

### p35i — Dynamic System Prompt Assembly (Medium)
**Why:** Today, the system prompt is built once from the base string plus hat
tool definitions. It should also automatically incorporate guidance from
registered skill files so the LLM has the full picture without the application
assembling it manually. See `tasks/p35i-dynamic-prompt-assembly.md`.

**Files:** `skillforge/forge.py`, `skillforge/registry.py`
**Acceptance:** Registered tools with skill files contribute their guidance
to the assembled system prompt; new teams get complete LLM context automatically.

---

### p35d — AWS Quickstart Example (Medium)
**Why:** Reusability is proven by a working non-OCI example, not a claim.
The quickstart demonstrates A2ADelegate, SimpleMemory, parallel tools, and
YAML registration in a self-contained AWS-domain scenario with no OCI credentials.

**Files:** `examples/aws_quickstart/` (new directory)
**Acceptance:** `python run.py` works; zero OCI/Archie imports in `aws_handlers.py`.

---

### p35e — Pre-routing Reduction, Increment 1 (Large)
**Why:** The long-term goal is for `archie_loop.py`'s 4,000-line pre-routing
to shrink as the LLM handles more coordination via skill files. This increment
moves three soft-guidance rules (architecture-chat detection, recall intent,
note capture) into `skills/intent_routing.md` and removes the Python call
sites. Hard blocks stay in Python.

**Files:** `agent/archie_loop.py`, `agent/archie_wiring.py`, `skills/intent_routing.md` (new)
**Acceptance:** 3 Python routing call sites removed; 5 eval tests pass;
45 routing tests still pass.

---

## Success Criteria for Phase 3.5

Phase 3.5 is done when:

1. **Multi-agent adoption test:** A developer can register an A2A sub-agent
   with `A2ADelegate(url, endpoint)` and declare parallel execution via
   `status="parallel"` — no bespoke handler code required.

2. **New-domain test:** A developer with no OCI knowledge runs
   `examples/aws_quickstart/run.py` in under 30 minutes following the README.

3. **Declarative wiring:** Archie's tool set can be described in `forge_tools.yaml`
   with no per-tool Python wiring.

4. **Prompt-first:** The Archie base system prompt and all tool guidance live
   in `.md` files. No guidance strings in Python.

5. **Dynamic assembly:** Registered skill files contribute to the assembled
   system prompt automatically — no manual concatenation in application code.

6. **Pre-routing reduction:** At least 3 routing rules removed from
   `archie_loop.py`, verified by eval tests.

7. **Regression baseline:** `pytest tests/test_specialist_mode_routing.py`
   passes 45/45 throughout. `archie_loop.py` is under 3,000 lines.
