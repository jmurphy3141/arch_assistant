# p45: Native Tool Use via OCI GenAI Function Calling

## Problem Statement

Forge uses a text-based ReAct pattern. The orchestrator LLM must emit a specific
JSON string — `{"tool": "generate_bom", "args": {...}}` — to indicate a tool
call. When the LLM writes a prose answer instead, Forge accepts it and returns
it as the final reply. None of the p39–p44 expert reasoning (pre-action thinking,
post-review, correction loops) ever fires because those all depend on the tool
being called first.

This has produced three rounds of band-aid fixes:
- Forced-tool fallback in `archie_session.py` (removed by p44e)
- Keyword-matching tool forcing (added and removed)
- `prose_guard` in Forge (added during p45 session — still present, still fragile)

The root cause is architectural: text-based ReAct is fundamentally unreliable
with chat-tuned language models that are trained to write helpful prose.

## Root Cause

OCI GenAI hosts Cohere Command R+ accessed via `GenericChatRequest` with
`api_format = "COHERE"`. Cohere Command R+ natively supports tool/function
calling. When tools are declared in the API request, the model returns structured
`tool_call` objects rather than free text. The model is explicitly trained to
use this path for actions and prose for conversation — the same distinction
Forge is trying to achieve via prompt instructions alone.

Current flow (broken):
```
User: "give me a BOM"
  → text_runner(prompt, system, "orchestrator")
  → OCI GenAI (text mode, no tools declared)
  → Model writes helpful markdown BOM table
  → _parse_tool_call(raw) returns _NO_TOOL
  → Forge returns prose as final reply
  → Expert reasoning: never runs
```

Correct flow:
```
User: "give me a BOM"
  → text_runner(prompt, system, tools=[...], "orchestrator")
  → OCI GenAI (tool mode, tools declared)
  → Model returns tool_call { name: "generate_bom", parameters: {...} }
  → Forge dispatches tool handler
  → Expert pre-action + post-review run
  → Forge returns real BOM
```

## What Must Change

### 1. `agent/llm_inference_client.py` — add tool-aware inference call

Add a new function `run_inference_with_tools(prompt, system_message, tools, ...)`.

The Cohere API in OCI GenAI accepts a `tools` list on `GenericChatRequest`.
Each tool is a `CohereToolDefinitionDetails` with:
- `name`: string matching the Forge tool name (e.g. `"generate_bom"`)
- `description`: one-sentence description of when to call it
- `parameter_definitions`: dict of parameter name → `{description, type, is_required}`

The response will contain `chat_response.chat_request.tool_calls` (a list of
`CohereToolCall` objects) when the model decides to use a tool.

Return type from the new function: `{"tool": str, "args": dict} | None`.
Return `None` if the model chose to reply conversationally.

### 2. `skillforge/protocols.py` and `skillforge/forge.py` — dual-mode text runner

The `AsyncTextRunner` type is currently `Callable[[str, str, str], Awaitable[str]]`
(prompt, system_msg, label) → raw string.

Forge needs a second callable type for the orchestrator loop only:

```python
# New type for tool-aware orchestrator calls
AsyncToolRunner = Callable[
    [str, str, list[ToolSchema], str],  # prompt, system, tools, label
    Awaitable[ToolCallResult | str]      # tool call dict OR prose string
]
```

`ToolSchema` is a lightweight dataclass:
```python
@dataclass
class ToolSchema:
    name: str
    description: str
    args: dict[str, ArgSchema]  # name → {description, type, required}

@dataclass
class ArgSchema:
    description: str
    type: str          # "string", "integer", "boolean"
    required: bool = False
```

Forge stores a `_tool_runner: AsyncToolRunner | None`. If set, the orchestrator
loop uses it instead of `_text_runner`. All other calls (step3_planning,
expert_pre_action, expert_post_review, critic) continue using `_text_runner`
unchanged — those are reasoning calls, not action-dispatch calls.

### 3. `skillforge/registry.py` — add description and arg schema to ToolSpec

`ToolSpec` needs two new optional fields:
```python
description: str = ""          # used to build ToolSchema for native tool use
args: dict[str, ArgSchema] = field(default_factory=dict)
```

`register_tool()` gains optional `description` and `args` parameters.

### 4. `skillforge/forge.py` — orchestrator loop change

In `run_turn()`, replace the orchestrator block:

```python
# BEFORE
raw = await self._text_runner(prompt_for_llm, system_msg, "orchestrator")
parsed = _parse_tool_call(raw)
if parsed is _NO_TOOL:
    reply = raw.strip()
    break
```

```python
# AFTER
if self._tool_runner is not None:
    schemas = self._build_tool_schemas(active_hats)
    result = await self._tool_runner(prompt_for_llm, system_msg, schemas, "orchestrator")
    if isinstance(result, str):
        # Model chose prose — genuinely conversational
        reply = result.strip()
        break
    parsed = result  # already a dict with "tool" and "args"
else:
    # Fallback: text-based ReAct (kept for test environments)
    raw = await self._text_runner(prompt_for_llm, system_msg, "orchestrator")
    parsed = _parse_tool_call(raw)
    if parsed is _NO_TOOL:
        reply = raw.strip()
        break
```

`_build_tool_schemas()` returns a list of `ToolSchema` for all registered tools
that are either always-active or whose `requires_hat` hat is currently active.
It excludes internal tools (`use_hat_*`, `drop_hat_*`, `save_notes`,
`get_summary`, `get_document`).

### 5. `drawing_agent_server.py` — wire the tool runner

`_make_orchestrator_text_runner()` already returns an async callable. Add a
companion `_make_orchestrator_tool_runner()` that returns `AsyncToolRunner`.

Pass it to `build_forge()`:

```python
forge = build_forge(
    ...
    tool_runner=_make_orchestrator_tool_runner(),
)
```

### 6. `agent/archie_wiring.py` — pass tool_runner to Forge, register descriptions

Update `build_forge()` signature:
```python
def build_forge(
    ...,
    tool_runner: Callable | None = None,
) -> Forge:
```

Update `register_tool` calls to include `description` and `args`:

```python
forge.register_tool(
    "generate_bom",
    BomHandler(...),
    description=(
        "Generate a priced OCI Bill of Materials. "
        "Call when the user asks for a BOM, pricing, cost estimate, "
        "or bill of materials."
    ),
    args={"prompt": ArgSchema(
        description="The user's BOM request, including workload sizing details.",
        type="string",
        required=True,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_bom_expert",
)
```

Similar descriptions for `generate_diagram`, `generate_terraform`, `generate_waf`,
`generate_pov`, `generate_jep`.

### 7. Remove `prose_guard`

Once native tool use is wired, the `prose_guard` parameter added in the current
session is dead weight. Remove it from Forge constructor and `archie_wiring.py`.

---

## OCI GenAI Generic Format Tool Use Reference

The inference client uses `API_FORMAT_GENERIC` (not Cohere format). This is
the standard format for Llama-family models on OCI GenAI. Tool support in
this format follows an OpenAI-compatible JSON Schema convention for parameter
definitions.

**Before implementing**, Codex must verify two things against the live OCI SDK:

1. Confirm the exact class names for `Tool` and `FunctionDefinition` under
   `oci.generative_ai_inference.models` — these differ by SDK version.
   Run on the server: `python3.11 -c "import oci; help(oci.generative_ai_inference.models.Tool)"`

2. Confirm the model supports tool calling. Run on the server:
   ```bash
   python3.11 -c "
   import oci, yaml
   cfg = yaml.safe_load(open('config.yaml'))
   model_id = cfg['inference']['model_id']
   print(model_id)
   # Check OCI GenAI model capabilities for this OCID
   "
   ```

**Expected API shape for Generic format** (verify against SDK before coding):

```python
# Declaring a tool — Generic format (OpenAI-compatible JSON Schema)
tool = oci.generative_ai_inference.models.Tool()
tool.type = "function"

fn = oci.generative_ai_inference.models.FunctionDefinition()
fn.name = "generate_bom"
fn.description = "Generate a priced OCI Bill of Materials."
fn.parameters = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The user's BOM request including workload sizing details."
        }
    },
    "required": ["prompt"]
}
tool.function = fn

# On GenericChatRequest
chat_request.tools = [tool]
# tool_choice can be "auto" (model decides) or "required" (must call a tool)
chat_request.tool_choice = "auto"

# Parsing the response
response_msg = result.data.chat_response.chat_request.messages[-1]
tool_calls = getattr(response_msg, 'tool_calls', None) or []
if tool_calls:
    tc = tool_calls[0]
    fn_call = tc.function
    import json
    args = json.loads(fn_call.arguments) if isinstance(fn_call.arguments, str) else (fn_call.arguments or {})
    return {"tool": fn_call.name, "args": args}
return response_msg.content[0].text  # prose response
```

**After a tool call**, the conversation history must include the tool result
as a proper `ToolMessage` (or equivalent) before the next inference call.
Forge's `_append_result()` currently appends `TOOL_RESULT(name): summary` as
plain text. With native tool use this must become a structured assistant +
tool result message pair in the messages list. **This is the most complex part
of the implementation.**

If the OCI SDK's Generic format does not yet support tool use for the deployed
model, there is a fallback path: switch the model to Cohere Command R+ with
`API_FORMAT_COHERE`. That format has well-documented tool support. The decision
on which path to take should be made after the verification step above.

---

## Files Changed

| File | Change |
|------|--------|
| `agent/llm_inference_client.py` | Add `run_inference_with_tools()` |
| `skillforge/protocols.py` | Add `AsyncToolRunner`, `ToolSchema`, `ArgSchema` |
| `skillforge/registry.py` | Add `description`, `args` to `ToolSpec` and `register_tool()` |
| `skillforge/forge.py` | Dual-mode orchestrator loop; `_build_tool_schemas()`; remove `prose_guard` |
| `agent/archie_wiring.py` | Pass `tool_runner`; add descriptions and arg schemas; remove `_archie_prose_guard` |
| `drawing_agent_server.py` | Add `_make_orchestrator_tool_runner()`; pass to `build_forge()` |

**Do NOT touch:** `agent/tools/`, hat files, `agent/bom_service.py`, sub-agents,
`tests/` (update test stubs separately after wiring is confirmed working).

---

## Verification

```bash
# Compile all changed files
python3.11 -m compileall skillforge/forge.py skillforge/protocols.py \
  skillforge/registry.py agent/llm_inference_client.py \
  agent/archie_wiring.py drawing_agent_server.py

# Forge wiring test (must all pass — confirms tool schema registration)
pytest tests/test_archie_forge_wiring.py -v --tb=short

# E2E test (uses mock tool_runner stub — confirms dispatch path)
pytest tests/test_archie_prompt_to_file_e2e.py -v --tb=short

# Prose guard removed
grep -r "prose_guard\|_archie_prose_guard" agent/ skillforge/ | wc -l
# must be 0
```

---

## What This Is NOT

This is not a change to:
- The expert reasoning (pre-action, post-review, hat activation) — that all stays
- The tool handlers (`agent/tools/`) — BomHandler, DiagramHandler etc. are unchanged
- The BOM sub-agent or any other sub-agent
- The UI

The only thing changing is the mechanism by which the orchestrator selects and
dispatches a tool. Everything downstream of tool selection stays exactly as built
in p39–p43.

---

## Commit Message

```
p45: native tool use — replace text-based ReAct with OCI GenAI function calling

The text-based ReAct pattern (LLM emits JSON on a line) is unreliable
with chat-tuned models. Replace with native Cohere function calling so
the model returns structured tool_call objects instead of prose.
Expert reasoning (p39-p43) is unchanged and fires as designed once the
tool is reliably selected.
```
