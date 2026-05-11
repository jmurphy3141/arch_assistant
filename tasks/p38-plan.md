# Phase 3.8 Plan — Expert Hat Quality Uplift

## Overall Approach

All six existing hats contain correct structure but thin domain knowledge. The
goal of Phase 3.8 is to make every expert perform at the level of a senior OCI
specialist by injecting deep, OCI-specific guidance directly into hat files —
with no Python changes. Each hat section (`Core Principles`, `Quality Bar`,
`Output Contract`, `Critic Evaluation Guidance`, `Failure Questions`) will be
rewritten to include concrete OCI service names, validation checklists, output
schemas, and coordination decision trees that the orchestrator can reason over
in every turn. Two entirely new hats (`oci_customer_pov_writer`,
`oci_jep_writer`) will be created. All hat files will be renamed to match the
`_MANDATORY_SKILL_FALLBACKS` keys in `archie_loop.py`.

---

## Scope

**Rename + upgrade (git mv + content rewrite):**
- `agent/hats/bom_reviewer.md`      → `agent/hats/oci_bom_expert.md`
- `agent/hats/diagram_builder.md`   → `agent/hats/diagram_for_oci.md`
- `agent/hats/terraform_reviewer.md`→ `agent/hats/terraform_for_oci.md`
- `agent/hats/waf_reviewer.md`      → `agent/hats/oci_waf_reviewer.md`

**Upgrade only (no rename):**
- `agent/hats/critic.md`
- `agent/hats/governor.md`

**Create new:**
- `agent/hats/oci_customer_pov_writer.md`
- `agent/hats/jep_writer.md`

**Why rename?**  
`archie_loop.py` `_MANDATORY_SKILL_FALLBACKS` uses `oci_bom_expert`,
`diagram_for_oci`, etc. to label selected skills in the orchestrator self-
guidance block. Aligning hat file stems with these names means the displayed
skill list exactly matches the active expert, reducing confusion.

---

## Task Breakdown

| Task | Files | Description |
|------|-------|-------------|
| p38a | `oci_bom_expert.md` | Rename + deep BOM/pricing expertise upgrade |
| p38b | `diagram_for_oci.md` | Rename + topology + draw.io constraint upgrade |
| p38c | `terraform_for_oci.md` | Rename + OCI HCL + state/module upgrade |
| p38d | `oci_waf_reviewer.md` | Rename + pillar scoring + compliance mapping |
| p38e | `oci_customer_pov_writer.md` | Create POV writer hat (new file) |
| p38f | `jep_writer.md` | Create JEP writer hat (new file) |
| p38g | `critic.md`, `governor.md` | Per-tool validation schema + OCI baseline upgrade |

**Run order:** p38a–p38g all in parallel (all different files).

---

## Acceptance Criteria (all tasks)

1. All 8 hat files compile/parse cleanly:
   ```bash
   python3.11 -c "import agent.hat_engine as h; hats = h.load_hats(); print(sorted(hats.keys()))"
   # must include: critic, diagram_for_oci, governor, jep_writer,
   # oci_bom_expert, oci_customer_pov_writer, oci_waf_reviewer, terraform_for_oci
   ```
2. Old hat names are gone:
   ```bash
   ls agent/hats/ | grep -E "bom_reviewer|diagram_builder|terraform_reviewer|waf_reviewer"
   # must return nothing
   ```
3. `use_hat_oci_bom_expert` appears in hat tool definitions:
   ```bash
   python3.11 -c "import agent.hat_engine as h; tools = h.get_hat_tool_definitions(); print([t['function']['name'] for t in tools])"
   ```
4. `pytest tests/ -q --tb=short` — same pass count
5. Manual spot check: each hat's Core Principles section contains at least one
   specific OCI service name (not generic cloud terms).
