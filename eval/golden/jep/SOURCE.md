# JEP golden anchor

`JEP_template.docx` — the SE-provided canonical JEP (Proof of Concept Joint
Execution Plan). Used by the p78 quality harness as the golden exemplar for the
`jep` artifact type: it anchors the JEP rubric and the objective structural-
completeness check.

Canonical section structure (the JEP producer should hit these):
1. Overview
2. High Level Scope and Approach
3. Future State Architecture
4. POC Plan
5. Proof of Concept Test Cases
6. Success Criteria
7. Bill of Materials
8. POC Participants
9. Deliverables
10. Logistics

Note: the current jep_composer output diverges from this (it emits Executive
Summary / Objectives / Risk Registry / Approvals and omits Future State
Architecture, POC Test Cases, embedded BOM, POC Participants, Deliverables,
Logistics). Closing that gap is an explicit Phase 6 target, validated pairwise
against this anchor.
