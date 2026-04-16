# Agent 0 — OCI SA Orchestrator: Design & Implementation Spec

## Purpose

Agent 0 is a conversational orchestrator that accepts natural-language messages from
an Oracle SA, decides which sub-agents to invoke using a ReAct-style agentic loop,
and returns a unified reply. It is the primary entry point for the fleet and exposes
both a browser chat UI and a standard A2A v1.0 (Oracle Agent Spec 26.1.0) endpoint.

---

## System Context

| # | Agent | Status | Entry point |
|---|-------|--------|-------------|
| **0** | **SA Orchestrator** | **this spec** | `/message:send`, `/api/chat` |
| 1 | Requirements gathering | planned | — |
| 2 | BOM sizing + pricing | planned | — |
| 3 | Architecture diagram | live | `/upload-bom`, `/generate` |
| 4 | POV document | live | `/pov/generate` |
| 5 | JEP document | live | `/jep/generate` |
| 6 | Terraform generation | live | `/terraform/generate` |
| 7 | WAF review | live | `/waf/generate` |

---

## Protocol: Oracle Agent Spec v26.1.0 (A2A v1.0)

**Reference**: https://oracle.github.io/agent-spec/26.1.0/agentspec/index.html

### Request format

```json
{
  "jsonrpc": "2.0",
  "id":      "req-001",
  "method":  "message/send",
  "params": {
    "message": {
      "role":      "user",
      "parts":     [{"kind": "text", "text": "draft me a POV"}],
      "contextId": "acme001",
      "messageId": "msg-abc"
    },
    "skill": "orchestrate_engagement"
  }
}
```

`contextId` = `customer_id` throughout — this is how session state is correlated.

### Response format (Task object)

```json
{
  "jsonrpc": "2.0",
  "id":      "req-001",
  "result": {
    "id":        "task-xyz",
    "contextId": "acme001",
    "status":    "COMPLETED",
    "artifacts": [
      {"artifactId": "a1", "name": "reply",
       "parts": [{"kind": "text", "text": "Your POV is ready…"}]},
      {"artifactId": "artifact-generate_pov", "name": "generate_pov",
       "parts": [{"kind": "data", "mimeType": "application/json",
                  "data": {"key": "pov/acme001/v1.md"}}]}
    ]
  }
}
```

**Task lifecycle**: `SUBMITTED` → `WORKING` → `COMPLETED` / `FAILED`

---

## Agent Card (schemaVersion 1.0)

Served at `/.well-known/agent.json` and `/.well-known/agent-card.json`:

```json
{
  "schemaVersion":   "1.0",
  "humanReadableId": "oracle-oci-fleet/agent0-sa-orchestrator",
  "name":            "OCI SA Orchestrator + Drawing Agent",
  "agentVersion":    "1.3.2",
  "url":             "https://<host>",
  "provider":        {"name": "Oracle"},
  "capabilities":    {"streaming": false, "pushNotifications": false},
  "authSchemes":     [{"type": "none"}],
  "skills": [
    {
      "id":          "orchestrate_engagement",
      "name":        "Orchestrate SA Engagement",
      "description": "Accept a natural-language SA message and orchestrate notes intake, POV, diagram, WAF, or JEP generation.",
      "inputModes":  ["text/plain"],
      "outputModes": ["text/plain", "application/json"]
    },
    {
      "id":          "generate_diagram",
      "name":        "Generate Architecture Diagram",
      "description": "Generate OCI draw.io diagram from BOM or resource list.",
      "inputModes":  ["text/plain", "application/json"],
      "outputModes": ["application/json"]
    }
  ]
}
```

The legacy v0.1 card is kept at `/.well-known/agent-card-legacy.json` for backward compatibility.

---

## Architecture

```
SA (browser chat tab or Telegram — future)
    │
    ├─── POST /api/chat          ← REST convenience (UI uses this)
    │
    └─── POST /message:send      ← A2A v1.0 JSON-RPC
              │  contextId = customer_id
              ▼
    orchestrator_agent.run_turn()      ← agent/orchestrator_agent.py
              │
              ├── load history         ← conversations/{customer_id}/history.json
              ├── build ReAct prompt   ← system msg + tool list + last 30 turns
              │
              └── Agentic loop (max 5 iterations):
                      │
                      ▼
                  OCI GenAI text_runner (ORCHESTRATOR_SYSTEM_MSG)
                      │
                      ├── Plain text → done; return Task{COMPLETED}
                      │
                      └── {"tool": "...", "args": {...}} detected:
                              ├── save_notes       → document_store.save_note()
                              ├── get_summary      → context_store.build_context_summary()
                              ├── generate_pov     → pov_agent.generate_pov() [in-process]
                              ├── generate_diagram → POST /message:send (A2A self-call via httpx)
                              ├── generate_waf     → waf_agent.generate_waf() [in-process]
                              ├── generate_jep     → jep_agent.generate_jep() [in-process]
                              └── get_document     → document_store.get_latest_doc()
                      │
                      ▼
              Persist history → return {reply, tool_calls, artifacts}
```

`generate_diagram` uses an A2A self-call (httpx `POST /message:send` on localhost) to
avoid circular imports between `orchestrator_agent.py` and `drawing_agent_server.py`.
Writing agents (POV, WAF, JEP) are called in-process via `asyncio.to_thread()`.

---

## ReAct Tool-Calling Pattern

OCI GenAI has no native function-calling. The orchestrator uses a prompt-based pattern:

**System message instructs the LLM:**
> "When you need to take an action, output ONLY the following JSON on a single line — no other text:
> `{"tool": "<name>", "args": {<key>: <value>}}`
> Otherwise respond in Markdown."

**Parse loop in `run_turn()`:**
1. Build prompt: rolling summary + last 30 history turns + user message
2. `text_runner(prompt, ORCHESTRATOR_SYSTEM_MSG)` → raw LLM text
3. Scan for `{"tool":` JSON via regex → execute → append result → loop (max 5)
4. No tool call found → return plain text reply

---

## Available Tools

| Tool | Args | Action |
|------|------|--------|
| `save_notes` | `{"text": "..."}` | Save meeting notes to object storage |
| `get_summary` | `{}` | Return current engagement state (context_store) |
| `generate_pov` | `{"feedback": "..."}` | Draft or update the POV document |
| `generate_diagram` | `{"bom_text": "..."}` | Generate architecture diagram via A2A self-call |
| `generate_waf` | `{}` | Run WAF review against the latest diagram |
| `generate_jep` | `{"feedback": "..."}` | Draft or update the JEP |
| `get_document` | `{"type": "pov"\|"jep"\|"waf"}` | Retrieve latest document content |

---

## Conversation Storage

```
conversations/{customer_id}/history.json   ← append-only turn list
conversations/{customer_id}/summary.txt    ← rolling LLM summary for turns > 30
```

Turn format:
```json
{"role": "user",      "content": "draft a POV",      "timestamp": "…"}
{"role": "assistant", "content": "…",                "timestamp": "…",
 "tool_call": {"tool": "generate_pov", "args": {}}}
{"role": "tool",      "tool": "generate_pov",
 "result_summary": "POV v1 saved. Key: pov/acme/v1.md", "timestamp": "…"}
```

---

## API Endpoints

### A2A v1.0 (Oracle Agent Spec)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/message:send` | A2A v1.0 JSON-RPC entry point |
| `GET` | `/tasks/{task_id}` | Poll task status |
| `POST` | `/tasks/{task_id}:cancel` | Cancel a pending/working task |

### Convenience REST (UI)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Send a message; returns `{reply, tool_calls, artifacts}` |
| `GET` | `/api/chat/{customer_id}/history` | Return conversation history |
| `DELETE` | `/api/chat/{customer_id}/history` | Clear conversation history |

### Agent card

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/.well-known/agent.json` | Oracle Agent Spec v26.1.0 card (schemaVersion 1.0) |
| `GET` | `/.well-known/agent-card.json` | Same as above (alias) |
| `GET` | `/.well-known/agent-card-legacy.json` | Legacy schema_version 0.1 card |

---

## Key Files

| File | Role |
|------|------|
| `agent/orchestrator_agent.py` | ReAct loop, tool dispatch, A2A self-call for diagram |
| `agent/document_store.py` | Conversation history CRUD + versioned doc storage |
| `agent/notifications.py` | Event notification stub (Telegram-ready) |
| `drawing_agent_server.py` | FastAPI server: `/message:send`, `/api/chat`, updated agent card |
| `config.yaml` `orchestrator:` block | `max_tool_iterations`, `history_max_turns`, Telegram stub |
| `ui/src/components/ChatInterface.tsx` | Chat UI component |
| `ui/src/api/client.ts` | `apiChat`, `apiGetChatHistory`, `apiClearChatHistory` |

---

## Verification Checklist

1. `GET /.well-known/agent.json` → `schemaVersion: "1.0"`, `orchestrate_engagement` skill present
2. `GET /.well-known/agent-card-legacy.json` → `schema_version: "0.1"` format still served
3. `POST /message:send` with notes, `contextId: "acme001"` → Task `COMPLETED`, `tool_calls=[save_notes]`
4. Same `contextId`, message `"draft POV"` → artifacts include `generate_pov` key
5. `GET /api/chat/acme001/history` → turns persisted across server restart
6. `POST /api/a2a/task` (old format) → still works unchanged
7. Chat UI: message → spinner → assistant bubble with tool chip
8. `DELETE /api/chat/acme001/history` → history cleared, next message starts fresh

---

## Telegram Integration (future)

`agent/notifications.py` currently logs events. To enable Telegram:

1. Set `config.yaml` → `orchestrator.telegram.enabled: true` and `bot_token: "<token>"`
   (or env var `TELEGRAM_BOT_TOKEN`)
2. Replace the `logger.info(…)` call in `notifications._send()` with
   `bot.send_message(chat_id=CHAT_ID, text=…)` using the `python-telegram-bot` library.
   No other code changes required.

---

## Config Block

```yaml
orchestrator:
  agent_id: "agent0-sa-orchestrator"
  max_tool_iterations: 5
  history_max_turns: 30
  telegram:
    enabled: false
    bot_token: ""    # env: TELEGRAM_BOT_TOKEN
```
