"""
tests/scenarios/fakes.py
------------------------
Fake implementations for testing drawing_agent_server without OCI access.

FakeLLMRunner   — deterministic LLM stub that tracks call_count
InMemoryObjectStoreFake — re-exports the in-memory store from persistence_objectstore
"""
from __future__ import annotations

from agent.persistence_objectstore import InMemoryObjectStore

# Re-export for convenience
InMemoryObjectStoreFake = InMemoryObjectStore


# ── Minimal deterministic specs ────────────────────────────────────────────────

MINIMAL_SPEC: dict = {
    "deployment_type": "single_ad",
    "page": {"width": 1654, "height": 1169},
    "regions": [
        {
            "id":    "region_primary",
            "label": "OCI Region — us-phoenix-1",
            "regional_subnets": [],
            "availability_domains": [
                {
                    "id":    "ad1",
                    "label": "AD-1",
                    "fault_domains": [],
                    "subnets": [
                        {
                            "id":    "pub_sub",
                            "tier":  "ingress",
                            "label": "Public Subnet",
                            "nodes": [
                                {"id": "lb_1", "type": "load balancer", "label": "LB"},
                            ],
                        },
                        {
                            "id":    "app_sub",
                            "tier":  "compute",
                            "label": "App Subnet",
                            "nodes": [
                                {"id": "compute_1", "type": "compute", "label": "Compute"},
                            ],
                        },
                        {
                            "id":    "db_sub",
                            "tier":  "db",
                            "label": "DB Subnet",
                            "nodes": [
                                {"id": "db_1", "type": "database", "label": "DB"},
                            ],
                        },
                    ],
                }
            ],
            "gateways": [
                {"id": "igw_1", "type": "internet gateway", "label": "IGW"},
                {"id": "nat_1", "type": "nat gateway",      "label": "NAT"},
                {"id": "drg_1", "type": "drg",              "label": "DRG"},
            ],
            "oci_services": [
                {"id": "monitoring_1", "type": "monitoring", "label": "Monitoring"},
            ],
        }
    ],
    "external": [
        {"id": "on_prem", "type": "on premises", "label": "On-Premises"},
    ],
    "edges": [],
}


MULTI_REGION_SPEC: dict = {
    "deployment_type": "multi_region",
    "page": {"width": 1654, "height": 1169},
    "regions": [
        {
            "id":    "region_primary",
            "label": "OCI Region — us-phoenix-1",
            "regional_subnets": [],
            "availability_domains": [
                {
                    "id":    "ad1",
                    "label": "AD-1",
                    "fault_domains": [],
                    "subnets": [
                        {
                            "id":    "pub_sub",
                            "tier":  "ingress",
                            "label": "Public Subnet",
                            "nodes": [
                                {"id": "lb_1", "type": "load balancer", "label": "LB"},
                            ],
                        },
                    ],
                }
            ],
            "gateways": [
                {"id": "igw_1", "type": "internet gateway", "label": "IGW"},
            ],
            "oci_services": [],
        },
        {
            "id":    "region_secondary",
            "label": "OCI Region — us-ashburn-1",
            "regional_subnets": [],
            "availability_domains": [
                {
                    "id":    "ad1b",
                    "label": "AD-1",
                    "fault_domains": [],
                    "subnets": [
                        {
                            "id":    "pub_sub_b",
                            "tier":  "ingress",
                            "label": "Public Subnet",
                            "nodes": [
                                {"id": "lb_2", "type": "load balancer", "label": "LB"},
                            ],
                        },
                    ],
                }
            ],
            "gateways": [
                {"id": "igw_2", "type": "internet gateway", "label": "IGW"},
            ],
            "oci_services": [],
        },
    ],
    "external": [
        {"id": "on_prem", "type": "on premises", "label": "On-Premises"},
    ],
    "edges": [],
}


# ── Fake runner ────────────────────────────────────────────────────────────────

class FakeLLMRunner:
    """
    Deterministic LLM runner for tests.

    Returns a deep copy of spec_to_return on every call and increments call_count.
    Optionally returns different specs based on a call sequence (using next_specs list).
    """

    def __init__(self, spec: dict | None = None):
        import copy
        self._default_spec = copy.deepcopy(spec) if spec is not None else copy.deepcopy(MINIMAL_SPEC)
        self._next_specs: list[dict] = []
        self.call_count: int = 0
        self.received_prompts: list[str] = []

    def queue_spec(self, spec: dict) -> "FakeLLMRunner":
        """Queue a spec to be returned on the NEXT call (one-time override)."""
        import copy
        self._next_specs.append(copy.deepcopy(spec))
        return self

    def __call__(self, prompt: str, client_id: str) -> dict:
        import copy
        self.call_count += 1
        self.received_prompts.append(prompt)
        if self._next_specs:
            return self._next_specs.pop(0)
        return copy.deepcopy(self._default_spec)
