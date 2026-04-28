---
name: Orchestrator Critique
description: Evaluator skill for orchestration quality, skill injection correctness, and refinement outcomes.
version: "1.2"
model_profile: critic
tool_tags: [generate_pov, generate_jep, generate_waf, generate_terraform]
tags: [critique, quality, orchestration, oci]
keywords: [quality_bar, checklist, pitfalls, refinement]
---

# Orchestrator Critique Domain Expertise

## When to Apply
Use after specialist generation when quality, consistency, or completeness must be validated before final user response.

## Inputs Required
- User request and acceptance intent
- Tool arguments used
- Specialist summary and structured result payload
- Applied skill guidance references

## Execution Pattern
1. Check scope alignment (did output match requested task?).
2. Check technical quality and OCI correctness.
3. Check contract quality (completeness, clarity, actionability).
4. Return structured pass/fail with actionable remediation.

## Quality Bar
- High-confidence pass/fail judgment.
- Concrete issues and concrete next edits.
- No vague criticism; every issue maps to a fix.

## Critic Evaluation Guidance
- Accept only if the evaluation cites concrete OCI or workflow evidence, not generic writing preferences.
- Verify scope alignment, prerequisite satisfaction, applied skill traceability, and whether refinement is required.
- Treat unresolved blockers, missing artifact persistence, unsupported OCI constructs, or hidden prerequisite gaps as fail conditions.
- Example pass: flags missing private subnet placement in a requested private app tier and gives a targeted retry instruction.
- Example fail: returns "looks good" without checking artifacts, governor status, or applied specialist guidance.

## Failure Questions
- Which section fails acceptance criteria and why?
- What exact changes would make this pass on next iteration?
- Are there unresolved prerequisite gaps?

## Output Contract
Return strict structured evaluation compatible with refinement loops:
- issues[]
- severity
- suggestions[]
- confidence
- overall_pass
- critique_summary
