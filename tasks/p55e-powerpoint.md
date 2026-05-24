# Codex Agent Prompt — Issue 4: PowerPoint Presentation Generation

**Repository:** jmurphy3141/arch_assistant
**Branch:** claude/explore-repo-Os53i
**Requirements:** docs/requirements-poc-workflow.md FR-4.*
**Can be worked in parallel with:** Issue 1

---

## Task

Build a PowerPoint presentation generation capability for Archie using Oracle's official OCI icon stencils and `python-pptx`.

---

## Context

### No PPTX capability exists today

Confirmed: no `python-pptx` in `requirements.txt`, no `sub_agents/presentation/`, no `generate_presentation` tool, no presentation hat.

### Reference implementation

`https://github.com/aruanurag/oci-architecture-codex-skill` — Oracle's reference. Key pattern:
- `oracle-oci-architecture-toolkit-v24.1.pptx` is a master stencil file containing OCI service icons as native PowerPoint vector shape groups
- `python-pptx` opens the toolkit PPTX, finds shapes by name, copies them into the output PPTX
- This is the same pattern as how Archie uses `OCI_Library.xml` for draw.io icons — do not build shapes from scratch

### Existing patterns to follow

**Sub-agent:** `sub_agents/pov/` — copy exactly. Same `server.py`, `system_prompt.md`, `config.yaml`, `__init__.py` structure.

**Handler:** `_SpecialistHandler` in `agent/tools/specialists.py` (lines 45–230).

**Hat format:** `agent/hats/oci_bom_expert.md` — YAML frontmatter + 10 Markdown sections.

**Tool registration:** `agent/archie_wiring.py` `build_forge()`.

**Download endpoint:** `drawing_agent_server.py` — search for `Content-Type` in the `/download` handler; add a branch for `.pptx` extension.

**Document store:** `document_store.save_doc(key, content)` — content can be bytes.

---

## What to Build

### 1. `requirements.txt` — Add `python-pptx`

```
python-pptx>=1.0.2
```

### 2. `sub_agents/presentation/assets/` — Oracle toolkit PPTX

Download `oracle-oci-architecture-toolkit-v24.1.pptx` from Oracle's official resources or the reference repo and commit to `sub_agents/presentation/assets/oracle-oci-architecture-toolkit-v24.1.pptx`.

If the toolkit is not available, create a minimal placeholder PPTX with a comment slide explaining where to source it, so the code path works without icons.

### 3. `sub_agents/presentation/scripts/resolve_oci_powerpoint_icon.py`

Maps OCI service display names to shape names/indices in the Oracle toolkit PPTX:

```python
# Maps service name → shape name in oracle-oci-architecture-toolkit-v24.1.pptx
OCI_ICON_MAP = {
    "Autonomous Database": "OCI_Autonomous_Database",
    "OCI Compute": "OCI_Compute_Instance",
    "Virtual Cloud Network": "OCI_VCN",
    "VCN": "OCI_VCN",
    "OKE": "OCI_Container_Engine",
    "Object Storage": "OCI_Object_Storage",
    "Load Balancer": "OCI_Load_Balancer",
    # ... extend as needed
}

def resolve_icon(service_name: str) -> str | None:
    """Returns the shape name in the toolkit PPTX, or None if not found."""
    return OCI_ICON_MAP.get(service_name) or OCI_ICON_MAP.get(service_name.strip())
```

### 4. `sub_agents/presentation/scripts/render_oci_powerpoint.py`

Renders a 7-slide PPTX from a JSON spec:

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

TOOLKIT_PATH = Path(__file__).parent.parent / "assets" / "oracle-oci-architecture-toolkit-v24.1.pptx"

def render(spec: dict, output_path: str) -> None:
    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    # Slide 1: Title
    _add_title_slide(prs, spec)
    # Slide 2: Customer Challenge
    _add_challenge_slide(prs, spec)
    # Slide 3: OCI Architecture (icons from toolkit)
    _add_architecture_slide(prs, spec)
    # Slide 4: Key OCI Services
    _add_services_slide(prs, spec)
    # Slide 5: Cost Estimate
    _add_bom_slide(prs, spec)
    # Slide 6: Implementation Timeline
    _add_timeline_slide(prs, spec)
    # Slide 7: Next Steps
    _add_next_steps_slide(prs, spec)

    prs.save(output_path)
```

**Icon copying pattern** (for the architecture slide):
```python
def _copy_icon_from_toolkit(toolkit_pptx_path: str, shape_name: str, target_slide, left, top, width, height):
    toolkit = Presentation(toolkit_pptx_path)
    for slide in toolkit.slides:
        for shape in slide.shapes:
            if shape.name == shape_name:
                # Copy shape XML element to target slide
                from copy import deepcopy
                sp = deepcopy(shape._element)
                target_slide.shapes._spTree.append(sp)
                # Reposition
                shape_obj = target_slide.shapes[-1]
                shape_obj.left = left
                shape_obj.top = top
                shape_obj.width = width
                shape_obj.height = height
                return True
    return False
```

### 5. `sub_agents/presentation/server.py`

Copy `sub_agents/pov/server.py`. Changes:
- `agent_name = "presentation"`
- AgentCard: `inputs` = `["task", "poc_name", "customer_name", "oci_services", "bom_summary", "jep_phases"]`
- `required` = `["task", "customer_name"]`
- `handle()`: parse inputs from request, build spec dict, call `render_oci_powerpoint.render(spec, tmp_path)`, read bytes, encode base64, return in result

```python
async def handle(request: A2ARequest) -> A2AResponse:
    ec = request.engagement_context or {}
    spec = {
        "poc_name": ec.get("poc_name", "OCI POC"),
        "customer_name": ec.get("customer_name", "Customer"),
        "pain_statement": ec.get("pain_statement", ""),
        "oci_services": ec.get("oci_services", []),
        "bom_summary": ec.get("bom_summary", ""),
        "jep_phases": ec.get("jep_phases", []),
        "date": datetime.today().strftime("%B %d, %Y"),
    }
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        output_path = f.name
    render_oci_powerpoint.render(spec, output_path)
    with open(output_path, "rb") as f:
        pptx_bytes = f.read()
    os.unlink(output_path)
    return A2AResponse(result=base64.b64encode(pptx_bytes).decode(), status="ok")
```

### 6. `sub_agents/presentation/system_prompt.md`

```markdown
You are an Oracle OCI PowerPoint architect. Your job is to generate a structured JSON specification for a 7-slide client-facing POC deck, then confirm the spec is complete.

The spec must include:
- All 7 slide types: title, challenge, architecture, services, cost, timeline, next_steps
- For the architecture slide: a list of oci_services with their canonical Oracle icon names
- For the cost slide: bom_rows as a list of {service, monthly_cost} dicts
- For the timeline slide: jep_phases as ordered list of phase names

Always use official Oracle OCI service names that match the icon library.
Output: JSON spec only, no markdown.
```

### 7. `sub_agents/presentation/config.yaml`

```yaml
name: presentation
port: 8088
llm:
  model_id: null
  max_tokens: 1024
  temperature: 0.2
```

### 8. `agent/tools/presentation.py` — `PresentationHandler`

```python
import base64
from agent import sub_agent_client
from skillforge.types import ToolResult

class PresentationHandler:
    def __init__(self, store, customer_id, customer_name):
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
        dc = memory.decision_context if memory else {}
        poc_option = args.get("poc_option") or dc.get("poc_recommendation", {})

        task_payload = {
            "poc_name": poc_option.get("option_name", "OCI POC"),
            "customer_name": self._customer_name,
            "pain_statement": dc.get("pain_statement", ""),
            "oci_services": poc_option.get("oci_services", []),
            "bom_summary": dc.get("bom_summary", ""),
            "jep_phases": dc.get("jep_phases", []),
        }

        response = await sub_agent_client.call_sub_agent(
            "presentation",
            task=f"Generate POC deck for {self._customer_name}",
            engagement_context=task_payload,
            trace_id=trace_id,
        )

        if response.status != "ok":
            return ToolResult(status="error", summary=f"Presentation generation failed: {response.result}")

        # Decode base64 PPTX bytes and save
        pptx_bytes = base64.b64decode(response.result)
        key = f"presentation/{self._customer_id}/v1.pptx"
        await self._store.save_doc(key, pptx_bytes)

        return ToolResult(
            status="ok",
            summary=f"PowerPoint deck generated: {task_payload['poc_name']}",
            artifact_key=key,
        )
```

### 9. `agent/hats/oci_presentation_writer.md`

Lightweight hat (~80 lines). Key sections:

**YAML frontmatter:**
```yaml
version: "1.0"
display_name: "OCI Presentation Writer"
memory_focus:
  priority_fields: [poc_recommendation, customer_name, bom_summary, jep_phases, pain_statement]
  summary_style: "presentation_oriented"
coordination:
  parallel_with: ["generate_diagram", "generate_bom", "generate_jep", "generate_terraform"]
  suggested_next_hat: null
```

**Pre-Action Checklist:**
- Verify `poc_recommendation` in memory — if absent, emit `NEEDS_CLARIFICATION: No POC has been planned yet. Run generate_poc_plan first.`
- Verify `customer_name` in context — if absent, emit `NEEDS_CLARIFICATION: What is the customer's name?`

**Quality Bar:**
- All 7 slides present in output
- Customer name on title slide
- OCI service names are official Oracle names (not generic: "compute" → "OCI Compute")
- File opens without errors

### 10. `agent/archie_wiring.py` — Register tool

```python
from agent.tools.presentation import PresentationHandler

forge.register_tool(
    name="generate_presentation",
    description="Generates a 7-slide client-facing Oracle-standard PowerPoint POC deck with OCI icon stencils.",
    handler=PresentationHandler(store, customer_id, customer_name),
    requires_hat="oci_presentation_writer",
)
```

### 11. `drawing_agent_server.py` — PPTX Content-Type in `/download`

Find the `/download` endpoint and add a branch:
```python
if artifact_key.endswith(".pptx"):
    media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    filename = artifact_key.split("/")[-1]
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

---

## Constraints

- Use Oracle toolkit PPTX for icon shapes — not generic python-pptx shapes or clip art
- Oracle toolkit PPTX committed to repo at `sub_agents/presentation/assets/` — not downloaded at runtime
- Save artifact with key pattern `presentation/{customer_id}/v{n}.pptx`
- PPTX bytes stored directly in document_store (not base64 encoded at rest)
- Sub-agent returns base64 over A2A (JSON-safe); handler decodes before saving

---

## Tests

Create `tests/test_presentation.py`:

```python
def test_pptx_artifact_key_format():
    # Invoke PresentationHandler with mock sub-agent
    # Assert artifact_key == "presentation/test-customer/v1.pptx"

async def test_download_endpoint_pptx_content_type(client):
    # Store a fake .pptx artifact, GET /download?key=presentation/test/v1.pptx
    # Assert Content-Type header == application/vnd.openxmlformats-officedocument.presentationml.presentation

async def test_needs_clarification_when_no_poc_recommendation(memory_no_poc):
    # memory has no poc_recommendation
    # Assert ToolResult.status == "needs_input"

def test_render_creates_seven_slides():
    # Call render_oci_powerpoint.render(spec, tmp_path)
    # Open with python-pptx, assert len(prs.slides) == 7
```
