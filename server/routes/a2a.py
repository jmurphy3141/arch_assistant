from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from server.models import A2AResponse, A2ATask


def create_a2a_router(deps) -> APIRouter:
    router = APIRouter()

    def _build_agent_card(host: str) -> dict:
        obj_ref_schema = {
            "type": "object",
            "required": ["bucket", "object"],
            "properties": {
                "namespace": {"type": "string"},
                "bucket": {"type": "string"},
                "object": {"type": "string"},
                "version_id": {"type": "string"},
            },
        }
        fleet_cfg = deps.fleet_config()
        return {
            "schema_version": "0.1",
            "agent_id": deps.agent_id,
            "name": "OCI Drawing Agent",
            "description": (
                "Generates OCI architecture draw.io diagrams from a Bill of Materials "
                "or resource list. Part of the OCI Agent Fleet (Agent 3 of 7)."
            ),
            "version": deps.agent_version,
            "url": f"{host}/api/a2a/task",
            "fleet": {
                "fleet_id": fleet_cfg.get("fleet_id", "oci-agent-fleet"),
                "position": fleet_cfg.get("position", 3),
                "total_agents": fleet_cfg.get("total_agents", 7),
                "upstream": fleet_cfg.get("upstream", ["agent2-bom-sizing"]),
                "downstream": fleet_cfg.get("downstream", ["agent4-sizing-validation"]),
            },
            "capabilities": {
                "clarification_flow": True,
                "streaming": False,
                "push_notifications": False,
                "object_storage_inputs": True,
            },
            "skills": [
                {
                    "id": "generate_diagram",
                    "name": "Generate Architecture Diagram",
                    "description": (
                        "Generate a draw.io OCI architecture diagram from a resource list. "
                        "Accepts inline resources[] or an OCI bucket reference. "
                        "May return need_clarification - call clarify_diagram to continue."
                    ),
                    "input_schema": {
                        "type": "object",
                        "oneOf": [{"required": ["resources"]}, {"required": ["resources_from_bucket"]}],
                        "properties": {
                            "resources": {"type": "array", "items": {"type": "object"}},
                            "resources_from_bucket": obj_ref_schema,
                            "context": {"type": "string"},
                            "context_from_bucket": obj_ref_schema,
                            "questionnaire": {"type": "string"},
                            "notes": {"type": "string"},
                            "deployment_hints": {"type": "object"},
                            "diagram_name": {"type": "string", "default": "oci_architecture"},
                        },
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["ok", "need_clarification"]},
                            "request_id": {"type": "string"},
                            "input_hash": {"type": "string"},
                            "drawio_xml": {"type": "string"},
                            "render_manifest": {"type": "object"},
                            "questions": {"type": "array", "description": "Present when status=need_clarification"},
                            "download": {"type": "object"},
                        },
                    },
                },
                {
                    "id": "upload_bom",
                    "name": "Upload BOM from Bucket",
                    "description": (
                        "Parse an Excel BOM stored in OCI Object Storage and generate a diagram. "
                        "Agent 2 should PUT the BOM to the shared bucket and pass the reference here. "
                        "May return need_clarification."
                    ),
                    "input_schema": {
                        "type": "object",
                        "required": ["bom_from_bucket"],
                        "properties": {
                            "bom_from_bucket": obj_ref_schema,
                            "context": {"type": "string"},
                            "diagram_name": {"type": "string", "default": "oci_architecture"},
                        },
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["ok", "need_clarification"]},
                            "request_id": {"type": "string"},
                            "drawio_xml": {"type": "string"},
                            "questions": {"type": "array"},
                            "download": {"type": "object"},
                        },
                    },
                },
                {
                    "id": "clarify_diagram",
                    "name": "Submit Clarification Answers",
                    "description": (
                        "Resume a pending diagram generation by providing answers to "
                        "clarification questions. Use the same client_id and diagram_name "
                        "as the original generate_diagram or upload_bom call."
                    ),
                    "input_schema": {
                        "type": "object",
                        "required": ["answers", "diagram_name"],
                        "properties": {
                            "answers": {"type": "string", "description": "Free-text answers to the questions"},
                            "diagram_name": {"type": "string"},
                        },
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["ok", "need_clarification"]},
                            "request_id": {"type": "string"},
                            "drawio_xml": {"type": "string"},
                            "questions": {"type": "array"},
                            "download": {"type": "object"},
                        },
                    },
                },
            ],
            "authentication": {
                "schemes": ["none"],
                "note": (
                    "Internal OCI network only. "
                    "Backend uses Instance Principal auth; no bearer token required from orchestrator."
                ),
            },
            "health_check_url": f"{host}/api/health",
        }

    def _make_a2a_task(context_id: str) -> dict:
        task_id = str(uuid.uuid4())
        task = {"id": task_id, "contextId": context_id, "status": "SUBMITTED", "artifacts": []}
        deps.a2a_tasks()[task_id] = task
        return task

    @router.get("/.well-known/agent.json")
    @router.get("/.well-known/agent-card.json")
    def agent_card():
        host = os.environ.get("AGENT_PUBLIC_HOST", "http://localhost:8000")
        fleet_cfg = deps.fleet_config()
        return JSONResponse(
            {
                "schemaVersion": "1.0",
                "humanReadableId": f"oracle-oci-fleet/{deps.agent_id}",
                "name": "OCI SA Orchestrator + Drawing Agent",
                "agentVersion": deps.agent_version,
                "url": host,
                "provider": {"name": "Oracle"},
                "capabilities": {"streaming": False, "pushNotifications": False},
                "authSchemes": [{"type": "none"}],
                "skills": [
                    {
                        "id": "orchestrate_engagement",
                        "name": "Orchestrate SA Engagement",
                        "description": (
                            "Accept a natural-language SA message and orchestrate "
                            "notes intake, POV, diagram, WAF, or JEP generation. "
                            "Uses contextId as customer_id for session continuity."
                        ),
                        "inputModes": ["text/plain"],
                        "outputModes": ["text/plain", "application/json"],
                    },
                    {
                        "id": "generate_diagram",
                        "name": "Generate Architecture Diagram",
                        "description": "Generate OCI draw.io diagram from BOM or resource list.",
                        "inputModes": ["text/plain", "application/json"],
                        "outputModes": ["application/json"],
                    },
                ],
                "fleet": {
                    "fleet_id": fleet_cfg.get("fleet_id", "oci-agent-fleet"),
                    "position": fleet_cfg.get("position", 3),
                    "total_agents": fleet_cfg.get("total_agents", 7),
                },
            }
        )

    @router.post("/message:send")
    async def a2a_message_send(request: Request):
        body = await request.json()
        rpc_id = body.get("id", "")
        params = body.get("params", {})
        message = params.get("message", {})
        context_id = message.get("contextId", "default")
        skill = params.get("skill", "orchestrate_engagement")
        parts = message.get("parts", [])
        user_text = " ".join(p.get("text", "") for p in parts if p.get("kind", "text") == "text").strip()

        task = _make_a2a_task(context_id)
        task["status"] = "WORKING"

        try:
            if skill == "generate_diagram":
                legacy_task = A2ATask(
                    task_id=task["id"],
                    skill="generate_diagram",
                    inputs={"resources": [], "notes": user_text},
                    client_id=context_id,
                )
                result = await deps.a2a_generate_diagram()(legacy_task)
                result_status = str(result.get("status", "error") or "error").lower()
                if result_status == "ok":
                    task["status"] = "COMPLETED"
                elif result_status == "need_clarification":
                    task["status"] = "INPUT_REQUIRED"
                else:
                    task["status"] = "FAILED"
                artifacts = [{"artifactId": "a1", "name": "reply", "parts": [{"kind": "text", "text": result_status}]}]
                drawio_key = result.get("object_key") or result.get("drawio_key") or ""
                if drawio_key:
                    artifacts.append(
                        {
                            "artifactId": "a2",
                            "name": "drawio_key",
                            "parts": [{"kind": "data", "mimeType": "application/json", "data": {"key": drawio_key}}],
                        }
                    )
                questions = result.get("questions", [])
                if isinstance(questions, list) and questions:
                    artifacts.append(
                        {
                            "artifactId": "a3",
                            "name": "questions",
                            "parts": [{"kind": "data", "mimeType": "application/json", "data": {"questions": questions}}],
                        }
                    )
                clarify_context = result.get("_clarify_context")
                if isinstance(clarify_context, dict) and clarify_context:
                    artifacts.append(
                        {
                            "artifactId": "a4",
                            "name": "clarify_context",
                            "parts": [{"kind": "data", "mimeType": "application/json", "data": clarify_context}],
                        }
                    )
                task["artifacts"] = artifacts
            else:
                store = getattr(deps.app().state, "object_store", None)
                if store is None:
                    raise RuntimeError("Object store not initialised.")

                from agent import orchestrator_agent

                orch_cfg = deps.config().get("orchestrator", {})
                turn_result = await orchestrator_agent.run_turn(
                    customer_id=context_id,
                    customer_name=params.get("customer_name", context_id),
                    user_message=user_text,
                    store=store,
                    text_runner=deps.make_orchestrator_text_runner(),
                    a2a_base_url=os.environ.get("A2A_BASE_URL", "http://localhost:8080"),
                    max_tool_iterations=int(orch_cfg.get("max_tool_iterations", 5)),
                    max_refinements=int(orch_cfg.get("max_refinements", 3)),
                )
                task["status"] = "COMPLETED"
                artifacts = [
                    {"artifactId": "a1", "name": "reply", "parts": [{"kind": "text", "text": turn_result["reply"]}]}
                ]
                for tool_name, artifact_key in turn_result.get("artifacts", {}).items():
                    artifacts.append(
                        {
                            "artifactId": f"artifact-{tool_name}",
                            "name": tool_name,
                            "parts": [{"kind": "data", "mimeType": "application/json", "data": {"key": artifact_key}}],
                        }
                    )
                task["artifacts"] = artifacts
        except Exception as exc:
            deps.logger.error("/message:send error context=%s skill=%s: %s", context_id, skill, exc)
            task["status"] = "FAILED"
            task["error"] = str(exc)

        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": task})

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str):
        task = deps.a2a_tasks().get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found.")
        return JSONResponse({"jsonrpc": "2.0", "id": None, "result": task})

    @router.post("/tasks/{task_id}:cancel")
    async def cancel_task(task_id: str):
        task = deps.a2a_tasks().get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found.")
        if task["status"] in ("SUBMITTED", "WORKING"):
            task["status"] = "CANCELLED"
        return JSONResponse({"jsonrpc": "2.0", "id": None, "result": task})

    @router.post("/api/a2a/task", response_model=A2AResponse)
    async def a2a_task(task: A2ATask) -> A2AResponse:
        skills = {
            "generate_diagram": deps.a2a_generate_diagram(),
            "upload_bom": deps.a2a_upload_bom(),
            "clarify_diagram": deps.a2a_clarify(),
        }
        handler = skills.get(task.skill)
        if handler is None:
            return A2AResponse(
                task_id=task.task_id,
                agent_id=deps.agent_id,
                status="error",
                error_message=f"Unknown skill {task.skill!r}. Available: {list(skills)}",
            )
        try:
            result = await handler(task)
            return A2AResponse(
                task_id=task.task_id,
                agent_id=deps.agent_id,
                status=result.get("status", "error"),
                outputs=result,
            )
        except HTTPException as exc:
            return A2AResponse(task_id=task.task_id, agent_id=deps.agent_id, status="error", error_message=str(exc.detail))
        except Exception as exc:
            deps.logger.error("A2A task %s skill=%s error: %s", task.task_id, task.skill, exc)
            return A2AResponse(task_id=task.task_id, agent_id=deps.agent_id, status="error", error_message=str(exc))

    router.build_agent_card = _build_agent_card  # type: ignore[attr-defined]
    router.make_a2a_task = _make_a2a_task  # type: ignore[attr-defined]
    return router
