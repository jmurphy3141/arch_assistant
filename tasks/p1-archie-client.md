# Task: Archie sub-agent client
Phase: 1
Status: todo
Depends on: all p1-sub-agent-*.md tasks complete

## Goal
Create `agent/sub_agent_client.py` — the single place in Archie's codebase
where sub-agents are called. Replace all direct in-process imports of sub-agent
modules in `orchestrator_agent.py` with calls through this client.

This is the final task of Phase 1. After this, no orchestrator code may import
`bom_service`, `pov_agent`, `jep_agent`, `waf_agent`, or `terraform_graph` directly.

## Files to create

### `agent/sub_agent_client.py`

```python
"""
sub_agent_client.py
--------------------
A2A HTTP client for Archie's sub-agents.

Archie calls sub-agents through this module only.
No other orchestrator file may import sub-agent modules directly.
"""
```

Public interface (these are the only functions orchestrator code may call):

```python
async def call_sub_agent(
    name: str,           # "diagram" | "bom" | "pov" | "jep" | "waf" | "terraform"
    task: str,           # prompt Archie constructed
    engagement_context: dict = {},
    trace_id: str = "",
) -> dict:
    """
    Calls the named sub-agent via A2A.
    Returns the A2AResponse as a dict: {"result": ..., "status": ..., "trace": ...}
    Raises SubAgentError on HTTP error or non-ok status.
    """

async def get_agent_card(name: str) -> dict:
    """
    Returns the agent card for the named sub-agent.
    Cards are cached after the first fetch.
    """
```

Implementation requirements:
1. Sub-agent base URLs come from `config.yaml` `sub_agents:` block.
   Add this block to `config.yaml` as part of this task:
   ```yaml
   sub_agents:
     diagram:   "http://localhost:8082"
     bom:       "http://localhost:8083"
     pov:       "http://localhost:8084"
     jep:       "http://localhost:8085"
     waf:       "http://localhost:8086"
     terraform: "http://localhost:8087"
   ```
2. Use `httpx.AsyncClient` for HTTP calls (it is already in requirements.txt).
3. Agent cards are fetched from `GET {base_url}/a2a/card` and cached in a
   module-level dict on first call.
4. `call_sub_agent` fetches the card first, validates that `task` is in the
   card's required inputs, then POSTs to `{base_url}/a2a`.
5. Define `class SubAgentError(Exception): pass` in this file.
   Raise it when: HTTP status != 200, or response `status` == "error".
6. When response `status` == "needs_input", do NOT raise — return the dict as-is
   so Archie can handle it conversationally.

## Files to modify

### `agent/orchestrator_agent.py`

Replace every direct in-process call to a sub-agent with a call through
`sub_agent_client.call_sub_agent()`.

Specifically, find and replace these patterns:

| Find | Replace with |
|------|-------------|
| `service = get_shared_bom_service()` + `service.chat(...)` | `call_sub_agent("bom", task, engagement_context, trace_id)` |
| `await generate_pov(...)` (from `pov_agent`) | `call_sub_agent("pov", task, engagement_context, trace_id)` |
| `await generate_jep(...)` (from `jep_agent`) | `call_sub_agent("jep", task, engagement_context, trace_id)` |
| `await generate_waf(...)` (from `waf_agent`) | `call_sub_agent("waf", task, engagement_context, trace_id)` |
| Terraform in-process call | `call_sub_agent("terraform", task, engagement_context, trace_id)` |

The diagram path already goes via A2A self-call — replace it with
`call_sub_agent("diagram", ...)` pointed at the diagram sub-agent URL.

Remove these imports from `orchestrator_agent.py` once their call sites are replaced:
```python
from agent.bom_service import CPU_SKU_TO_MEM_SKU, get_shared_bom_service, new_trace_id
from agent.pov_agent import generate_pov        # if directly imported
from agent.jep_agent import generate_jep        # if directly imported
from agent.waf_agent import generate_waf        # if directly imported
```

Keep these imports — they are used for non-generation purposes (lifecycle, persistence):
```python
import agent.document_store as document_store
import agent.context_store as context_store
from agent.jep_lifecycle import ...
```

## Files to NOT touch
- Any file in `sub_agents/`
- `drawing_agent_server.py` (it has its own direct routes that are separate from the orchestrator)
- Any test file outside of the acceptance test below

## Acceptance criteria
- `python3.11 -m compileall agent/sub_agent_client.py` exits 0
- `grep -n "get_shared_bom_service\|from agent.pov_agent\|from agent.jep_agent\|from agent.waf_agent" agent/orchestrator_agent.py` returns nothing
- Write `tests/test_sub_agent_client.py` with httpx mock tests:
  - `call_sub_agent` sends correct payload
  - `get_agent_card` caches after first call (second call makes no HTTP request)
  - `SubAgentError` raised on HTTP 500
  - `needs_input` response returned without raising
- `pytest tests/test_sub_agent_client.py -v` passes
- `pytest tests/ -v -m "not live"` passes (no regressions in existing tests)
