"""
safety_rules.py
---------------
Deterministic safety checks for Archie. No LLM calls. Max 150 lines.
Called by archie_loop.py before finalising any BOM, Terraform, or WAF output.

Rules implemented here must be expressible as regex/threshold checks on
result_data alone. Rules that require LLM interpretation live in the governor
hat (agent/hats/governor.md).
"""

from __future__ import annotations

import re


def check(tool_name: str, result_data: dict) -> tuple[bool, str]:
    """
    Returns (passed: bool, reason: str).
    passed=True means the result is safe to deliver.
    passed=False means Archie must block delivery and reason contains the issue.
    """
    if tool_name == "generate_terraform":
        main_tf = str(result_data.get("main_tf") or "")

        # Hardcoded OCIDs must be variables
        if re.search(r'ocid1\.', main_tf):
            return (False, "main.tf contains hardcoded OCIDs — use variables instead")

        # Root compartment is never acceptable
        if re.search(r'compartment_id\s*=\s*["\']ocid1\.tenancy\.', main_tf):
            return (False, "main.tf places a resource in the root tenancy compartment — use a child compartment")

        # Block volumes without KMS key
        if re.search(r'resource\s+"oci_core_volume"', main_tf):
            if not re.search(r'kms_key_id', main_tf):
                return (False, "Block volume defined without kms_key_id — all storage must have encryption at rest")

    if tool_name == "generate_bom":
        totals = result_data.get("bom_payload", {}).get("totals", {})
        estimated = totals.get("estimated_monthly_cost", 0)

        # Absolute cost hard block
        if estimated > 500_000:
            return (
                False,
                "Estimated monthly cost exceeds $500k — explicit confirmation required",
            )

        # Over-budget check when engagement budget is present in result_data
        cost_max = result_data.get("cost_max_monthly")
        if cost_max and cost_max > 0 and estimated > cost_max * 1.10:
            over_pct = int(100 * (estimated - cost_max) / cost_max)
            return (
                False,
                f"Estimated monthly cost is {over_pct}% over the stated budget of "
                f"${cost_max:,.0f} — explicit user confirmation required before delivery",
            )

    return (True, "")
