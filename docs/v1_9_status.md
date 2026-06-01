# v1.9 Completion Status

Last updated: 2026-06-01 for Archie OCI Architecture Assistant v1.9.x.

This is the canonical repo evidence page for answering whether v1.9 is complete.
Fast paths are valid deterministic orchestration paths: they bypass ReAct prompt
construction by design, so they do not count as ReAct self-guidance failures.

## Acceptance Matrix

| Requirement | Status | Evidence |
|---|---|---|
| Orchestrator Self-Guidance | Complete | `skillforge/forge.py` owns the structured ReAct loop, Step 3 planning, expert pre-action, post-review, and critic pass. `agent/archie_session.py` preserves compatibility fast paths and routes chat turns through Forge. Covered by `test_react_prompt_includes_internal_orchestrator_self_guidance`, `test_react_followup_prompt_preserves_internal_orchestrator_self_guidance`, and Forge loop tests. |
| Decision Context | Complete | `agent/decision_context.py` builds per-turn Decision Context. `agent/archie_session.py` persists it through `context_store.set_latest_decision_context`, refreshes Archie memory, records decision state, and passes context into tool traces. Covered by `tests/test_orchestrator_decision_flow.py` and `tests/test_decision_context.py`. |
| Governor / Safety Enforcement | Complete | LLM governor guidance lives in `agent/hats/governor.md`; deterministic hard blocks live in `agent/safety_rules.py` and compatibility review logic in `agent/archie_session.py`. `safety_rules.py` remains a thin no-LLM guard. Covered by specialist routing, safety, and artifact-manifest tests. |
| Management Summary | Complete | `agent/archie_session.py` renders management summaries from deterministic synthesis metadata, including applied skills, refinement count, review status, assumptions/tradeoffs, artifact refs, and checkpoint status. Clarification, recall, pending checkpoint, and answer-only paths suppress the summary. Covered by `tests/test_orchestrator_parallel_reply.py` and `tests/test_orchestrator_decision_flow.py`. |
| Synthesis | Complete | `agent/archie_session.py` deterministically consolidates applied skills, refinements, review status, tradeoffs, artifact refs, and critic/governor summaries without an extra LLM call. Covered by `test_synthesize_management_metadata_is_stable_and_complete`. |
| Fast Paths | Complete | Compatibility fast paths in `agent/archie_session.py` execute deterministic tool sequences without LLM freewrite and without full ReAct prompt assembly. Covered by fast-path tests in `tests/test_orchestrator_parallel_reply.py`; explicitly exempt from ReAct self-guidance checks because there is no ReAct prompt. |
| Archie Expert Review | Complete | `agent/archie_session.py` records Archie lens, sanitized specialist input, expert metadata, context source, and review verdict in tool traces. Deterministic BOM sizing review blocks hard mismatches before XLSX exposure. Covered by `test_execute_tool_bom_expert_review_blocks_undersized_retry`, `test_execute_tool_bom_expert_review_passes_matching_sizing`, and `test_artifact_manifest_hides_failed_review_bom_xlsx`. |
| Evidence Document | Complete | This file is the v1.9 completion evidence reference. |

## Deterministic Governor Rules

Security:

- Public ingress for compute/application workload without WAF or explicit
  justification produces `checkpoint_required`.
- Root compartment usage produces `checkpoint_required`.
- Missing encryption for block volume or database context produces
  `checkpoint_required`.

Cost:

- Estimated monthly cost more than 10% over `cost_max_monthly` produces
  `checkpoint_required`.
- Any single resource over 40% of the total budget records warning metadata in
  governor cost findings and reason codes only; it does not block by itself.

General:

- A high-risk Decision Context assumption with missing required input produces
  `checkpoint_required`.
- A directly contradicted requirement versus generated structured result data
  produces `blocked`; an unstructured contradiction signal produces
  `checkpoint_required`.
- Archie deterministic expert review is fail-closed for hard tool-result
  mismatches. BOM finalization compares explicit OCPU, RAM, and storage
  requirements against `bom_payload.line_items`; failed review blocks artifact
  manifest/download exposure even if the LLM critic is unavailable or
  fail-open.

## Implementation Pointers

- ReAct loop and expert review prompts: `skillforge/forge.py`.
- Archie tool wiring and required hats: `agent/archie_wiring.py`.
- Decision Context propagation, compatibility fast paths, deterministic
  synthesis, management summary, and expert artifact review:
  `agent/archie_session.py`.
- Deterministic no-LLM safety blocks: `agent/safety_rules.py`.
- Expert lenses: `agent/hats/*.md`.
- A2A sub-agent dispatch: `agent/sub_agent_client.py` and `sub_agents/*/server.py`.
