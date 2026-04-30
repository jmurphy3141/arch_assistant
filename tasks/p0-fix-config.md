# Task: Fix stale git_push branch in config.yaml
Phase: 0
Status: todo

## Goal
Update the server's auto-push branch so generated diagrams commit to main,
not a stale development branch.

## Files to change
- `config.yaml` — one line change

## Files to NOT touch
Everything else.

## What to do

In `config.yaml` find:
```yaml
git_push:
  branch: "claude/webapp-fastapi-tests-sWH4S"
```

Change to:
```yaml
git_push:
  branch: "main"
```

## Acceptance criteria
- `grep 'branch:' config.yaml` returns `branch: "main"`
- `python3.11 -m compileall drawing_agent_server.py agent` exits 0
- `pytest tests/test_runtime_config.py -v` passes
