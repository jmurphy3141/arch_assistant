# Task: native archie loop
Phase: 5
Status: todo

## Goal
Add a native tool-calling agent loop selectable by `agent_mode: native`, retiring
Forge orchestration on that path only; forge mode stays byte-for-byte unchanged.

## Files to create
- `agent/archie_native_loop.py` — `run_turn(...)`: assemble messages (Archie
  identity + injected memory/context from `archie_memory` + session history + user
  message); build native function declarations from the EXISTING tool registry
  (sub-agents, `list_documents`/`get_document`/`get_summary`, memory tools, and
  `use_hat_*` hats); loop on `llm_inference_client.run_inference_with_tools` —
  execute the called handler, append its result, repeat until the model returns
  text. Cap at `inference.max_tool_iterations`. No planning, no `requires_hat`
  gate, no pre/post-review, no `_parse_tool_call`.
- `tests/test_archie_native_loop.py` — the acceptance tests below.

## Files to change
- `config.yaml` — add `agent_mode: forge` (default) under the runtime block.
- `agent/archie_session.py` — if `agent_mode == "native"`, call
  `archie_native_loop.run_turn`; else call forge exactly as today. No other change.
- `agent/archie_wiring.py` — expose the registered tools/handlers so the native
  loop builds native function declarations from the SAME registrations (reuse; do
  not re-register or fork the tool list).
- `agent/hat_engine.py` — in native mode, expose each hat as a `use_hat_{name}`
  tool whose result is the hat's `.md` content (the model reads it and continues).

## Files to delete
- None. Forge is retired by flag, not removed.

## Do not touch
- `skillforge/forge.py` and the entire forge path
- `sub_agents/**` internals and the composers (`jep_composer`, `poc_composer`, …)
- Sub-agent A2A contracts
- The Forge `excluded` set

## What to do
1. Add `agent_mode` to `config.yaml` (default `"forge"`).
2. Build `archie_native_loop.run_turn` per the create-file spec, reusing the
   existing tool registry and the same memory/context assembly the forge path uses.
3. System identity is the ONLY prose the loop injects (no sequencing rules):
   "You are Archie, a manager of expert OCI sub-agents and a sharp
   solutions-architect colleague. Converse and advise freely. When the user wants
   a deliverable, call the sub-agent; when they ask whether one exists or what it
   says, fetch and read it; otherwise just talk. Never fabricate a deliverable or a
   stored fact — call the tool, or say you don't have it."
4. Branch `archie_session` on `agent_mode`; leave the forge path identical.
5. Hats are native tools returning `.md` content; the model stacks/clears them
   itself — no `requires_hat` gate in this path.

## Acceptance criteria
- `agent_mode: native`:
  - "do we have a BOM?" → reply is yes+link or no, with NO prose BOM.
  - "make me a BOM for this POC" → real `.xlsx` produced via the bom sub-agent.
  - "what would you recommend for HA here?" → conversational reply, no artifact.
  (assert all three in `tests/test_archie_native_loop.py`)
- The native path calls `run_inference_with_tools`; grep shows no `_parse_tool_call`
  import or use in `agent/archie_native_loop.py`.
- `agent_mode: forge`: full suite unchanged → `pytest -m "not live"` green.
- New tests green → `pytest tests/test_archie_native_loop.py -m "not live"`.
