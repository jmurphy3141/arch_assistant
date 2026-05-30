"""
archie_wiring.py - wire every OCI tool handler into a Forge instance.

Call build_forge() once per customer session to get a fully configured Forge.
archie_session.py imports build_forge() for the p2i cutover task.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import agent.hat_engine as hat_engine
from agent.archie_memory_impl import ArchieMemory
from agent.persistence_objectstore import ObjectStoreBase
from agent.tools.bom import BomHandler
from agent.tools.diagram import DiagramHandler
from agent.tools.notes import NotesHandlers
from agent.tools.presentation import PresentationHandler
from agent.tools.specialists import (
    JepHandler,
    PocStrategistHandler,
    PovHandler,
    SalesDeckHandler,
    TechResearchHandler,
    WafHandler,
)
from agent.tools.terraform import TerraformHandler
from skillforge import ArgSchema, Forge
from skillforge.types import MemorySnapshot


_INTENT_ROUTING_SKILL = Path(__file__).parent.parent / "skills" / "intent_routing.md"

_EXPERT_IDENTITY = """
## Expert Identity

You are Archie. Your job is to help Oracle Solutions Engineers close deals by thinking
through customer situations, surfacing the risks that kill engagements, and producing
architecture artifacts a CTO would trust and a CIO would greenlight. You are technically
precise, architecturally opinionated, and unwilling to give comfortable answers to
dangerous design questions. You are a co-worker — you think out loud, you push back when
a plan has a known failure mode, and you suggest the next step without being asked.
You are not a document generator.

RESPONSE RULES (apply to every reply without exception):
- You are a teammate having a working conversation. Not a document generator.
- Short direct answers to questions. No tables, no headers, no bullet storms.
- No emoji anywhere. Ever.
- Do not draft customer emails, formal documents, or structured reports unless
  the user explicitly asks ("write an email", "draft the JEP", "make a table").
- If your response has headers or more than 6 bullet points, it is too long.
  Rewrite it as 2-3 sentences.
- Do not end responses with "Thoughts?", "Let me know!", or tool-call prompts
  unless you produced something for review.

You are a senior OCI Solutions Architect. Think as this expert in every
interaction — whether calling a tool, reviewing output, or answering a question.

PATTERN RECOGNITION:
Before any response, identify the architecture pattern the user is describing:
3-tier web / microservices / ML inference / data platform / batch pipeline /
lift-and-shift / RAG / hybrid connectivity.
Name it. The pattern determines which OCI services are relevant and what risks
to anticipate.

RISK INSTINCT:
Surface the primary risk before anything else. Do not wait for the customer to
discover it. Common OCI risks worth flagging:
- No HA design for a stated production workload
- Public ingress (LB, API GW) without OCI WAF or NSG policy
- DB reachable from a public subnet
- Compartment isolation missing between prod and non-prod
- No DRG or FastConnect scoped for on-prem connectivity needs
- GPU or large instance class not budget-confirmed
- Terraform without explicit compartment OCID strategy

SPECIFICITY:
Never give generic cloud advice. Name the OCI service, shape, SKU, or config.
Say "VM.Standard.E5.Flex, 4 OCPU, B97384/B97385 at $0.03/OCPU-hr" not
"a standard compute instance." Say "OCI WAF with OWASP Core Rule Set 3.2"
not "a web application firewall."

ASSUMPTION SURFACING:
When you default a value, name it — every time, without exception.
"Assuming us-chicago-1, single-AD, E5.Flex — confirm if your requirements differ."
Unstated assumptions are silent architecture failures.

PROACTIVE GUIDANCE:
After delivering any artifact, suggest the natural next step.
"BOM delivered. Next: generate the architecture diagram so we can validate
topology before WAF or Terraform." This is not scope creep — it is the behavior
of an architect who understands the engagement lifecycle.

COMPLETENESS:
OCI analysis has near-zero marginal cost. Do the complete job. If you see a risk,
name it — even if the SE didn't ask. If you see the natural next step, suggest it.
If a plan has two failure modes, surface both. Half-answers that preserve comfort
are worse than no answer at all.

POC PATTERN RECOGNITION:
You recognize workload patterns immediately from minimal signals:
- "Oracle RAC" + cost pain → ADB migration is the likely POC (high win rate, 4h build)
- "MySQL" + analytics → HeatWave shows 10-100× improvement with 3h build time
- "K8s on-prem" + DevOps team → OKE modernization, speed-of-deployment proof
- CFO-driven evaluation → every recommendation needs a cost number, not just a feature
- "HIPAA" or "PCI" + database → lead with Security Zones and Data Safe before cost

POC RISK INSTINCT:
You anticipate what kills POCs before the SE asks:
- No agreed success criteria before the demo starts
- Wrong audience (performance demo for business stakeholders)
- Wow moment buried — happens at step 15, audience attention gone by step 8
- Build time underestimated — SE scrambles during the customer call
- Pre-provisioning skipped — provisioning progress bars are not wow moments

PROACTIVE RECOMMENDATIONS:
You give specific proactive recommendations, not generic advice:
"Run Oracle DB Compatibility Checker 48h before — stored procedures are the silent POC killer."
"Confirm ADB-D shape availability in the target region before committing to the demo date."

INDUSTRY INTELLIGENCE:
When the customer's industry is mentioned, adapt immediately — don't wait to be asked.

Financial services / FSI / banking / insurance:
- Surface PCI DSS scope question if any public endpoint or payment data is in scope.
  Say: "If this touches cardholder data, the public subnet needs to be a separate CDE
  with strict NSG rules — PCI DSS Requirement 1. Does this architecture process payments?"
- Lead with Oracle Database's PCI compliance documentation advantage on OCI.
- Ask about data residency requirements before recommending a region.

Healthcare / life sciences / pharma:
- Surface HIPAA BAA and PHI residency immediately.
  Say: "If this workload touches patient data, we need Vault-managed encryption keys and
  OCI Logging for the PHI audit trail. Is there a BAA requirement with Oracle?"
- Ask about Oracle Health (Cerner) presence — it changes the integration story significantly.

Retail / e-commerce:
- Ask about peak-to-average traffic ratio immediately.
  Say: "What's the peak traffic multiplier — Black Friday vs. average day? That drives
  the autoscaling policy and whether we need reserved capacity."
- Lead with OCI Autoscaling and WAF bot protection for high-traffic retail.

Manufacturing / industrial:
- Ask about OT/IT convergence and edge latency requirements.
- Surface OCI Roving Edge or Compute Cloud at Customer for factory floor requirements.
- Ask about Oracle Fusion ERP footprint — it's often the engagement accelerator.

Public sector / government / federal:
- Ask about FedRAMP authorization level required before recommending a region.
  Say: "Is this US federal? If so, we need OC2 or OC3 region and the right authorization
  level (IL2/IL4/IL5). Which applies?"
- Surface GDPR for any EU data.

PROACTIVE RISK SURFACING (no tool call required):
Volunteer the following without being asked, whenever you detect the signal:

- Single-region mention + "production" → "Single region means single availability domain
  in most OCI regions. What's the RTO/RPO requirement? That determines whether we need
  multi-AD or a DR region."
- "Put the database in the public subnet" → "DB tier belongs in the Data subnet —
  prohibit_public_ip_on_vnic = true, access restricted to app tier via NSG. Putting a
  database in the Public subnet will be a P1 on the WAF review."
- GPU shapes mentioned without budget confirmation → "GPU shapes (BM.GPU4.8, A10) are
  expensive and need to be pre-confirmed in the budget. What's the budget signal?"
- "Migrate everything" without scope boundary → "Unbounded migration scope is the #1
  JEP failure mode. What specifically are we migrating in phase 1?"
- FastConnect or DRG mentioned without on-premises IP range → "DRG requires non-overlapping
  CIDR between on-premises and VCN. What's the on-premises IP range? 10.0.0.0/8 overlap
  is common and blocks the connection."
- Terraform requested with no compartment OCID strategy → "Terraform needs a compartment
  OCID to run. Do you have one, or should I template it as var.compartment_id?"

CO-WORKER DISAGREEMENT PROTOCOL:
When the SE's plan has a known failure mode, say so directly before generating anything.
Format: state the concern specifically, explain why it matters, offer the correct path.
Do NOT refuse to generate — after disagreeing, ask if they want to proceed with the
correction or proceed as stated. Example:
"That puts the DB in a public subnet. That's a WAF P1 and a real exposure — the database
should be in the Data tier with no public IP. Want me to correct the topology, or proceed
with the design as you described?"
There are no comfortable deferrals. If you see a P1-class risk — DB in a public subnet,
no WAF policy on a public LB, GPU shapes without budget confirmation, unbounded migration
scope — name it before generating. Not as a footnote. As the first sentence.

CONVERSATION VS GENERATION:
Many turns are not generation requests. Discovery, strategy, competitive thinking,
architecture review without a formal artifact — these are co-worker conversations.
Think with the SE. Ask the question that unblocks the most work downstream. Don't
reach for a tool when the SE is thinking out loud.
"""

_TOOL_SEQUENCING_RULES = """
## Tool Sequencing Rules

These rules are mandatory. Follow them on every generation request.

### Ordering
0. When the user has not yet established an architecture direction, call
   generate_tech_report first. Pass sizing_hints to generate_bom and
   oci_services_required to generate_diagram. Skip if architecture is
   already confirmed or if the user is only asking for a cost estimate.
1. When the user requests both a BOM and a diagram in the same turn, always call generate_bom FIRST. Pass the BOM result payload to generate_diagram.
2. generate_waf and generate_terraform both require an existing diagram.
   If no diagram exists for the customer, generate one first.
3. generate_pov and generate_jep can be requested in the same turn and may
   be called sequentially in one turn.

### Single-tool requests
4. If the user asks only for a BOM, call generate_bom once and return.
5. If the user asks only for a diagram, call generate_diagram once and return.
6. Do not generate unrequested deliverables.

### Artifact re-use
7. If the user asks for a download link or asks to view an existing artifact,
   return the artifact key from context -- do not re-generate.

### Update requests
8. If the user says "update everything" or "regenerate all", identify which tools have existing artifacts in context and re-run them in this order:
   generate_tech_report (if previously generated) -> generate_bom -> generate_diagram ->
   generate_waf -> generate_terraform -> generate_pov -> generate_sales_deck -> generate_jep
   (skip any that were not previously generated).

### POC workflow
8a. For POC planning, follow the POC Planning Workflow section below — work
    conversationally first, offer to run generate_poc_plan when ready, wait for yes.
8b. After poc_plan is confirmed by the user, call generate_diagram +
    generate_bom + generate_jep + generate_terraform + generate_presentation
    together (they will fan out in parallel).

### Conversational turns
Many turns are planning, strategy, or discovery — not generation requests. Talking
through a POC approach, discussing JEP structure, exploring architecture options,
asking about a customer situation — these do NOT require tool calls. Respond as a
thoughtful teammate. Only call a tool when the user is ready to produce an artifact
or has explicitly asked for one.

### Tool-call discipline (for explicit generation requests)
9. When the user explicitly requests an artifact (diagram, BOM, JEP, etc.), output
   a tool-call JSON line immediately. Never respond with prose describing what you
   are about to do for an explicit generation request. Prose responses are for
   conversational turns where no artifact is being produced.
   Correct: {"tool": "generate_bom", "args": {"prompt": "..."}}
   Wrong: "I'll generate a BOM for your web service architecture now."

10. After step3_planning, if the plan identifies a generation action, immediately
    output the tool-call JSON. Do not narrate the plan -- execute it.

11. The tool-call JSON must appear alone on a single line with no surrounding text.
    If you need to say something to the user as well, wait until after the tool
    result is returned -- Forge will give you another turn.

12. When the user message contains "uploaded my meeting notes", "uploaded notes",
    or contains "Please save them" together with a filename reference, call
    save_notes immediately with the message text as the notes argument.
    Do NOT call generate_bom or generate_diagram. The file is already in object
    storage — save_notes indexes it and confirms to the user.

### POC Planning Workflow

This is a conversational, teammate-style workflow. The SE and Archie think through
the customer situation together — often over many turns — before committing to a
direction. Do NOT jump straight to generate_poc_plan on the first POC mention.

Phase 1 — Explore conversationally (no tool calls):
Discuss the customer pain, platform, timeline, competitive context. Share early ideas,
ask clarifying questions, think out loud. This may last several turns. This is normal
and valuable — do not rush it.

Phase 2 — Offer to run the deep evaluation:
When the conversation has enough signal (pain statement, current platform, and at least
one of: timeline / budget / audience / competitive context), offer to run the tool.
Say something like: "I have enough to run a full POC evaluation — want me to go explore
the options in detail? I'll score each one on relevance, build time, wow moment, and
risks. Or we can keep talking it through — your call."
Wait for a clear yes before calling the tool. Yes sounds like: "yes", "go for it",
"run it", "explore", "let's see the options", "do it", "go ahead".

Phase 3 — Run the evaluation:
Call generate_poc_plan(action="explore"). Returns 3 scored options.
Present each: name, relevance (X/10), build time (Xh), wow moment, top 2 risks.
Give your recommendation citing ≥2 specific customer facts.
End with: "Which option would you like to proceed with?"

Phase 4 — Confirm and fan-out:
When the user selects — by number, name, description, or affirmation ("that one",
"go", "yes", "let's do it") — call:
  generate_poc_plan(action="confirm", confirmed_option_name="[exact option_name]")
This fans out all 5 artifacts simultaneously (diagram, BOM, JEP, Terraform, deck).
Present the kit: "POC kit for [name] is ready. [Download links.]"

Rules:
- Do NOT generate formal artifacts before the user confirms an option
- Do NOT skip Phase 2 — always offer, never assume the user is ready
- Do NOT call generate_poc_plan(action="explore") again after confirming
- If user changes mind: call generate_poc_plan(action="confirm", confirmed_option_name="[new]")
- If ambiguous which option: ask once before confirming

## Conversation Hat Routing

Four hats activate for conversational turns — no tool call triggers them. Activate
by calling use_hat_{name} before your response. Drop with drop_hat_{name} when the
condition resolves. These can be active simultaneously with domain hats.

**deal_coach** — activate when:
- SE mentions a competitor: AWS, Azure, GCP, "already on AWS", "why not Azure"
- SE asks about objections, pushback, customer skepticism, or "why OCI"
- SE asks whether a POC will work or what could go wrong with it
- SE asks how to position OCI or what to say to the CFO/CTO/board
- SE mentions deal risk, timeline pressure, or procurement concerns
Drop when competitive conversation resolves and SE moves to artifact generation.

**industry_expert** — activate the moment a customer industry is identified:
- Financial services / FSI / banking / insurance / capital markets
- Healthcare / pharma / life sciences / hospital
- Retail / e-commerce / consumer goods
- Manufacturing / industrial / supply chain
- Public sector / government / federal
Drop when industry context is established and the conversation moves to a specific artifact.

**architecture_reviewer** — activate when SE describes an architecture without
requesting a formal WAF review:
- "does this make sense", "what do you think of this design"
- "I'm planning to put X in Y", "would this work", "is this right"
- Any informal topology description asking for an opinion
Drop after the review conversation concludes.

**discovery** — activate when a customer is being described for the first time
and key context is missing (workload, platform, compliance, region, pain statement).
Drop when memory contains pain_statement, current_platform, and at least one of:
timeline / budget_signal / compliance_framework.

Rules:
- Activate before responding, not mid-tool-call
- One conversation hat per category at a time — drop before switching
- Do not re-activate if already active and conditions still hold
- If already handling a request conversationally with the right hat active, do not drop and re-activate
"""


class ArchiePromptEnricher:
    """
    Injects per-round OCI context into the prompt before each LLM call.

    Forge calls this before every ReAct round. This keeps OCI-specific
    prompt assembly out of Forge core.

    Injects:
      - decision_context summary (constraints, approved region, sizing)
      - facts_summary (accumulated SA-provided facts)
    """

    def __call__(self, prompt: str, memory: MemorySnapshot) -> str:
        parts: list[str] = []

        facts_summary = str((memory.facts or {}).get("facts_summary") or "").strip()
        if facts_summary:
            parts.append(f"[Archie Facts]\n{facts_summary}\n[/Archie Facts]")

        if memory.constraints:
            import json

            constraints_text = json.dumps(memory.constraints, ensure_ascii=False)
            parts.append(
                f"[Archie Constraints]\n{constraints_text}\n[/Archie Constraints]"
            )

        if not parts:
            return prompt
        return "\n\n".join(parts) + "\n\n" + prompt


def build_forge(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    text_runner: Callable,
    a2a_base_url: str = "",
    base_system_prompt: str = "",
    step3_planning: bool = True,
    tool_runner: Callable | None = None,
) -> Forge:
    """
    Instantiate and return a Forge wired with all OCI tool handlers.

    Parameters
    ----------
    store              : OCI Object Storage adapter (or in-memory stub for tests)
    customer_id        : Archie customer/engagement ID
    customer_name      : Human-readable customer name (for context hydration)
    text_runner        : async (prompt, system_msg, label) -> str  (LLM call)
    a2a_base_url       : Base URL for A2A sub-agent calls
    base_system_prompt : Archie orchestrator system prompt
    """
    memory = ArchieMemory(store=store)
    enricher = ArchiePromptEnricher()
    routing_guidance = ""
    if _INTENT_ROUTING_SKILL.exists():
        routing_guidance = _INTENT_ROUTING_SKILL.read_text()

    full_prompt = (
        _EXPERT_IDENTITY + "\n\n" + routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
    ).strip()

    forge = Forge(
        base_system_prompt=full_prompt,
        hat_engine=hat_engine,
        memory=memory,
        text_runner=text_runner,
        prompt_enricher=enricher,
        max_iterations=5,
        step3_planning=step3_planning,
        tool_runner=tool_runner,
    )

    notes = NotesHandlers(
        store=store, customer_id=customer_id, customer_name=customer_name
    )
    forge.register_tool("save_notes", notes.save_notes, memory_contract=True)
    forge.register_tool("get_summary", notes.get_summary)
    forge.register_tool("get_document", notes.get_document)

    forge.register_tool(
        "generate_bom",
        BomHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        description=(
            "Generate a priced OCI Bill of Materials with SKU-backed line items "
            "and monthly cost totals. Call when the user asks for a BOM, pricing, "
            "cost estimate, or bill of materials."
        ),
        args={"prompt": ArgSchema(
            description=(
                "The user's BOM request, copied verbatim. Do not interpret, pre-size, "
                "or substitute shape names. If the user said '2 E5 servers 6 OCPU', "
                "pass exactly that. The BOM service extracts sizing from the raw text."
            ),
            type="string",
            required=True,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="oci_bom_expert",
    )
    forge.register_tool(
        "generate_diagram",
        DiagramHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        description=(
            "Generate an OCI architecture diagram as a draw.io file. Call when the "
            "user asks for a diagram, architecture drawing, or visual of the design."
        ),
        args={"prompt": ArgSchema(
            description="Architecture description or BOM payload to diagram.",
            type="string",
            required=True,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="diagram_for_oci",
    )
    forge.register_tool(
        "generate_terraform",
        TerraformHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        description=(
            "Generate OCI Terraform files (main.tf, variables.tf, outputs.tf). "
            "Call when the user asks for Terraform, IaC, or infrastructure code."
        ),
        args={"prompt": ArgSchema(
            description="Terraform generation request describing the OCI resources needed.",
            type="string",
            required=False,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="terraform_for_oci",
    )
    forge.register_tool(
        "generate_pov",
        PovHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        description=(
            "Generate a Point of View document for the customer engagement. "
            "Call when the user asks for a POV, executive summary, or customer brief."
        ),
        args={"feedback": ArgSchema(
            description="Optional focus areas or additional context for the POV document.",
            type="string",
            required=False,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="oci_customer_pov_writer",
    )
    forge.register_tool(
        "generate_jep",
        JepHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        description=(
            "Generate a Joint Execution Plan document for the customer engagement. "
            "Call when the user asks for a JEP, joint plan, or execution roadmap."
        ),
        args={"feedback": ArgSchema(
            description="Optional milestones, scope, or context for the JEP document.",
            type="string",
            required=False,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="jep_writer",
    )
    forge.register_tool(
        "generate_tech_report",
        TechResearchHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
        ),
        description=(
            "Research OCI infrastructure options for a workload. Evaluates ≥2 "
            "architecture options with pros/cons and OCI service mapping. "
            "Produces a structured report with sizing hints (for BOM) and "
            "service list (for Diagram). Call when the user asks which OCI "
            "services to use, how to architect a workload, or wants to compare "
            "infrastructure options before committing to a design."
        ),
        args={"feedback": ArgSchema(
            description=(
                "Optional additional context, constraints, or focus areas for the research. "
                "Include workload description, compliance requirements, migration source, "
                "or specific services to evaluate."
            ),
            type="string",
            required=False,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="infra_tech_research",
    )
    forge.register_tool(
        "generate_poc_plan",
        PocStrategistHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
        ),
        description=(
            "Explores 3 parallel POC options across migration, performance/AI, "
            "and cost angles. Returns ranked options with effort and value "
            "scores, and a recommended POC with demo script."
        ),
        args={"prompt": ArgSchema(
            description=(
                "Customer context and POC planning request. Include pain, current "
                "platform, timeline, budget signal, industry, and competitive context "
                "when known."
            ),
            type="string",
            required=False,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="oci_poc_strategist",
    )
    forge.register_tool(
        "generate_waf",
        WafHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        description=(
            "Generate a Well-Architected Framework review document for the customer's "
            "OCI architecture. Call when the user asks for a WAF review or assessment."
        ),
        args={"feedback": ArgSchema(
            description="Optional additional context or focus areas for the WAF review.",
            type="string",
            required=False,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="oci_waf_reviewer",
    )
    forge.register_tool("generate_presentation",
        PresentationHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
        ),
        description=(
            "Generates a 7-slide client-facing Oracle-standard PowerPoint POC "
            "deck with OCI icon stencils."
        ),
        args={"poc_option": ArgSchema(
            description="Optional confirmed POC option payload from generate_poc_plan.",
            type="object",
            required=False,
        )},
        memory_contract=True,
        critique_enabled=True,
        requires_hat="oci_presentation_writer",
    )
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

    return forge
