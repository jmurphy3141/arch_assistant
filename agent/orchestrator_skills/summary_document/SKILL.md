# Orchestrator Skill: Summary and Document Retrieval

## Intent
Validate summary/document retrieval requests and prevent unsupported or missing-document completions.

## Preconditions
- Customer context is identified.
- For document retrieval, requested type is supported.

## Input Validation Rules
- Allow `get_summary`.
- For `get_document`, allow only `pov`, `jep`, or `waf`.

## Expected Output Contract
- Summary/doc retrieval returns coherent content or explicit non-availability.
- Non-availability must not be treated as completed document delivery.

## Pushback Rules
- Block unsupported document types.
- Block completion when requested document does not exist yet.

## Escalation Questions Template
- Which document type do you want: `pov`, `jep`, or `waf`?
- If missing, should I generate that document now?

## Retry Guidance
- Retry `get_document` with a supported type.
- Generate missing document first, then retrieve it.
