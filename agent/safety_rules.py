"""
safety_rules.py
---------------
Deterministic safety checks for Archie. No LLM calls. Max 100 lines.
Called by archie_loop.py before finalising any BOM, Terraform, or WAF output.
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
        main_tf = result_data.get("main_tf") or ""
        if re.search(r"ocid1\.", str(main_tf)):
            return (False, "main.tf contains hardcoded OCIDs — use variables instead")

    if tool_name == "generate_bom":
        estimated = (
            result_data.get("bom_payload", {})
            .get("totals", {})
            .get("estimated_monthly_cost", 0)
        )
        if estimated > 500_000:
            return (
                False,
                "Estimated monthly cost exceeds $500k — explicit confirmation required",
            )

    return (True, "")
