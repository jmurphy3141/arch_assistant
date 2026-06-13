from __future__ import annotations

import functools

import anyio
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from agent.context_store import read_context, write_context, merge_archie_relationship_facts
from agent.document_store import (
    get_approved_doc,
    get_jep_questions,
    get_latest_doc,
    get_latest_terraform_bundle,
    get_terraform_file,
    list_notes,
    list_terraform_versions,
    list_versions,
    save_approved_doc,
    save_jep_questions,
    save_note,
    save_note_text,
)
from agent.note_extractor import extract_text
from agent.jep_lifecycle import (
    generate_policy_block_payload as jep_generate_policy_block_payload,
    mark_approved as mark_jep_approved,
    mark_generated as mark_jep_generated,
    request_revision as request_jep_revision,
    sync_jep_state,
)
from agent.tools.specialists import build_inference_runner
from agent.tools.terraform import generate_terraform_bundle
from server.models import (
    ApproveDocRequest,
    JepAnswersRequest,
    JepKickoffRequest,
    JepRequest,
    JepRevisionRequest,
    PovRequest,
    TerraformGenerateRequest,
    WafRequest,
)


def create_documents_router(deps) -> APIRouter:
    router = APIRouter()

    @router.post("/api/notes/upload")
    async def upload_note(
        customer_id: str = Form(...),
        note_name: str = Form(default=""),
        file: UploadFile = File(...),
    ):
        store = deps.require_object_store()
        name = note_name.strip() or (file.filename or "note.txt")
        content_bytes = await file.read()
        content_type = file.content_type or "text/plain"
        try:
            key = await anyio.to_thread.run_sync(
                functools.partial(save_note, store, customer_id, name, content_bytes, content_type)
            )

            extraction = await anyio.to_thread.run_sync(
                functools.partial(extract_text, content_bytes, content_type, file.filename or name)
            )
            debrief: dict = {}
            if extraction.get("text"):
                try:
                    await anyio.to_thread.run_sync(
                        functools.partial(save_note_text, store, customer_id, name, extraction["text"])
                    )
                except Exception as exc:
                    deps.logger.error("Error saving extracted note text for %s: %s", name, exc)
                    extraction = {
                        **extraction,
                        "text": "",
                        "extraction_status": "failed",
                        "char_count": 0,
                        "warning": f"Text extraction succeeded but extracted text could not be saved: {exc}",
                    }

                if extraction.get("text"):
                    try:
                        from agent.archie_memory import _extract_relationship_facts, _extract_client_facts
                        text = extraction["text"]
                        rel_facts = _extract_relationship_facts(text)
                        client_facts = _extract_client_facts(text)
                        debrief = {
                            "stakeholders": rel_facts.get("stakeholders", []),
                            "action_items": rel_facts.get("action_items", []),
                            "objections": rel_facts.get("objections", []),
                            "commitments": rel_facts.get("commitments", []),
                            "competitive": rel_facts.get("competitive", {}),
                            "client_facts": client_facts,
                            "fact_count": sum(
                                len(rel_facts.get(k, [])) for k in ("stakeholders", "action_items", "objections", "commitments")
                            ),
                            "pending_confirmation": True,
                        }
                        context = await anyio.to_thread.run_sync(
                            functools.partial(read_context, store, customer_id, "")
                        )
                        context.setdefault("pending_debrief", debrief)
                        from agent.context_store import write_context as _write_ctx
                        await anyio.to_thread.run_sync(
                            functools.partial(_write_ctx, store, customer_id, context)
                        )
                    except Exception as exc:
                        deps.logger.warning("Debrief extraction failed for %s: %s", name, exc)
                        debrief = {}

            return {
                "status": "ok",
                "key": key,
                "customer_id": customer_id,
                "note_name": name,
                "detected_type": extraction["detected_type"],
                "extraction_status": extraction["extraction_status"],
                "char_count": extraction["char_count"],
                "warning": extraction["warning"],
                "debrief": debrief,
            }
        except Exception as exc:
            deps.logger.error("Error in /notes/upload: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/notes/{customer_id}")
    async def list_customer_notes(customer_id: str):
        store = deps.require_object_store()
        try:
            notes = await anyio.to_thread.run_sync(functools.partial(list_notes, store, customer_id))
            return {"status": "ok", "customer_id": customer_id, "notes": notes}
        except Exception as exc:
            deps.logger.error("Error in /notes/%s: %s", customer_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/pov/generate")
    async def pov_generate(req: PovRequest):
        store = deps.require_object_store()
        text_runner = build_inference_runner(deps.app().state, inference_config=deps.writing_inference_config())

        try:
            result = await anyio.to_thread.run_sync(
                functools.partial(
                    deps.generate_pov(),
                    req.customer_id,
                    req.customer_name,
                    store,
                    text_runner,
                    feedback=req.feedback or "",
                )
            )
            if result.get("status") == "need_clarification":
                return {"status": "need_clarification", "questions": result.get("questions", "")}
            return {
                "status": "ok",
                "agent_version": deps.agent_version,
                "customer_id": req.customer_id,
                "doc_type": "pov",
                "version": result["version"],
                "key": result["key"],
                "latest_key": result["latest_key"],
                "content": result["content"],
                "errors": [],
            }
        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("Error in /pov/generate: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/pov/{customer_id}/latest")
    async def pov_latest(customer_id: str):
        store = deps.require_object_store()
        content = await anyio.to_thread.run_sync(functools.partial(get_latest_doc, store, "pov", customer_id))
        if content is None:
            raise HTTPException(status_code=404, detail=f"No POV found for customer_id={customer_id!r}")
        return {"status": "ok", "customer_id": customer_id, "doc_type": "pov", "content": content}

    @router.get("/api/pov/{customer_id}/versions")
    async def pov_versions(customer_id: str):
        store = deps.require_object_store()
        versions = await anyio.to_thread.run_sync(functools.partial(list_versions, store, "pov", customer_id))
        return {"status": "ok", "customer_id": customer_id, "doc_type": "pov", "versions": versions}

    @router.post("/api/jep/generate")
    async def jep_generate(req: JepRequest):
        store = deps.require_object_store()
        persistence_cfg = getattr(deps.app().state, "persistence_config", None) or {}
        prefix = persistence_cfg.get("prefix", "agent3")
        text_runner = build_inference_runner(deps.app().state, inference_config=deps.writing_inference_config())

        try:
            policy_block = await anyio.to_thread.run_sync(
                functools.partial(jep_generate_policy_block_payload, store, req.customer_id)
            )
            if policy_block is not None:
                return JSONResponse(status_code=409, content=policy_block)

            result = await anyio.to_thread.run_sync(
                functools.partial(
                    deps.generate_jep(),
                    req.customer_id,
                    req.customer_name,
                    store,
                    text_runner,
                    feedback=req.feedback or "",
                    diagram_key=req.diagram_key,
                    diagram_url=req.diagram_url,
                    persistence_prefix=prefix,
                )
            )
            jep_state = await anyio.to_thread.run_sync(
                functools.partial(mark_jep_generated, store, req.customer_id)
            )
            return {
                "status": "ok",
                "agent_version": deps.agent_version,
                "customer_id": req.customer_id,
                "doc_type": "jep",
                "version": result["version"],
                "key": result["key"],
                "latest_key": result["latest_key"],
                "content": result["content"],
                "bom": result.get("bom"),
                "diagram_key": result.get("diagram_key"),
                "jep_state": jep_state,
                "errors": [],
            }
        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("Error in /jep/generate: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/jep/{customer_id}/latest")
    async def jep_latest(customer_id: str):
        store = deps.require_object_store()
        content = await anyio.to_thread.run_sync(functools.partial(get_latest_doc, store, "jep", customer_id))
        if content is None:
            raise HTTPException(status_code=404, detail=f"No JEP found for customer_id={customer_id!r}")
        jep_state = await anyio.to_thread.run_sync(functools.partial(sync_jep_state, store, customer_id))
        return {"status": "ok", "customer_id": customer_id, "doc_type": "jep", "content": content, "jep_state": jep_state}

    @router.get("/api/jep/{customer_id}/versions")
    async def jep_versions(customer_id: str):
        store = deps.require_object_store()
        versions = await anyio.to_thread.run_sync(functools.partial(list_versions, store, "jep", customer_id))
        return {"status": "ok", "customer_id": customer_id, "doc_type": "jep", "versions": versions}

    @router.post("/api/pov/approve")
    async def pov_approve(req: ApproveDocRequest):
        store = deps.require_object_store()
        try:
            key = await anyio.to_thread.run_sync(
                functools.partial(save_approved_doc, store, "pov", req.customer_id, req.content)
            )
            from agent.notifications import notify

            notify("pov_approved", req.customer_id, f"Approved POV uploaded for {req.customer_name}")
            return {"status": "ok", "customer_id": req.customer_id, "doc_type": "pov", "key": key}
        except Exception as exc:
            deps.logger.error("Error in /pov/approve: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/pov/{customer_id}/approved")
    async def pov_get_approved(customer_id: str):
        store = deps.require_object_store()
        content = await anyio.to_thread.run_sync(functools.partial(get_approved_doc, store, "pov", customer_id))
        if content is None:
            raise HTTPException(status_code=404, detail=f"No approved POV for customer_id={customer_id!r}")
        return {"status": "ok", "customer_id": customer_id, "doc_type": "pov", "content": content}

    @router.post("/api/jep/approve")
    async def jep_approve(req: ApproveDocRequest):
        store = deps.require_object_store()
        try:
            key = await anyio.to_thread.run_sync(
                functools.partial(save_approved_doc, store, "jep", req.customer_id, req.content)
            )
            jep_state = await anyio.to_thread.run_sync(functools.partial(mark_jep_approved, store, req.customer_id))
            from agent.notifications import notify

            notify("jep_approved", req.customer_id, f"Approved JEP uploaded for {req.customer_name}")
            return {
                "status": "ok",
                "customer_id": req.customer_id,
                "doc_type": "jep",
                "key": key,
                "jep_state": jep_state,
            }
        except Exception as exc:
            deps.logger.error("Error in /jep/approve: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/jep/{customer_id}/approved")
    async def jep_get_approved(customer_id: str):
        store = deps.require_object_store()
        content = await anyio.to_thread.run_sync(functools.partial(get_approved_doc, store, "jep", customer_id))
        if content is None:
            raise HTTPException(status_code=404, detail=f"No approved JEP for customer_id={customer_id!r}")
        jep_state = await anyio.to_thread.run_sync(functools.partial(sync_jep_state, store, customer_id))
        return {"status": "ok", "customer_id": customer_id, "doc_type": "jep", "content": content, "jep_state": jep_state}

    @router.post("/api/jep/revision-request")
    async def jep_revision_request(req: JepRevisionRequest):
        store = deps.require_object_store()
        try:
            jep_state = await anyio.to_thread.run_sync(
                functools.partial(jep_revision, store, req.customer_id, req.reason or "")
            )
            return {
                "status": "ok",
                "customer_id": req.customer_id,
                "doc_type": "jep",
                "revision_requested": True,
                "jep_state": jep_state,
            }
        except ValueError:
            jep_state = await anyio.to_thread.run_sync(functools.partial(sync_jep_state, store, req.customer_id))
            return JSONResponse(
                status_code=409,
                content={
                    "status": "policy_block",
                    "customer_id": req.customer_id,
                    "doc_type": "jep",
                    "reason_codes": ["REVISION_REQUEST_INVALID_STATE"],
                    "missing_fields": list(jep_state.get("missing_fields", [])),
                    "required_next_step": jep_state.get("required_next_step", ""),
                    "retry_instructions": ["Approve a generated JEP first, then request revision."],
                    "jep_state": jep_state,
                },
            )
        except Exception as exc:
            deps.logger.error("Error in /jep/revision-request: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/jep/kickoff")
    async def jep_kickoff(req: JepKickoffRequest):
        store = deps.require_object_store()

        def _run_kickoff():
            text_runner = getattr(deps.app().state, "text_runner", None)
            if text_runner is None:
                from agent.llm_inference_client import run_inference as _ri

                def text_runner(prompt, system_message=""):
                    inference = deps.inference_settings()
                    return _ri(
                        prompt,
                        endpoint=inference["endpoint"],
                        model_id=inference["model_id"],
                        compartment_id=inference["compartment_id"],
                        max_tokens=inference["max_tokens"],
                        temperature=inference["temperature"],
                        top_p=inference["top_p"],
                        top_k=inference["top_k"],
                        system_message=system_message,
                    )

            return deps.kickoff_jep()(req.customer_id, req.customer_name, store, text_runner)

        try:
            result = await anyio.to_thread.run_sync(_run_kickoff)
            jep_state = await anyio.to_thread.run_sync(functools.partial(sync_jep_state, store, req.customer_id))
            return {
                "status": "ok",
                "customer_id": req.customer_id,
                "questions": result["questions"],
                "extracted": result["extracted"],
                "questions_key": result["questions_key"],
                "jep_state": jep_state,
            }
        except Exception as exc:
            deps.logger.error("Error in /jep/kickoff: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/jep/answers")
    async def jep_save_answers(req: JepAnswersRequest):
        store = deps.require_object_store()
        try:
            existing = await anyio.to_thread.run_sync(functools.partial(get_jep_questions, store, req.customer_id))
            questions = existing.get("questions", [])
            await anyio.to_thread.run_sync(
                functools.partial(save_jep_questions, store, req.customer_id, questions, req.answers)
            )
            jep_state = await anyio.to_thread.run_sync(functools.partial(sync_jep_state, store, req.customer_id))
            return {"status": "ok", "customer_id": req.customer_id, "answers_saved": len(req.answers), "jep_state": jep_state}
        except Exception as exc:
            deps.logger.error("Error in /jep/answers: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/jep/{customer_id}/questions")
    async def jep_get_questions(customer_id: str):
        store = deps.require_object_store()
        data = await anyio.to_thread.run_sync(functools.partial(get_jep_questions, store, customer_id))
        jep_state = await anyio.to_thread.run_sync(functools.partial(sync_jep_state, store, customer_id))
        return {"status": "ok", "customer_id": customer_id, "jep_state": jep_state, **data}

    @router.post("/api/waf/generate")
    async def waf_generate(req: WafRequest):
        store = deps.require_object_store()
        text_runner = build_inference_runner(deps.app().state, inference_config=deps.writing_inference_config())

        try:
            result = await anyio.to_thread.run_sync(
                functools.partial(
                    deps.generate_waf(),
                    req.customer_id,
                    req.customer_name,
                    store,
                    text_runner,
                    feedback=req.feedback or "",
                )
            )
            content = deps.ensure_waf_test_pillars(result["content"])
            return {
                "status": "ok",
                "agent_version": deps.agent_version,
                "customer_id": req.customer_id,
                "doc_type": "waf",
                "version": result["version"],
                "key": result["key"],
                "latest_key": result["latest_key"],
                "content": content,
                "overall_rating": result["overall_rating"],
                "errors": [],
            }
        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error("Error in /waf/generate: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/waf/{customer_id}/latest")
    async def waf_latest(customer_id: str):
        store = deps.require_object_store()
        content = await anyio.to_thread.run_sync(functools.partial(get_latest_doc, store, "waf", customer_id))
        if content is None:
            raise HTTPException(status_code=404, detail=f"No WAF review found for customer_id={customer_id!r}")
        return {"status": "ok", "customer_id": customer_id, "doc_type": "waf", "content": deps.ensure_waf_test_pillars(content)}

    @router.get("/api/waf/{customer_id}/versions")
    async def waf_versions(customer_id: str):
        store = deps.require_object_store()
        versions = await anyio.to_thread.run_sync(functools.partial(list_versions, store, "waf", customer_id))
        return {"status": "ok", "customer_id": customer_id, "doc_type": "waf", "versions": versions}

    @router.post("/api/terraform/generate")
    async def terraform_generate(req: TerraformGenerateRequest):
        store = deps.require_object_store()
        return await generate_terraform_bundle(
            customer_id=req.customer_id,
            customer_name=req.customer_name,
            prompt=req.prompt or "",
            store=store,
            trace_id=deps.current_trace_id(),
        )

    @router.get("/api/terraform/{customer_id}/latest")
    async def terraform_latest(customer_id: str):
        store = deps.require_object_store()
        latest = await anyio.to_thread.run_sync(functools.partial(get_latest_terraform_bundle, store, customer_id))
        if not latest:
            raise HTTPException(status_code=404, detail=f"No Terraform bundle for customer_id={customer_id!r}")
        file_contents: dict[str, str] = {}
        for filename, key in (latest.get("files", {}) or {}).items():
            try:
                data = await anyio.to_thread.run_sync(functools.partial(store.get, key))
                file_contents[filename] = data.decode("utf-8", errors="replace")
            except KeyError:
                file_contents[filename] = ""
        return {
            "status": "ok",
            "trace_id": deps.current_trace_id(),
            "customer_id": customer_id,
            "doc_type": "terraform",
            "files": file_contents,
            "latest": latest,
        }

    @router.get("/api/terraform/{customer_id}/versions")
    async def terraform_versions(customer_id: str):
        store = deps.require_object_store()
        versions = await anyio.to_thread.run_sync(functools.partial(list_terraform_versions, store, customer_id))
        return {
            "status": "ok",
            "trace_id": deps.current_trace_id(),
            "customer_id": customer_id,
            "doc_type": "terraform",
            "versions": versions,
        }

    @router.get("/api/context/{customer_id}")
    async def get_customer_context(customer_id: str):
        store = deps.require_object_store()
        try:
            context = await anyio.to_thread.run_sync(functools.partial(read_context, store, customer_id, ""))
            return {"status": "ok", "customer_id": customer_id, "context": context}
        except Exception as exc:
            deps.logger.error("Error in /context/%s: %s", customer_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/terraform/{customer_id}/download/{filename}")
    async def terraform_download(customer_id: str, filename: str):
        store = deps.require_object_store()
        content = await anyio.to_thread.run_sync(functools.partial(get_terraform_file, store, customer_id, filename))
        if content is None:
            raise HTTPException(status_code=404, detail=f"Terraform file {filename!r} not found for customer {customer_id!r}")
        return Response(
            content=content,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router


def jep_revision(store, customer_id: str, reason: str) -> dict:
    return request_jep_revision(store, customer_id, reason)
