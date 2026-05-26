"""Resolve OCI service display names to Oracle toolkit PowerPoint shape names."""
from __future__ import annotations


OCI_ICON_MAP = {
    "Autonomous Database": "OCI_Autonomous_Database",
    "Oracle Autonomous Database": "OCI_Autonomous_Database",
    "ADB": "OCI_Autonomous_Database",
    "OCI Compute": "OCI_Compute_Instance",
    "Compute": "OCI_Compute_Instance",
    "Compute Instance": "OCI_Compute_Instance",
    "Virtual Cloud Network": "OCI_VCN",
    "VCN": "OCI_VCN",
    "OCI VCN": "OCI_VCN",
    "OKE": "OCI_Container_Engine",
    "Oracle Kubernetes Engine": "OCI_Container_Engine",
    "Container Engine for Kubernetes": "OCI_Container_Engine",
    "Object Storage": "OCI_Object_Storage",
    "OCI Object Storage": "OCI_Object_Storage",
    "Load Balancer": "OCI_Load_Balancer",
    "OCI Load Balancer": "OCI_Load_Balancer",
    "OCI Data Integration": "OCI_Data_Integration",
    "Data Integration": "OCI_Data_Integration",
    "OCI Vault": "OCI_Vault",
    "Vault": "OCI_Vault",
    "Cloud Guard": "OCI_Cloud_Guard",
    "Data Safe": "OCI_Data_Safe",
    "Security Zones": "OCI_Security_Zones",
    "FastConnect": "OCI_FastConnect",
    "Dynamic Routing Gateway": "OCI_DRG",
    "DRG": "OCI_DRG",
}


def resolve_icon(service_name: str) -> str | None:
    """Return the toolkit PPTX shape name for a service, or None."""
    raw = str(service_name or "")
    return OCI_ICON_MAP.get(raw) or OCI_ICON_MAP.get(raw.strip())
