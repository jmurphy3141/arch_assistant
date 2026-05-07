# SkillForge Skill File — Reference Template

This file documents the canonical format for a SkillForge skill file.
A skill file is a Markdown document with an optional YAML frontmatter block
that configures framework behavior. Skill files are the primary mechanism
for extending and customizing SkillForge without writing Python code.

There are two kinds of skill files:

| Kind | Purpose | Registered via |
|------|---------|----------------|
| **Global skill** | Routing guidance, safety rules, format instructions | `forge.register_skill_file(path)` |
| **Hat (expert lens)** | Expert persona injected when a hat is activated | `agent/hats/*.md` |

---

## Full Format Reference

```markdown
---
# ── Machine-readable metadata (parsed by SkillForge) ─────────────────────────

# Required for hat files. For global skill files, these fields are ignored.
version: "1.0"
display_name: "Human-readable expert role name"

# Hat transition rules — when this hat should activate and what it can hand off to.
hat_rules:
  when_to_activate:
    - "trigger phrase 1 — plain English, case-insensitive keyword match"
    - "trigger phrase 2"
  can_hand_off_to:
    - "other_hat_name"
  suggested_next_hat: "other_hat_name"   # or null
  resume_condition: "plain English — when this hat becomes relevant again"

# Memory filtering — what facts this expert focuses on.
memory_focus:
  priority_fields:
    - "fact_key_1"
    - "fact_key_2"
  summary_style: "cost_and_sizing_oriented"   # free text label for logs
  include_full_memory: false   # true = give full snapshot (e.g. critic hat)
  emphasis: >
    One or two sentences injected into the [MEMORY VIEW] block to orient
    the LLM toward the most important facts for this expert role.

# Multi-agent coordination — how this hat interacts with sibling hats.
coordination:
  triggers:
    - "phrase that signals this hat's workflow step is happening"
  recommended_hats:
    - "hat_to_suggest_next"
  parallel_with:
    - "hat_that_can_run_concurrently"
  handoff_message: "Status event message emitted when this hat is dropped."
  synthesis_step: "Description of synthesis to perform after parallel hats complete"
  required_approvals:
    - "approval_token_1"   # tokens referenced in governor logic

---

# Hat Display Name

One sentence describing when I wear this hat.

## Core Principles

- Bullet: what this expert always does (3–6 bullets)
- Bullet: what drives their reasoning and judgment
- Bullet: non-negotiable behaviors regardless of context

## Quality Bar

Ordered list — minimum criteria that MUST be met before output is approved:

1. Criterion 1 — concrete and testable
2. Criterion 2
3. Criterion 3

## Output Contract

Required fields in the tool result. Map to actual result dict keys:

- `field_name`: description and type
- `artifact_key`: object-store key of the persisted output (always required)

## Critic Evaluation Guidance

Questions this expert asks when reviewing a sub-agent's output:

- Does [X] match [Y]?
- Is [artifact signal] present?
- Are [required fields] non-empty and internally consistent?

## Failure Questions

Specific questions to ask the customer or sub-agent when the result fails:

- "Clarification question 1?"
- "Clarification question 2?"

## Activation & Drop

When to activate: [condition].
I drop this hat when [completion condition].
```

---

## Global Skill File Format

Global skills contain only Markdown — no YAML frontmatter. The entire file
content is injected into the system prompt on every turn.

```markdown
# Skill: Intent Routing

When the user message is conversational (greetings, clarifications, follow-up
questions) with no tool intent, respond directly in text — do not call any tool.

When the user message is a recall query ("what did we decide", "show me the last
diagram", "what was the BOM"), call `get_document` or `get_summary` rather than
regenerating.

When the user message is a note capture ("remember that", "note:", "write down"),
call `save_notes` rather than a generation tool.
```

---

## Minimal Hat File Example

The smallest valid hat file that enables expert injection:

```markdown
---
version: "1.0"
display_name: "Cost Reviewer"
hat_rules: {}
memory_focus: {}
coordination: {}
---

# Cost Reviewer Hat

I wear this hat when cost analysis or budget validation is requested.

## Core Principles
- Every cost estimate must reference real pricing data, not approximations.
- Budget thresholds must be checked against stated customer constraints.

## Quality Bar
1. All cost figures have unit and currency.
2. Monthly total is present and internally consistent.

## Output Contract
- `monthly_total`: total estimated monthly cost in USD.
- `line_items`: list of cost line items.

## Critic Evaluation Guidance
- Are all line items priced with real SKUs?
- Does the monthly_total match the sum of line_items?

## Failure Questions
- "What is the target monthly budget?"

## Activation & Drop
I activate on any cost or budget request. I drop when the customer has confirmed
the cost estimate.
```

---

## Hat Name to File Mapping

SkillForge discovers hat files from the `agent/hats/` directory. The hat name
used in `use_hat_{name}` tool calls is the filename stem:

| File | Hat name | Tool calls |
|------|----------|------------|
| `agent/hats/bom_reviewer.md` | `bom_reviewer` | `use_hat_bom_reviewer` / `drop_hat_bom_reviewer` |
| `agent/hats/diagram_builder.md` | `diagram_builder` | `use_hat_diagram_builder` / `drop_hat_diagram_builder` |
| `agent/hats/waf_reviewer.md` | `waf_reviewer` | `use_hat_waf_reviewer` / `drop_hat_waf_reviewer` |
| `agent/hats/my_custom_hat.md` | `my_custom_hat` | `use_hat_my_custom_hat` / `drop_hat_my_custom_hat` |

Adding a new `.md` file to `agent/hats/` automatically registers it — no Python
changes required.

---

## Hat Stack Rules

- Maximum 3 hats active simultaneously (FIFO eviction when exceeded).
- `active_hats[0]` = primary (first activated).
- `active_hats[-1]` = most recently activated.
- Hats are not persisted across turns — each turn starts with an empty hat list.
- The LLM activates hats by calling `use_hat_{name}` and drops them with
  `drop_hat_{name}` as tool calls within the ReAct loop.
