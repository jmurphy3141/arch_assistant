# OCI Architecture Assistant
## v1.8.0 Requirements — Orchestrator Skill Hardening + JEP Lifecycle Governance

- Date: April 22, 2026
- Owner: Jason Murphy
- Version target: v1.8.0
- Status: Delivered (JEP lifecycle contract + revision-request flow implemented; orchestrator hardening baseline retained)

## 1) Objective

Strengthen orchestrator specialist governance and make JEP behavior explicit, enforceable, and auditable across chat and JEP UI workflows.

## 2) Locked Decisions

1. v1.8 scope is limited to:
- orchestrator skill hardening
- JEP lifecycle clarity and enforcement

2. JEP generation policy:
- `generate_jep` should produce best-effort drafts when context is incomplete.
- Missing required details are surfaced as structured gaps.

3. Required JEP fields (quality contract):
- `duration`
- `scope_in`
- `scope_out`
- `success_criteria`
- `owners`
- `milestones`

4. Lifecycle representation:
- lifecycle is embedded in JEP API responses (no separate lifecycle endpoint).

5. Revision policy:
- lock applies only to `approved` JEPs.
- revision of approved JEP requires intentional user action (dedicated revision request flow).
- non-approved states may continue normal regenerate/update flows.

6. Orchestrator policy behavior:
- expert-mode policy pushback.
- hard stop when contract requires block.

7. Source traceability requirements:
- include document/version references used by JEP generation.
- include short excerpts/snippets used in generation context.

## 3) In Scope

1. Hardening `agent/orchestrator_skills/*` contracts with path-specific, testable validation rules.
2. Extending orchestrator preflight/postflight for structured JEP contract checks.
3. Embedding `jep_state` metadata in JEP responses.
4. Enforcing approved-lock + intentional revision workflow.
5. Propagating JEP lifecycle and contract metadata to chat tool traces.
6. UI support for lock state, missing fields, and revision action.

## 4) Out of Scope

1. New standalone lifecycle microservice or separate lifecycle endpoint.
2. Replacement of the overall orchestrator architecture.
3. Scope expansion into non-JEP specialist behavior beyond required no-regression safeguards.

## 5) JEP Lifecycle Model

Canonical states:
- `not_started`
- `kickoff_ready`
- `questions_pending`
- `ready_to_generate`
- `generated`
- `approved`
- `revision_requested`

Locking rule:
- `approved` => locked.
- all other states => unlocked.

## 6) Required API Contract Additions (Embedded)

JEP responses (generate/latest, and any relevant JEP read APIs) must embed a `jep_state` object:

- `state`: lifecycle state
- `is_locked`: boolean
- `missing_fields`: array of required-field gaps
- `required_next_step`: canonical next action string
- `source_context`:
  - `references`: object keys/versions used
  - `snippets`: short excerpts used for generation context

## 7) Orchestrator Contract Requirements

1. `generate_jep` path must evaluate lifecycle + lock status before execution.
2. If blocked by policy, orchestrator returns hard policy pushback with structured reasoning:
- `reason_codes`
- `missing_fields`
- `required_next_step`
- `retry_instructions`
3. Tool trace metadata must include JEP lifecycle/lock state and contract outcomes in `tool_calls[].result_data.trace`.

## 8) JEP Agent Requirements

1. Best-effort draft generation remains enabled.
2. Prompt context must prioritize (in order):
- approved JEP (if exists)
- latest generated JEP
- kickoff Q&A answers
- notes + feedback history
3. Generated output must carry machine-readable gap reporting for required fields.
4. Generation from approved lock state must require explicit revision request intent.

## 9) UI Requirements

1. JEP UI must display embedded lifecycle/lock state.
2. Locked (`approved`) JEP must hide direct regenerate/update and require explicit revision request action.
3. Non-approved states allow normal regenerate/update flows.
4. Missing required fields are visible as actionable checklist items.
5. Source-context snippets are visible for transparency/debug.

## 10) Security + Governance

1. Continue existing global auth model and role conventions.
2. No JEP-specific auth stack divergence.
3. All policy stops must be deterministic and explainable.

## 11) Testing Requirements

### Unit
1. JEP required-field extraction and gap reporting.
2. Approved lock-state enforcement.
3. Skill contract parser + reason code behavior.

### Integration
1. JEP response includes `jep_state` fields and correct values by state.
2. Approved lock blocks regenerate unless revision requested.
3. Non-approved states allow normal generation.

### System
1. Orchestrator `generate_jep` emits hard-stop pushback for blocked conditions.
2. `tool_calls[].result_data.trace` includes JEP lifecycle metadata.
3. No regression in `generate_diagram`, `generate_pov`, `generate_waf`, `generate_terraform` routing.

### UI/E2E
1. JEP flow: draft generation with missing fields shown.
2. Approve -> lock enforced in UI.
3. Revision request unlock path.

## 12) Merge Gate

1. New JEP lifecycle and policy tests passing.
2. Existing deterministic gates passing.
3. No orchestrator specialist regression.
4. UI JEP flow validations passing.
