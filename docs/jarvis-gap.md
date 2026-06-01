# What Fixes the Jarvis Gap

The hats now reason well. The problem is what they're reasoning *from*.

When the five deal-context fields are populated — `pain_statement`, `economic_buyer`,
`competitive_context`, `deal_stage`, `budget_signal` — the hats produce Jarvis-quality
output. When those fields are empty, they ask questions. Jarvis doesn't ask questions. He
infers from available signals and states his inference with confidence, then invites
correction. That gap is not a hat problem. It's three fixable things.

---

## 1. The Discovery Hat Doesn't Extract `economic_buyer`

**The problem.** `economic_buyer` is not in the discovery hat's `memory_focus.priority_fields`.
It's mentioned once, buried in prose under "People." Every other expert hat silently assumes
this field exists when it's almost never populated. The POC Strategist's most important
question — "who specifically needs to say yes?" — never gets answered because discovery
never asks it.

**The fix.** Add `economic_buyer` and `competitive_context` to `memory_focus.priority_fields`
in `discovery.md`. Add a single rule: *if the customer has been described and
`economic_buyer` is still empty, ask it before suggesting any artifact.* It is the one
field that changes POC scope, BOM framing, and POV audience in one shot.

---

## 2. The Hats Ask When They Should Infer

**The problem.** A 10-year Oracle SE hearing "Deutsche Bank, Oracle Database renewal, MPLS
to OCI" doesn't ask "what industry is this customer in?" or "is compliance a concern?" They
already know: FSI, PCI DSS mandatory, BYOL likely, FastConnect over MPLS not VPN,
economic buyer is the CIO not the DBA. The hats currently have none of that pattern-firing
behavior. When `customer_industry` or `compliance_requirements` is empty, they ask. Jarvis
infers.

**The fix.** Add an **Industry Inference Rules** block to the discovery hat and to the
Archie system prompt in `archie_wiring.py`. Format:

```
If customer_industry is empty but customer signals are present, infer:
- "bank", "insurance", "financial", "FSI" → industry: Financial Services;
  compliance: PCI DSS likely; economic_buyer: CIO or CFO; preferred connectivity: FastConnect
- "hospital", "health", "pharma", "clinical" → industry: Healthcare;
  compliance: HIPAA mandatory; data_classification: PHI
- "retail", "ecommerce", "D2C" → peak seasonality matters; autoscaling POC angle
- "government", "federal", "DoD" → FedRAMP likely; OCI GovCloud region
State the inference explicitly: "I'm reading this as FSI — HIPAA/PCI in scope. Correct me
if wrong." Then proceed. Don't wait for confirmation before reasoning.
```

This one change moves every hat from "interview mode" to "colleague mode" for the majority
of real engagements, because industry is almost always inferrable from the company name or
context even when the SE hasn't named it.

---

## 3. Cross-Hat Memory Write-Back Is Missing

**The problem.** When the Diagram hat determines the topology is active-active, the BOM hat
doesn't know. When the WAF hat scores Security at 2, the POV hat doesn't know what to write
in the executive summary. When the BOM hat resolves the BYOL question, the diagram hat
doesn't update its assumptions. Each hat re-derives facts from scratch because there's no
write-back contract between them.

This is why a sequence of Archie turns feels like talking to different people rather than
one senior colleague who remembers what they said five minutes ago.

**The fix.** Add four named write-back fields to `context_store.py`'s archie state schema,
and add a write instruction to each hat's **Post-Action Review** section:

| Hat | Writes to context after completing |
|-----|------------------------------------|
| `diagram_for_oci` | `resolved_topology.ha_mode`, `resolved_topology.subnet_tiers`, `resolved_topology.gateways` |
| `oci_bom_expert` | `resolved_sizing.ha_multiplier_applied`, `resolved_sizing.byol_confirmed`, `resolved_sizing.shape_family` |
| `oci_waf_reviewer` | `resolved_waf.security_score`, `resolved_waf.p1_findings`, `resolved_waf.compliance_framework` |
| `oci_poc_strategist` | `resolved_poc.recommended_option`, `resolved_poc.wow_moment`, `resolved_poc.economic_buyer_doubt` |

The downstream hat reads from these fields before generating rather than starting cold. The
BOM hat sees `resolved_topology.ha_mode = active-active` and applies ×2 without asking.
The WAF hat sees `resolved_topology.subnet_tiers` and checks placement against facts rather
than assumptions.

Implementation: `context_store.set_archie_decision_state()` already exists and accepts
arbitrary key-value pairs. This is a hat-instruction change plus a schema addition, not a
new system.

---

## The Order to Fix These

1. **`economic_buyer` in discovery** — one field addition, one rule. Immediate payoff
   on every POC and POV turn.

2. **Industry inference rules in `archie_wiring.py`** — 20 lines in the system prompt,
   pattern-fire for the 6 most common verticals. Eliminates the "what industry?" question
   from 80% of real engagements.

3. **Cross-hat write-back** — the most work, the biggest payoff. This is what makes
   Archie feel like a single colleague rather than a committee. Without it, the hat rewrites
   get you to "smart assistant who asks good questions." With it, you get to "senior
   colleague who already knows what was decided and reasons forward from it."

---

## What This Still Won't Fix

Jarvis knows things Tony Stark hasn't told him — he infers from environmental signals. The
version of Archie that exists after these three fixes will be close, but it will still be
reactive to what the SE provides rather than proactively asking "did you consider that their
VxRail refresh cycle is probably in 18 months based on their infrastructure profile?"

That last step — proactive deal-moment inference from engagement timeline + industry + known
Oracle renewal cycles — requires either a richer oracle_customer_case_studies corpus or a
deal intelligence tool that Archie doesn't have yet. It's a data gap, not a reasoning gap.
The three fixes above close the reasoning gap. The data gap is a future phase.
