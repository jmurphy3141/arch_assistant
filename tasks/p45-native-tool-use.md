# p45: Native Tool Use via OCI GenAI Function Calling

## Verified Environment

- **OCI SDK version**: 2.165.1
- **API format in use**: `API_FORMAT_GENERIC` (already correct — no change needed)
- **GenericChatRequest.tools**: confirmed present
- **GenericChatRequest.tool_choice**: confirmed present
- **GenericChatRequest.reasoning_effort**: present (NONE/MINIMAL/LOW/MEDIUM/HIGH)
- **Model OCID**: `ocid1.generativeaimodel.oc1.us-chicago-1.amaaaaaask7dceyadd6ow2hxfppx7dmwmok4pon2jtsw2m2wiwoplexjrqaq`

Confirmed SDK classes (all under `oci.generative_ai_inference.models`):

| Class | Purpose |
|-------|---------|
| `ToolDefinition` | Declare a tool in the API request |
| `FunctionDefinition` | The function inside a `ToolDefinition` |
| `ToolChoiceAuto` | Model decides whether to call a tool |
| `ToolChoiceRequired` | Model must call a tool |
| `ToolChoiceNone` | Model must not call tools |
| `ToolCall` | A tool call in the model response |
| `FunctionCall` | The function invocation inside a `ToolCall` |
| `ToolMessage` | Inject a tool result back into conversation history |

No Cohere classes. No format switch. All implementation below uses these exact names.

---

## Problem Statement

Forge uses a text-based ReAct pattern. The orchestrator LLM must emit a specific
JSON string — `{"tool": "generate_bom", "args": {...}}` — to indicate a tool
call. When the LLM writes a prose answer instead, Forge accepts it and returns
it as the final reply. None of the p39–p43 expert reasoning (pre-action thinking,
post-review, correction loops) ever fires because those depend on the tool being
called first.

**Root cause**: chat-tuned language models are trained to write helpful prose.
That training consistently beats JSON-format instructions. No amount of prompt
wording is a reliable fix.

**Correct fix**: declare tools in the API request. When tools are declared,
the model returns `ToolCall` objects — not text. The model is trained to use
this path for actions and prose for conversation. Reliability goes from ~50%
to ~99%.

---

## What the Flows Look Like

**Current (broken)**
```
User: "give me a BOM"
  → text_runner(prompt, system_msg, "orchestrator")
  → OCI GenAI — no tools declared, model writes markdown BOM table
  → _parse_tool_call(raw) → _NO_TOOL
  → Forge returns prose
  → Expert reasoning: never fires
```

**After p45 (correct)**
```
User: "give me a BOM"
  → tool_runner(prompt, system_msg, [generate_bom, generate_diagram, ...], "orchestrator")
  → OCI GenAI — tools declared, model returns ToolCall{generate_bom, {prompt: "..."}}
  → Forge dispatches BomHandler
  → Expert pre-action + post-review fire
  → Forge returns real priced BOM
```

---

## Changes Required

### 1. `agent/llm_inference_client.py` — new function `run_inference_with_tools`

Add alongside the existing `run_inference`. Do not modify `run_inference`.

**Signature**:
```python
def run_inference_with_tools(
    prompt: str,
    *,
    endpoint: str,
    model_id: str,
    compartment_id: str,
    tools: list[dict],          # list of ToolDefinition dicts — see format below
    tool_choice: str = "auto",  # "auto" | "required" | "none"
    system_message: str = "",
    max_tokens: int = 4000,
    temperature: float = 0.0,
    top_p: float = 0.9,
) -> dict | str:
    """
    Call OCI GenAI with tool declarations.
    Returns {"tool": str, "args": dict} if the model called a tool.
    Returns str (the prose response) if the model replied conversationally.
    Raises RuntimeError or oci.exceptions.ServiceError on failure.
    """
```

**Building the request** — exact SDK classes:
```python
import oci, json

signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
client = oci.generative_ai_inference.GenerativeAiInferenceClient(
    config={}, signer=signer, service_endpoint=endpoint, timeout=(10, 180)
)

# Build tool definitions
oci_tools = []
for t in tools:
    fn = oci.generative_ai_inference.models.FunctionDefinition()
    fn.name = t["name"]
    fn.description = t["description"]
    fn.parameters = t["parameters"]   # JSON Schema dict

    td = oci.generative_ai_inference.models.ToolDefinition()
    td.type = "function"
    td.function = fn
    oci_tools.append(td)

# Build tool_choice object
if tool_choice == "required":
    tc = oci.generative_ai_inference.models.ToolChoiceRequired()
elif tool_choice == "none":
    tc = oci.generative_ai_inference.models.ToolChoiceNone()
else:
    tc = oci.generative_ai_inference.models.ToolChoiceAuto()

# Build the request (same pattern as run_inference)
content = oci.generative_ai_inference.models.TextContent()
content.text = prompt
message = oci.generative_ai_inference.models.Message()
message.role = "USER"
message.content = [content]

chat_request = oci.generative_ai_inference.models.GenericChatRequest()
chat_request.api_format = oci.generative_ai_inference.models.BaseChatRequest.API_FORMAT_GENERIC
chat_request.messages = [message]
chat_request.tools = oci_tools
chat_request.tool_choice = tc
chat_request.max_tokens = max_tokens
chat_request.temperature = temperature
chat_request.top_p = top_p
if system_message:
    chat_request.system = system_message

chat_detail = oci.generative_ai_inference.models.ChatDetails()
chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(model_id=model_id)
chat_detail.chat_request = chat_request
chat_detail.compartment_id = compartment_id

result = client.chat(chat_detail)
```

**Parsing the response**:
```python
# The response message is in result.data.chat_response.chat_request.messages[-1]
# (same path as existing _extract_text helper — extend rather than duplicate)
response_msg = result.data.chat_response.chat_request.messages[-1]

tool_calls = getattr(response_msg, "tool_calls", None) or []
if tool_calls:
    tc = tool_calls[0]                         # ToolCall object
    fn_call = tc.function                      # FunctionCall object
    raw_args = fn_call.arguments or "{}"
    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
    return {"tool": fn_call.name, "args": args}

# No tool call — extract prose text the same way _extract_text does today
return _extract_text(result)   # reuse existing helper
```

---

### 2. `skillforge/protocols.py` — add three new types

```python
from dataclasses import dataclass, field

@dataclass
class ArgSchema:
    description: str
    type: str           # "string" | "integer" | "number" | "boolean"
    required: bool = False

@dataclass
class ToolSchema:
    name: str
    description: str
    args: dict[str, ArgSchema] = field(default_factory=dict)

    def to_api_dict(self) -> dict:
        """Convert to the dict format expected by run_inference_with_tools."""
        properties = {
            k: {"type": v.type, "description": v.description}
            for k, v in self.args.items()
        }
        required = [k for k, v in self.args.items() if v.required]
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

# New callable type: orchestrator tool-aware runner
# Returns {"tool": str, "args": dict} | str
AsyncToolRunner = Callable[
    [str, str, list[ToolSchema], str],
    Awaitable[dict | str],
]
```

Export `ArgSchema`, `ToolSchema`, `AsyncToolRunner` from `skillforge/__init__.py`.

---

### 3. `skillforge/registry.py` — add `description` and `args` to ToolSpec

Add two optional fields to the `ToolSpec` dataclass:
```python
description: str = ""
args: dict[str, "ArgSchema"] = field(default_factory=dict)
```

Add optional `description` and `args` parameters to `register_tool()`. Forward
them into the `ToolSpec`.

---

### 4. `skillforge/forge.py` — dual-mode orchestrator loop

**Constructor**: add `tool_runner: AsyncToolRunner | None = None` parameter.
Store as `self._tool_runner`.

**New private method** `_build_tool_schemas(active_hats: list[str]) -> list[ToolSchema]`:
- Iterate all registered tools in `self._registry`
- Skip internal tools: any whose name starts with `use_hat_`, `drop_hat_`, or
  whose name is in `{"save_notes", "get_summary", "get_document"}`
- For tools with `requires_hat`: only include if that hat is in `active_hats`
- For tools with no `requires_hat`: always include
- Build and return a list of `ToolSchema` from each spec's `description` and `args`

**Orchestrator loop change** — replace lines 458–464 (the current block):
```python
raw = await self._text_runner(prompt_for_llm, system_msg, "orchestrator")
parsed = _parse_tool_call(raw)
if parsed is _NO_TOOL:
    reply = raw.strip()
    break
```

With:
```python
if self._tool_runner is not None:
    schemas = self._build_tool_schemas(active_hats)
    result = await self._tool_runner(
        prompt_for_llm, system_msg, schemas, "orchestrator"
    )
    if isinstance(result, str):
        reply = result.strip()
        break
    parsed = result   # {"tool": ..., "args": ...}
else:
    # Text-based fallback — used in tests and when tool_runner is not configured
    raw = await self._text_runner(prompt_for_llm, system_msg, "orchestrator")
    parsed = _parse_tool_call(raw)
    if parsed is _NO_TOOL:
        reply = raw.strip()
        break
```

**Remove `prose_guard`**: delete the `prose_guard` constructor parameter and the
prose-guard block that was added to the `_NO_TOOL` branch. It is replaced by
this change entirely.

---

### 5. `drawing_agent_server.py` — add `_make_orchestrator_tool_runner`

Add alongside `_make_orchestrator_text_runner`. Returns `AsyncToolRunner`.

```python
def _make_orchestrator_tool_runner():
    """
    Return an async callable (prompt, system_msg, tools, label) -> dict | str
    for native tool use in the orchestrator loop.
    """
    def _sync_runner(
        prompt: str,
        system_msg: str,
        schemas: list,          # list[ToolSchema]
        model_profile: str = "orchestrator",
    ) -> dict | str:
        if not (_INFERENCE_AVAILABLE and INFERENCE_ENABLED):
            raise RuntimeError("Inference not enabled.")
        from agent.llm_inference_client import run_inference_with_tools
        llm_cfg = resolve_agent_llm_config(_cfg, model_profile)
        return run_inference_with_tools(
            prompt=prompt,
            system_message=system_msg,
            tools=[s.to_api_dict() for s in schemas],
            tool_choice="auto",
            model_id=llm_cfg.get("model_id", INFERENCE_MODEL_ID),
            endpoint=llm_cfg.get("service_endpoint", INFERENCE_ENDPOINT),
            compartment_id=COMPARTMENT_ID,
            max_tokens=int(llm_cfg.get("max_tokens", 4000)),
            temperature=float(llm_cfg.get("temperature", 0.0)),
            top_p=float(llm_cfg.get("top_p", 0.9)),
        )

    async def _async_runner(
        prompt: str,
        system_msg: str,
        schemas: list,
        model_profile: str = "orchestrator",
    ) -> dict | str:
        import asyncio
        return await asyncio.to_thread(_sync_runner, prompt, system_msg, schemas, model_profile)

    return _async_runner
```

In the `/api/chat` handler, add the tool runner to `build_forge()`:
```python
forge = build_forge(
    store=store,
    customer_id=customer_id,
    customer_name=customer_name,
    text_runner=_make_orchestrator_text_runner(),
    tool_runner=_make_orchestrator_tool_runner(),   # ← add this
    a2a_base_url=A2A_BASE_URL,
    base_system_prompt=ARCHIE_SYSTEM_PROMPT,
    step3_planning=True,
)
```

---

### 6. `agent/archie_wiring.py` — wire tool_runner and add tool descriptions

Update `build_forge()` signature:
```python
def build_forge(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    text_runner: Callable,
    a2a_base_url: str = "",
    base_system_prompt: str = "",
    step3_planning: bool = True,
    tool_runner: Callable | None = None,    # ← add
) -> Forge:
```

Pass `tool_runner` to `Forge(...)`.

**Remove `_archie_prose_guard` and `_PROSE_GUARD_RULES`** — these are dead code
once native tool use is wired.

Add `description` and `args` to every generation tool registration.
Import `ArgSchema` from `skillforge`.

```python
from skillforge import ArgSchema

forge.register_tool(
    "generate_bom",
    BomHandler(...),
    description=(
        "Generate a priced OCI Bill of Materials with SKU-backed line items "
        "and monthly cost totals. Call when the user asks for a BOM, pricing, "
        "cost estimate, or bill of materials."
    ),
    args={"prompt": ArgSchema(
        description="Full BOM request including workload sizing (OCPU, memory, storage, services).",
        type="string",
        required=True,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_bom_expert",
)

forge.register_tool(
    "generate_diagram",
    DiagramHandler(...),
    description=(
        "Generate an OCI architecture diagram as a draw.io file. Call when the "
        "user asks for a diagram, architecture drawing, or visual of the design."
    ),
    args={"prompt": ArgSchema(
        description="Architecture description or BOM payload to diagram.",
        type="string",
        required=True,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="diagram_for_oci",
)

forge.register_tool(
    "generate_terraform",
    TerraformHandler(...),
    description=(
        "Generate OCI Terraform files (main.tf, variables.tf, outputs.tf). "
        "Call when the user asks for Terraform, IaC, or infrastructure code."
    ),
    args={"prompt": ArgSchema(
        description="Terraform generation request describing the OCI resources needed.",
        type="string",
        required=False,
    )},
    memory_contract=True,
    requires_hat="terraform_for_oci",
)

forge.register_tool(
    "generate_waf",
    WafHandler(...),
    description=(
        "Generate a Well-Architected Framework review document for the customer's "
        "OCI architecture. Call when the user asks for a WAF review or assessment."
    ),
    args={"feedback": ArgSchema(
        description="Optional additional context or focus areas for the WAF review.",
        type="string",
        required=False,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_waf_reviewer",
)

forge.register_tool(
    "generate_pov",
    PovHandler(...),
    description=(
        "Generate a Point of View document for the customer engagement. "
        "Call when the user asks for a POV, executive summary, or customer brief."
    ),
    args={"feedback": ArgSchema(
        description="Optional focus areas or additional context for the POV document.",
        type="string",
        required=False,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_customer_pov_writer",
)

forge.register_tool(
    "generate_jep",
    JepHandler(...),
    description=(
        "Generate a Joint Execution Plan document for the customer engagement. "
        "Call when the user asks for a JEP, joint plan, or execution roadmap."
    ),
    args={"feedback": ArgSchema(
        description="Optional milestones, scope, or context for the JEP document.",
        type="string",
        required=False,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="jep_writer",
)
```

Notes and other internal tools (`save_notes`, `get_summary`, `get_document`) do
not get descriptions or args — they are excluded from tool schemas passed to the
API (see `_build_tool_schemas` rule above).

---

## Files Changed

| File | Change |
|------|--------|
| `agent/llm_inference_client.py` | Add `run_inference_with_tools()` |
| `skillforge/protocols.py` | Add `ArgSchema`, `ToolSchema`, `AsyncToolRunner` |
| `skillforge/__init__.py` | Export `ArgSchema`, `ToolSchema` |
| `skillforge/registry.py` | Add `description`, `args` to `ToolSpec` and `register_tool()` |
| `skillforge/forge.py` | Add `tool_runner` param; add `_build_tool_schemas()`; dual-mode orchestrator loop; remove `prose_guard` |
| `agent/archie_wiring.py` | Add `tool_runner` param to `build_forge()`; add descriptions/args to all tool registrations; remove `_archie_prose_guard` and `_PROSE_GUARD_RULES` |
| `drawing_agent_server.py` | Add `_make_orchestrator_tool_runner()`; pass to `build_forge()` |

**Do NOT touch**: `agent/tools/`, hat files, `agent/bom_service.py`, sub-agents,
`tests/` (update E2E test stubs separately in p45b once wiring is confirmed live).

---

## Verification

```bash
# Compile all changed files
python3.11 -m compileall \
  skillforge/forge.py skillforge/protocols.py skillforge/__init__.py \
  skillforge/registry.py agent/llm_inference_client.py \
  agent/archie_wiring.py drawing_agent_server.py

# Forge wiring tests
pytest tests/test_archie_forge_wiring.py -v --tb=short
# All 5 must pass

# E2E tests — still use text-based fallback path in test environment
# (tool_runner is None in tests; _parse_tool_call fallback stays active)
pytest tests/test_archie_prompt_to_file_e2e.py -v --tb=short
# All 6 must pass

# prose_guard is gone
grep -r "prose_guard\|_archie_prose_guard\|_PROSE_GUARD_RULES" agent/ skillforge/
# must return nothing

# Tool descriptions registered
grep -c "description=" agent/archie_wiring.py
# must be >= 6 (one per generation tool)
```

---

## What This Is NOT

- Not a change to expert reasoning (pre-action, post-review, correction loops)
- Not a change to tool handlers (`agent/tools/`)
- Not a change to sub-agents, BOM service, or UI
- Not a change to the system prompt content — tool selection is now structural,
  not prompt-based

The only thing changing is the mechanism by which the orchestrator decides which
tool to call. Everything built in p39–p43 fires as designed once tool selection
is reliable.

---

## Commit Message

```
p45: replace text-based ReAct with OCI GenAI native function calling

Declare all generation tools as ToolDefinition objects on GenericChatRequest.
Model returns structured ToolCall objects instead of text containing JSON.
Removes prose_guard and all keyword-based tool forcing hacks. Expert
reasoning (p39-p43) is unchanged and now fires reliably on every request.

SDK verified: OCI SDK 2.165.1, GenericChatRequest.tools confirmed present,
ToolDefinition/FunctionDefinition/ToolCall/ToolMessage all available.
```
