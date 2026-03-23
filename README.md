# OCI Drawing Agent

Generates OCI architecture diagrams in draw.io format from an Excel Bill of
Materials (BOM). Part of a 7-agent OCI fleet (Agent 3 of 7).

## Quick Start

```bash
pip install -r requirements.txt

# Start the server (requires OCI Instance Principal auth)
uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080

# Upload a BOM
curl -X POST http://localhost:8080/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "diagram_name=my_diagram" \
  -F "client_id=test1"
```

## Pipeline

```
BOM.xlsx → bom_parser → OCI GenAI (layout spec) → layout_engine → drawio_generator → .drawio
```

See [docs/pipeline.md](docs/pipeline.md) for the full reference.

## Project Structure

```
drawing_agent_server.py   FastAPI server (main entry point)
a2a_server.py             A2A protocol server
mcp_server.py             MCP stdio server
config.yaml               Configuration (endpoints, region)
Dockerfile                Container build
agent/
  bom_parser.py           Excel BOM → service list + LLM prompt
  layout_engine.py        Layout spec → x,y positions
  drawio_generator.py     Positions → draw.io XML
  oci_standards.py        OCI icon stencil data
  llm_client.py           OCI GenAI ADK wrapper (standalone)
  png_exporter.py         .drawio → PNG via draw.io CLI
tests/                    pytest test suite
docs/                     Documentation
```

## Auth

OCI Instance Principal only. No credentials in code or config. Run on OCI
Compute with an instance principal attached to the appropriate dynamic group.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/upload-bom` | Upload BOM Excel + optional context file |
| POST | `/clarify` | Submit answers to clarification questions |
| POST | `/generate` | Generate from pre-parsed resource list |
| POST | `/chat` | Free-form chat |
| GET | `/download/{file}` | Download generated diagram |
| GET | `/health` | Health check |
| GET | `/mcp/tools` | MCP tool manifest |
| GET | `/.well-known/agent-card.json` | A2A agent card |

## Testing

### Default (offline, deterministic)

```bash
pytest -q
```

All unit and integration tests run offline. The BOM fixture is generated at
runtime via openpyxl — no binary Excel file needs to be committed. The
v1.3.2 layout contract is enforced by `tests/test_layout_engine.py` and
`tests/test_llm_scenarios.py`.

### Live LLM tests (opt-in only)

> **Warning:** Live tests call the Anthropic Claude API and consume API
> credits. Never run them in CI without explicit intent.

```bash
RUN_LIVE_LLM_TESTS=1 ANTHROPIC_API_KEY=sk-ant-... pytest -m live -v -s
```

Live tests are tagged `@pytest.mark.live` and gated behind both
`RUN_LIVE_LLM_TESTS=1` **and** a valid `ANTHROPIC_API_KEY`. Default
`pytest -q` skips them automatically — the `anthropic` package is not
imported at all during offline runs.

## Requirements

- Python 3.11+
- `oci[adk]==2.165.1`
- draw.io CLI (optional, for PNG export — included in Dockerfile)
