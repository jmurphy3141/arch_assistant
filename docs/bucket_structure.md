# OCI Object Storage — Bucket Structure

**Bucket**: `agent_assistante`
**Namespace**: `oraclejamescalise`
**Region**: `us-chicago-1`

All agents in the fleet share this single bucket. Paths are prefixed by
agent or document type to keep namespaces isolated.

---

## Layout

```
agent_assistante/
│
├── notes/{customer_id}/
│   ├── {note_name}             ← individual meeting note (text/markdown)
│   └── MANIFEST.json           ← list of all notes with timestamps
│
├── pov/{customer_id}/
│   ├── v1.md                   ← first POV version
│   ├── v2.md                   ← second POV version (after new notes added)
│   ├── ...
│   ├── LATEST.md               ← content of the most recent version
│   └── MANIFEST.json           ← version history with timestamps and metadata
│
├── jep/{customer_id}/
│   ├── v1.md                   ← first JEP version
│   ├── v2.md
│   ├── ...
│   ├── LATEST.md
│   └── MANIFEST.json
│
└── agent3/{client_id}/{diagram_name}/
    ├── {request_id}/
    │   ├── diagram.drawio
    │   ├── spec.json
    │   ├── draw_dict.json
    │   ├── render_manifest.json
    │   └── node_to_resource_map.json
    └── LATEST.json             ← atomic pointer to most recent successful run
```

---

## Key Conventions

### Customer ID vs Client ID

| Identifier   | Used by          | Meaning                                              |
|--------------|------------------|------------------------------------------------------|
| `customer_id`| POV, JEP, Notes  | Customer name slug (`jane_street`, `acme_corp`)      |
| `client_id`  | Agent 3 diagrams | UI session identifier (UUID generated in browser)    |

When the JEP agent looks for the latest architecture diagram, it searches:
`agent3/{customer_id}/LATEST.json` — so the customer_id should match
the diagram's client_id when they are generated together.

### MANIFEST.json Schema

**Notes manifest** (`notes/{customer_id}/MANIFEST.json`):
```json
{
  "notes": [
    {
      "key": "notes/jane_street/kickoff.md",
      "name": "kickoff.md",
      "timestamp": "2025-03-27T14:00:00Z"
    }
  ]
}
```

**Document manifest** (`pov/{customer_id}/MANIFEST.json`):
```json
{
  "versions": [
    {
      "version": 1,
      "key": "pov/jane_street/v1.md",
      "timestamp": "2025-03-27T14:05:00Z",
      "metadata": { "customer_name": "Jane Street Capital" }
    }
  ]
}
```

**LATEST.json for diagrams** (`agent3/{client_id}/{diagram_name}/LATEST.json`):
```json
{
  "schema_version": "1.0",
  "request_id": "uuid-...",
  "artifacts": {
    "diagram.drawio": "agent3/.../diagram.drawio",
    "spec.json": "agent3/.../spec.json",
    "draw_dict.json": "agent3/.../draw_dict.json",
    "render_manifest.json": "agent3/.../render_manifest.json",
    "node_to_resource_map.json": "agent3/.../node_to_resource_map.json"
  }
}
```

---

## Atomicity Guarantees

- **Versioned copy first**: `v{n}.md` is written before `LATEST.md` and `MANIFEST.json`.
  If the write fails mid-way, `LATEST.md` is not updated — the previous version
  remains the latest.
- **Agent 3 diagrams**: All artifact files are uploaded before `LATEST.json` is written.
  A partial run that fails mid-upload does not update `LATEST.json`.

---

## Agent Access Pattern

| Agent           | Reads                                     | Writes                          |
|-----------------|-------------------------------------------|---------------------------------|
| Notes (UI)      | —                                         | `notes/{customer_id}/`          |
| POV (Agent 4)   | `notes/{customer_id}/`, `pov/.../LATEST`  | `pov/{customer_id}/`            |
| JEP (Agent 5)   | `notes/`, `jep/.../LATEST`, `agent3/.../LATEST.json` | `jep/{customer_id}/` |
| Drawing (Agent 3) | —                                       | `agent3/{client_id}/`           |
| BOM (Agent 2)   | advisory context, optional prior BOM data | `bom/{customer_id}/`            |

---

## Adding a New Agent

1. Choose a prefix: `{agent_slug}/{customer_id}/`
2. Follow the `LATEST.md` + `MANIFEST.json` pattern from `agent/document_store.py`
3. Update this document
4. Update the fleet position in `config.yaml`
