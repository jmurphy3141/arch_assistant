"""
tests/fixtures/hpc_oke_scenario.py
------------------------------------
Known-good scenario: HPC cluster on OKE with RDMA networking and FSS mount.

Source:
  Blog:      https://blogs.oracle.com/cloud-infrastructure/deploying-hpc-cluster-rdma-network-oke-fss-mount
  Diagram:   https://github.com/dezma/oci-hpc-oke/blob/main/Architecture/oci-hpc-arc.png
  Terraform: https://github.com/dezma/oci-hpc-oke/tree/main

BOM (from blog "Solution Components" section):
  - Bastion host:   VM.Standard.E4.Flex  — SSH jump host (public subnet)
  - Operator VM:    VM.Standard.E4.Flex  — Kubernetes API access / K9s (private subnet)
  - HPC Nodes (3):  BM.Optimized3.36     — 36 cores, 3.84TB NVMe, 100Gbps RDMA (worker subnet)
  - OCI File Storage (FSS)               — NFS PVC for Kubernetes pods
  - Block Volume                         — Boot volumes for compute nodes
  - Object Storage                       — Build artefacts, datasets
  - IAM                                  — Dynamic groups, instance principals
  - OKE Cluster:    Enhanced + Flannel   — Required for RDMA to function

This file is consumed by tests/test_hpc_oke_scenario.py and provides:
  - Customer notes text (replaces an Excel BOM for writing-agent tests)
  - A draw.io layout spec JSON (fed directly to spec_to_draw_dict)
  - Expected strings in each agent's output (for assertion)
  - Fake LLM responses that are faithful to the reference architecture
"""
import json

# ---------------------------------------------------------------------------
# Customer identity
# ---------------------------------------------------------------------------
CUSTOMER_ID   = "hpc_oke_reference"
CUSTOMER_NAME = "HPC OKE Reference"

# ---------------------------------------------------------------------------
# Notes text (what a customer or SE would upload for this engagement)
# ---------------------------------------------------------------------------
NOTES_TEXT = """\
Customer wants to deploy an HPC cluster on OCI OKE with RDMA networking for
tightly-coupled MPI workloads.

Architecture components:
- Bastion host: VM.Standard.E4.Flex shape, 1 OCPU 4GB RAM, used as SSH jump host
- Operator VM: VM.Standard.E4.Flex shape, used for Kubernetes API access with K9s
- HPC Nodes: 3x BM.Optimized3.36 bare metal instances (36 Intel Xeon 6354 cores,
  3.84-TB NVMe SSD, 100-Gbps RDMA network) in a cluster-network node pool
- OCI File Storage Service (FSS): NFS mount exposed as Kubernetes PersistentVolume
- Block Volume: Boot volumes for all compute instances (50 GB each)
- Object Storage: Datasets, job output, and container images
- IAM: Dynamic groups and instance principal policies for node access

OKE cluster requirements:
- cluster_type = "enhanced" (required for RDMA)
- cni_type = "flannel" (required for RDMA — do not use VCN-native CNI)
- kubernetes_version = "v1.29.1"
- cluster_name = "oke-rdma-cluster"

Reference Terraform: https://github.com/dezma/oci-hpc-oke
Uses oracle-terraform-modules/oke/oci v5.1.5 module.

HPC node pool config:
- cloud_init_content with OFED driver setup
- rdma_authentication_enabled = true
- rdma_auto_config_enabled = true
- 250 GB boot volume

Network layout:
- VCN "hpc" with dedicated subnets: bastion-pub, operator-prv, control-plane-prv,
  load-balancer-pub, worker-prv (HPC nodes)
- FSS in its own subnet with security list permitting NFS (TCP/UDP 2048-2050, 111)
""".encode("utf-8")

# ---------------------------------------------------------------------------
# Layout spec — fed directly to spec_to_draw_dict (legacy layers/groups format)
# Represents Figure 1 from the blog post architecture diagram
# ---------------------------------------------------------------------------
LAYOUT_SPEC = {
    "direction": "LR",
    "page": {"width": 1654, "height": 1169},
    "layers": {
        "external": [],
        "ingress": [
            {"id": "bastion_1", "type": "bastion", "label": "Bastion Host\nVM.Standard.E4.Flex"},
            {"id": "igw_1",     "type": "internet gateway", "label": "Internet\nGateway"},
        ],
        "compute": [
            {"id": "operator_1", "type": "compute",    "label": "Operator VM\nVM.Standard.E4.Flex"},
            {"id": "oke_1",      "type": "oke",         "label": "OKE Cluster\noke-rdma-cluster\nEnhanced / Flannel"},
            {"id": "hpc_1",      "type": "compute",     "label": "HPC Node 1\nBM.Optimized3.36\nRDMA"},
            {"id": "hpc_2",      "type": "compute",     "label": "HPC Node 2\nBM.Optimized3.36\nRDMA"},
            {"id": "hpc_3",      "type": "compute",     "label": "HPC Node 3\nBM.Optimized3.36\nRDMA"},
        ],
        "async": [],
        "data": [
            {"id": "fss_1",    "type": "file storage",   "label": "OCI File Storage\n(FSS / NFS PVC)"},
            {"id": "block_1",  "type": "block storage",  "label": "Block Volume\n50 GB boot"},
            {"id": "objstr_1", "type": "object storage", "label": "Object Storage\nDatasets / Output"},
        ],
    },
    "groups": [
        {"id": "pub_sub_box",    "label": "Public Subnet (bastion)",    "nodes": ["bastion_1", "igw_1"]},
        {"id": "prv_sub_box",    "label": "Private Subnet (operator)",  "nodes": ["operator_1", "oke_1"]},
        {"id": "worker_sub_box", "label": "Worker Subnet (RDMA)",       "nodes": ["hpc_1", "hpc_2", "hpc_3"]},
        {"id": "storage_sub_box","label": "Storage Subnet (FSS)",       "nodes": ["fss_1"]},
        {"id": "vcn_box",        "label": "VCN — hpc",                  "nodes": []},
    ],
    "edges": [
        {"source": "bastion_1",  "target": "operator_1", "label": "SSH"},
        {"source": "operator_1", "target": "oke_1",      "label": "kubectl"},
        {"source": "oke_1",      "target": "hpc_1",      "label": ""},
        {"source": "oke_1",      "target": "hpc_2",      "label": ""},
        {"source": "oke_1",      "target": "hpc_3",      "label": ""},
        {"source": "hpc_1",      "target": "fss_1",      "label": "NFS"},
        {"source": "hpc_2",      "target": "fss_1",      "label": "NFS"},
        {"source": "hpc_3",      "target": "fss_1",      "label": "NFS"},
    ],
}

# ---------------------------------------------------------------------------
# Expected strings — each agent's output must contain these
# (based on the known reference architecture)
# ---------------------------------------------------------------------------
EXPECTED_DIAGRAM_NODE_IDS = {
    "bastion_1", "operator_1", "oke_1", "hpc_1", "hpc_2", "hpc_3", "fss_1",
}

EXPECTED_TERRAFORM_STRINGS = [
    "BM.Optimized3.36",    # HPC node shape
    "VM.Standard.E4.Flex", # Bastion / operator shape
    "flannel",             # Required CNI for RDMA
    "enhanced",            # Required cluster type for RDMA
    "oke",                 # OKE resource or module reference
    "fss",                 # File Storage Service
    "rdma",                # RDMA config in some form
]

EXPECTED_POV_STRINGS = [
    "HPC",
    "RDMA",
    "OKE",
]

EXPECTED_JEP_STRINGS = [
    "BM.Optimized3.36",
    "OKE",
    "FSS",
]

EXPECTED_WAF_PILLARS = [
    "Operational Excellence",
    "Security",
    "Reliability",
    "Performance Efficiency",
    "Cost Optimization",
    "Sustainability",
]

# ---------------------------------------------------------------------------
# Fake LLM responses — faithful to the reference architecture
# Used by _hpc_oke_runner in the scenario tests (no real LLM needed)
# ---------------------------------------------------------------------------

FAKE_LAYOUT_SPEC_JSON = json.dumps(LAYOUT_SPEC)

FAKE_POV = """\
# HPC OKE Reference — Oracle Cloud Point of View

## Internal Visionary Press Release

### Summary
HPC OKE Reference deploys tightly-coupled MPI workloads on OCI OKE with 100-Gbps
RDMA networking, achieving bare-metal HPC performance with Kubernetes orchestration.

## Key Value Drivers

**RDMA at cloud scale.** BM.Optimized3.36 nodes provide 100-Gbps RDMA via cluster
networks — the same low-latency, kernel-bypass networking used in on-premises HPC.

**OCI File Storage for shared datasets.** FSS provides enterprise NFS, mounted as
Kubernetes PersistentVolumes so all HPC pods share a common scratch filesystem.

## OCI Services Used
- OKE Enhanced cluster (Flannel CNI for RDMA compatibility)
- BM.Optimized3.36 bare metal HPC nodes in cluster-network mode
- OCI File Storage Service (FSS) as NFS PVC
- Object Storage for job input/output
"""

FAKE_JEP = """\
# HPC Cluster on OKE — Joint Execution Plan
*Confidential — Oracle Restricted*

## Overview
Deploy 3-node BM.Optimized3.36 RDMA cluster on OCI OKE for MPI workloads.

## Success Criteria
- OKE cluster type: enhanced; CNI: flannel (required for RDMA)
- All 3 HPC nodes pass ib_write_bw RDMA bandwidth test
- FSS PVC mounted and accessible from all HPC pods
- MPI job completes across nodes via RDMA interconnect

## Timing
**POC Duration**: 14 days

## Steps
1. Provision VCN with bastion, operator, worker, and storage subnets
2. Deploy OKE enhanced cluster with Flannel CNI (kubernetes v1.29.1)
3. Add BM.Optimized3.36 cluster-network node pool (3 nodes, cloud-init OFED)
4. Configure FSS and create NFS PersistentVolume / PersistentVolumeClaim
5. Validate RDMA with ib_write_bw across all node pairs
6. Run customer MPI benchmark (HPL/HPCG)
"""

FAKE_TERRAFORM = """\
// FILE: main.tf
```hcl
terraform {
  required_providers {
    oci = { source = "oracle/oci", version = "~> 5.0" }
  }
  required_version = ">= 1.2.0"
}

module "oke" {
  source  = "oracle-terraform-modules/oke/oci"
  version = "5.1.5"

  tenancy_id     = var.tenancy_id
  compartment_id = var.compartment_id
  region         = var.region
  home_region    = var.home_region

  cluster_name    = "oke-rdma-cluster"
  cluster_type    = "enhanced"
  cni_type        = "flannel"
  kubernetes_version = var.kubernetes_version

  vcn_name = "hpc"
  subnets = {
    bastion   = { newbits = 13 }
    operator  = { newbits = 13 }
    cp        = { newbits = 13 }
    pub_lb    = { newbits = 11 }
    workers   = { newbits = 4  }
    fss       = { newbits = 11 }
  }

  bastion_shape  = { shape = "VM.Standard.E4.Flex", ocpus = 1, memory = 4, boot_volume_size = 50 }
  operator_shape = { shape = "VM.Standard.E4.Flex", ocpus = 1, memory = 4, boot_volume_size = 50 }

  node_pools = {
    hpc = {
      shape       = "BM.Optimized3.36"
      node_labels = { "oci.oraclecloud.com/node.info.backend.rdma" = "true" }
      node_count  = 3
      boot_volume_size = 250

      cloud_init_content = templatefile("cloud-init/ol8.sh", {})

      rdma_authentication_enabled = true
      rdma_auto_config_enabled    = true

      placement_configs = [{
        availability_domain = 1
        subnet_id           = module.oke.worker_subnet_id
      }]
    }
  }

  freeform_tags = {
    "managed-by" = "terraform"
    "customer"   = "HPC OKE Reference"
  }
}

resource "oci_file_storage_file_system" "hpc_shared" {
  compartment_id      = var.compartment_id
  availability_domain = var.availability_domain
  display_name        = "hpc-shared-fss"
  freeform_tags       = { "managed-by" = "terraform" }
}

resource "oci_file_storage_mount_target" "hpc_mt" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_id
  subnet_id           = module.oke.fss_subnet_id
  display_name        = "hpc-fss-mount-target"
}
```

// FILE: variables.tf
```hcl
variable "tenancy_id"          { description = "OCI Tenancy OCID" }
variable "compartment_id"      { description = "Compartment OCID" }
variable "region"              { default = "us-chicago-1" }
variable "home_region"         { default = "us-ashburn-1" }
variable "ssh_public_key"      { description = "SSH public key for bastion/operator/nodes" }
variable "ssh_private_key_path"{ description = "Local path to SSH private key" }
variable "hpc_shape"           { default = "BM.Optimized3.36" }
variable "kubernetes_version"  { default = "v1.29.1" }
variable "availability_domain" { default = "AD-1" }
```

// FILE: outputs.tf
```hcl
output "cluster_id"          { value = module.oke.cluster_id }
output "bastion_public_ip"   { value = module.oke.bastion_public_ip }
output "operator_private_ip" { value = module.oke.operator_private_ip }
output "fss_mount_target_ip" { value = oci_file_storage_mount_target.hpc_mt.ip_address }
```

// FILE: terraform.tfvars.example
```hcl
# tenancy_id           = "ocid1.tenancy.oc1...[TBD]"
# compartment_id       = "ocid1.compartment.oc1...[TBD]"
# ssh_public_key       = "ssh-rsa AAAA...[TBD]"
# ssh_private_key_path = "~/.ssh/id_rsa"
region              = "us-chicago-1"
home_region         = "us-ashburn-1"
hpc_shape           = "BM.Optimized3.36"
kubernetes_version  = "v1.29.1"
```
"""

FAKE_WAF = """\
# WAF Review — HPC OKE Reference

## Operational Excellence ✅
RDMA cluster deployed via Terraform with cloud-init automation. OKE enhanced cluster
with managed node pools simplifies day-2 operations. Bastion + operator pattern
provides clean separation of access and API management.

## Security ⚠️
Instance principal auth for nodes is configured. However, RDMA traffic between
BM.Optimized3.36 nodes bypasses the VCN security lists — RDMA network is isolated
at the cluster-network level. Recommend adding OCI Vault for SSH key management.

## Reliability ✅
3 HPC nodes in cluster-network provide redundancy for embarrassingly parallel
workloads. FSS provides highly available NFS; OKE control plane is managed.
Consider adding a second node pool in a separate AD for fault isolation.

## Performance Efficiency ✅
BM.Optimized3.36 with 100-Gbps RDMA delivers near bare-metal MPI latency
(<2 µs) and bandwidth (>90 Gbps). Flannel CNI is mandatory for RDMA pass-through.
FSS provides sufficient throughput for shared checkpoint files.

## Cost Optimization ⚠️
BM.Optimized3.36 is priced per-node at a premium. Recommend OCI Capacity
Reservations for sustained HPC workloads and leveraging pre-emptible instances
for non-critical batch jobs to reduce costs.

## Sustainability ✅
OCI Chicago region uses renewable energy. BM nodes deliver higher utilisation
per watt than virtualised alternatives for HPC workloads.

**Overall Rating: ✅**
"""
