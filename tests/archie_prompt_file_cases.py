from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

import openpyxl


@dataclass(frozen=True)
class ArchiePromptFileCase:
    case_id: str
    expected_tool: str
    prompt: str


ARCHIE_PROMPT_FILE_CASES: tuple[ArchiePromptFileCase, ...] = (
    ArchiePromptFileCase(
        case_id="bom",
        expected_tool="generate_bom",
        prompt=(
            "Create an OCI XLSX bill of materials for Customer Apex Retail. "
            "Architecture: internet-facing 3-tier web app in us-ashburn-1 with WAF, "
            "public load balancer, 2 web servers, private database layer, Object Storage, "
            "and Block Volume storage. Include OCPU, memory, storage, WAF, load balancer, "
            "database, object storage, and block storage line items."
        ),
    ),
    ArchiePromptFileCase(
        case_id="diagram",
        expected_tool="generate_diagram",
        prompt=(
            "Generate a draw.io architecture diagram for Customer Apex Retail's OCI "
            "internet-facing 3-tier web app: WAF, load balancer, VCN with public and "
            "private subnets, 2 web servers, database subnet, Object Storage, Block Volume, "
            "internet gateway, NAT gateway, service gateway, and security lists."
        ),
    ),
    ArchiePromptFileCase(
        case_id="pov",
        expected_tool="generate_pov",
        prompt=(
            "Draft a customer POV for Apex Retail migrating the same 3-tier web app to OCI. "
            "Cover OCI migration approach, WAF and load balancer, security, Object Storage, "
            "Block Volume storage, database modernization, and business value."
        ),
    ),
    ArchiePromptFileCase(
        case_id="jep",
        expected_tool="generate_jep",
        prompt=(
            "Create a 14-day JEP/POC plan for Apex Retail's OCI migration of the same "
            "internet-facing 3-tier web app. Include overview, POC plan, success criteria, "
            "bill of materials, timeline, owners, risks, and handoff deliverables."
        ),
    ),
    ArchiePromptFileCase(
        case_id="waf",
        expected_tool="generate_waf",
        prompt=(
            "Run an OCI Well-Architected review for Apex Retail's internet-facing 3-tier "
            "architecture with WAF, load balancer, web tier, database, Object Storage, "
            "Block Volume, gateways, and security controls. Include all OCI WAF pillars and "
            "an overall rating."
        ),
    ),
    ArchiePromptFileCase(
        case_id="terraform",
        expected_tool="generate_terraform",
        prompt=(
            "Generate an OCI Terraform bundle for Apex Retail's 3-tier web app. Include "
            "main.tf, variables.tf, outputs.tf, terraform.tfvars.example, OCI provider, VCN, "
            "public/private subnets, gateways, security controls, load balancer/WAF evidence, "
            "compute web servers, database subnet, Object Storage, and Block Volume."
        ),
    ),
)


DETERMINISTIC_DRAWIO = """<mxfile>
  <diagram name="OCI 3 Tier">
    <mxGraphModel><root>
      <mxCell id="waf" value="OCI Web Application Firewall (WAF)" />
      <mxCell id="lb" value="Public Load Balancer" />
      <mxCell id="vcn" value="VCN 10.0.0.0/16 with public and private subnets" />
      <mxCell id="web" value="Web Tier: 2 × VM.Standard.E5.Flex Compute web servers" />
      <mxCell id="db" value="Private Database Subnet and OCI Database" />
      <mxCell id="obj" value="Object Storage bucket" />
      <mxCell id="block" value="Block Volume storage" />
      <mxCell id="igw" value="Internet Gateway" />
      <mxCell id="nat" value="NAT Gateway" />
      <mxCell id="sgw" value="Service Gateway" />
      <mxCell id="security" value="NSGs and Security Lists" />
    </root></mxGraphModel>
  </diagram>
</mxfile>"""

DETERMINISTIC_POV = """# Customer POV: Apex Retail OCI Migration

## Customer Context
Apex Retail will migrate its internet-facing 3-tier web app to OCI.

## OCI Migration Approach
The target uses OCI WAF, a public load balancer, private web and database tiers, Object Storage, and Block Volume storage.

## Security
Security controls include WAF policies, network security groups, private subnets, and least-privilege access.

## Business Value
OCI improves resilience, operational control, storage durability, and migration economics for the customer.
"""

DETERMINISTIC_JEP = """# Joint Execution Plan - Apex Retail OCI Migration

## Overview
Apex Retail will validate its OCI 3-tier application migration in a 14-day POC.

## High Level Scope and Approach
The POC includes WAF, load balancing, VCN networking, two web servers, a private database tier, Object Storage, and Block Volume. Production cutover is out of scope.

## Future State Architecture
Traffic passes through OCI WAF and a public load balancer to two web servers in private subnets. The web tier reaches the private database tier and OCI storage services.

## POC Plan
| Phase | Days | Activities | Exit Gate |
|-------|------|------------|-----------|
| Phase 1 - Assessment | Days 1-3 | Confirm tenancy access, quotas, connectivity, and architecture | Access and architecture sign-off complete |
| Phase 2 - Build | Days 4-9 | Deploy networking, WAF, load balancer, web tier, database tier, and storage | Workload ready for validation |
| Phase 3 - Validate | Days 10-14 | Measure criteria, record sign-off, run go/no-go review, and use fallback teardown if criteria fail | Customer approves the go/no-go decision |

### Risks and decision controls
| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| Tenancy quota delays compute provisioning | Medium | High | Confirm quota in Phase 1 | Oracle SA |
| Firewall rules block application traffic | Medium | High | Validate paths before Phase 2 | Apex Retail Lead |
| Database restore exceeds target | Low | High | Test backup and fallback in Phase 3 | Database Owner |

## Proof of Concept Test Cases
| Test Objective | Procedure | Evidence |
|----------------|-----------|----------|
| Web availability | Observe the validation window | Availability record |
| Application response time | Run the agreed workload test | Latency and throughput record |
| Recovery validation | Run the database restore | Elapsed restore-time record |

## Success Criteria
| Criterion | Target |
|-----------|--------|
| Web availability | >= 99.9% during the 5-day validation window |
| Application response time | < 500 ms at 100 requests/second |
| Recovery validation | Restore the database within 60 minutes |

## Bill of Materials
- WAF and load balancing
- Two web servers and a private database tier
- Object Storage and Block Volume

This section does not create or authorize a separate BOM workbook.

## POC Participants
| Organization | Role | Weekly Hours |
|--------------|------|--------------|
| Oracle | Solutions Architect | 8 hours |
| Apex Retail | Technical Lead | 8 hours |

## Deliverables
- Validation evidence for each success criterion
- Joint go/no-go and fallback record

## Logistics
| Topic | Grounded Plan |
|-------|---------------|
| Access | Confirm during Phase 1 |
| Communication cadence | [TBD] |
| Data transfer | [TBD] |
| Timing | 14 days |
"""

DETERMINISTIC_WAF = """# OCI Well-Architected Review: Apex Retail

## Security and Compliance
Use WAF, NSGs, private subnets, encryption, IAM policies, and audit logging.

## Reliability and Resilience
Use redundant load balancer paths, fault domains, backup policies, and recovery runbooks.

## Performance and Cost Optimization
Size compute OCPUs and memory conservatively, monitor utilization, and tune storage performance.

## Operational Efficiency
Use Terraform, tagging, monitoring, alarms, and documented handoff procedures.

## Distributed Cloud
Keep the current deployment single-region while documenting future DR and distributed cloud options.

## Overall Rating
Overall summary: ready for POC with medium residual risk.
"""

DETERMINISTIC_TERRAFORM_FILES = {
    "main.tf": """terraform {
  required_providers {
    oci = { source = "oracle/oci" }
  }
}

provider "oci" { region = var.region }

resource "oci_core_vcn" "app" { cidr_block = var.vcn_cidr compartment_id = var.compartment_ocid display_name = "apex-vcn" }
resource "oci_core_subnet" "public" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id cidr_block = "10.0.1.0/24" display_name = "public-lb-subnet" }
resource "oci_core_subnet" "web" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id cidr_block = "10.0.2.0/24" display_name = "private-web-subnet" }
resource "oci_core_subnet" "db" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id cidr_block = "10.0.3.0/24" display_name = "private-database-subnet" }
resource "oci_core_internet_gateway" "igw" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id display_name = "igw" }
resource "oci_core_nat_gateway" "nat" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id display_name = "nat" }
resource "oci_core_service_gateway" "sgw" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id services { service_id = var.object_storage_service_id } }
resource "oci_core_network_security_group" "web" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id display_name = "web-nsg" }
resource "oci_core_security_list" "lb" { compartment_id = var.compartment_ocid vcn_id = oci_core_vcn.app.id display_name = "lb-security-controls" }
resource "oci_load_balancer_load_balancer" "public" { compartment_id = var.compartment_ocid display_name = "public-lb" shape = "flexible" subnet_ids = [oci_core_subnet.public.id] }
resource "oci_waf_web_app_firewall_policy" "edge" { compartment_id = var.compartment_ocid display_name = "apex-waf-policy" }
resource "oci_core_instance" "web" { count = 2 compartment_id = var.compartment_ocid availability_domain = var.availability_domain shape = var.compute_shape display_name = "web-${count.index}" create_vnic_details { subnet_id = oci_core_subnet.web.id nsg_ids = [oci_core_network_security_group.web.id] } source_details { source_type = "image" source_id = var.image_ocid } }
resource "oci_objectstorage_bucket" "assets" { compartment_id = var.compartment_ocid namespace = var.object_storage_namespace name = "apex-assets" }
resource "oci_core_volume" "web_data" { compartment_id = var.compartment_ocid availability_domain = var.availability_domain size_in_gbs = 1024 display_name = "web-block-volume" }
""",
    "variables.tf": """variable "compartment_ocid" {}
variable "region" { default = "us-ashburn-1" }
variable "vcn_cidr" { default = "10.0.0.0/16" }
variable "availability_domain" {}
variable "image_ocid" {}
variable "compute_shape" { default = "VM.Standard.E5.Flex" }
variable "object_storage_namespace" {}
variable "object_storage_service_id" {}
""",
    "outputs.tf": """output "vcn_id" { value = oci_core_vcn.app.id }
output "load_balancer_id" { value = oci_load_balancer_load_balancer.public.id }
output "bucket_name" { value = oci_objectstorage_bucket.assets.name }
""",
    "terraform.tfvars.example": """compartment_ocid = "ocid1.compartment.oc1..example"
availability_domain = "example:US-ASHBURN-AD-1"
image_ocid = "ocid1.image.oc1.iad.example"
object_storage_namespace = "example"
object_storage_service_id = "ocid1.service.oc1.iad.objectstorage"
""",
}

DETERMINISTIC_BOM_PAYLOAD = {
    "line_items": [
        {"sku": "B94176", "description": "Compute OCPU for VM.Standard.E5.Flex web tier", "category": "Compute", "metric": "OCPU per hour", "quantity": 4, "instance_count": 2, "ocpu_per_instance": 2},
        {"sku": "B94177", "description": "Compute memory for VM.Standard.E5.Flex web tier", "category": "Compute", "metric": "GB memory per hour", "quantity": 64, "instance_count": 2, "gb_per_instance": 32},
        {"sku": "B93030", "description": "OCI Flexible Load Balancer", "category": "Networking", "metric": "Load balancer hour", "quantity": 1},
        {"sku": "BWAF01", "description": "OCI WAF policy for internet-facing application", "category": "Security", "metric": "Policy", "quantity": 1},
        {"sku": "B91628", "description": "Object Storage standard bucket", "category": "Storage", "metric": "GB month", "quantity": 1024},
        {"sku": "B91961", "description": "Block Volume storage", "category": "Storage", "metric": "GB month", "quantity": 2048},
        {"sku": "B91962", "description": "Block Volume performance units", "category": "Storage", "metric": "VPUs per GB", "quantity": 10},
        {"sku": "B99060", "description": "OCI Database service private DB layer", "category": "Database", "metric": "OCPU per hour", "quantity": 2},
    ],
    "totals": {"estimated_monthly_cost": 1000},
    "assumptions": ["Deterministic prompt-to-file E2E fixture."],
}


def assert_no_blocked_or_needs_input(body: dict) -> None:
    assert body.get("status") == "ok"
    assert not body.get("needs_input")
    for call in body.get("tool_calls", []) or []:
        status = str(call.get("result_status") or call.get("status") or "ok").lower()
        assert status not in {"needs_input", "blocked"}, call


def expected_tool_call(body: dict, expected_tool: str) -> dict:
    for call in body.get("tool_calls", []) or []:
        if call.get("tool") == expected_tool:
            return call
    raise AssertionError(f"expected tool {expected_tool!r} in {body.get('tool_calls')!r}")


def manifest_download(body: dict, artifact_type: str) -> dict:
    for item in (body.get("artifact_manifest", {}) or {}).get("downloads", []) or []:
        if item.get("type") == artifact_type:
            return item
    raise AssertionError(f"expected {artifact_type!r} download in artifact_manifest")


def assert_bom_xlsx(content: bytes) -> None:
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
    ws = wb.active
    rows = [
        ["" if value is None else str(value) for value in row]
        for row in ws.iter_rows(values_only=True)
    ]
    text = "\n".join(" ".join(row) for row in rows).lower()
    skus = {row[0].upper() for row in rows[1:] if row and row[0]}
    assert skus & {"B94176", "B97384", "B111129", "B93297", "B93113"}
    assert skus & {"B94177", "B97385", "B111130", "B93298", "B93114"}
    assert "B93030" in skus
    assert "B91628" in skus
    assert {"B91961", "B91962"} & skus
    assert "B99060" in skus or "database" in text
    assert "BWAF01" in skus or "waf" in text


def assert_diagram_drawio(content: str) -> None:
    assert "<mxfile" in content.lower()
    ET.fromstring(content)
    lowered = content.lower()
    for marker in ("waf", "load balancer", "vcn", "subnet", "web", "database", "object storage"):
        assert marker in lowered
    assert "block volume" in lowered or "storage" in lowered


def assert_markdown(case_id: str, content: str) -> None:
    lowered = content.lower()
    if case_id == "pov":
        for marker in ("customer", "oci migration", "waf", "load balancer", "security", "storage", "business value"):
            assert marker in lowered
    elif case_id == "jep":
        for marker in (
            "overview",
            "high level scope and approach",
            "future state architecture",
            "poc plan",
            "proof of concept test cases",
            "success criteria",
            "bill of materials",
            "poc participants",
            "deliverables",
            "logistics",
        ):
            assert marker in lowered
        assert "14-day" in lowered or "14 day" in lowered or "timeline" in lowered or "duration" in lowered
    elif case_id == "waf":
        for marker in (
            "security and compliance",
            "reliability and resilience",
            "performance and cost optimization",
            "operational efficiency",
            "distributed cloud",
        ):
            assert marker in lowered
        assert "overall" in lowered and ("rating" in lowered or "summary" in lowered)
    else:
        raise AssertionError(f"unknown markdown case {case_id!r}")


def assert_terraform_files(files: dict[str, str] | Callable[[str], str | bytes]) -> None:
    if callable(files):
        loaded = {name: files(name) for name in ("main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example")}
        files = {
            name: (content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content))
            for name, content in loaded.items()
        }
    for filename in ("main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example"):
        assert filename in files
        assert str(files[filename]).strip()
    main = files["main.tf"].lower()
    for pattern in (
        r'provider\s+"oci"',
        r"oci_core_vcn",
        r"oci_core_subnet",
        r"security|network_security_group|security_list|nsg",
        r"oci_core_instance",
        r"objectstorage|object_storage",
        r"oci_core_volume|block volume",
        r"load_balancer|waf",
    ):
        assert re.search(pattern, main), pattern
