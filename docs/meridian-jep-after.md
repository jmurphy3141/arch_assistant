# Joint Execution Plan — Meridian Health Plans

## Executive Summary

Meridian Health Plans and Oracle will execute a 25 days POC in us-chicago-1. This effort focuses on the architecture and components defined in the brief to produce validation evidence against the listed success criteria. The POC draws from the selected POC, approved diagram, and finalized BOM as controlled inputs, ensuring alignment with the agreed fact set.

The execution follows a structured three-phase approach, with defined owners committing time weekly. Phase 3 culminates in a joint review where approvals determine go/no-go based strictly on whether all criteria evidence meets the stated targets. Results remain evidence only, with fallback applied as agreed if needed.

## Objectives

- Validate: support 400 concurrent portal sessions
- Validate: keep p95 claims lookup under 600 milliseconds
- Validate: restore service within 45 minutes

## Scope

In scope: OCI WAF, Flexible Load Balancer, VM.Standard.E5.Flex, Oracle Base Database Service, File Storage, FastConnect, OCI IAM, Audit Logging, Monitoring.

Out of scope: Anything not explicitly listed in the grounded brief is out of scope.

## POC Architecture

The POC deploys OCI WAF in front of a public Flexible Load Balancer. IIS web, claims application, and Oracle database operate across separate private subnets. Integration to private hospital and identity systems routes through FastConnect and a DRG, with audit evidence required throughout. This setup references the approved diagram artifact, finalized BOM artifact from the selected POC, and the confirmed POC itself.

Controlled inputs like these artifacts ensure the POC remains tied to the validated brief. Evidence collection emphasizes audit logging and monitoring within the scoped services, supporting the test conditions for each criterion.

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

Oracle SA and Meridian security lead and Meridian application owner approve the Phase 3 evidence and sign the go/no-go record. Go requires every stated success criterion to be met; no-go applies the agreed fallback without claiming success. This process closes the POC with documented evidence tied to the measured results.