# p53 — infra-tech-research: New SkillForge Hat + Sub-Agent

## Context

An AE needs to research infrastructure options before committing to a BOM or
diagram. Currently Archie can generate artifacts but cannot first produce a
structured **technology assessment** — "which OCI services, which architecture
pattern, which sizing approach fits this workload?"

This series creates the `infra_tech_research` expert hat and `generate_tech_report`
tool so Archie can wear a Research hat, call a specialist sub-agent, and produce a
senior-level infrastructure research report that feeds directly into BOM (sizing
hints), Diagram (architecture pattern), and POV (business narrative).

The hat is the **first stage of the engagement lifecycle**:
`generate_tech_report → generate_bom → generate_diagram → generate_waf → generate_terraform → generate_pov → generate_jep`

**Files already created (do not recreate):**
- `gstack_skills/infra_tech_research/SKILL.md` — canonical skill specification
- `agent/hats/infra_tech_research.md` — hat file (auto-discovered by hat_engine)
- `sub_agents/tech_research/__init__.py`, `config.yaml`, `server.py`, `system_prompt.md`, `README.md`
- `agent/tools/specialists.py` — `TechResearchHandler` class added
- `agent/archie_wiring.py` — `generate_tech_report` registered, sequencing rules updated
- `agent/hats/oci_bom_expert.md` — research triggers + parallel_with updated
- `agent/hats/diagram_for_oci.md` — research triggers updated
- `agent/hats/oci_customer_pov_writer.md` — priority_fields updated

---

## Task p53a — Smoke-test hat discovery and wiring

```
Context: The infra_tech_research hat has been added to agent/hats/ and wired into
archie_wiring.py on branch claude/explore-repo-Os53i. Verify everything is
discoverable and the module compiles.

IMPORTANT: Branch from claude/explore-repo-Os53i (NOT origin/main — all p53 files
live on this branch).

  git fetch origin
  git checkout -b claude/p53a origin/claude/explore-repo-Os53i

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/tools/specialists.py
  python3.11 -m py_compile agent/archie_wiring.py
  python3.11 -m py_compile sub_agents/tech_research/server.py

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  hats = he.load_hats()
  assert 'infra_tech_research' in hats, f'FAIL: hat not discovered. Found: {list(hats.keys())}'
  print('PASS: infra_tech_research hat discovered')
  rules = he.get_hat_rules('infra_tech_research')
  triggers = rules.get('when_to_activate', [])
  assert any('research' in t for t in triggers), f'FAIL: no research trigger. Got: {triggers}'
  print(f'PASS: {len(triggers)} activation triggers')
  mem = he.get_memory_focus('infra_tech_research')
  assert 'workload_pattern' in mem.get('priority_fields', []), 'FAIL: missing workload_pattern'
  print('PASS: memory focus includes workload_pattern')
  coord = he.get_coordination_rules('infra_tech_research')
  assert 'oci_bom_expert' in coord.get('recommended_hats', []), 'FAIL: missing BOM coordination'
  print('PASS: coordination → oci_bom_expert')
  print('All hat checks passed.')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from agent.tools.specialists import TechResearchHandler
  print('PASS: TechResearchHandler importable')
  mro_names = [c.__name__ for c in TechResearchHandler.__mro__]
  assert '_SpecialistHandler' in mro_names, f'FAIL: not a subclass. MRO: {mro_names}'
  print('PASS: TechResearchHandler extends _SpecialistHandler')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from unittest.mock import MagicMock
  store = MagicMock()
  runner = MagicMock()
  from agent.archie_wiring import build_forge
  forge = build_forge(store=store, customer_id='test', customer_name='Test',
                      text_runner=runner, step3_planning=False)
  tools = list(forge._registry.names())
  assert 'generate_tech_report' in tools, f'FAIL: tool not registered. Got: {tools}'
  print('PASS: generate_tech_report registered in Forge')
  required = ['generate_tech_report', 'generate_bom', 'generate_diagram',
              'generate_pov', 'generate_jep', 'generate_waf', 'generate_terraform']
  for t in required:
      assert t in tools, f'FAIL: {t} missing. Got: {tools}'
      print(f'PASS: {t} registered')
  spec = forge._registry.get('generate_tech_report')
  assert getattr(spec, 'requires_hat', None) == 'infra_tech_research', f'FAIL: requires_hat wrong: {getattr(spec, \"requires_hat\", None)}'
  print('PASS: requires_hat = infra_tech_research')
  assert getattr(spec, 'memory_contract', False), 'FAIL: memory_contract not set'
  print('PASS: memory_contract = True')
  assert getattr(spec, 'critique_enabled', False), 'FAIL: critique_enabled not set'
  print('PASS: critique_enabled = True')
  "

  # Verify coordination updates in existing hats
  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  coord = he.get_coordination_rules('oci_bom_expert')
  parallel = coord.get('parallel_with', [])
  assert 'infra_tech_research' in parallel, f'FAIL: infra_tech_research not in BOM parallel. Got: {parallel}'
  print('PASS: BOM hat coordination updated')
  rules = he.get_hat_rules('diagram_for_oci')
  triggers = rules.get('when_to_activate', [])
  assert any('research' in t for t in triggers), f'FAIL: no research trigger in diagram hat. Got: {triggers}'
  print('PASS: Diagram hat updated')
  mem = he.get_memory_focus('oci_customer_pov_writer')
  assert 'workload_pattern' in mem.get('priority_fields', []), 'FAIL: missing workload_pattern in POV memory_focus'
  print('PASS: POV hat updated')
  print('All coordination checks passed.')
  "

  # Existing tests still pass
  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -15

One PR. Do not modify any other files.
```

---

## Task p53b — Sub-agent config registration

```
Context: The tech_research sub-agent is in sub_agents/tech_research/ on port 8087.
The main server registers sub-agents in config.yaml or drawing_agent_server.py.
Check both locations for how existing sub-agents (pov port 8084, jep, waf) are
registered and add tech_research on port 8087 using the same pattern.

IMPORTANT: Branch from claude/explore-repo-Os53i (same base as p53a — all p53
files live there).

  git fetch origin
  git checkout -b claude/p53b origin/claude/explore-repo-Os53i

Search for where pov sub-agent is registered (port 8084). The pattern is likely:
  grep -n "8084\|pov.*port\|sub_agent.*port" config.yaml drawing_agent_server.py

Add tech_research in the same location:
  name: tech_research
  port: 8087

Run acceptance criteria:

  # Config loads without error
  python3.11 -c "
  import yaml
  cfg = yaml.safe_load(open('config.yaml'))
  # Find tech_research in whatever structure sub-agents are registered
  cfg_str = str(cfg)
  assert '8087' in cfg_str or 'tech_research' in cfg_str, \
      'FAIL: tech_research not found in config.yaml'
  print('PASS: tech_research registered in config')
  "

  # Server imports without error
  python3.11 -m py_compile drawing_agent_server.py

  # Existing tests still pass
  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files beyond config.yaml and drawing_agent_server.py.
```

---

## Task p53c — End-to-end integration test

```
Context: The generate_tech_report tool is now registered in Forge with requires_hat
infra_tech_research. Write a pytest test that verifies the full Forge loop activates
the correct hat and returns a ToolResult with artifact_key.

File to create: tests/test_tech_research_forge.py

IMPORTANT: Branch from claude/explore-repo-Os53i (all p53 files live there).

  git fetch origin
  git checkout -b claude/p53c origin/claude/explore-repo-Os53i

Note: forge._registry is a ToolRegistry object. Use forge._registry.names() to
list registered tool names and forge._registry.get('tool_name') to fetch a spec.
Do NOT call forge._registry.keys() — that method does not exist.

Follow the pattern from tests/test_archie_forge_wiring.py (Forge wiring tests).
The test should:
1. Build a Forge instance via build_forge() with a mock store and mock text_runner.
2. Assert 'generate_tech_report' in list(forge._registry.names()).
3. Assert forge._registry.get('generate_tech_report').requires_hat == 'infra_tech_research'.
4. Assert forge._registry.get('generate_tech_report').memory_contract is True.
5. Assert forge._registry.get('generate_tech_report').critique_enabled is True.

No live LLM calls. Use MagicMock for store and text_runner.

Run acceptance criteria:

  pytest tests/test_tech_research_forge.py -v

  python3.11 -m py_compile tests/test_tech_research_forge.py

One PR. Only create tests/test_tech_research_forge.py.
```

---

## Verification (End-to-End)

```bash
# 1. Syntax checks
python3.11 -m compileall agent/ sub_agents/tech_research/ skillforge/ -q

# 2. Hat discovery
python3.11 -c "
import sys; sys.path.insert(0, '.')
import agent.hat_engine as he
hats = he.load_hats()
for h in ['infra_tech_research', 'oci_bom_expert', 'diagram_for_oci']:
    assert h in hats, f'FAIL: {h} not discovered'
    print(f'PASS: {h} discovered')
"

# 3. Forge wiring
python3.11 -c "
import sys; sys.path.insert(0, '.')
from unittest.mock import MagicMock
forge = __import__('agent.archie_wiring', fromlist=['build_forge']).build_forge(
    store=MagicMock(), customer_id='test', customer_name='Test',
    text_runner=MagicMock(), step3_planning=False
)
tools = list(getattr(forge, '_registry', {}).keys())
required = ['generate_tech_report', 'generate_bom', 'generate_diagram',
            'generate_pov', 'generate_jep', 'generate_waf', 'generate_terraform']
for t in required:
    assert t in tools, f'FAIL: {t} missing'
    print(f'PASS: {t} registered')
"

# 4. Sub-agent config
python3.11 -c "
import yaml
cfg = yaml.safe_load(open('sub_agents/tech_research/config.yaml'))
assert cfg['port'] == 8087
assert cfg['name'] == 'tech_research'
assert cfg['llm']['max_tokens'] == 6000
print('PASS: tech_research sub-agent config valid')
"

# 5. Existing tests pass
pytest tests/ -q --tb=short -m 'not live' 2>&1 | tail -10
```

---

## Run Order

```
p53a (smoke tests)  →  p53b (sub-agent registration)  →  p53c (integration test)
```

p53a and p53b are **independent** — run in parallel.
p53c requires p53a and p53b.

## Critical Files

| File | Task | Change |
|------|------|--------|
| `gstack_skills/infra_tech_research/SKILL.md` | done | New file |
| `agent/hats/infra_tech_research.md` | done | New file |
| `sub_agents/tech_research/__init__.py` | done | New empty file |
| `sub_agents/tech_research/config.yaml` | done | New file, port 8087 |
| `sub_agents/tech_research/system_prompt.md` | done | New file |
| `sub_agents/tech_research/server.py` | done | New file (follows pov pattern) |
| `sub_agents/tech_research/README.md` | done | New file |
| `agent/tools/specialists.py` | done | Added TechResearchHandler |
| `agent/archie_wiring.py` | done | Import + register generate_tech_report |
| `agent/hats/oci_bom_expert.md` | done | Added research triggers + parallel_with |
| `agent/hats/diagram_for_oci.md` | done | Added research triggers |
| `agent/hats/oci_customer_pov_writer.md` | done | Added priority_fields |
| `config.yaml` or `drawing_agent_server.py` | p53b | Register tech_research sub-agent on port 8087 |
| `tests/test_tech_research_forge.py` | p53c | New integration test |
