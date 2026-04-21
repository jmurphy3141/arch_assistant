#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Deterministic PR gate: excludes prompt_judge and live suites.
pytest tests server/tests -m "(unit or integration or system or e2e or prompt_static) and not live and not prompt_judge" "$@"
