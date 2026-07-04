# Task: baseline integrity — measure terraform for real, add producer variance
Phase: 6
Status: todo

## Goal
Make the Phase 6 quality baseline trustworthy before any tuning decisions are made
on it. The committed baseline (`docs/subagent-quality.json`) has three integrity
gaps: (1) terraform was never actually validated (`terraform_cli_available: false`
— its `terraform_validate` fail means "unmeasured," not "failed"); (2)
`producer_runs: 1`, so the score distribution captures judge variance only, not
producer variance; (3) the recorded golden paths point at an ephemeral
`/tmp/...` checkout, and objective scores were rescored after a WAF parsing fix
without re-running the judge. Measurement environment only — no producer,
composer, or orchestrator changes.

Authorized by PLAN.md Decision #9 + Phase 6.

## Files to change
- `docs/subagent-quality.json` — replaced by the refreshed baseline output.
- (Only if a bug is found while running) `scripts/eval_subagent_quality.py` —
  minimal fixes to the harness itself, e.g. recording repo-relative golden paths
  instead of absolute ones. No rubric changes; no check-logic changes beyond path
  recording.

## Files to create
- None expected.

## Do not touch
- All producers: `sub_agents/**`, composers, `agent/**` orchestration
- `eval/rubrics/*.md` (changing rubrics would invalidate comparability)
- `eval/golden/**`
- `skillforge/forge.py`, config defaults (`agent_mode` stays forge)

## Already done (code half — verified)
- `load_golden_exemplar` now records repo-relative golden paths (fix in
  `scripts/eval_subagent_quality.py`; verified: `eval/golden/jep/JEP_template.docx`).
- `_terraform_validate` proven end-to-end with terraform v1.9.8: a valid bundle
  returns `Success! The configuration is valid.`; a broken bundle returns the real
  HCL error. No harness changes needed for the CLI — it inherits the environment.

## What to do (live half — eval host)
1. Install the terraform CLI on the isolated eval host (`terraform version` works).
   `terraform init -backend=false` must be able to install the `oracle/oci`
   provider: either the host can reach `registry.terraform.io`, or configure a
   filesystem mirror (verified recipe: download the provider zip into
   `<mirror>/registry.terraform.io/<namespace>/<name>/`, write a `terraformrc` with
   a `provider_installation { filesystem_mirror { path = "<mirror>" } }` block, and
   export `TF_CLI_CONFIG_FILE=<path to terraformrc>` — terraform picks it up with
   no harness changes).
2. From a CLEAN checkout of this repo (so golden paths resolve in-repo), bring up
   the isolated A2A stack and re-run the full baseline:
   `python scripts/eval_subagent_quality.py --runs 3 --judge-runs 3`
   (all six types; judge model distinct from producers, as enforced).
3. Verify the output records: `terraform_cli_available: true`, `producer_runs: 3`,
   repo-relative (or at least valid) golden paths, and consistent
   objective+subjective scoring from the same producer outputs (no post-hoc
   rescoring).
4. Commit the refreshed `docs/subagent-quality.json` and report the refreshed
   worst-to-best ranking vs the previous baseline (JEP 1.4 / TF 1.8 / Diagram 2.6 /
   BOM 3.2 / POV 4.0 / WAF 4.8).

## Acceptance criteria
- Refreshed `docs/subagent-quality.json`: `environment.terraform_cli_available`
  is true and the terraform `terraform_validate` check reflects a REAL validate
  run (pass or fail on content, not CLI absence).
- `producer_runs: 3` and `judge_runs: 3` recorded; subjective distributions span
  3 producer artifacts × 3 judgments per type.
- Golden paths in the JSON resolve from a clean checkout of this repo.
- No producer/composer/rubric files changed → `pytest -m "not live"` green.
- Report: refreshed ranking table + any rank changes vs the prior baseline.
