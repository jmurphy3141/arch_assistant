# Archie Engagement Intelligence Plan

Last updated: 2026-06-01.

## North Star

Archie is not primarily an artifact factory. Archie is a proactive senior Oracle
SE teammate that helps work through customer issues between formal documents:
reviewing long meeting notes and transcripts, extracting what matters, identifying
risks and contradictions, asking the one question that unlocks progress, and
recommending the next best move.

Forge is the reusable orchestration engine. Archie is the Oracle SE
implementation and flagship proof point. Forge must stay domain-agnostic; Archie
owns Oracle-specific reasoning, hats, memory, handlers, and sub-agents.

## Locked Decisions

- `docs/archie-golden-spec.md` is the target behavioral contract when docs
  conflict.
- Archie field usefulness for Oracle SEs is the primary success target.
- "Jarvis-level" means proactive expert judgment, not autonomous artifact spam.
- Quality gates beat speed for customer-ready outputs.
- `/api/chat/stream` is the primary user experience.
- Generation requests must route through `forge.run_turn()` so hats, pre-action,
  post-review, critic, and governor gates run consistently.
- Engagement intelligence is a core Archie behavior, separate from the 10 domain
  artifact tools.
- The 10 domain artifact tools remain unchanged. `save_notes`, `get_summary`,
  and `get_document` are internal utility tools, not domain tools.
- Transcript/note intelligence should be implemented as an internal utility and
  memory path, not as a new sub-agent for the first version.
- Uploaded and pasted notes should support `.txt`, `.md`, `.pdf`, and `.docx`.
- Transcript analysis should use deterministic file text extraction plus chunked
  OCI LLM synthesis.
- Long note analysis should stream progress in chat; background mode is used when
  the SE explicitly toggles it.

## Target Behavior

When an SE pastes or uploads meeting notes, Archie should:

- Save the raw notes.
- Extract readable text from supported file formats.
- Analyze the content for the full SE picture: technical architecture, deal risk,
  stakeholders, success criteria, objections, next actions, and open questions.
- Persist structured engagement memory:
  - confirmed facts
  - constraints
  - stakeholders
  - decisions
  - risks
  - open questions
  - action items
  - deal signals
  - artifact readiness
  - light evidence snippets for major risks, decisions, and contentious
    interpretations
- Respond with a short signal brief:
  - What matters
  - Top risks
  - Open questions
  - Likely OCI direction
  - Recommended next move
- If the notes contain a contradiction or important gap, state it, explain the
  impact, and ask exactly one highest-leverage follow-up question.
- Avoid creating formal artifacts unless the SE asks for them or explicitly
  confirms that Archie should proceed.

## Staged Implementation

### Stage 1 - Document And Architecture Alignment

Goal: make the intended behavior explicit before code changes.

- Add Engagement Intelligence to `docs/archie-golden-spec.md` as a core Archie
  behavior outside the 10 domain artifact tools.
- Clarify that utility tools do not count against the 10 domain tool contract.
- Standardize the tech research tool naming in docs and code plan:
  `generate_tech_research` is canonical; compatibility aliases may remain only
  where required by existing tests or clients.
- Add an implementation note that Forge remains generic and should not gain
  Oracle SE vocabulary.

Acceptance:

- Golden spec describes engagement intelligence behavior and boundaries.
- Golden spec still states exactly 10 domain artifact tools.
- Existing utility tools are documented as internal support capabilities.

### Stage 2 - Strict Forge Path Cleanup

Goal: remove generation bypasses so the actual runtime follows the Golden spec.

- Remove deterministic generation workflow and parallel-plan bypasses from
  `agent/archie_session.py`.
- Keep session responsibilities in `archie_session.py`: history load/save,
  context persistence, pending confirmation state, and response packaging.
- Ensure all generation requests call `forge.run_turn()`.
- Keep compatibility only where it is true session management, not orchestration
  or tool dispatch.

Acceptance:

- Architecture guard tests prove BOM, diagram, WAF, Terraform, POV, JEP, POC,
  presentation, sales deck, and tech research generation requests call
  `forge.run_turn()`.
- Existing chat, artifact, and route compatibility tests remain green.

### Stage 3 - Note Text Extraction

Goal: make uploaded notes usable by Archie, not just stored as raw bytes.

- Add dependencies for `.pdf` and `.docx` extraction.
- Extract text from `.txt`, `.md`, `.pdf`, and `.docx` uploads.
- Store raw note bytes and extracted text separately or with clear metadata so
  legacy note listing remains compatible.
- Return upload metadata including detected type, extraction status, extracted
  character count, and any extraction warning.
- Fail gracefully when extraction is impossible: save the raw file and tell the
  SE the text could not be extracted.

Acceptance:

- Upload tests cover `.txt`, `.md`, `.pdf`, `.docx`, and unsupported binary
  files.
- `get_all_notes_text()` or its replacement returns readable extracted text for
  supported uploads.
- Existing `/api/notes/upload` clients continue to work.

### Stage 4 - Structured Engagement Memory

Goal: persist the signal Archie needs for later reasoning and artifact work.

- Add an internal engagement intelligence utility for chunked note analysis.
- Use the configured OCI LLM to synthesize structured signals from extracted
  notes.
- Persist structured memory fields under the existing context store without
  breaking current context readers.
- Include light evidence snippets only for major risks, decisions, or
  contentious interpretations.
- Expose the structured signals through `MemorySnapshot` so Forge and hats can
  reason from them.

Acceptance:

- Tests show long notes produce persisted facts, risks, decisions, open
  questions, actions, deal signals, and artifact readiness.
- Contradictory notes preserve the contradiction instead of silently choosing
  one side.
- Existing artifact handlers can still hydrate from memory.

### Stage 5 - Signal Brief In Chat

Goal: make the primary user experience feel like a senior SE reviewed the notes.

- After note ingestion or transcript paste, Archie returns a short structured
  signal brief.
- Stream status while chunk analysis runs.
- Use conversation hats such as discovery, deal coach, industry expert, and
  architecture reviewer where appropriate.
- Ask exactly one highest-leverage follow-up question when a gap or
  contradiction blocks progress.
- Do not generate formal artifacts unless the SE asks or confirms.

Acceptance:

- Chat tests cover pasted notes, uploaded notes, long transcript progress, and
  one-question gap handling.
- The default brief uses the agreed sections: What matters, Top risks, Open
  questions, Likely OCI direction, Recommended next move.
- The reply stays concise enough for field use.

### Stage 6 - Quality And Regression Gates

Goal: prevent the system from drifting back into artifact-first or bypass-heavy
behavior.

- Add prompt/static tests that assert Archie treats notes as engagement
  intelligence by default.
- Add tests that verify generation still routes through Forge after note
  ingestion.
- Add tests that verify utility tools are not counted as domain artifact tools.
- Run focused backend and UI tests for chat, notes, history, streaming, and
  artifact generation.

Acceptance:

- Deterministic test gates pass.
- Golden spec, README, and AGENTS guidance are consistent with the new behavior.
- No OCI or Archie-specific language is added to `skillforge/`.

## Test Gates

Use focused gates first, then broaden when touched surface justifies it.

```bash
python3.11 -m compileall drawing_agent_server.py agent server tests
pytest tests/test_archie_forge_wiring.py -v
pytest tests/test_chat_history_streaming.py -v
pytest tests/test_orchestrator_decision_flow.py -v
pytest tests/test_sub_agent_port_config.py -v
pytest server/tests/test_api.py -v
cd ui && npm run test -- ChatInterface
cd ui && npm run typecheck
```

For larger stage merges:

```bash
./scripts/test_pr_gate.sh -v
PROMPT_JUDGE_STRICT=0 ./scripts/test_nightly_prompt.sh -v
```

## Open Implementation Notes

- The current UI accepts `.docx` and `.pdf` notes, but backend extraction is not
  yet implemented.
- The current code has generation bypass logic in `agent/archie_session.py`; this
  conflicts with the strict Forge path decision.
- The current docs use both `generate_tech_report` and
  `generate_tech_research`; the canonical name should be settled during Stage 1.
- Existing dirty worktree state should be preserved. Implement each stage in a
  scoped patch and do not clean unrelated generated files or user changes.
