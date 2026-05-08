# Phase 3.7 Codex Prompts

Run order: p37a alone → p37b + p37c + p37d + p37e + p37f in parallel.

---

## Prompt 1 — p37a: Hat Persistence (run first, alone)

```
Implement tasks/p37a-hat-persistence.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p37a origin/main

Prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Implement in skillforge/forge.py:

1. At the start of run_turn, restore active_hats and hat_rounds from context:
     known = set(self._hat_engine.load_hats().keys())
     active_hats = [h for h in context.get("_active_hats", []) if h in known]
     hat_rounds = dict(context.get("_hat_rounds", {}))

2. Before run_turn returns (before constructing TurnResult), persist back:
     context["_active_hats"] = active_hats
     context["_hat_rounds"] = hat_rounds

Verify:
  python3.11 -m compileall skillforge/forge.py
  grep "_active_hats" skillforge/forge.py   # at least 2 matches
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Also write a quick inline test:
  python3.11 -c "
import asyncio
from skillforge import Forge, SimpleMemory
import agent.hat_engine as hat_engine

async def runner(prompt, system, label=''):
    # simulate LLM activating bom_reviewer hat then approving
    if 'bom_reviewer' not in prompt:
        return '{\"tool\": \"use_hat_bom_reviewer\", \"args\": {}}'
    return 'reply: hello'

forge = Forge(
    base_system_prompt='You are helpful.',
    hat_engine=hat_engine,
    memory=SimpleMemory(),
    text_runner=runner,
)
ctx = {}
async def main():
    result = await forge.run_turn('s1', 'hello', ctx)
    assert '_active_hats' in ctx, 'hats not persisted'
    print('p37a OK — active_hats persisted:', ctx['_active_hats'])
asyncio.run(main())
"

Commit message: p37a: persist active_hats and hat_rounds across turns in context dict
Branch: claude/p37a (from main). Push when done.
```

---

## Prompt 2a — p37b: BOM SKU Fix + Live Catalog (run after p37a merges)

```
Implement tasks/p37b-bom-sku-fix.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p37b origin/main

Prerequisite check:
  python3.11 -m compileall agent/bom_parser.py agent/bom_service.py
  grep "B94176\|B93113" agent/bom_parser.py
  grep "cpu_sku" agent/bom_service.py | grep -v DEFAULT_PRICE | grep -v CPU_SKU_TO

Read tasks/p37b-bom-sku-fix.md in full before writing any code.

Implement (two files only):

1. agent/bom_parser.py — fix SKU_MAP:
   - Change B94176 comment from "E3/E4 OCPU" to "X9 (Intel Standard) OCPU"
   - Add B93113 ("E4 (AMD Standard) OCPU") and B93114 (None, None) entries

2. agent/bom_service.py — three changes:
   a. Add _build_shape_catalog(self, price_table) method as specified in the task.
      It must iterate price_table to find E4/E5/E6/X9/A1 CPU SKUs by matching
      keywords in the description field, not by hardcoded SKU lists.
   b. Fix _draft_bom_payload: replace the final else branch from "B94176" to
      "B93113" and add e4/e5/x9/intel text hints before the else.
   c. Fix _build_compute_from_structured: replace "B94176" default with "B93113".

Do NOT add new entries to DEFAULT_PRICE_TABLE (E4 is already there).
Do NOT modify sub_agents/bom/system_prompt.md.

Verify:
  python3.11 -m compileall agent/bom_parser.py agent/bom_service.py
  grep "B93113" agent/bom_parser.py
  grep "X9" agent/bom_parser.py        # comment on B94176 must say X9
  grep "cpu_sku.*B94176" agent/bom_service.py | grep -v DEFAULT | grep -v CPU_SKU_TO
  # above must return zero lines
  python3.11 -c "
from agent.bom_service import BomService, DEFAULT_PRICE_TABLE
s = BomService()
cat = s._build_shape_catalog(DEFAULT_PRICE_TABLE)
assert 'E4' in cat, f'Missing E4 in catalog: {cat[:200]}'
print('p37b catalog OK')
print(cat)
"
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p37b: default to E4.Flex SKU; build shape catalog from live price table
Branch: claude/p37b. Push when done.
```

---

## Prompt 2b — p37c: Diagram Instance Count (run after p37a merges, parallel with p37b)

```
Implement tasks/p37c-diagram-instance-count.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p37c origin/main

Prerequisite check:
  python3.11 -m compileall agent/bom_parser.py agent/intent_compiler.py
  grep "ServiceItem\|quantity" agent/bom_parser.py | head -20

Implement:

1. Add instance_count: Optional[int] = None to the ServiceItem dataclass
   in agent/bom_parser.py.

2. When parsing BOM rows, populate instance_count separately from quantity.
   Look for patterns: "N ×", "N x", "N instances", "qty: N" in the row text.
   Example: "2 × E4.Flex 2 OCPU" → instance_count=2, quantity=4.0

3. Find where the layout intent prompt is assembled (grep for
   "build_layout_intent_prompt" or the function that builds the LLM prompt
   for diagram generation). Update the node label to include count:
     "2 × E4.Flex" when instance_count=2 and instance_count > 1

Verify:
  python3.11 -m compileall agent/bom_parser.py agent/intent_compiler.py
  grep "instance_count" agent/bom_parser.py
  python3.11 -c "
from agent.bom_parser import ServiceItem
s = ServiceItem(id='c1', oci_type='compute', label='E4.Flex', layer='compute',
                quantity=4.0, instance_count=2)
assert s.instance_count == 2
print('ServiceItem OK')
"
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p37c: add instance_count to ServiceItem and propagate through layout intent prompt
Branch: claude/p37c. Push when done.
```

---

## Prompt 2c — p37d: Auto-Coordinate Hats (run after p37a merges, parallel with p37b/p37c)

```
Implement tasks/p37d-auto-coordinate-hats.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p37d origin/main

Verify p37a present:
  grep "_active_hats" skillforge/forge.py   # must match

Prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  grep "get_coordination_rules" skillforge/forge.py   # p35m must be present
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Implement in skillforge/forge.py:

1. Add auto_coordinate: bool = True parameter to Forge.__init__ and store as
   self._auto_coordinate.

2. Add _MANUAL_ONLY_HATS = {"critic", "governor"} module-level constant.

3. In run_turn, replace the existing coordination trigger check (from p35m that
   only emits status events) with auto-execution logic:
   - For each active hat, check coordination.triggers against user_message
   - If triggered, auto-activate recommended_hats (skipping _MANUAL_ONLY_HATS)
     and parallel_with hats via apply_hat
   - Log INFO + yield status TurnEvent for each auto-activation
   - Fall back to suggestion-only status events when auto_coordinate=False

Verify:
  python3.11 -m compileall skillforge/forge.py
  grep "auto_coordinate" skillforge/forge.py   # at least 3 matches
  grep "_MANUAL_ONLY_HATS" skillforge/forge.py
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p37d: auto-execute coordination rules — activate recommended and parallel hats without LLM call
Branch: claude/p37d. Push when done.
```

---

## Prompt 2d — p37e: POV Interview Mode (run after p37a merges, parallel with p37b/p37c/p37d)

```
Implement tasks/p37e-pov-interview-mode.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p37e origin/main

Prerequisite check:
  python3.11 -m compileall agent/pov_agent.py
  grep "generate_pov\|context_summary" agent/pov_agent.py | head -20

Implement in agent/pov_agent.py:

1. Add _context_is_sufficient(context_summary, new_notes_text) -> bool function.
   Returns False when combined text is under 150 chars or is boilerplate-only.

2. Add POV_DISCOVERY_QUESTIONS module-level string with 7 structured questions:
   customer challenge, current state, target workloads, success criteria,
   timeline, decision-makers, risks/objections.

3. At the top of generate_pov (after reading context and notes, before calling
   the LLM), add:
     if not _context_is_sufficient(context_summary, new_notes_text):
         return {"status": "need_clarification", "questions": POV_DISCOVERY_QUESTIONS, ...}

4. Check drawing_agent_server.py pov_generate handler — verify it already
   handles status=="need_clarification". If not, add the check.

Verify:
  python3.11 -m compileall agent/pov_agent.py
  grep "_context_is_sufficient" agent/pov_agent.py
  grep "POV_DISCOVERY_QUESTIONS" agent/pov_agent.py
  grep "need_clarification" agent/pov_agent.py
  python3.11 -c "
import asyncio
from agent.pov_agent import generate_pov
from agent.persistence_objectstore import InMemoryObjectStore
async def main():
    store = InMemoryObjectStore()
    result = await generate_pov(
        customer_id='test', customer_name='Test Co',
        store=store, text_runner=None, feedback=''
    )
    assert result.get('status') == 'need_clarification', f'Got: {result}'
    print('p37e OK')
asyncio.run(main())
"
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p37e: POV agent interview mode — ask discovery questions when context is sparse
Branch: claude/p37e. Push when done.
```

---

## Prompt 2e — p37f: Hat UI Events (run after p37a merges, parallel with p37b/p37c/p37d/p37e)

```
Implement tasks/p37f-hat-ui-events.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p37f origin/main

Verify p37a present:
  grep "_active_hats" skillforge/forge.py

Prerequisite check:
  python3.11 -m compileall skillforge/forge.py agent/chat_stream.py

Implement:

1. In skillforge/forge.py, in the run_turn ReAct loop:
   - After apply_hat succeeds, yield TurnEvent(type="hat_activate",
     data={"hat": hat_name, "display_name": self._hat_engine.get_hat_meta(hat_name).get("display_name", hat_name)})
   - After drop_hat succeeds, yield TurnEvent(type="hat_drop",
     data={"hat": hat_name})
   - Also yield hat_activate for auto-coordinated hats (from p37d if present,
     or wherever apply_hat is called for coordination)

2. In agent/chat_stream.py, in the event dispatch, add:
     elif event_type == "hat_activate":
         yield json.dumps({"type": "hat_activate", "hat": ..., "display_name": ...}) + "\n"
     elif event_type == "hat_drop":
         yield json.dumps({"type": "hat_drop", "hat": ...}) + "\n"

3. In ui/src/components/ChatInterface.tsx:
   - Add activeHats: string[] state (useState<string[]>([]))
   - Clear activeHats on new turn submit
   - In the streaming event handler, handle hat_activate (add to list) and
     hat_drop (remove from list)
   - Render a badge strip showing active hat names when activeHats is non-empty:
     blue rounded-full pills with the hat name (underscores → spaces)

Verify:
  python3.11 -m compileall skillforge/forge.py agent/chat_stream.py
  grep "hat_activate" skillforge/forge.py
  grep "hat_activate" agent/chat_stream.py
  grep "hat_activate" ui/src/components/ChatInterface.tsx
  grep "activeHats" ui/src/components/ChatInterface.tsx   # at least 3 matches
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p37f: hat_activate/hat_drop events — stream hat state to UI badge
Branch: claude/p37f. Push when done.
```
