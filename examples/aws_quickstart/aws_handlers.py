"""
AWS-domain tool handlers for the SkillForge quickstart.
Zero OCI imports. Zero Archie imports.
"""
from __future__ import annotations
from skillforge.types import MemorySnapshot, ToolResult


async def size_ec2_handler(
    args: dict,
    *,
    memory: MemorySnapshot | None,
    context: dict,
    trace_id: str,
) -> ToolResult:
    """
    Simulates EC2 instance sizing based on workload description.
    In production, this would call the AWS Pricing API.
    """
    workload = args.get("workload", "unknown workload")
    # Simulated sizing logic
    sizing = {
        "instance_type": "m6i.2xlarge",
        "vcpu": 8,
        "memory_gb": 32,
        "estimated_monthly_usd": 280,
        "reasoning": f"Sized for: {workload}",
    }
    return ToolResult(
        summary=f"EC2 sized: m6i.2xlarge (8 vCPU, 32GB) ~$280/month for '{workload}'",
        status="ok",
        data={"facts": {"ec2_sizing": sizing}},
    )
