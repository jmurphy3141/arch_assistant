# tasks/

Each file is a self-contained unit of work for Codex.

## How to use

1. Read `PLAN.md` first. Understand the target architecture.
2. Pick the next `todo` task in the current phase.
3. Read the task file completely before writing a single line.
4. Implement only what the task says. Nothing more.
5. Run the acceptance criteria commands. All must pass.
6. Open a PR. Do not merge. Set status to `in-progress` in the task file header.

## Rules

- If the task conflicts with `PLAN.md`, stop. Flag it. Do not improvise.
- Do not modify `PLAN.md` or `AGENTS.md` or `CLAUDE.md` unless the task explicitly says to.
- Do not add error handling, logging, or abstractions not mentioned in the task.
- Do not clean up unrelated code while doing a task.
- One PR per task file.

## Status

| Task | Phase | Status |
|------|-------|--------|
| p0-fix-config.md | 0 | todo |
| p1-a2a-base.md | 1 | todo |
| p1-sub-agent-diagram.md | 1 | todo |
| p1-sub-agent-bom.md | 1 | todo |
| p1-sub-agent-pov.md | 1 | todo |
| p1-sub-agent-jep.md | 1 | todo |
| p1-sub-agent-waf.md | 1 | todo |
| p1-sub-agent-terraform.md | 1 | todo |
| p1-archie-client.md | 1 | todo |
