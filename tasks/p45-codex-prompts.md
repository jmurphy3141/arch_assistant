# p45 Codex Prompts — Native Tool Use via OCI GenAI Function Calling

## Background

Forge uses a text-based ReAct pattern where the orchestrator LLM must emit
`{"tool": "generate_bom", "args": {...}}` as raw text to trigger a tool call.
Chat-tuned models write helpful prose instead, so the tool never fires and the
p39–p43 expert reasoning is never reached.

Fix: declare tools as `ToolDefinition` objects on `GenericChatRequest`. The
model then returns structured `ToolCall` objects. The existing tool handlers,
expert reasoning, and sub-agents are unchanged.

**Verified environment** (do not re-verify — these are confirmed):
- OCI SDK 2.165.1
- `GenericChatRequest.tools` and `.tool_choice` confirmed present
- SDK classes confirmed: `ToolDefinition`, `FunctionDefinition`, `ToolCall`,
  `FunctionCall`, `ToolMessage`, `ToolChoiceAuto`, `ToolChoiceRequired`

Full implementation spec is in `tasks/p45-native-tool-use.md`. Read it
completely before writing any code.

Run order: p45a → p45b. Branch p45a from main. Branch p45b from main after
p45a merges.

---

## p45a — Wire Native Tool Use

```
Read tasks/p45-native-tool-use.md completely before touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p45a origin/main

Run the prerequisite check first:

  python3.11 -m compileall skillforge/forge.py agent/archie_wiring.py -q
  grep -c "prose_guard\|_archie_prose_guard\|_PROSE_GUARD_RULES" \
    skillforge/forge.py agent/archie_wiring.py
  # note the count — you will delete these

Implement the following in order. Compile after each file.

Step 1 — skillforge/protocols.py
Add ArgSchema, ToolSchema (with to_api_dict() method), and AsyncToolRunner
exactly as specified in the task spec.

Step 2 — skillforge/__init__.py
Export ArgSchema and ToolSchema from the public API surface.

Step 3 — skillforge/registry.py
Add optional description: str = "" and args: dict[str, ArgSchema] fields to
ToolSpec. Add optional description and args parameters to register_tool() and
forward them into ToolSpec.

Step 4 — agent/llm_inference_client.py
Add run_inference_with_tools() alongside the existing run_inference(). Do not
modify run_inference(). Use the exact SDK classes from the task spec:
ToolDefinition, FunctionDefinition, ToolChoiceAuto, ToolChoiceRequired,
ToolChoiceNone. Reuse the existing _extract_text() helper for the prose path.

Step 5 — skillforge/forge.py
a) Add tool_runner: AsyncToolRunner | None = None constructor parameter.
   Store as self._tool_runner.
b) Add _build_tool_schemas(active_hats) method. Exclude tools whose name
   starts with use_hat_, drop_hat_, or is in {save_notes, get_summary,
   get_document}. Include tools with requires_hat only when that hat is active.
c) Replace the orchestrator block (the lines calling self._text_runner with
   label "orchestrator" and then _parse_tool_call) with the dual-mode block
   from the task spec.
d) Delete the prose_guard constructor parameter and the prose-guard correction
   block from the _NO_TOOL branch. Remove self._prose_guard storage.

Step 6 — agent/archie_wiring.py
a) Import ArgSchema from skillforge.
b) Add tool_runner: Callable | None = None parameter to build_forge().
   Pass it to Forge(...).
c) Add description and args to every register_tool() call for the six
   generation tools: generate_bom, generate_diagram, generate_terraform,
   generate_waf, generate_pov, generate_jep. Use the descriptions from
   the task spec exactly.
d) Delete _archie_prose_guard() and _PROSE_GUARD_RULES entirely.

Step 7 — drawing_agent_server.py
a) Add _make_orchestrator_tool_runner() alongside _make_orchestrator_text_runner().
   Follow the exact structure in the task spec.
b) Find every call to build_forge() in the /api/chat handler path and add
   tool_runner=_make_orchestrator_tool_runner() to it.

Run ALL acceptance criteria:

  python3.11 -m compileall \
    skillforge/forge.py skillforge/protocols.py skillforge/__init__.py \
    skillforge/registry.py agent/llm_inference_client.py \
    agent/archie_wiring.py drawing_agent_server.py -q
  # must be clean

  pytest tests/test_archie_forge_wiring.py -v --tb=short
  # all 5 must pass

  pytest tests/test_archie_prompt_to_file_e2e.py -v --tb=short
  # all 6 must pass — these use the text-based fallback (tool_runner=None in tests)

  grep -r "prose_guard\|_archie_prose_guard\|_PROSE_GUARD_RULES" agent/ skillforge/
  # must return nothing

  grep -c "description=" agent/archie_wiring.py
  # must be >= 6

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p45a: native tool use — OCI GenAI ToolDefinition replaces text-based ReAct

Branch: claude/p45a (from main). Push when done.
```

---

## p45b — Update E2E Test Stubs for Tool Runner

```
Read tasks/p45-native-tool-use.md section "Files Changed" before starting.

IMPORTANT: Branch from origin/main AFTER p45a is merged.

  git fetch origin
  git checkout -b claude/p45b origin/main

Context: p45a added a tool_runner parameter to build_forge(). The E2E tests
in tests/test_archie_prompt_to_file_e2e.py use the text-based fallback because
tool_runner=None in test fixtures. This is correct and intentional — tests
should not call OCI GenAI.

However, test_archie_forge_wiring.py should be updated to assert that:
1. All six generation tools have non-empty description registered in the spec
2. All six have at least one ArgSchema entry in args
3. _build_tool_schemas() returns schemas for generation tools and excludes
   internal tools (save_notes, get_summary, get_document)

Run prerequisite check:

  pytest tests/test_archie_forge_wiring.py -v --tb=short
  # note which assertions currently exist

Add the following tests to tests/test_archie_forge_wiring.py:

Test: test_generation_tools_have_descriptions
  Build a forge with build_forge(). Get the registry. For each of
  generate_bom, generate_diagram, generate_terraform, generate_waf,
  generate_pov, generate_jep: assert spec.description is not empty string.

Test: test_generation_tools_have_arg_schemas
  Same setup. For each generation tool: assert spec.args is a non-empty dict.

Test: test_build_tool_schemas_excludes_internal_tools
  Build a forge. Call forge._build_tool_schemas(active_hats=[]).
  Assert none of the returned schemas have name in
  {save_notes, get_summary, get_document, use_hat_*, drop_hat_*}.
  Assert all six generation tools ARE in the returned schemas when no
  requires_hat gate applies (or the relevant hat is active).

Run ALL acceptance criteria:

  pytest tests/test_archie_forge_wiring.py -v --tb=short
  # all tests must pass including the 3 new ones

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no regressions

Commit message:
p45b: forge wiring tests — assert tool descriptions and schema exclusion rules

Branch: claude/p45b (from main after p45a merges). Push when done.
```

---

## p45c — Wire tool_runner into Streaming Path

```
Context: p45a wired the native OCI GenAI tool runner into /api/chat but missed
the streaming endpoint /api/chat/stream. The UI always uses the streaming path,
so native tool use never fires in production.

Root cause (confirmed by code inspection):
- drawing_agent_server.py /api/chat/stream handler (line ~3579) calls
  stream_chat_turn() with text_runner but no tool_runner.
- agent/chat_stream.py _chat_event_dicts() calls _run_orchestrator_turn()
  with text_runner but no tool_runner.
- _run_orchestrator_turn() accepts tool_runner but receives None, so Forge
  falls back to text-based ReAct.

IMPORTANT: Branch from origin/main AFTER p45b is merged.

  git fetch origin
  git checkout -b claude/p45c origin/main

Read agent/chat_stream.py lines 18–75 fully before editing.
Read drawing_agent_server.py lines 3579–3594 fully before editing.

Implement the following — three small changes only:

Change 1 — agent/chat_stream.py: _chat_event_dicts()
Add tool_runner=None as a keyword-only parameter after text_runner.
Pass it through to _run_orchestrator_turn():
  result = await server._run_orchestrator_turn(
      req=req,
      store=store,
      text_runner=text_runner,
      tool_runner=tool_runner,          ← add this line
      orch_cfg=server._cfg.get("orchestrator", {}),
      reasoning_sink=_thinking_sink,
  )

Change 2 — agent/chat_stream.py: stream_chat_turn() and stream_chat_turn_sse()
Add tool_runner=None as a keyword-only parameter to both functions.
Forward it to _chat_event_dicts() in each.

Change 3 — drawing_agent_server.py: /api/chat/stream handler (line ~3579)
Build the tool_runner (same pattern as /api/chat):
  tool_runner = (
      None
      if isinstance(store, InMemoryObjectStore)
      else _make_orchestrator_tool_runner()
  )
Pass it to stream() calls:
  stream(
      ...,
      text_runner=_make_orchestrator_text_runner(),
      tool_runner=tool_runner,          ← add this
      ...
  )

Do NOT change _run_orchestrator_turn(), build_forge(), Forge, or any test files.

Run ALL acceptance criteria:

  python3.11 -m compileall agent/chat_stream.py drawing_agent_server.py -q
  # must be clean

  grep -n "tool_runner" agent/chat_stream.py
  # must show tool_runner in _chat_event_dicts, stream_chat_turn,
  # and stream_chat_turn_sse

  pytest tests/test_archie_forge_wiring.py tests/test_archie_prompt_to_file_e2e.py \
    -v --tb=short
  # all 11 must pass

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline (pre-existing JEP lifecycle failure is ok)

Commit message:
p45c: wire tool_runner into streaming path — /api/chat/stream was missing it

Branch: claude/p45c (from main after p45b merges). Push when done.
```

---

## p45d — Fix run_inference_with_tools() Response Parsing

```
Context: p45a introduced run_inference_with_tools() in agent/llm_inference_client.py.
The function uses OCI GenAI Generic format (API_FORMAT_GENERIC) but the code that
reads back the model response uses the Cohere-format attribute path:

  result.data.chat_response.chat_request.messages[-1]   ← WRONG (Cohere only)

GenericChatResponse has no `chat_request` attribute, so every call crashes with:

  AttributeError: 'GenericChatResponse' object has no attribute 'chat_request'

This means native tool use never fires in production — every tool call crashes before
returning the tool name to Forge.

The correct path for Generic format is the same one used by the existing _extract_text()
helper (lines 225–244 in the same file):

  result.data.chat_response.choices[0].message

IMPORTANT: Branch from origin/main AFTER p45c is merged.

  git fetch origin
  git checkout -b claude/p45d origin/main

Read agent/llm_inference_client.py lines 124–222 fully before editing.

Make exactly ONE change — nothing else:

In run_inference_with_tools(), find this block (around line 213):

  result = client.chat(chat_detail)

  response_msg = result.data.chat_response.chat_request.messages[-1]
  tool_calls = getattr(response_msg, "tool_calls", None) or []

Replace the single `response_msg` assignment line with:

  response_msg = result.data.chat_response.choices[0].message

Do NOT change any other line. Do NOT change run_inference(). Do NOT change _extract_text().
Do NOT change forge.py, archie_wiring.py, drawing_agent_server.py, or any test files.

Verify the fix is complete and nothing else changed:

  python3.11 -m compileall agent/llm_inference_client.py -q
  # must be clean

  grep -n "chat_request\." agent/llm_inference_client.py
  # must return nothing — the old Cohere-format path is gone

  grep -n "choices\[0\]" agent/llm_inference_client.py
  # must appear in BOTH _extract_text() (original) AND run_inference_with_tools() (new fix)

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures

Commit message:
p45d: fix run_inference_with_tools() response path — Generic format uses choices[0], not chat_request

Branch: claude/p45d (from main after p45c merges). Push when done.
```

---

## p45e — Fix _build_tool_schemas() requires_hat Gate

```
Context: p45d fixed the response parsing crash. However native tool use still
does not fire — the model returns prose on every request. Root cause confirmed
by code inspection of skillforge/forge.py.

In _build_tool_schemas(active_hats), this gate exists (around line 285):

  if spec.requires_hat and spec.requires_hat not in active_hats:
      continue

All six generation tools (generate_bom, generate_diagram, generate_terraform,
generate_waf, generate_pov, generate_jep) have requires_hat set. At the start
of every turn active_hats=[] because no hats are pre-activated. Therefore
_build_tool_schemas([]) returns an empty list on every first call. The tool
runner is called with zero schemas, the OCI model sees no available tools and
returns prose, the isinstance(result, str) branch fires, and no tool is ever
called.

Forge already has hat auto-activation at tool dispatch time (around line 608):

  if spec.requires_hat and spec.requires_hat not in active_hats:
      active_hats = self._hat_engine.apply_hat(active_hats, spec.requires_hat)

This means the requires_hat gate in _build_tool_schemas() is redundant for
native tool use: the hat is activated automatically when the tool fires. The
gate only makes sense in text-based ReAct where the orchestrator must
explicitly call use_hat_* before a tool is available. In native tool use,
Forge handles it transparently.

IMPORTANT: Branch from origin/main AFTER p45d is merged.

  git fetch origin
  git checkout -b claude/p45e origin/main

Read skillforge/forge.py _build_tool_schemas() fully before editing. Confirm:
- It excludes use_hat_*, drop_hat_*, save_notes, get_summary, get_document
- It has the requires_hat gate (the bug to remove)
- Forge's domain tool dispatch block has hat auto-activation already

Make exactly ONE change — nothing else:

In _build_tool_schemas(), remove these two lines:

  if spec.requires_hat and spec.requires_hat not in active_hats:
      continue

Do NOT add any replacement logic. Do NOT change the exclusion of use_hat_*,
drop_hat_*, or the internal tools set. Do NOT change the tool dispatch block.
Do NOT change any other file.

Verify:

  python3.11 -m compileall skillforge/forge.py -q
  # must be clean

  grep -n "requires_hat" skillforge/forge.py
  # must NOT show the gate inside _build_tool_schemas()
  # MUST still show the auto-activation block in the tool dispatch section

  pytest tests/test_archie_forge_wiring.py -v --tb=short
  # test_build_tool_schemas_excludes_internal_tools must pass
  # generation tools must appear in schemas even with active_hats=[]

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures (pre-existing JEP lifecycle failure is ok)

Commit message:
p45e: remove requires_hat gate from _build_tool_schemas — Forge auto-activates hats at dispatch

Branch: claude/p45e (from main after p45d merges). Push when done.
```

---

## p45f — Fix ToolDefinition type Case

```
Context: p45e made generation tools visible to the model. The OCI API now
receives tool schemas but rejects every request with:

  Invalid value for `type`, must be None or one of ['FUNCTION']

Root cause: in agent/llm_inference_client.py run_inference_with_tools(),
the ToolDefinition type field is set to lowercase "function":

  td.type = "function"   ← wrong case

The OCI SDK enum accepts only "FUNCTION" (uppercase) or None. The SDK
validates on assignment.

IMPORTANT: Branch from origin/main AFTER p45e is merged.

  git fetch origin
  git checkout -b claude/p45f origin/main

Read agent/llm_inference_client.py lines 155–175 fully before editing.
Find the loop that builds oci_tools — it contains:

  td = oci.generative_ai_inference.models.ToolDefinition()
  td.type = "function"
  td.function = fn
  oci_tools.append(td)

Make exactly ONE change — change the type assignment to uppercase:

  td.type = "FUNCTION"

Do NOT change any other line. Do NOT change run_inference(), _extract_text(),
forge.py, archie_wiring.py, drawing_agent_server.py, or any test files.

Verify:

  python3.11 -m compileall agent/llm_inference_client.py -q
  # must be clean

  grep -n 'td\.type' agent/llm_inference_client.py
  # must show "FUNCTION" (uppercase)

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures

Commit message:
p45f: fix ToolDefinition type case — OCI SDK requires "FUNCTION" not "function"

Branch: claude/p45f (from main after p45e merges). Push when done.
```
