# Phase 3.8 Codex Prompts — Expert Hat Quality Uplift

Run order: **p38a–p38g all in parallel** (all different files, no dependencies).

---

## Prompt 1 — p38a: BOM Expert Hat

```
Implement tasks/p38a-bom-hat-upgrade.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p38a origin/main

Prerequisite check:
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"
  ls agent/hats/

Step 1 — rename:
  git mv agent/hats/bom_reviewer.md agent/hats/oci_bom_expert.md

Step 2 — replace the ENTIRE file content with the new content specified in
tasks/p38a-bom-hat-upgrade.md. Read the spec carefully — it contains the
complete replacement file as a fenced code block. Write exactly that content.
Do NOT preserve any of the old bom_reviewer.md content.

Verify:
  python3.11 -c "
import agent.hat_engine as h
hats = h.load_hats()
assert 'oci_bom_expert' in hats, 'oci_bom_expert missing'
assert 'bom_reviewer' not in hats, 'bom_reviewer still present'
meta = h.get_hat_meta('oci_bom_expert')
assert meta.get('display_name') == 'OCI BOM Expert', f'Wrong display_name: {meta}'
print('p38a OK')
"
  grep "E4.Flex\|B93113\|730 hours\|XLSX" agent/hats/oci_bom_expert.md
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p38a: rename bom_reviewer → oci_bom_expert; deep OCI pricing/sizing upgrade
Branch: claude/p38a. Push when done.
```

---

## Prompt 2 — p38b: Diagram Architect Hat

```
Implement tasks/p38b-diagram-hat-upgrade.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p38b origin/main

Prerequisite check:
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"

Step 1 — rename:
  git mv agent/hats/diagram_builder.md agent/hats/diagram_for_oci.md

Step 2 — replace the ENTIRE file content with the new content specified in
tasks/p38b-diagram-hat-upgrade.md. Read the full spec before writing.

Verify:
  python3.11 -c "
import agent.hat_engine as h
hats = h.load_hats()
assert 'diagram_for_oci' in hats, 'diagram_for_oci missing'
assert 'diagram_builder' not in hats, 'diagram_builder still present'
print('p38b OK')
"
  grep "parent.*1\|flat.*draw\|IGW\|Public subnet\|instance_count" agent/hats/diagram_for_oci.md
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p38b: rename diagram_builder → diagram_for_oci; OCI topology + draw.io constraint upgrade
Branch: claude/p38b. Push when done.
```

---

## Prompt 3 — p38c: Terraform Expert Hat

```
Implement tasks/p38c-terraform-hat-upgrade.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p38c origin/main

Prerequisite check:
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"

Step 1 — rename:
  git mv agent/hats/terraform_reviewer.md agent/hats/terraform_for_oci.md

Step 2 — replace the ENTIRE file content with the new content specified in
tasks/p38c-terraform-hat-upgrade.md.

Verify:
  python3.11 -c "
import agent.hat_engine as h
hats = h.load_hats()
assert 'terraform_for_oci' in hats, 'terraform_for_oci missing'
assert 'terraform_reviewer' not in hats, 'terraform_reviewer still present'
print('p38c OK')
"
  grep "hashicorp/oci.*5\|locals\|freeform_tags\|tfvars.example\|Five required" agent/hats/terraform_for_oci.md
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p38c: rename terraform_reviewer → terraform_for_oci; OCI HCL + module patterns upgrade
Branch: claude/p38c. Push when done.
```

---

## Prompt 4 — p38d: WAF Reviewer Hat

```
Implement tasks/p38d-waf-hat-upgrade.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p38d origin/main

Prerequisite check:
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"

Step 1 — rename:
  git mv agent/hats/waf_reviewer.md agent/hats/oci_waf_reviewer.md

Step 2 — replace the ENTIRE file content with the new content specified in
tasks/p38d-waf-hat-upgrade.md.

Verify:
  python3.11 -c "
import agent.hat_engine as h
hats = h.load_hats()
assert 'oci_waf_reviewer' in hats, 'oci_waf_reviewer missing'
assert 'waf_reviewer' not in hats, 'waf_reviewer still present'
print('p38d OK')
"
  grep "maturity_score\|P1\|SOC 2\|OCI Vault\|Bastion Service" agent/hats/oci_waf_reviewer.md
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p38d: rename waf_reviewer → oci_waf_reviewer; pillar scoring + compliance mapping upgrade
Branch: claude/p38d. Push when done.
```

---

## Prompt 5 — p38e: POV Writer Hat (new)

```
Implement tasks/p38e-pov-hat-new.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p38e origin/main

Prerequisite check:
  ls agent/hats/ | grep pov   # should be absent
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"

Create agent/hats/oci_customer_pov_writer.md with the complete content specified
in tasks/p38e-pov-hat-new.md. The spec contains the full file content inside a
fenced code block — write exactly that content.

Verify:
  python3.11 -c "
import agent.hat_engine as h
hats = h.load_hats()
assert 'oci_customer_pov_writer' in hats, 'oci_customer_pov_writer missing'
meta = h.get_hat_meta('oci_customer_pov_writer')
assert meta.get('display_name') == 'OCI POV Writer', f'Wrong: {meta}'
print('p38e OK')
"
  grep "discovery mode\|need_clarification\|Exadata\|measurable\|seven discovery" agent/hats/oci_customer_pov_writer.md
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p38e: create oci_customer_pov_writer hat — discovery mode, OCI competitive narrative
Branch: claude/p38e. Push when done.
```

---

## Prompt 6 — p38f: JEP Writer Hat (new)

```
Implement tasks/p38f-jep-hat-new.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p38f origin/main

Prerequisite check:
  ls agent/hats/ | grep jep   # should be absent
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"

Create agent/hats/jep_writer.md with the complete content specified in
tasks/p38f-jep-hat-new.md.

Verify:
  python3.11 -c "
import agent.hat_engine as h
hats = h.load_hats()
assert 'jep_writer' in hats, 'jep_writer missing'
meta = h.get_hat_meta('jep_writer')
assert meta.get('display_name') == 'JEP Writer', f'Wrong: {meta}'
print('p38f OK')
"
  grep "kickoff\|SMART\|Phase 1.*Assessment\|Phase 2.*Build\|risk registry" agent/hats/jep_writer.md
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p38f: create jep_writer hat — kickoff flow, SMART criteria, phased POC execution plan
Branch: claude/p38f. Push when done.
```

---

## Prompt 7 — p38g: Critic + Governor Upgrade

```
Implement tasks/p38g-critic-governor-upgrade.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p38g origin/main

Prerequisite check:
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"

Replace the ENTIRE content of agent/hats/critic.md with the new critic content
specified in tasks/p38g-critic-governor-upgrade.md.

Replace the ENTIRE content of agent/hats/governor.md with the new governor
content specified in tasks/p38g-critic-governor-upgrade.md.

Both replacements are specified as fenced code blocks in the task spec.
Read the full spec before writing either file.

Verify:
  python3.11 -c "
import agent.hat_engine as h
hats = h.load_hats()
assert 'critic' in hats
assert 'governor' in hats
print('p38g hat parse OK')
"
  grep "Per-Tool Validation Schema" agent/hats/critic.md
  grep "artifact_key\|bom_payload\|drawio_xml\|ocid1" agent/hats/critic.md
  grep "Hard Blocks\|Root compartment\|port 22\|GPU shape\|Cost overrun" agent/hats/governor.md
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p38g: critic per-tool validation schemas; governor OCI security baselines + hard blocks
Branch: claude/p38g. Push when done.
```
