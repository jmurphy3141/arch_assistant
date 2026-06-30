# Joint Execution Plan — Meridian Health Plans

## Executive Summary

Meridian Health Plans and Oracle will execute a 25 days POC in us-chicago-1 to validate the agreed scope and success criteria. Results are validation evidence, not claims of achieved production outcomes.

## Objectives

- Validate: support 400 concurrent portal sessions
- Validate: keep p95 claims lookup under 600 milliseconds
- Validate: restore service within 45 minutes

## Scope

In scope: OCI WAF, Flexible Load Balancer, VM.Standard.E5.Flex, Oracle Base Database Service, File Storage, FastConnect, OCI IAM, Audit Logging, Monitoring.

Out of scope: Anything not explicitly listed in the grounded brief is out of scope.

## POC Architecture

OCI WAF fronts a public Flexible Load Balancer; IIS web, claims application, and Oracle database run in separate private subnets. Private hospital and identity-system integration traverses FastConnect and a DRG; audit evidence is mandatory. It uses the approved same-engagement diagram, finalized same-engagement BOM, confirmed same-engagement POC as controlled inputs.

## Phased Execution Plan

| Phase | Window | Activities | Exit evidence |
|---|---|---|---|
| Phase 1 Assessment | Days 1-3 | Confirm the in-scope architecture, access, test method, owners, risks, and approvals. | Approved scope, architecture, and test plan. |
| Phase 2 Build | Days 4-21 | Configure the explicitly in-scope POC components and prepare the agreed tests. | Joint test-readiness record. |
| Phase 3 Validate | Days 22-25 | Run the agreed tests, record evidence, and conduct the joint go/no-go review. | Signed results and go/no-go record; apply the agreed fallback if criteria are not met. |

## Success Criteria

| Criterion | Evidence requirement |
|---|---|
| support 400 concurrent portal sessions | Record the measured result for the stated target and test condition. |
| keep p95 claims lookup under 600 milliseconds | Record the measured result for the stated target and test condition. |
| restore service within 45 minutes | Record the measured result for the stated target and test condition. |

## Resource Plan

| Organization | Owner | Commitment |
|---|---|---|
| Oracle | Oracle SA | 10 hours per week |
| Meridian Health Plans | Meridian security lead | 10 hours per week |
| Meridian Health Plans | Meridian application owner | 10 hours per week |

## Risk Registry

| Risk | Mitigation | Owner |
|---|---|---|
| The measured result may not meet: support 400 concurrent portal sessions | Preserve evidence and apply the agreed fallback if the criterion is not met. | Oracle SA / Meridian security lead / Meridian application owner |
| The measured result may not meet: keep p95 claims lookup under 600 milliseconds | Preserve evidence and apply the agreed fallback if the criterion is not met. | Oracle SA / Meridian security lead / Meridian application owner |
| The measured result may not meet: restore service within 45 minutes | Preserve evidence and apply the agreed fallback if the criterion is not met. | Oracle SA / Meridian security lead / Meridian application owner |

## Approvals

Oracle SA and Meridian security lead and Meridian application owner approve the Phase 3 evidence and sign the go/no-go record. Go requires every stated success criterion to be met; no-go applies the agreed fallback without claiming success.
