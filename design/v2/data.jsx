// ============================================================
// Archie — engagement data (≈20 concurrent engagements)
// ============================================================
const ENGAGEMENTS = [
  {
    id: "acme", name: "Acme Corp", focus: "Oracle DB migration",
    challenge: "Migrating a 40TB on-prem Oracle estate to OCI with a zero-downtime cutover; needs active-active HA and cross-region DR.",
    services: ["OCI Database", "Exadata", "Object Storage", "VCN", "Bastion", "Data Guard"],
    status: "live", hat: "BOM Expert", task: "Running generate_bom…", updated: "now", stage: "Sizing",
    artifacts: [
      { id: "a-bom", icon: "▦", name: "acme_oci_bom_v2.xlsx", hat: "BOM Expert", time: "just now", fresh: true, kind: "Excel workbook", size: "52 KB", rows: "38 line items" },
      { id: "a-dgm", icon: "◫", name: "acme_landing_zone.drawio", hat: "Diagram", time: "14m ago", kind: "Diagram", size: "—", rows: "—" },
    ],
  },
  {
    id: "northwind", name: "Northwind Logistics", focus: "Lakehouse + analytics",
    challenge: "Consolidating siloed warehouse data into an OCI lakehouse for near-real-time fleet analytics.",
    services: ["Autonomous DW", "Data Flow", "Object Storage", "Streaming", "Analytics Cloud"],
    status: "live", hat: "WAF Reviewer", task: "Running waf_review…", updated: "now", stage: "Review",
    artifacts: [{ id: "nw-waf", icon: "◆", name: "northwind_waf_review.pdf", hat: "WAF Reviewer", time: "now", kind: "PDF", size: "1.2 MB", rows: "5 pillars" }],
  },
  {
    id: "helios", name: "Helios Bank", focus: "DR + sovereign region",
    challenge: "Standing up a sovereign DR region with strict residency controls and < 15min RTO for core banking.",
    services: ["Exadata", "Data Guard", "Vault", "FastConnect", "Audit", "Cloud Guard"],
    status: "live", hat: "Terraform", task: "Generating terraform plan…", updated: "now", stage: "IaC",
    artifacts: [{ id: "he-tf", icon: "▢", name: "helios_dr_region.tf", hat: "Terraform", time: "now", kind: "HCL", size: "18 KB", rows: "240 lines" }],
  },
  {
    id: "vertex", name: "Vertex Health", focus: "OKE app modernization",
    challenge: "Re-platforming a monolith onto OKE with HIPAA-aligned network isolation and autoscaling.",
    services: ["OKE", "API Gateway", "WAF", "Vault", "Load Balancer", "Logging"],
    status: "idle", updated: "22m", stage: "Diagram",
    artifacts: [{ id: "vx-dgm", icon: "◫", name: "vertex_oke_topology.drawio", hat: "Diagram", time: "22m ago", kind: "Diagram", size: "—", rows: "—" }],
  },
  { id: "summit", name: "Summit Retail", focus: "E-commerce burst scaling", challenge: "Black-Friday burst scaling for a stateless storefront with sub-100ms checkout latency targets.", services: ["OKE", "Load Balancer", "Redis", "Object Storage", "WAF"], status: "idle", updated: "1h", stage: "BOM", artifacts: [] },
  { id: "orion", name: "Orion Aerospace", focus: "HPC batch on OCI", challenge: "Migrating CFD simulation batch from on-prem HPC to OCI cluster networking with RDMA.", services: ["HPC", "Cluster Networking", "File Storage", "Compute BM"], status: "idle", updated: "2h", stage: "Sizing", artifacts: [] },
  { id: "pinnacle", name: "Pinnacle Insurance", focus: "Claims data warehouse", challenge: "Modernizing a legacy claims DW onto Autonomous Data Warehouse with row-level security.", services: ["Autonomous DW", "Data Integration", "Vault", "Analytics Cloud"], status: "idle", updated: "3h", stage: "POV", artifacts: [] },
  { id: "atlas", name: "Atlas Manufacturing", focus: "IoT telemetry pipeline", challenge: "Ingesting plant-floor IoT telemetry into a streaming pipeline for predictive maintenance.", services: ["Streaming", "Functions", "Object Storage", "Data Flow"], status: "idle", updated: "5h", stage: "Diagram", artifacts: [] },
  { id: "meridian", name: "Meridian Telecom", focus: "5G core on OCI", challenge: "Evaluating OCI for a cloud-native 5G core control plane with strict latency SLAs.", services: ["OKE", "Cluster Networking", "Service Mesh", "Monitoring"], status: "idle", updated: "yesterday", stage: "JEP", artifacts: [] },
  { id: "cascade", name: "Cascade Energy", focus: "SCADA DR", challenge: "Disaster recovery for SCADA historian databases with air-gapped backup requirements.", services: ["Exadata", "Data Guard", "Object Storage", "Vault"], status: "idle", updated: "yesterday", stage: "WAF", artifacts: [] },
  { id: "verdant", name: "Verdant Agritech", focus: "Geospatial analytics", challenge: "Geospatial crop-yield analytics over satellite imagery at petabyte scale.", services: ["Object Storage", "Data Flow", "GPU Compute", "Analytics Cloud"], status: "idle", updated: "2d", stage: "Sizing", artifacts: [] },
  { id: "harborview", name: "Harborview Health", focus: "EHR migration", challenge: "Migrating an Epic EHR estate to OCI with HIPAA controls and 99.99% availability.", services: ["Exadata", "Data Guard", "Vault", "Cloud Guard", "Audit"], status: "idle", updated: "2d", stage: "BOM", artifacts: [] },
  { id: "ironclad", name: "Ironclad Defense", focus: "Sovereign cloud", challenge: "Air-gapped sovereign cloud landing zone for classified workloads.", services: ["Dedicated Region", "Vault", "Bastion", "Audit"], status: "idle", updated: "3d", stage: "POV", artifacts: [] },
  { id: "lumen", name: "Lumen Media", focus: "Video transcoding", challenge: "Elastic GPU transcoding farm for live-event video with regional failover.", services: ["GPU Compute", "Object Storage", "CDN", "Load Balancer"], status: "idle", updated: "3d", stage: "Diagram", artifacts: [] },
  { id: "keystone", name: "Keystone Bank", focus: "Core banking refresh", challenge: "Refreshing core banking onto Exadata Cloud Service with zero-downtime patching.", services: ["Exadata", "Data Guard", "GoldenGate", "Vault"], status: "idle", updated: "4d", stage: "Sizing", artifacts: [] },
  { id: "novapharm", name: "NovaPharm", focus: "GxP validated env", challenge: "GxP-validated compute environment for clinical-trial data processing.", services: ["Compute", "Block Storage", "Vault", "Audit", "Logging"], status: "idle", updated: "4d", stage: "JEP", artifacts: [] },
  { id: "tidalwave", name: "Tidalwave Gaming", focus: "Global game backend", challenge: "Low-latency multiplayer game backend across 6 OCI regions.", services: ["OKE", "Redis", "Streaming", "Load Balancer"], status: "idle", updated: "5d", stage: "Diagram", artifacts: [] },
  { id: "graniteco", name: "Granite Construction", focus: "ERP lift-and-shift", challenge: "Lift-and-shift of JD Edwards ERP onto OCI compute with managed DB.", services: ["Compute", "Base DB", "Object Storage", "VCN"], status: "idle", updated: "6d", stage: "BOM", artifacts: [] },
  { id: "everbright", name: "Everbright Solar", focus: "Time-series at scale", challenge: "Storing and querying inverter time-series telemetry across 2M devices.", services: ["Streaming", "Autonomous DB", "Object Storage", "Functions"], status: "idle", updated: "1w", stage: "Sizing", artifacts: [] },
  { id: "cobalt", name: "Cobalt Logistics", focus: "Route optimization", challenge: "Real-time fleet route optimization with an OCI Data Science pipeline.", services: ["Data Science", "Functions", "Streaming", "Autonomous DB"], status: "idle", updated: "1w", stage: "POV", artifacts: [] },
];

const FOCUSED_ID = "acme";

const ACME_BOM_TEXT =
`Here's the first-pass bill of materials for the Acme Oracle migration. I sized it for the 40TB estate with active-active HA in Ashburn and a warm DR standby in Phoenix.

Highlights:
• Exadata X10M quarter rack (DB) — covers the 40TB working set with headroom for 18mo growth.
• Data Guard (Max Availability) for the cross-region standby — meets the < 5min RPO you flagged.
• Object Storage (Standard) for RMAN backups + an archive bucket tier for 7yr retention.
• A hub-spoke VCN with a Bastion service for jump access; no public DB subnets.

I cross-checked every SKU against the current OCI price list. The workbook breaks out monthly vs. annual-commit pricing on separate tabs.`;

function defaultReply() {
  return `Good question. Based on the engagement memory for Acme, here's how I'd approach that:

• I'll keep the active-active HA assumption in Ashburn and re-validate the DR standby sizing for Phoenix.
• The numbers below are cross-checked against the live OCI price list, so they're commit-ready.

Want me to regenerate the BOM with this folded in, or draft the WAF review next?`;
}

const STATUS_CYCLE = [
  "Running generate_bom…",
  "Cross-checking SKUs against OCI price list…",
  "Validating HA + DR topology…",
];

Object.assign(window, { ENGAGEMENTS, FOCUSED_ID, ACME_BOM_TEXT, defaultReply, STATUS_CYCLE });
