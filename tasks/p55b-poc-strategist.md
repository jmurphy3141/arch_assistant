# Codex Agent Prompt — Issue 2: POC Strategist (3 Parallel Option Exploration)

**Repository:** jmurphy3141/arch_assistant
**Branch:** claude/explore-repo-Os53i
**Requirements:** docs/requirements-poc-workflow.md FR-1.*
**Must be merged before:** Issue 3 (fan-out)

---

## Task

Build the POC Strategist: a new Archie tool that takes rough customer requirements and returns 3 ranked POC options explored via 3 parallel sub-agent calls.

---

## Context

### Existing patterns to follow exactly

**Sub-agent pattern:** `sub_agents/pov/` is the reference. Copy its structure:
- `server.py` — FastAPI A2A handler with `AgentCard` and `async handle()`
- `system_prompt.md` — LLM instructions loaded at startup
- `config.yaml` — port, llm.model_id, max_tokens, temperature
- `__init__.py` — empty marker

**Handler pattern:** `agent/tools/specialists.py` — `_SpecialistHandler` (lines 45–230). Key points:
- `__call__(self, args, *, memory, context, trace_id) -> ToolResult`
- Hydrates args from `memory.decision_context` and `context` dict
- Calls `sub_agent_client.call_sub_agent(agent_name, task=..., engagement_context=..., trace_id=...)`
- Returns `ToolResult(summary=..., status="ok"|"needs_input", artifact_key=..., data=...)`

**Hat format:** `agent/hats/oci_bom_expert.md` (263 lines). Required sections:
1. YAML frontmatter with `version`, `display_name`, `hat_rules`, `memory_focus`, `coordination`
2. `# {Hat Name}` — intro
3. `## Core Principles`
4. `## Quality Bar`
5. `## Output Contract`
6. `## Critic Evaluation Guidance`
7. `## Failure Questions`
8. `## Activation & Drop`
9. `## Pre-Action Checklist`
10. `## Post-Action Review`

**Tool registration:** `agent/archie_wiring.py` `build_forge()` function. Each tool registered with:
```python
forge.register_tool(
    name="generate_X",
    description="...",
    handler=XHandler(...),
    requires_hat="hat_name",
    memory_contract=[...],
)
```

**Parallel sub-agent calls:** Use `asyncio.gather()` directly in the handler — NOT via `ToolResult(status="parallel")`. The parallel ToolResult mechanism is for dispatching other Forge tools; within a handler, use asyncio directly.

**sub_agent_client:** `agent/sub_agent_client.py` — `call_sub_agent(agent_name, task, engagement_context, trace_id)`

---

## What to Build

### 1. `sub_agents/poc_strategist/` — New sub-agent (port 8087)

**`config.yaml`:**
```yaml
name: poc_strategist
port: 8087
llm:
  model_id: null  # inherits from root config.yaml
  max_tokens: 2048
  temperature: 0.6
```

**`system_prompt.md`:** Expert POC strategist. Given customer context and an exploration angle, generate exactly ONE POC option as JSON. The option must be scored on:
- `relevance_score` (1–10): does this directly prove the customer's stated pain?
- `executability_hours` (int): can an SE build and demo this in under 8 hours?
- `cost_effectiveness` (string): is the OCI monthly cost defensible vs. current spend?
- `security_highlights` (list): OCI security controls the customer would care about
- `wow_moment` (string): the single demonstration moment that will land hardest
- `demo_script_summary` (string): 2–3 sentence walk-through of what to show
- `oci_services` (list): specific OCI service names
- `option_name` (string): a concrete, customer-specific title (not generic)

Output format: raw JSON object, no markdown wrapping.

**`server.py`:** Copy `sub_agents/pov/server.py`. Change:
- `agent_name = "poc_strategist"`
- AgentCard: `inputs` = `["task", "angle", "customer_context"]`, `required` = `["task"]`
- Port from `config.yaml`

### 2. `agent/tools/specialists.py` — Add `PocStrategistHandler`

```python
class PocStrategistHandler:
    def __init__(self, store, customer_id, customer_name):
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
        # Build customer context string from memory.decision_context
        dc = memory.decision_context if memory else {}
        pain = dc.get("pain_statement", "")
        platform = dc.get("current_platform", "")

        if not pain or not platform:
            return ToolResult(
                status="needs_input",
                summary="Need customer pain statement and current platform before planning POC options.",
                clarification="What is the customer's primary pain (cost, performance, risk, compliance) and what platform are they currently running on?",
            )

        user_message = args.get("_user_message", "")
        base_task = f"Customer: {self._customer_name}\nContext: {user_message}\n\nDecision context:\n{dc}"

        # 3 parallel calls — one per exploration angle
        results = await asyncio.gather(
            call_sub_agent("poc_strategist",
                task=base_task,
                engagement_context={"angle": "migration_modernization", "customer_id": self._customer_id},
                trace_id=trace_id),
            call_sub_agent("poc_strategist",
                task=base_task,
                engagement_context={"angle": "performance_scale_ai", "customer_id": self._customer_id},
                trace_id=trace_id),
            call_sub_agent("poc_strategist",
                task=base_task,
                engagement_context={"angle": "cost_optimization_tco", "customer_id": self._customer_id},
                trace_id=trace_id),
            return_exceptions=True,
        )

        # Parse each result, skip any that errored
        options = []
        for r in results:
            if isinstance(r, Exception):
                continue
            if r.status == "ok":
                try:
                    options.append(json.loads(r.result))
                except (json.JSONDecodeError, AttributeError):
                    pass

        if not options:
            return ToolResult(status="error", summary="All 3 POC exploration angles failed.")

        # Rank by composite: relevance_score * (1 / max(executability_hours, 1))
        options.sort(key=lambda o: o.get("relevance_score", 0) / max(o.get("executability_hours", 8), 1), reverse=True)
        recommendation = options[0]

        payload = {
            "poc_options": options,
            "recommendation": {
                "poc_name": recommendation.get("option_name"),
                "rationale": f"Highest relevance ({recommendation.get('relevance_score')}/10) with {recommendation.get('executability_hours')}h build time.",
                "build_sequence": [],
                "success_criteria": recommendation.get("wow_moment", ""),
            }
        }

        # Save to document store
        key = f"poc_plan/{self._customer_id}/v1.json"
        await self._store.save_doc(key, json.dumps(payload, indent=2))

        return ToolResult(
            status="ok",
            summary=f"Generated 3 POC options. Recommended: {recommendation.get('option_name')}",
            artifact_key=key,
            data=payload,
        )
```

### 3. `agent/hats/oci_poc_strategist.md` — New hat

Follow the exact format of `oci_bom_expert.md`. Key values:

**YAML frontmatter:**
```yaml
version: "1.0"
display_name: "OCI POC Strategist"
memory_focus:
  priority_fields:
    - pain_statement
    - current_platform
    - deal_stage
    - timeline
    - budget_signal
    - customer_industry
    - competitive_context
  summary_style: "poc_strategy_oriented"
  include_full_memory: false
coordination:
  parallel_with: ["infra_tech_research"]
  suggested_next_hat: "diagram_for_oci"
```

**Pre-Action Checklist** must include:
- If `pain_statement` absent: emit `NEEDS_CLARIFICATION: What is the customer's primary pain?`
- If `current_platform` absent: emit `NEEDS_CLARIFICATION: What platform is the customer currently running on?`
- Default `deal_stage` to "discovery" if absent
- Default `timeline` to "flexible" if absent

**Output Contract:** JSON with `poc_options` (array of 3) and `recommendation` object.

**Quality Bar:**
- All 3 options must be present
- Each option must have all 7 fields scored
- Recommendation rationale must reference at least one specific customer input (pain, timeline, or budget)
- Option names must be specific (e.g., "Live Oracle DB migration to ADB-Dedicated" not "Database POC")

### 4. `agent/archie_wiring.py` — Register tool + update sequencing

Add import and register:
```python
from agent.tools.specialists import PocStrategistHandler

forge.register_tool(
    name="generate_poc_plan",
    description="Explores 3 parallel POC options across migration, performance/AI, and cost angles. Returns ranked options with effort and value scores, and a recommended POC with demo script.",
    handler=PocStrategistHandler(store, customer_id, customer_name),
    requires_hat="oci_poc_strategist",
    memory_contract=["pain_statement", "poc_recommendation", "poc_options"],
)
```

In `_TOOL_SEQUENCING_RULES`, add after the existing tool rules:
```
POC workflow: When the user needs to know what to build for a customer, call generate_poc_plan first.
After poc_plan is confirmed by the user, call generate_diagram + generate_bom + generate_jep +
generate_terraform + generate_presentation together (they will fan out in parallel).
```

---

## Constraints

- 3 parallel `asyncio.gather()` calls in `PocStrategistHandler` — NOT 1 call returning 3 options
- `NEEDS_CLARIFICATION:` must fire before any sub-agent call if required fields are absent
- Recommendation rationale must cite at least one specific customer input
- Hat file must follow `oci_bom_expert.md` format exactly (YAML frontmatter + 10 sections)
- Do NOT modify `skillforge/forge.py`

---

## Tests

Create `tests/test_poc_strategist.py`:

```python
async def test_three_parallel_calls_made():
    # Mock call_sub_agent, invoke PocStrategistHandler
    # Assert call_sub_agent called exactly 3 times with different angles

async def test_needs_clarification_when_pain_absent():
    # memory has no pain_statement
    # Assert ToolResult.status == "needs_input"

async def test_options_ranked_by_composite_score():
    # Provide 3 mock options with different relevance/effort scores
    # Assert recommendation is the one with highest relevance/effort ratio

async def test_failed_angle_skipped_gracefully():
    # One gather() call raises exception
    # Assert other 2 options still returned, no crash
```
