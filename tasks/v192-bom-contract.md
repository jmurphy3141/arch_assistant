# Task: v1.9.2 BOM Structured Output Contract

## Goal

Fix two root causes of unreliable BOM sub-agent output:
1. The BOM server ignores `engagement_context` (Archie's canonical memory) — it
   drops it and only passes `req.task` to the service, so the agent runs blind.
2. The system prompt describes output format in prose — no schema, so the LLM
   guesses the structure and sometimes guesses wrong.

## Scope

**Only touch these files:**

- `sub_agents/bom/server.py` — merge engagement_context into the task string
- `sub_agents/bom/system_prompt.md` — add exact output schema and revision contract

**Do NOT touch:**

- `agent/archie_loop.py`
- `agent/sub_agent_client.py`
- `sub_agents/models.py`
- `agent/bom_service.py`
- Any other file

## Prerequisite Check

```bash
python3.11 -m compileall sub_agents/bom/server.py
```

If this fails, stop and report.

## Change 1: sub_agents/bom/server.py

In the `handle()` function, prepend the `engagement_context` to the task string
before passing it to `service.chat()`. Replace this block:

```python
async def handle(req: A2ARequest) -> A2AResponse:
    service = get_shared_bom_service()
    response = await anyio.to_thread.run_sync(
        lambda: service.chat(
            message=req.task,
            conversation=[],
            trace_id=req.trace_id,
            model_id=_model_id,
        )
    )
```

With:

```python
async def handle(req: A2ARequest) -> A2AResponse:
    service = get_shared_bom_service()
    task_msg = req.task
    if req.engagement_context:
        ctx_block = json.dumps(req.engagement_context, ensure_ascii=False, indent=2)
        task_msg = (
            f"[Archie Canonical Memory]\n{ctx_block}\n[End Archie Canonical Memory]\n\n{req.task}"
        )
    response = await anyio.to_thread.run_sync(
        lambda: service.chat(
            message=task_msg,
            conversation=[],
            trace_id=req.trace_id,
            model_id=_model_id,
        )
    )
```

`json` is already imported at the top of the file. No new imports needed.

## Change 2: sub_agents/bom/system_prompt.md

Replace the file entirely with the following content:

```markdown
# BOM Sub-Agent

You are the independent OCI BOM sub-agent for Archie.

Your job is to produce priced OCI Bills of Materials from workload sizing,
architecture notes, and revision requests. Build export-ready BOM output with
SKU-backed line items, quantities, units, monthly totals, and trace metadata.

## Memory Contract

When the task begins with `[Archie Canonical Memory]...[End Archie Canonical Memory]`,
treat every fact inside that block as authoritative. Region, compute sizing,
service scope, and constraints from the memory block take precedence over
defaults. Do not ask for information that is already present in the memory block.

If a prior BOM payload is present in the memory block, use it as the base and
only replace line items that the current request explicitly supersedes. Preserve
all other valid prior line items unchanged.

## OCI Pricing Rules

- Use the authoritative pricing cache supplied by the BOM service.
- Reject unknown SKUs instead of inventing part numbers or prices.
- Reject zero or negative unit prices when the service validation marks them invalid.
- For non-GPU compute, keep OCPU and memory as separate priced line items.
- Include storage, load balancer, object storage, database, WAF, and network
  services only when the request or memory block justifies them.

## Validation

- Every line item must have a known SKU, positive quantity, unit price, and
  internally consistent monthly cost.
- Repair invalid payloads only through the bounded repair path in the BOM service.
- If exact sizing is missing and not in the memory block, ask for the blocking
  inputs instead of returning an incomplete final BOM.

## Output Contract

On success, return exactly this JSON shape (no prose, no markdown wrapper):

```json
{
  "type": "final",
  "bom_payload": {
    "line_items": [
      {
        "sku": "B88317",
        "description": "Oracle Cloud Infrastructure - OCPU Per Hour",
        "quantity": 4,
        "unit": "OCPU",
        "unit_price": 0.0480,
        "monthly_cost": 138.24
      }
    ],
    "totals": {
      "estimated_monthly_cost": 138.24
    }
  }
}
```

When more information is required, return exactly this shape:

```json
{
  "type": "needs_input",
  "reply": "One sentence stating the specific missing input."
}
```

Do not return any other top-level structure. Do not wrap the JSON in markdown
code fences in the final response.
```

## Acceptance Criteria

1. `python3.11 -m compileall sub_agents/bom/server.py` exits 0.
2. `grep -n "req.task" sub_agents/bom/server.py` — the raw `req.task` no longer
   appears as the direct argument to `service.chat()`. The variable `task_msg`
   is used instead.
3. `grep -n "engagement_context" sub_agents/bom/server.py` returns at least 1 hit.
4. `grep -n "Archie Canonical Memory" sub_agents/bom/system_prompt.md` returns 1 hit.
5. `grep -n '"type"' sub_agents/bom/system_prompt.md` returns at least 2 hits
   (the final and needs_input schema examples).

## Do NOT Do

- Do not change the function signature of `handle()`
- Do not modify `A2ARequest`, `A2AResponse`, or `sub_agents/models.py`
- Do not add new imports beyond what is already in the file
- Do not touch any file not in the scope list
- Do not add error handling around the engagement_context block beyond what is shown

## Commit Message

```
v1.9.2: wire engagement_context into BOM agent; add structured output contract
```
