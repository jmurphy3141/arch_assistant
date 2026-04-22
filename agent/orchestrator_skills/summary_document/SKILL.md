# Orchestrator Skill: Summary and Document Retrieval

## Intent
Ensure summary/document retrieval is valid, type-safe, and not misrepresented as successful generation when artifacts are missing.

## Preconditions
- Customer context is identified.
- For document retrieval, requested document type is supported.

## Input Validation Rules
- Allow `get_summary`.
- For `get_document`, allow only `pov`, `jep`, or `waf`.
- Block unsupported document types.

## Expected Output Contract
- Summary responses are coherent and context-relevant.
- Document retrieval returns actual content preview or clear non-availability status.

## Pushback Rules
- Block unsupported doc type requests.
- Block completion language that implies document exists when it does not.

## Escalation Questions Template
- Which document do you want: `pov`, `jep`, or `waf`?
- If missing, should I generate it now?
- Do you want latest summary before retrieving artifacts?

## Retry Guidance
- Retry `get_document` with supported type.
- Generate missing artifact first, then retry retrieval.
