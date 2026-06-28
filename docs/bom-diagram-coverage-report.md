# BOM–Diagram Coverage Report

Date: 2026-06-28 GMT
Scope: same-engagement BOM→Diagram parity Cycles 2-5 for Apex Retail

## Executive Result

Every BOM service family and requested quantitative attribute is represented in
the draw.io diagram. The Diagram was generated after the BOM under the same
engagement ID, using the persisted BOM baseline as an authoritative parity
contract.

- Service-family coverage: **8 of 8 BOM rows represented**
- Exact quantitative/semantic coverage: **8 of 8 full**
- Overall verdict: **Pass**
- End-to-end BOM-derived Diagram assurance: **Proven across four consecutive cycles**

## Line-by-Line Mapping

| BOM element | BOM value | Diagram evidence | Status |
|---|---:|---|---|
| E5 compute OCPU | 2 servers, 4 OCPU total | `2 × VM.Standard.E5.Flex Web Servers — 2 OCPU / 16 GB each` | Full |
| E5 compute memory | 32 GB total | Same compute label shows 16 GB on each of two servers | Full |
| Balanced Block Volume storage | 500 GB | `500 GB Balanced Block Volume` | Full |
| Block Volume performance | 5,000 performance units; 10 VPU/GB | `5000 Performance Units` on the Block Volume label | Full |
| Flexible Load Balancer | 1 | `Load Balancer ×1 (per region)` | Full |
| Web Application Firewall policy | 1 | `OCI WAF Policy ×1` | Full |
| PostgreSQL database | 2 OCPU | `Private PostgreSQL Database — 2 OCPU` | Full |
| Object Storage | 1,024 GB / 1 TB | `1024 GB Object Storage` | Full |

## Additional Diagram Elements

The diagram also contains the required network context that is not separately
priced in the BOM: VCN, public/private subnets, Internet Gateway, NAT Gateway,
Service Gateway, route tables, NSGs, one Availability Domain, three Fault
Domains, and the public Internet. The primary flow is encoded as Internet → WAF
→ Flexible Load Balancer → web tier → PostgreSQL.

## Enforced Controls

1. The successful BOM payload is persisted into canonical engagement memory.
2. Diagram generation receives normalized authoritative BOM parity requirements.
3. The Diagram specialist renders Block Volume performance, Object Storage
   capacity, and WAF policy labels deterministically.
4. A fail-closed XML coverage gate checks service families and quantitative
   attributes before the diagram is exposed.
5. Four consecutive same-engagement cycles passed. BOM latency was 0.42-0.58
   seconds; Diagram latency was 0.58-1.59 seconds.

## Evidence Sources

- Primary evidence engagement: `bom_diagram_parity_cycle2_20260628`
- BOM workbook: `oci-bom-20260628-185721-1-e42fc75d.xlsx`
- Diagram artifact:
  `f44019f4-195b-48c3-a5f5-d5030001548d/v1/diagram.drawio`
- Repeated evidence engagements: `bom_diagram_parity_cycle3_20260628` through
  `bom_diagram_parity_cycle5_20260628`

## Historical Finding

The earlier Cycle 22 comparison used separate engagement IDs and showed three
partial mappings. That result motivated the same-engagement persistence and
fail-closed parity controls documented above.
