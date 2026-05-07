# Task: A2A sub-agent base
Phase: 1
Status: todo
Depends on: p0-fix-config.md

## Goal
Create the shared base that every sub-agent builds on: request/response models,
the agent card schema, and a minimal FastAPI app factory. All six sub-agent tasks
depend on this.

## Files to create

### `sub_agents/__init__.py`
Empty.

### `sub_agents/models.py`
Pydantic models shared across all sub-agents.

```python
from pydantic import BaseModel
from typing import Any

class A2ARequest(BaseModel):
    task: str                              # prompt Archie constructed
    engagement_context: dict[str, Any] = {}  # only fields the card declared
    trace_id: str = ""

class A2AResponse(BaseModel):
    result: str
    status: str   # "ok" | "needs_input" | "error"
    trace: dict[str, Any] = {}

class AgentCard(BaseModel):
    name: str
    description: str
    inputs: dict[str, list[str]]  # {"required": [...], "optional": [...]}
    output: str
    llm_model_id: str
```

### `sub_agents/base.py`
Factory function that creates a FastAPI app wired with the two required endpoints.

```python
def make_agent_app(card: AgentCard, handler) -> FastAPI:
    """
    Returns a FastAPI app with:
      GET  /a2a/card  → returns card as JSON
      POST /a2a       → calls handler(req: A2ARequest) → A2AResponse
      GET  /health    → {"status": "ok", "agent": card.name}
    """
```

The handler signature is:
```python
async def handle(req: A2ARequest) -> A2AResponse: ...
```

## Files to NOT touch
- `agent/` — any file in the existing agent directory
- `drawing_agent_server.py`
- Any existing test file
- `PLAN.md`, `AGENTS.md`, `CLAUDE.md`

## What to do

1. Create `sub_agents/__init__.py` (empty).
2. Create `sub_agents/models.py` with the three Pydantic models above.
   Use `pydantic` v2 style (`model_config`, `model_fields`) matching the
   existing codebase (check `requirements.txt` for the pydantic version).
3. Create `sub_agents/base.py` with `make_agent_app()`.
   The factory attaches three routes to the app:
   - `GET /a2a/card` returns `card.model_dump()`
   - `POST /a2a` calls `await handler(req)` and returns the result
   - `GET /health` returns `{"status": "ok", "agent": card.name}`
4. Create `sub_agents/requirements.txt` listing only what sub-agents need
   beyond the main `requirements.txt`:
   ```
   fastapi
   uvicorn[standard]
   pydantic
   ```

## Acceptance criteria
- `python3.11 -m compileall sub_agents/` exits 0
- Write a one-file smoke test at `sub_agents/test_base_smoke.py`:
  ```python
  from sub_agents.models import A2ARequest, A2AResponse, AgentCard
  from sub_agents.base import make_agent_app
  from fastapi.testclient import TestClient

  card = AgentCard(name="test", description="test agent",
                   inputs={"required": ["task"], "optional": []},
                   output="text", llm_model_id="mock")

  async def handler(req):
      return A2AResponse(result="ok", status="ok")

  app = make_agent_app(card, handler)
  client = TestClient(app)

  def test_card():
      r = client.get("/a2a/card")
      assert r.status_code == 200
      assert r.json()["name"] == "test"

  def test_a2a():
      r = client.post("/a2a", json={"task": "hello"})
      assert r.status_code == 200
      assert r.json()["status"] == "ok"

  def test_health():
      r = client.get("/health")
      assert r.status_code == 200
  ```
- `pytest sub_agents/test_base_smoke.py -v` passes
