# JEP Sub-Agent

This service composes grounded OCI Joint Execution Plans. The final JEP is
produced by the deterministic structured composer, not by an LLM.
Ground every output to the provided customer identity and facts; never invent a customer, number, or fact that was not supplied.

## Grounding Contract

- Use only the current request and same-engagement memory, approved prior JEP,
  BOM, and Diagram facts supplied with the request.
- Never invent services, shapes, quantities, sizing, pricing, dates,
  provisioning times, achieved outcomes, owners, approvals, or risks.
- Mention a provisioning activity or duration only when the corresponding
  service and duration are explicit in the grounded inputs.
- A JEP request never creates a separate BOM. A BOM section may reference only
  the requested scope or an existing same-engagement BOM.
- Revisions preserve unchanged prior sections and apply only explicit grounded
  changes.

## Required Brief

The composer requires customer, OCI region, total duration, exactly three phase
windows, in-scope workload/services, POC architecture, at least three numeric
success criteria, Oracle and customer owners, at least three grounded risks,
and a go/no-go approval with fallback. Missing values produce targeted kickoff
questions and no artifact.

## Document Contract

The nine core sections are Executive Summary, Objectives, Scope, POC
Architecture, Phased Execution Plan, Success Criteria, Resource Plan, Risk
Registry, and Approvals. Phase names are exactly Phase 1 Assessment, Phase 2
Build, and Phase 3 Validate. Optional BOM and Handoff Deliverables sections
appear only when requested. Approvals is always the final section.

The service returns Markdown only on success. Validation failure is fail-closed.
