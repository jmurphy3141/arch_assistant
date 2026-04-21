# Hybrid Test Framework + Recursive Prompt Quality (v1)

## Summary

This document defines the v1 testing strategy for the OCI Architecture Assistant:

- A unified multi-layer test framework with fast deterministic PR gates.
- A recursive prompt-quality suite in two layers:
  - Deterministic static recursion in PR.
  - LLM-judge recursion in nightly/manual runs.

Coverage target for v1 includes all six core paths:

- Orchestrator
- Diagram
- POV
- JEP
- Terraform
- WAF

No runtime product behavior changes are required for v1. This is a testing-framework expansion.

## Goals

- Make PR validation fast, deterministic, and merge-blocking.
- Add deep prompt-chain quality checks without introducing flakiness in PR.
- Provide a clear split between deterministic checks and model-judge checks.
- Standardize local and CI execution modes.

## Test Taxonomy and Markers

The following pytest markers define execution lanes:

- `unit`
- `integration`
- `system`
- `e2e`
- `prompt_static`
- `prompt_judge`
- `live`

Policy:

- `live` and `prompt_judge` are opt-in by default.
- PR gate includes deterministic suites only.
- Nightly/manual includes judge-based recursion and optionally live suites.

## Deterministic Framework (PR Gate)

### Scope

- Unit + integration + system (with mocked external dependencies) + mocked e2e + `prompt_static`.

### Harness Requirements

- Standard fixtures/fakes for:
  - Orchestrator tool routing
  - Specialist outputs
  - Artifact manifests
  - Context-store propagation
- API-level system tests for multi-step flows:
  - Notes -> orchestrator -> specialists -> persisted artifacts/history
- UI e2e smoke expanded into structured e2e suites with stable network mocking contracts.

## Recursive Static Prompt Suite (`prompt_static`)

Run in PR as deterministic validation.

### Recursion Model

For each orchestrator tool path, recursively validate reachable downstream specialist/stage prompts.

### Invariants

- Required sections exist in each prompt/stage.
- Tool contract format is valid and consistent.
- Required context fields are present.
- Disallowed patterns are not present.
- Stage handoff consistency holds across recursive transitions.
- Cross-agent dependencies are respected:
  - summary/context injection
  - required document references

## Recursive LLM-Judge Suite (`prompt_judge`)

Run in nightly/manual lanes only.

### Inputs

- Curated scenario corpus per core path.

### Evaluation Loop

- Multi-pass judge recursion:
  - Evaluate prompt/output quality.
  - Re-evaluate downstream prompt chains for coherence and contract adherence.

### Artifacts

Persist scorecards and failure diffs for each scenario run.

## Prompt-Quality Report Schema

Prompt-quality checks (static and judge) emit artifact rows with:

- `path_id`
- `agent`
- `stage`
- `check_type`
- `status`
- `evidence`
- `score` (judge only)

## Execution Entry Points

### Fast PR (deterministic only)

Use this lane for merge gating:

- `unit`
- `integration`
- `system` (mocked externals)
- `e2e` (mocked)
- `prompt_static`

Recommended command:

```bash
./scripts/test_pr_gate.sh -v
```

### Nightly / Manual

Includes:

- `prompt_judge` recursive suite across all core paths
- optional `live` suites

Recommended command:

```bash
./scripts/test_nightly_prompt.sh -v
```

If running manually without available LLM judge infrastructure, allow skip fallback:

```bash
PROMPT_JUDGE_STRICT=0 ./scripts/test_nightly_prompt.sh -v
```

With live suites:

```bash
RUN_LIVE_TESTS=1 RUN_LIVE_LLM_TESTS=1 ./scripts/test_nightly_prompt.sh -v
```

Live gating notes:

- `tests/test_llm_live.py` uses configured OCI inference and requires `RUN_LIVE_LLM_TESTS=1`.
- `tests/test_server_live.py` requires `AGENT_BASE_URL=http://<host>:<port>` to be reachable.
- `SKIP_LLM_TESTS=1` disables LLM-heavy portions of `test_server_live.py`.

## Acceptance Scenarios (v1)

- Orchestrator multi-tool chains preserve context across recursive downstream prompts.
- Terraform staged prompts block/clarify correctly under invalid or missing constraints.
- POV/JEP/WAF prompt contracts enforce required structure and context references.
- Diagram path prompt constraints preserve layout/edge contract expectations.

## CI Policy

- PR: deterministic lanes only, must pass for merge.
- Nightly/manual: run `prompt_judge`; include `live` as scheduled/manual opt-in.
- Keep local commands aligned with CI marker policy for parity.

## Assumptions and Defaults

- Primary strategy: hybrid staged approach (framework + recursive prompt suite together).
- Recursive prompt quality includes both static recursion and LLM-judge recursion.
- Coverage v1 includes all six core agent paths.
- CI policy remains split deterministic vs. judge/live lanes.
