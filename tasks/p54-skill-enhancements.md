# p54 — Skill Enhancement: BOM Gate, Diagram Quality, PowerPoint, XLSX

## 1:1 Comparison Table

| Dimension | External Toolkit | Our System | Gap |
|-----------|-----------------|------------|-----|
| **BOM confirmation gate** | User reviews and confirms sizing assumptions BEFORE any pricing call. Prevents pricing unvalidated defaults. | Pre-Action Checklist runs, then immediately calls sub-agent. No user confirmation step. | Medium — we price first, user corrects after |
| **BOM source priority** | Architecture JSON artifacts take priority over diagrams, which take priority over prompts. Explicit source hierarchy. | All paths feed the same `generate_bom` prompt. No source ranking. | Low — our memory contract partially compensates |
| **XLSX package integrity** | Preserves formulas, freeze panes, filters, named ranges, charts. Validates workbook structure post-write. Separate openpyxl vs pandas paths. | Generates XLSX; no post-write integrity validation in the hat or sub-agent prompt. | Medium — corrupt/incomplete XLSX silently delivered |
| **Diagram clarification priorities** | Ranked 1–5: topology gaps first, network gaps, service gaps, visual-baseline gaps, layout gaps. Ask 1–4 questions max. | Pre-Action Checklist asks "are the following known?" but no ranked priority triage. | Medium — wastes sub-agent calls on wrong questions |
| **Diagram physical vs logical** | Defaults to physical only; logical view added on explicit request. | No physical/logical distinction — always generates physical but doesn't say so. | Low — implicit, not a workflow problem today |
| **Diagram repair passes** | Mandatory ≥3 repair passes after first render; ≥2 consecutive clean passes before sign-off. | `critique_enabled=True` triggers critic loop but no minimum iteration count. | Medium — first-pass artifacts accepted too readily |
| **Visual preview audit** | Export to PNG, run visual inspection as a required quality gate before delivery. | `png_exporter.py` exists but is NOT in the diagram hat's quality gate chain. | High — no visual validation of rendered output |
| **Reference replication** | Dedicated mode: produce Reference Summary + Recreation Prompt, compare with similarity scoring (≥95%), iterate up to 10 times. | `external_corpus_scorer.py` exists but is not invoked via the hat. | Medium — reference matching is manual today |
| **Icon resolution fallback** | 5-step strict hierarchy: direct OCI icon → alias → approved fallback → generic → placeholder. Explicit disclosure at each step. | Hat says "OCI standard icons only." No fallback chain. Silently uses generic boxes. | Medium — unknown services degrade without disclosure |
| **PowerPoint generation** | 4 dedicated skills: architecture PPT, diagram patterns, sales decks, technical decks. Design director as mandatory review gate. | **Zero PowerPoint capability.** | **Critical gap — AEs need decks** |
| **Design director gate** | `oci-ppt-design-director` is a mandatory review step for all customer-facing decks. | No equivalent visual quality gate for POV/JEP documents. | High — POV/JEP delivered without presentation review |
| **Opportunity coach** | Early-stage sales enablement: account briefs, discovery plans, stakeholder maps before any technical artifacts. | `infra_tech_research` hat covers the technical research side but not the sales framing. | Medium — we're missing the presales narrative layer |
| **Sibling skill routing** | Skills explicitly route to each other (architecture-generator → PPT-generator → design-director). | Hats coordinate via `recommended_hats` but no concept of complementary artifact pipelines. | Low — coordination works differently but achieves similar result |

---

## 2. Prioritized Enhancement Plan

### P1 — Highest impact, implement first

**A. PowerPoint sales deck generation** (`generate_sales_deck`)
No PowerPoint capability is the single largest gap. AEs need a deck after the
POV is approved. Build a `sales_deck` sub-agent (python-pptx, Oracle red theme)
and `oci_sales_deck` hat. The deck draws from POV, BOM, and diagram artifacts.

**B. BOM assumption confirmation gate**
Before the BOM sub-agent is called, surface the sizing table to the user for
explicit confirmation. Prevents a full pricing run on wrong defaults. Implement
as a `needs_input` early-return in BomHandler when assumptions have never been
confirmed, plus a hat section update.

### P2 — Quality improvements

**C. Diagram clarification priority ranking**
Add a ranked priority system to the diagram hat's Pre-Action Checklist
(topology → network → service → visual). The hat should derive its single
"focused question" from the highest-ranked gap, not ask randomly.

**D. XLSX package integrity validation**
Add post-write validation to the BOM sub-agent's output contract and hat
Post-Action Review: freeze panes present, formula cells calculated,
monthly_total formula matches sum of line items, no empty SKU cells.

### P3 — Enhancement, lower urgency

**E. Visual preview gate for diagrams** — invoke `png_exporter.py` after
diagram generation and surface render failures to the user.

**F. Reference replication mode** — expose `external_corpus_scorer.py` through
a diagram hat trigger ("match this Oracle reference architecture").

---

## 3. Key Pattern: PowerPoint Architecture

The external toolkit pattern we should follow for PowerPoint:
```
User asks for deck
  → oci_sales_deck hat activates
  → Pre-Action: hydrate from POV artifact + BOM artifact + diagram artifact_key
  → generate_sales_deck tool called
  → sales_deck sub-agent builds structured slide JSON
  → pptx_builder.py renders .pptx from JSON
  → artifact saved to object storage
  → Post-Action: verify slide count, no placeholder text, design review pass
```

Slide structure for a standard OCI solution deck (8-12 slides):
1. Title + customer name
2. Customer situation / challenge  
3. OCI solution overview (architecture summary)
4. Architecture diagram (embed diagram artifact)
5. Bill of Materials summary (top-line BOM numbers)
6. Migration / implementation approach
7. Success criteria and POC outline
8. Why OCI / competitive positioning
9. Next steps

---

## Task p54a — BOM assumption confirmation gate

```
Context: The OCI BOM Expert hat calls generate_bom immediately after the
Pre-Action Checklist runs. The external OCI Codex toolkit requires users to
review and confirm sizing assumptions BEFORE any pricing call. We need to add
this confirmation gate to prevent pricing unvalidated defaults.

IMPORTANT: Branch from origin/main (p53 is already merged).

  git fetch origin
  git checkout -b claude/p54a origin/main

---

CHANGE 1: agent/hats/oci_bom_expert.md

In the Pre-Action Checklist section, replace the current `[SUB-AGENT INSTRUCTIONS]`
block ending with:

> End your pre-action output with a concrete sizing table in this exact format
> so the BOM sub-agent (a deterministic regex pipeline) can extract the numbers:

Replace the closing instruction with:

```
End your pre-action output with a sizing confirmation table for the user. Use
exactly this format so the user can approve or correct before you call the
sub-agent:

[ASSUMPTION REVIEW — Please confirm or correct]
Region: us-chicago-1
Compute shape: E5.Flex
Server count: 1
OCPU per server: 4
Total OCPU: 4
Memory per server GB: 32
Total memory GB: 32
Block Volume GB: 500
Block Volume tier: Balanced
HA mode: single-AD
Monthly hours: 730
[/ASSUMPTION REVIEW]

After presenting this table, wait for user confirmation before calling
generate_bom. If the user says "confirmed", "looks good", "yes", "proceed",
or similar, call generate_bom with the confirmed values. If the user corrects
any value, update it and call generate_bom with the corrected values.

Exception: If the user's original message already contained explicit sizing
numbers ("4 OCPUs", "8 servers", "500 GB"), these are user-confirmed values —
skip the confirmation gate and call generate_bom directly.
```

CHANGE 2: agent/hats/oci_bom_expert.md — Post-Action Review

After the existing mandatory checks, add:

```
XLSX quality checks:
- Freeze panes applied to header row (row 1)
- Monthly Total row uses a SUM formula, not a hardcoded value
- No cells with empty SKU but non-zero unit_price
- Assumptions sheet or section is present in the workbook
```

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/archie_wiring.py

  python3.11 -c "
  from pathlib import Path
  hat = Path('agent/hats/oci_bom_expert.md').read_text()
  assert 'ASSUMPTION REVIEW' in hat, 'FAIL: confirmation gate not found'
  assert 'wait for user confirmation' in hat, 'FAIL: wait instruction missing'
  assert 'Exception:' in hat, 'FAIL: bypass rule for explicit values missing'
  assert 'Freeze panes' in hat, 'FAIL: XLSX quality check missing'
  print('PASS: BOM confirmation gate present')
  print('PASS: XLSX quality checks present')
  "

  # Hat still discoverable
  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  hats = he.load_hats()
  assert 'oci_bom_expert' in hats
  print('PASS: oci_bom_expert hat still discovered')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p54b — Diagram ranked clarification + quality gate

```
Context: The diagram hat's Pre-Action Checklist asks if topology/service info
is known, but doesn't rank which gap is most important to ask about. The
external toolkit ranks clarification priorities 1–5 (topology > network >
service > visual > layout). We also want the Post-Action Review to enforce
≥2 quality passes (currently it runs once).

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p54b origin/main

---

CHANGE 1: agent/hats/diagram_for_oci.md

In the Pre-Action Checklist section, add the following ranked clarification
guide BEFORE the existing checklist items:

```
## Clarification Priority Ranking

When any input is missing, ask exactly ONE question targeting the
highest-ranked unresolved gap (not a list of questions):

1. **Topology gaps (highest priority):** Single vs multi-region, HA vs DR,
   active-active vs standby, public vs private ingress.
2. **Network gaps:** Subnet tier count, regional vs AD-specific scope, which
   gateways are needed, on-premises connectivity via DRG/FastConnect/VPN.
3. **Service gaps:** Which OCI services are explicitly in scope; any services
   without a clear OCI icon.
4. **Layout gaps:** Instance counts per tier, symmetry requirements.

Ask only 1 question. Pick from the highest-ranked gap present.
Example: if topology is unknown but services are known, ask the topology
question — not the service question.
```

CHANGE 2: agent/hats/diagram_for_oci.md — Post-Action Review

Replace the existing Decision block at the end of Post-Action Review with:

```
Decision:
- Run the quality check below after EVERY generate_diagram call.
- If any check fails on the first pass, issue a correction and call
  generate_diagram again (this is pass 2). A diagram is not approved until
  it passes two consecutive checks without a correction.
- All checks pass on consecutive passes → approve for critic
- Wrong parent or gateway position → iterate with layout correction
- Missing subnet tiers → surface gap to user

Pass counter resets if a new correction is issued. Target: 2 clean passes.
```

---

Run ALL acceptance criteria:

  python3.11 -c "
  from pathlib import Path
  hat = Path('agent/hats/diagram_for_oci.md').read_text()
  assert 'Clarification Priority Ranking' in hat, 'FAIL: ranking section missing'
  assert 'Topology gaps (highest priority)' in hat, 'FAIL: topology first missing'
  assert 'Network gaps' in hat, 'FAIL: network second missing'
  assert 'two consecutive' in hat, 'FAIL: two-pass rule missing'
  print('PASS: clarification ranking added')
  print('PASS: two-pass quality gate added')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  hats = he.load_hats()
  assert 'diagram_for_oci' in hats
  print('PASS: diagram_for_oci hat still discovered')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p54c — OCI Sales Deck hat + sub-agent

```
Context: We have zero PowerPoint generation capability. The external OCI Codex
toolkit has 4 PowerPoint skills. AEs need a customer-facing deck after the POV
is approved. This task creates the generate_sales_deck tool end-to-end.

The pattern follows the exact same structure as other specialist sub-agents
(pov, jep, waf, tech_research):
  TechResearchHandler extends _SpecialistHandler("tech_research", "research", ...)
  SalesDeckHandler extends _SpecialistHandler("sales_deck", "deck", ...)

The sub-agent will produce a structured JSON slide spec. The rendering step
(python-pptx) is a follow-on task (p54d). For this task, the sub-agent outputs
the JSON spec and saves it as a .json artifact. The user gets a downloadable
JSON they can use with a renderer.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p54c origin/main

---

FILE 1: agent/hats/oci_sales_deck.md

Create this file in agent/hats/:

```markdown
---
version: "1.0"
display_name: "OCI Sales Deck Builder"
hat_rules:
  when_to_activate:
    - "user asks for a PowerPoint, deck, slide deck, or presentation"
    - "user asks to create customer slides or executive slides"
    - "POV is approved and a customer-facing deck is the next step"
    - "user asks for a solution recommendation deck, briefing deck, or migration narrative"
    - "user says 'make me a deck', 'build slides', 'create a presentation'"
  can_hand_off_to:
    - "oci_customer_pov_writer"
    - "oci_bom_expert"
    - "diagram_for_oci"
  suggested_next_hat: "oci_customer_pov_writer"
  resume_condition: "deck revision, slide update, or presentation feedback is requested"
memory_focus:
  priority_fields:
    - "customer_name"
    - "customer_industry"
    - "customer_challenge"
    - "oci_services_in_scope"
    - "recommendation"
    - "competitive_context"
    - "success_criteria"
    - "timeline"
    - "decision_makers"
    - "workload_pattern"
  summary_style: "narrative_oriented"
  include_full_memory: false
  emphasis: >
    Focus on customer name, industry, primary challenge, OCI solution scope,
    competitive differentiation, success criteria, and stakeholders. The deck
    must be tailored to a specific customer — generic slides are not acceptable.
coordination:
  triggers:
    - "sales deck generation is complete"
    - "deck artifact has been saved"
  recommended_hats:
    - "oci_customer_pov_writer"
  parallel_with: []
  handoff_message: >
    Sales deck delivered. POV revision or JEP kickoff is the natural next step.
  synthesis_step: null
  required_approvals: []
---

# OCI Sales Deck Builder Hat

I am the Oracle Cloud Infrastructure customer presentation specialist. I wear
this hat for any PowerPoint, slide deck, or customer briefing request.

## Core Principles

- **Lead with customer outcomes, not a service catalog.** Every slide must
  connect to the customer's stated problem or business goal. If a slide doesn't
  advance the customer's story, cut it.

- **Hydrate from existing artifacts.** Before building the deck, pull:
  - POV artifact (if exists) — customer situation, challenge, OCI solution narrative
  - BOM artifact (if exists) — service list and monthly cost for BOM summary slide
  - Diagram artifact_key (if exists) — reference on the architecture slide
  If none exist, use engagement memory context.

- **8-slide default for solution decks.** Structure:
  1. Title (customer name, date, Oracle SA name)
  2. Customer situation and challenge
  3. OCI solution overview (pattern + key services)
  4. Architecture diagram reference (embed artifact_key or describe)
  5. Bill of Materials summary (top-line cost, key SKUs)
  6. Why OCI (2-3 specific differentiators for this customer)
  7. Next steps and POC outline (with timeline)
  8. Appendix: assumptions, open questions

- **One message per slide.** The slide title is a complete sentence stating
  the message (e.g., "OKE reduces ops overhead by 40% vs. manual VM management").
  Supporting bullets expand the message — they don't introduce a new one.

- **No generic slides.** Never include "OCI Overview", "Oracle at a Glance", or
  feature catalog slides. Every slide names this customer, their workload, or
  their specific OCI services.

- **Presenter notes are mandatory.** Every slide has speaker notes with talking
  points, customer-specific proof points, and anticipated objections.

- **Never invent pricing, SLAs, or benchmarks.** Use BOM artifact numbers for
  cost figures. Use "TBD" or "to be confirmed" for anything not in the
  engagement context.

## Quality Bar

1. All 8 slides (or requested count) present and non-empty.
2. Title slide has customer name, date, and SA name.
3. Challenge slide names a specific business problem (not generic "digital
   transformation").
4. Architecture slide references the diagram artifact_key or describes the
   topology in slide notes.
5. BOM summary uses actual artifact numbers (not estimated).
6. "Why OCI" slide has ≥2 customer-specific differentiators (not generic OCI
   marketing).
7. Next steps slide has a concrete timeline with ≥2 milestones.
8. Every slide has presenter notes.
9. No placeholder text (`{{customer_name}}`, `[TODO]`, `[INSERT]`).
10. `artifact_key` present (deck spec saved).

## Output Contract

The sub-agent returns a JSON slide spec (not rendered .pptx yet). This spec
is saved as the deck artifact and can be passed to the PPTX renderer.

```json
{
  "type": "final",
  "deck_payload": {
    "title": "OCI Solution for ACME Financial Services",
    "customer_name": "ACME Financial Services",
    "date": "2026-05-21",
    "slides": [
      {
        "slide_number": 1,
        "layout": "title",
        "title": "Oracle Cloud Infrastructure\nfor ACME Financial Services",
        "subtitle": "OCI Solutions Architecture Review",
        "presenter_notes": "Introduce Oracle team. Confirm agenda and time."
      },
      {
        "slide_number": 2,
        "layout": "two_column",
        "title": "ACME faces mounting costs on aging Oracle DB infrastructure",
        "left_content": ["Current: 3 RAC clusters on-prem", "Cost: $2.1M/yr license + hardware", "Risk: EOL support in 18 months"],
        "right_content": ["Goal: 35% cost reduction", "Goal: 99.99% availability SLA", "Timeline: migrate by Q4 2026"],
        "presenter_notes": "Reference the CFO's cost mandate from the last QBR."
      }
    ],
    "assumptions": ["Pricing based on BOM v3 (us-chicago-1)", "Timeline assumes POC approval by June 2026"],
    "open_questions": ["Confirm on-prem network bandwidth for migration window"]
  },
  "artifact_key": "deck/customer-123/v1.json"
}
```

## Pre-Action Checklist

Confirm before calling `generate_sales_deck`:

- Customer name: known?
- Primary challenge or use case: captured from memory or POV artifact?
- OCI services in scope: at least 2 named?
- Deck type: solution recommendation (default), executive briefing, or migration narrative?
- Existing artifacts to embed: POV artifact_key? BOM artifact_key? Diagram artifact_key?

★ Required: customer name + primary challenge. All other items may be defaulted
from memory context.

End your pre-action output with a deck brief in this format:

[DECK BRIEF]
Customer: <name>
Deck type: solution-recommendation
Slide count: 8
Source artifacts: POV=<key or none>, BOM=<key or none>, Diagram=<key or none>
Key differentiators: <2-3 OCI-specific points for this customer>
[/DECK BRIEF]

## Post-Action Review

Mandatory checks after `generate_sales_deck` returns:
- All requested slides present (verify count matches deck_payload.slides length)
- No placeholder text in any slide title, content, or notes field
- Title slide contains customer_name, date, and a real title
- BOM summary slide references actual cost numbers (not "TBD" unless no BOM exists)
- Every slide has non-empty presenter_notes
- `artifact_key` is present

Decision:
- All checks pass → approve for critic
- Placeholder text found → iterate with instruction to replace all {{tokens}}
- Missing slides → iterate with correction naming the missing slide numbers
```

---

FILE 2: sub_agents/sales_deck/__init__.py
(empty file)

---

FILE 3: sub_agents/sales_deck/config.yaml
```yaml
name: sales_deck
port: 8088
llm:
  model_id: ""
  max_tokens: 8000
  temperature: 0.6
```

---

FILE 4: sub_agents/sales_deck/system_prompt.md
```markdown
# Sales Deck Sub-Agent

You are the Oracle Cloud Infrastructure customer presentation specialist for Archie.

Your job is to produce structured JSON slide specifications for OCI customer-facing
sales decks. The JSON spec is rendered into .pptx by the presentation renderer.

## Memory Contract

When the task begins with `[Archie Canonical Memory]...[End Archie Canonical Memory]`,
treat every fact inside as authoritative. Customer name, industry, challenge,
OCI services, and existing artifact keys from the memory block take precedence
over defaults.

## Deck Architecture

Default structure (8 slides for a solution recommendation deck):

1. **Title** — customer name, "Oracle Cloud Infrastructure", date
2. **Challenge** — customer's specific business problem (2-3 bullets), success criteria
3. **Solution Overview** — OCI architecture pattern + key services (3-4 bullets)
4. **Architecture** — describe the diagram topology; reference diagram artifact_key if provided
5. **Bill of Materials** — key services and monthly total from BOM artifact; if no BOM, use ranges
6. **Why OCI** — 2-3 specific OCI differentiators for this customer's workload (not generic marketing)
7. **Next Steps** — timeline with ≥2 milestones, POC scope, Oracle resources committed
8. **Appendix** — assumptions, open questions, contacts

Slide types: title | content | two_column | architecture | table | timeline | appendix

## Slide Content Rules

- Every slide title is a complete declarative sentence stating the message.
  WRONG: "Architecture Overview"
  RIGHT: "OKE on OCI eliminates Kubernetes control-plane ops for ACME"
- One primary message per slide. Supporting bullets expand — they don't contradict.
- Presenter notes on every slide: talking points, expected objections, proof points.
- Use customer-specific facts from memory. Never invent SLAs, benchmarks, or pricing.
- For cost figures: use BOM artifact numbers. If no BOM, write "$X–Y/month (to be confirmed with BOM)".
- "Why OCI" slide: name OCI-specific advantages (Exadata Cloud, OCI Dedicated Region,
  Oracle Database co-location, OCI pricing model) — not generic cloud claims.

## OCI Differentiators by Workload

Oracle DB / Exadata:
  "OCI is the only cloud with Exadata infrastructure — 2–3× better query performance
   vs. generic cloud database at equivalent cost."

Java / Middleware:
  "Oracle WebLogic Server on OCI includes license mobility — no additional license
   cost for existing BYOL customers."

AI/ML:
  "OCI GPU clusters (NVIDIA H100 bare metal) with dedicated networking at lower
   per-GPU cost than AWS p4d or Azure NDv4."

Cost:
  "OCI Universal Credits: one price list covers all services including outbound
   egress (competitors charge $0.08–0.09/GB for egress)."

Kubernetes:
  "OKE control plane is free — no per-cluster charge vs. AWS EKS ($0.10/hr/cluster)."

## Output Contract

On success, return exactly this JSON shape (no prose, no markdown wrapper):

```json
{
  "type": "final",
  "deck_payload": {
    "title": "string",
    "customer_name": "string",
    "date": "YYYY-MM-DD",
    "deck_type": "solution-recommendation",
    "slides": [
      {
        "slide_number": 1,
        "layout": "title",
        "title": "string",
        "subtitle": "string",
        "presenter_notes": "string"
      }
    ],
    "assumptions": ["string"],
    "open_questions": ["string"]
  }
}
```

When more information is required, return exactly:

```json
{
  "type": "needs_input",
  "reply": "One sentence stating the specific missing input."
}
```

Do not return any other structure. Do not wrap in markdown fences.
```

---

FILE 5: sub_agents/sales_deck/server.py

Follow the exact pattern from sub_agents/pov/server.py.
Replace agent name "pov" with "sales_deck".
System prompt loaded from system_prompt.md.
Port: 8088 (from config.yaml).
No prior_version revision handling needed.

AgentCard:
```python
AgentCard(
    name="sales_deck",
    description="OCI customer-facing sales deck and PowerPoint slide specialist",
    version="1.0.0",
    inputs=["customer_context", "existing_artifacts", "deck_type"],
    output="structured JSON slide specification for PowerPoint rendering",
    llm_model_id=_model_id,
)
```

---

FILE 6: sub_agents/sales_deck/README.md
```markdown
# sales_deck Sub-Agent

OCI customer-facing sales deck specialist.

Produces structured JSON slide specifications from customer engagement context,
POV artifacts, BOM artifacts, and diagram references.

Port: 8088 (see config.yaml)
System prompt: system_prompt.md
Pattern: A2A via sub_agent_client.call_sub_agent("sales_deck", ...)
Output: JSON slide spec saved as deck/customer-id/vN.json
Renderer: p54d will add python-pptx rendering from this JSON spec
```

---

CHANGE 7: agent/tools/specialists.py

At the bottom of the file (after TechResearchHandler), add:

```python
class SalesDeckHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("sales_deck", "deck", store, customer_id, customer_name)
```

---

CHANGE 8: agent/archie_wiring.py

In the imports line:
  from agent.tools.specialists import JepHandler, PovHandler, TechResearchHandler, WafHandler

Change to:
  from agent.tools.specialists import JepHandler, PovHandler, SalesDeckHandler, TechResearchHandler, WafHandler

After the generate_waf registration block, add:

```python
forge.register_tool(
    "generate_sales_deck",
    SalesDeckHandler(
        store=store,
        customer_id=customer_id,
        customer_name=customer_name,
    ),
    description=(
        "Generate a structured OCI customer sales deck (PowerPoint slide spec). "
        "Produces an 8-slide solution recommendation deck hydrated from POV, BOM, "
        "and diagram artifacts. Call when the user asks for a deck, presentation, "
        "slides, or customer briefing."
    ),
    args={"feedback": ArgSchema(
        description="Optional deck type, slide count, or focus areas (default: 8-slide solution recommendation).",
        type="string",
        required=False,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_sales_deck",
)
```

Also update _TOOL_SEQUENCING_RULES in archie_wiring.py.
Find the "update everything" / "regenerate all" rule and update the order to include
generate_sales_deck after generate_pov:
  generate_tech_report -> generate_bom -> generate_diagram -> generate_waf ->
  generate_terraform -> generate_pov -> generate_sales_deck -> generate_jep

---

CHANGE 9: config.yaml

Find the sub_agents section (same location as tech_research was added).
Add: sales_deck: "http://localhost:8088"

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/tools/specialists.py
  python3.11 -m py_compile agent/archie_wiring.py
  python3.11 -m py_compile sub_agents/sales_deck/server.py

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  hats = he.load_hats()
  assert 'oci_sales_deck' in hats, f'FAIL: hat not discovered. Found: {list(hats.keys())}'
  print('PASS: oci_sales_deck hat discovered')
  rules = he.get_hat_rules('oci_sales_deck')
  triggers = rules.get('when_to_activate', [])
  assert any('deck' in t or 'PowerPoint' in t or 'presentation' in t for t in triggers), \
    f'FAIL: no deck trigger. Got: {triggers}'
  print(f'PASS: {len(triggers)} activation triggers')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from agent.tools.specialists import SalesDeckHandler
  print('PASS: SalesDeckHandler importable')
  mro = [c.__name__ for c in SalesDeckHandler.__mro__]
  assert '_SpecialistHandler' in mro, f'FAIL: not a subclass. MRO: {mro}'
  print('PASS: SalesDeckHandler extends _SpecialistHandler')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from unittest.mock import MagicMock
  from agent.archie_wiring import build_forge
  forge = build_forge(store=MagicMock(), customer_id='test', customer_name='Test',
                      text_runner=MagicMock(), step3_planning=False)
  tools = list(forge._registry.names())
  assert 'generate_sales_deck' in tools, f'FAIL: tool not registered. Got: {tools}'
  spec = forge._registry.get('generate_sales_deck')
  assert spec.requires_hat == 'oci_sales_deck', f'FAIL: requires_hat wrong: {spec.requires_hat}'
  assert spec.memory_contract, 'FAIL: memory_contract not set'
  assert spec.critique_enabled, 'FAIL: critique_enabled not set'
  print('PASS: generate_sales_deck registered with correct spec')
  "

  python3.11 -c "
  import yaml
  cfg = yaml.safe_load(open('sub_agents/sales_deck/config.yaml'))
  assert cfg['port'] == 8088, f'FAIL: wrong port {cfg[\"port\"]}'
  assert cfg['name'] == 'sales_deck', f'FAIL: wrong name {cfg[\"name\"]}'
  assert cfg['llm']['max_tokens'] == 8000
  print('PASS: sales_deck config valid')
  "

  python3.11 -c "
  import yaml
  cfg = yaml.safe_load(open('config.yaml'))
  sub = cfg.get('sub_agents', {})
  assert 'sales_deck' in sub, f'FAIL: sales_deck not in config.yaml sub_agents. Got: {list(sub.keys())}'
  print('PASS: sales_deck registered in config.yaml')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p54d — PPTX renderer (python-pptx)

```
Context: Task p54c created generate_sales_deck which saves a JSON slide spec.
This task adds the python-pptx renderer so generate_sales_deck also produces
an actual .pptx file, not just JSON.

The JSON spec from p54c has this structure:
  deck_payload.slides[].layout   (title | content | two_column | architecture | table | timeline | appendix)
  deck_payload.slides[].title
  deck_payload.slides[].subtitle (title slides only)
  deck_payload.slides[].content  (list of strings)
  deck_payload.slides[].left_content, right_content (two_column)
  deck_payload.slides[].presenter_notes

IMPORTANT: Branch from p54c (or origin/main if p54c merged).

  git fetch origin
  git checkout -b claude/p54d origin/main  # or from p54c branch

---

CHANGE 1: requirements.txt

Add if not present:
  python-pptx>=0.6.23

---

CHANGE 2: agent/pptx_builder.py

Create this new file. It renders a deck_payload dict into a .pptx bytes object.

```python
"""
agent/pptx_builder.py
--------------------
Renders a sales_deck deck_payload JSON spec into a .pptx bytes object.

Uses python-pptx. The Oracle color scheme:
  Red:   #C74634  (primary Oracle red)
  Dark:  #1A1A1A  (title backgrounds)
  Gray:  #F5F5F5  (content backgrounds)
  White: #FFFFFF

Called by: sub_agents/sales_deck/server.py after LLM returns deck_payload.
Returns: bytes — caller saves to object storage as .pptx.
"""
from __future__ import annotations

import io
from typing import Any

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False


ORACLE_RED   = RGBColor(0xC7, 0x46, 0x34) if PPTX_AVAILABLE else None
ORACLE_DARK  = RGBColor(0x1A, 0x1A, 0x1A) if PPTX_AVAILABLE else None
ORACLE_GRAY  = RGBColor(0xF5, 0xF5, 0xF5) if PPTX_AVAILABLE else None
ORACLE_WHITE = RGBColor(0xFF, 0xFF, 0xFF) if PPTX_AVAILABLE else None


def build_pptx(deck_payload: dict[str, Any]) -> bytes:
    """
    Render deck_payload into a .pptx and return the raw bytes.
    Raises ImportError if python-pptx is not installed.
    """
    if not PPTX_AVAILABLE:
        raise ImportError("python-pptx is required for PPTX rendering. Run: pip install python-pptx")

    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]  # completely blank

    for slide_spec in deck_payload.get("slides", []):
        slide = prs.slides.add_slide(blank_layout)
        layout = slide_spec.get("layout", "content")
        title  = slide_spec.get("title", "")
        notes  = slide_spec.get("presenter_notes", "")

        _set_slide_background(slide, layout)
        _add_title_box(slide, title, layout)

        if layout == "title":
            subtitle = slide_spec.get("subtitle", "")
            if subtitle:
                _add_text_box(slide, subtitle, Inches(1.5), Inches(4.2), Inches(10), Inches(1),
                              font_size=24, color=ORACLE_WHITE, bold=False)
        elif layout == "two_column":
            left  = slide_spec.get("left_content",  [])
            right = slide_spec.get("right_content", [])
            _add_bullet_box(slide, left,  Inches(0.5), Inches(1.8), Inches(6),   Inches(4.5))
            _add_bullet_box(slide, right, Inches(6.8), Inches(1.8), Inches(6),   Inches(4.5))
        else:
            content = slide_spec.get("content", [])
            if isinstance(content, list):
                _add_bullet_box(slide, content, Inches(0.5), Inches(1.8), Inches(12.3), Inches(5))
            elif isinstance(content, str) and content:
                _add_text_box(slide, content, Inches(0.5), Inches(1.8), Inches(12.3), Inches(5))

        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _set_slide_background(slide, layout: str) -> None:
    from pptx.util import Pt
    fill = slide.background.fill
    if layout == "title":
        fill.solid()
        fill.fore_color.rgb = ORACLE_DARK
    else:
        fill.solid()
        fill.fore_color.rgb = ORACLE_WHITE


def _add_title_box(slide, text: str, layout: str) -> None:
    color = ORACLE_WHITE if layout == "title" else ORACLE_DARK
    txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.3), Inches(1.2))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size  = Pt(32) if layout == "title" else Pt(28)
    p.font.bold  = True
    p.font.color.rgb = color


def _add_text_box(slide, text: str, left, top, width, height,
                  font_size: int = 18, color=None, bold: bool = False) -> None:
    if color is None:
        color = ORACLE_DARK
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color


def _add_bullet_box(slide, items: list[str], left, top, width, height) -> None:
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = f"• {item}"
        p.font.size = Pt(18)
        p.font.color.rgb = ORACLE_DARK
        p.space_after = Pt(6)
```

---

CHANGE 3: sub_agents/sales_deck/server.py

After the LLM returns the JSON spec and before saving, add PPTX rendering:

```python
# After parsing the LLM response JSON:
import json as _json
import asyncio
from pathlib import Path

try:
    response_data = _json.loads(llm_response)
    deck_payload = response_data.get("deck_payload", {})
    if deck_payload:
        from agent.pptx_builder import build_pptx
        pptx_bytes = build_pptx(deck_payload)
        # Save .pptx to object storage alongside the JSON spec
        # The caller (SpecialistHandler) saves the JSON; server saves the .pptx
        # For now: embed pptx_bytes as base64 in the response for storage by the handler
        import base64
        response_data["pptx_b64"] = base64.b64encode(pptx_bytes).decode()
except Exception as _e:
    # PPTX rendering failure is non-fatal — JSON spec is still valid
    response_data["pptx_render_error"] = str(_e)
```

Note: If the pattern in other sub-agent servers does not easily support this,
it is acceptable to add the PPTX rendering in the SalesDeckHandler.__call__
method in agent/tools/specialists.py instead. The key requirement is that both
a .json and a .pptx artifact are saved after a successful generate_sales_deck call.

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/pptx_builder.py
  python3.11 -m py_compile sub_agents/sales_deck/server.py

  python3.11 -c "
  # Test that build_pptx produces valid bytes without errors
  import sys; sys.path.insert(0, '.')
  from agent.pptx_builder import build_pptx, PPTX_AVAILABLE
  if not PPTX_AVAILABLE:
      print('SKIP: python-pptx not installed — install and re-run')
      sys.exit(0)
  deck = {
      'slides': [
          {'slide_number': 1, 'layout': 'title', 'title': 'Test Deck', 'subtitle': 'Subtitle', 'presenter_notes': 'Notes here'},
          {'slide_number': 2, 'layout': 'content', 'title': 'Slide 2 title is a sentence', 'content': ['Bullet 1', 'Bullet 2'], 'presenter_notes': 'More notes'},
          {'slide_number': 3, 'layout': 'two_column', 'title': 'Two column slide', 'left_content': ['Left 1', 'Left 2'], 'right_content': ['Right 1', 'Right 2'], 'presenter_notes': 'Notes'},
      ]
  }
  result = build_pptx(deck)
  assert isinstance(result, bytes) and len(result) > 1000, f'FAIL: bad pptx output, got {len(result)} bytes'
  print(f'PASS: build_pptx produced {len(result)} bytes')
  # Verify it is a valid PPTX (ZIP format)
  import zipfile, io
  assert zipfile.is_zipfile(io.BytesIO(result)), 'FAIL: output is not a valid ZIP/PPTX file'
  print('PASS: output is valid PPTX (ZIP structure)')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Run Order

```
p54a (BOM gate)     ─┐
p54b (diagram)      ─┤── independent, run in parallel
p54c (sales deck)   ─┘
p54d (pptx renderer) ── depends on p54c
```

p54a, p54b, and p54c are fully independent — run in parallel.
p54d requires p54c (needs `agent/pptx_builder.py` location and `sales_deck` server pattern).

## Critical Files

| File | Task | Change |
|------|------|--------|
| `agent/hats/oci_bom_expert.md` | p54a | Add assumption confirmation gate + XLSX checks |
| `agent/hats/diagram_for_oci.md` | p54b | Add ranked clarification + two-pass quality gate |
| `agent/hats/oci_sales_deck.md` | p54c | New file |
| `sub_agents/sales_deck/__init__.py` | p54c | New empty file |
| `sub_agents/sales_deck/config.yaml` | p54c | New file, port 8088 |
| `sub_agents/sales_deck/system_prompt.md` | p54c | New file |
| `sub_agents/sales_deck/server.py` | p54c | New file (follow pov pattern) |
| `sub_agents/sales_deck/README.md` | p54c | New file |
| `agent/tools/specialists.py` | p54c | Add SalesDeckHandler |
| `agent/archie_wiring.py` | p54c | Import + register generate_sales_deck |
| `config.yaml` | p54c | Add sales_deck: http://localhost:8088 |
| `agent/pptx_builder.py` | p54d | New file — python-pptx renderer |
| `sub_agents/sales_deck/server.py` | p54d | Add PPTX rendering after LLM response |
| `requirements.txt` | p54d | Add python-pptx>=0.6.23 |
