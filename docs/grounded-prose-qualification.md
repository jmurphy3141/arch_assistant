# Grounded Generative JEP/POV Qualification

Qualification ran on 2026-06-30 against an isolated in-process current-source
stack. The main app and artifact reload checks shared an `InMemoryObjectStore`;
no synthetic qualification engagement was written to the production OCI bucket.

## Results

- General suite run `20260630T183213Z`: qualified, 5 consecutive passes.
- Complex three-tier run `20260630T184306Z`: qualified, 5 consecutive passes.
- Every POV Markdown, JEP Markdown, and JEP DOCX reload enabled the new content
  assertions and passed them.
- Assertions cover unsupported numeric/date/shape/service/claim tokens, internal
  object keys and file paths, exact owners and commitments, exact phase windows,
  exact success criteria, canonical structure, and substantive prose paragraphs.
- The existing latency, tool-gating, question-count, BOM, diagram, and
  conversational grounding gates remained enabled.

## Meridian Before/After

The comparison uses one validated `JepBrief` for both renderings:

- [Deterministic template before](meridian-jep-before.md)
- [Constrained generative rendering after](meridian-jep-after.md)

The constrained rendering passed on its second attempt after the first draft was
rejected by the stricter guard. The accepted draft had:

- zero grounding findings;
- zero internal artifact references;
- all three owners and commitments, all phase windows, and all success criteria
  preserved exactly;
- multiple materially revised prose paragraphs;
- 4,200 bytes versus 3,359 bytes for the deterministic template.

The factual tables and approval conditions remain unchanged. The prose adds
transitions and explanation around the executive summary and architecture without
adding a service, target, date, SLA, benchmark, customer claim, or storage path.
