# Task: native engagement simulation (live)
Phase: 5
Status: done

## Goal
Live acceptance test for native mode: run one realistic, natural-language,
multi-meeting engagement (weeks of calls, notes, and questions) against
`agent_mode: native` and prove Archie converses, asks, remembers, delegates,
fetches, and never fabricates — using human phrasing only, no command/"magic-word"
syntax anywhere.

## Files to create
- `scripts/simulate_engagement_native.py` — the live harness described below.
- `docs/native-engagement-sim.json` — evidence log written by the harness.

## Files to change
- None committed as default. The harness runs against an ISOLATED stack with
  `agent_mode: native`; `main`/prod stay `forge`. Document the isolated bring-up
  in the script header. Do not commit native as the default in `config.yaml`.

## Do not touch
- `skillforge/forge.py` and the forge path
- `sub_agents/**` internals and the composers
- The Forge `excluded` set

## What to do
1. Bring up an isolated full stack (main server + all A2A sub-agents) with
   `orchestrator.agent_mode: native`. Do NOT change prod or the committed default.
2. Run the scenario below as sequential `POST /api/chat` turns under ONE
   engagement `customer_id` across multiple session ids ("meetings"), uploading
   notes between meetings via `POST /api/notes/upload`. Every turn is natural
   language — NEVER "Build only the BOM/XLSX: <sizing>" style phrasing.
3. Per turn capture: `reply`, `tool_calls` (names + args + artifact_key),
   `artifact_manifest`, `elapsed`. Write all of it to the evidence JSON.
4. Assert the per-turn INTENT (below). This is live and non-deterministic, so
   assert BEHAVIOR (which class of tool fired: generate vs lookup vs none) and the
   ABSENCE of fabrication — not exact strings.

## Scenario — Northwind Health (natural language, same intent, no magic words)

Meeting 1 (session `m1`) — first contact, sparse info:
1. "Just wrapped a first call with Northwind Health. They run a .NET member portal
   and are kicking around a move to OCI. Honestly that's about all I got today."
   → conversational; may ask a discovery question; NO generate_* tool; no invented facts.
2. "What would you want to find out before we meet again?"
   → conversational discovery advice; NO artifact; no fabricated specifics.
3. "Have we written anything up for them yet?"
   → a LOOKUP fires (get_document/list); reply says no; NO prose document.

Between meetings — notes:
4. Upload messy call notes via /api/notes/upload, then: "Dropped my call notes in —
   can you pull out what matters?"
   → notes saved/extracted; confirms; no facts invented beyond the notes.

Meeting 2 (session `m2`, days later) — more detail:
5. "Had our second call. Three tiers — IIS web, a claims app, an Oracle database.
   HIPAA is non-negotiable, and they're leaning toward us-chicago-1."
   → acknowledges, updates memory; conversational.
6. "Remind me everything we know about them so far."
   → recall from memory across both meetings + notes; grounded; no invented facts.
7. "So what's your gut on the architecture?"
   → conversational architecture judgment grounded in the stated facts; no
   fabricated numbers, SLAs, percentages, or customer evidence.

POV:
8. "I think we're ready — can you pull together a POV for them?"
   → generate_pov fires → real POV artifact; grounded.

Meeting 3 — POC:
9. "They liked the POV. What POC could we run to prove it out?"
   → POC options produced as a draft (generate_poc_plan); unknown duration,
   owners, commitments, criteria, and scope are `[TBD]`, never fabricated.
10. "Let's go with the second option."
    → records the selection; no downstream artifacts yet.

Build the POC:
11. "Great — can you get the architecture diagram going for that?"
    → generate_diagram fires → real .drawio (three tiers).
12. "We'll need a BOM too — figure a couple of web boxes, a few app servers, an HA
    Oracle database, and some file storage."
    → generate_bom fires → real .xlsx grounded to what was said; NO AI/token line items.
13. "Did that BOM slip in any Gen AI token costs?"
    → a LOOKUP fires (reads the stored BOM); answer derived from it; NO re-derived
    prose BOM.
14. "Last thing — put the JEP together for it."
    → generate_jep fires → real draft .docx; unknown logistics are `[TBD]`.

Wrap:
15. "Remind me what we've actually produced for Northwind so far."
    → a LOOKUP/list fires; reply lists the real artifacts (POV, diagram, BOM, JEP)
    with links; nothing fabricated.

## Acceptance criteria
- Conversational turns (1,2,5,7): no `generate_*` tool fired; reply contains no
  fabricated number/percentage/SLA/customer-evidence/AI-token claim, and no
  "E5/E6 = Ampere/Arm".
- Lookup turns (3,13,15): a fetch tool (get_document/get_summary/list_documents)
  fired and NO `generate_*` fired; reply reflects real store state; reply contains
  no artifact-shaped prose (no "| SKU", no invented "$/mo", no fake version string).
- Deliverable turns (8,9,11,12,14): the matching sub-agent fired and produced a
  real artifact whose key exists and reloads from object storage
  (.md/.drawio/.xlsx/.docx as appropriate). POC and JEP drafts contain `[TBD]`
  logistics; `needs_input` for missing logistics fails.
- Memory turns (6,15): facts/artifacts introduced in earlier meetings appear in
  the reply.
- Whole run: no HTTP 500s; every produced artifact reloads from object storage;
  max turn latency recorded.
- Evidence written to docs/native-engagement-sim.json; PASS requires every
  per-turn assertion above. Report per-turn PASS/FAIL and the overall verdict.
