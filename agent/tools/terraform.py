"""
agent/tools/terraform.py
------------------------
ToolHandler implementation for the generate_terraform pipeline.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import Any

import anyio
from fastapi import HTTPException

from agent import archie_memory, document_store, sub_agent_client
from agent.context_store import (
    build_context_summary,
    get_new_notes,
    read_context,
    record_agent_run,
    write_context,
)
from agent.document_store import save_terraform_bundle
from agent.persistence_objectstore import ObjectStoreBase
from agent.sub_agent_client import SubAgentError
from skillforge.types import MemorySnapshot, ToolResult

logger = logging.getLogger(__name__)


def _terraform_fallback_files() -> dict[str, str]:
    """Deterministic OCI Terraform starter bundle used when graph asks for clarification."""
    return {
        "main.tf": (
            'terraform {\n'
            '  required_version = ">= 1.4.0"\n'
            "  required_providers {\n"
            "    oci = {\n"
            '      source  = "oracle/oci"\n'
            '      version = ">= 5.0.0"\n'
            "    }\n"
            "  }\n"
            "}\n\n"
            'provider "oci" {\n'
            "  region = var.region\n"
            "}\n\n"
            'resource "oci_core_vcn" "main" {\n'
            "  compartment_id = var.compartment_id\n"
            '  cidr_block     = "10.0.0.0/16"\n'
            '  display_name   = "${var.prefix}-vcn"\n'
            '  dns_label      = "mainvcn"\n'
            "}\n\n"
            'resource "oci_core_subnet" "private" {\n'
            "  compartment_id      = var.compartment_id\n"
            "  vcn_id              = oci_core_vcn.main.id\n"
            '  cidr_block          = "10.0.1.0/24"\n'
            '  display_name        = "${var.prefix}-private-subnet"\n'
            '  dns_label           = "privsub"\n'
            "  prohibit_public_ip_on_vnic = true\n"
            "}\n"
        ),
        "variables.tf": (
            'variable "region" {\n'
            '  type    = string\n'
            '  default = "us-ashburn-1"\n'
            "}\n\n"
            'variable "compartment_id" {\n'
            "  type = string\n"
            "}\n\n"
            'variable "prefix" {\n'
            "  type    = string\n"
            '  default = "ai-poc"\n'
            "}\n"
        ),
        "outputs.tf": (
            'output "vcn_id" {\n'
            "  value = oci_core_vcn.main.id\n"
            "}\n\n"
            'output "private_subnet_id" {\n'
            "  value = oci_core_subnet.private.id\n"
            "}\n"
        ),
        "terraform.tfvars.example": (
            'region         = "us-ashburn-1"\n'
            'compartment_id = "ocid1.compartment.oc1..exampleuniqueID"\n'
            'prefix         = "install-test"\n'
        ),
    }


def _default_tfvars_example() -> str:
    return (
        'region = "us-ashburn-1"\n'
        'compartment_ocid = "ocid1.compartment.oc1..example"\n'
        'compartment_id = "ocid1.compartment.oc1..example"\n'
        'availability_domain = "example:US-ASHBURN-AD-1"\n'
        'image_ocid = "ocid1.image.oc1.iad.example"\n'
        'object_storage_namespace = "example"\n'
        'object_storage_service_id = "ocid1.service.oc1.iad.objectstorage"\n'
    )


async def generate_terraform_bundle(
    *,
    customer_id: str,
    customer_name: str,
    prompt: str,
    store: ObjectStoreBase,
    trace_id: str,
) -> dict:
    """
    Orchestrate Terraform generation: context hydration, sub-agent call,
    result parsing, persistence, context recording.

    Returns the response dict ready to be returned from the FastAPI handler.
    Raises HTTPException on terminal failures.
    """
    try:
        context = await anyio.to_thread.run_sync(
            functools.partial(read_context, store, customer_id, customer_name)
        )
        new_note_keys, new_notes_text = await anyio.to_thread.run_sync(
            functools.partial(get_new_notes, store, context, "terraform")
        )
        context_summary = build_context_summary(context)

        requested_prompt = (prompt or "").strip()
        prompt = requested_prompt
        if not prompt:
            prompt = (
                "Generate a complete OCI Terraform deployment for this customer.\n"
                f"Customer ID: {customer_id}\n"
                f"Customer Name: {customer_name}\n\n"
                "Assumptions (use these defaults instead of asking clarification questions):\n"
                "- This is a greenfield deployment.\n"
                "- Region: us-ashburn-1.\n"
                "- Network: one VCN with public and private subnets.\n"
                "- Security: NSGs and least-privilege security list rules.\n"
                "- Compute/workload: GPU-capable platform suitable for AI workloads.\n"
                "- Availability: multi-AD where possible, otherwise resilient single-region design.\n\n"
                "Prior fleet context:\n"
                f"{context_summary or '(none)'}\n\n"
                "Meeting notes incorporated in this run:\n"
                f"{new_notes_text or '(none)'}\n\n"
                "Output requirements:\n"
                "- Return production-usable Terraform files.\n"
                "- Include provider, variables, outputs, and example tfvars.\n"
                "- Make pragmatic defaults when details are missing.\n"
            )

        response = await sub_agent_client.call_sub_agent(
            "terraform",
            prompt,
            {
                "customer_id": customer_id,
                "customer_name": customer_name,
                "architecture_summary": context_summary,
                "engagement_context": {
                    "new_notes": new_notes_text,
                    "context_summary": context_summary,
                },
            },
            trace_id,
        )
        status = str(response.get("status") or "").lower()
        raw_result = response.get("result", {})
        if isinstance(raw_result, str):
            try:
                parsed_result = json.loads(raw_result)
            except json.JSONDecodeError:
                parsed_result = {"main_tf": raw_result}
        elif isinstance(raw_result, dict):
            parsed_result = raw_result
        else:
            parsed_result = {}

        file_aliases = {
            "main_tf": "main.tf",
            "variables_tf": "variables.tf",
            "outputs_tf": "outputs.tf",
            "terraform_tfvars_example": "terraform.tfvars.example",
            "tfvars_example": "terraform.tfvars.example",
            "readme_md": "README.md",
        }
        files = {
            file_aliases.get(str(name), str(name)): str(content)
            for name, content in parsed_result.items()
            if str(content or "").strip()
        }
        if not str(files.get("terraform.tfvars.example", "") or "").strip():
            files["terraform.tfvars.example"] = _default_tfvars_example()
        summary = str(response.get("summary") or "Terraform generation completed")
        result_data = {
            "ok": status == "ok" and bool(files),
            "files": files,
            "stages": [],
            "blocking_questions": list(response.get("questions", []) or response.get("blocking_questions", []) or []),
            "trace": dict(response.get("trace", {}) or {}),
        }
        used_fallback = False
        if not result_data.get("ok"):
            if requested_prompt:
                return {
                    "status": "need_clarification",
                    "trace_id": trace_id,
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "summary": summary,
                    "blocking_questions": result_data.get("blocking_questions", []),
                    "stages": result_data.get("stages", []),
                }
            used_fallback = True
            files = _terraform_fallback_files()
            summary = (
                "Terraform generated using fallback starter bundle because the planner "
                "requested clarification. Fill `compartment_id` and tune network/workload variables."
            )

        persisted = await anyio.to_thread.run_sync(
            functools.partial(
                save_terraform_bundle,
                store,
                customer_id,
                files,
                {
                    "customer_name": customer_name,
                    "summary": summary[:500],
                    "trace_id": trace_id,
                },
            )
        )
        context = await anyio.to_thread.run_sync(
            functools.partial(read_context, store, customer_id, customer_name)
        )
        context = record_agent_run(
            context,
            "terraform",
            new_note_keys,
            {
                "version": persisted["version"],
                "file_count": len(persisted.get("files", {})),
                "prefix_key": f"customers/{customer_id}/terraform/v{persisted['version']}",
                "key": persisted["key"],
                "latest_key": persisted["latest_key"],
                "summary": summary[:500],
            },
        )
        await anyio.to_thread.run_sync(
            functools.partial(write_context, store, customer_id, context)
        )
        files_list = sorted(list(persisted["files"].keys()))
        return {
            "status": "ok",
            "trace_id": trace_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "doc_type": "terraform",
            "summary": summary,
            "version": persisted["version"],
            "key": persisted["key"],
            "latest_key": persisted["latest_key"],
            "files": files_list,
            "file_count": len(files_list),
            "fallback_used": used_fallback,
            "stages": result_data.get("stages", []),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error in /api/terraform/generate: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


class TerraformHandler:
    def __init__(
        self,
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
        text_runner: Any,
        a2a_base_url: str = "",
    ) -> None:
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name
        self._text_runner = text_runner
        self._a2a_base_url = a2a_base_url

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        ctx = context
        decision_context = memory.decision_context if memory else {}
        user_message = str(args.get("_user_message", "") or "")
        args = archie_memory._hydrate_tool_args_from_context(
            tool_name="generate_terraform",
            args=args,
            context=ctx,
            decision_context=decision_context,
            user_message=user_message,
        )
        args = archie_memory._enforce_memory_contract_on_tool_args(
            tool_name="generate_terraform",
            args=args,
            context=ctx,
        )

        if (
            ctx
            and archie_memory._has_architecture_definition(ctx)
            and not archie_memory._terraform_scope_is_bounded(
                context=ctx,
                args=args,
                decision_context=decision_context,
                user_message=user_message,
            )
        ):
            return ToolResult(
                summary="Terraform scope clarification required.",
                status="needs_input",
                clarification=(
                    "Please clarify which modules or resources to include in the "
                    "Terraform bundle."
                ),
            )

        raw_prompt = str(args.get("prompt", "") or "")
        task = raw_prompt or str(
            args.get("_user_request_text", "")
            or "Generate Terraform for the current architecture."
        )

        try:
            response = await sub_agent_client.call_sub_agent(
                "terraform",
                task=task,
                engagement_context={
                    "customer_id": self._customer_id,
                    "customer_name": self._customer_name,
                    "architect_brief": dict(args.get("_architect_brief", {}) or {}),
                },
                trace_id=trace_id,
            )
        except SubAgentError as exc:
            return ToolResult(
                summary=f"Terraform sub-agent error: {exc}",
                status="blocked",
            )

        if str(response.get("status") or "").lower() == "needs_input":
            clarification = str(response.get("result") or "")
            return ToolResult(
                summary=clarification or "Terraform needs more input.",
                status="needs_input",
                clarification=clarification,
            )

        from agent.archie_session import _parse_terraform_sub_agent_result

        files = _parse_terraform_sub_agent_result(response.get("result"))
        saved = await asyncio.to_thread(
            document_store.save_terraform_bundle,
            self._store,
            self._customer_id,
            files,
            {"trace": response.get("trace", {}), "source": "sub_agent_client"},
        )
        key = str((saved.get("files") or {}).get("main.tf") or saved.get("latest_key") or "")
        return ToolResult(
            summary=f"Terraform bundle v{saved.get('version')} saved.",
            status="ok",
            artifact_key=key,
            data={
                "terraform_files": files,
                "terraform_bundle": saved,
                "bundle": saved,
            },
        )
