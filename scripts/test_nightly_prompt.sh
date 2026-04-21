#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Nightly/manual lane: deterministic + prompt_judge, optionally including live.
RUN_PROMPT_JUDGE=1 pytest tests server/tests -m "unit or integration or system or e2e or prompt_static or prompt_judge" "$@"

if [[ "${RUN_LIVE_TESTS:-0}" == "1" ]]; then
  RUN_PROMPT_JUDGE=1 pytest tests -m "live" "$@"
fi
