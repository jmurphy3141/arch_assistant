import json
import sys
import types
import json

import pytest

from agent import context_store, sub_agent_client
from agent.persistence_objectstore import InMemoryObjectStore
from agent.tools import specialists as specialists_module
from agent.tools.specialists import REQUIRED_WAF_PILLARS, JepHandler, PovHandler, WafHandler
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def memory_arg_stubs(monkeypatch):
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_hydrate_tool_args_from_context",
        lambda tool_name, args, context, decision_context, user_message: args,
    )
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_enforce_memory_contract_on_tool_args",
        lambda tool_name, args, context: args,
    )
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_pov_has_sufficient_context",
        lambda context, decision_context, args, user_message: True,
    )
    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_pov_targeted_questions",
        lambda: [{"id": "pov.scope", "question": "What audience?"}],
    )


def make_memory():
    return MemorySnapshot(
        session_id="s1",
        decision_context={"constraints": {"region": "us-ashburn-1"}},
        raw={"customer_id": "cust-1"},
    )


def install_jep_lifecycle_stub(
    monkeypatch,
    *,
    policy_block=None,
    generated_state=None,
):
    existing = sys.modules.get("agent.jep_lifecycle")
    module = types.ModuleType("agent.jep_lifecycle")
    module.generate_policy_block_payload = lambda store, customer_id: policy_block
    module.mark_generated = lambda store, customer_id: (
        generated_state or {"jep_state": "generated"}
    )
    if existing is not None:
        monkeypatch.setattr(existing, "generate_policy_block_payload", module.generate_policy_block_payload)
        monkeypatch.setattr(existing, "mark_generated", module.mark_generated)
    monkeypatch.setitem(sys.modules, "agent.jep_lifecycle", module)


def stub_save_doc(monkeypatch, key, version=1):
    monkeypatch.setattr(
        specialists_module.document_store,
        "save_doc",
        lambda store, doc_type, customer_id, content, metadata: {
            "key": key,
            "version": version,
        },
    )


def stub_save_jep_docx(monkeypatch, key="docs/jep_v1.docx"):
    monkeypatch.setattr(
        specialists_module.document_store,
        "save_jep_docx",
        lambda store, customer_id, version, content, metadata: {
            "docx_key": key,
            "docx_filename": f"v{version}.docx",
            "docx_content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )


def valid_jep_markdown() -> str:
    return """# Joint Execution Plan - ACME

## Executive Summary
ACME will validate an OCI POC for VMware log analytics over 6 weeks.

## Objectives
1. Validate OCI ingestion for 10 GB/day of telemetry.
2. Confirm query latency targets for 50 users.
3. Validate operating handoff and support readiness.

## Scope
### In Scope
- OCI Logging Analytics, Object Storage, Functions, Vault, and Vector Search.

### Out of Scope
- Production cutover and migration of non-telemetry workloads.

## POC Architecture
The POC uses OCI Logging Analytics, Object Storage, Vector Search, OCI Functions, Vault, and private networking.

## Phased Execution Plan
| Phase | Weeks | Activities | Exit Gate |
|-------|-------|------------|-----------|
| Phase 1 - Assessment | Weeks 1-2 | Confirm tenancy quota, firewall paths, and baseline telemetry flow | Access and quota confirmed |
| Phase 2 - Build | Weeks 3-4 | Provision OCI services and deploy the log ingestion path | Workload deployed and ready for measurement |
| Phase 3 - Validate | Weeks 5-6 | Measure success criteria, run go/no-go review, capture sign-off, and define fallback if criteria fail | Customer signs go/no-go decision |

## Success Criteria
| # | Criterion | Target | Validation Week |
|---|-----------|--------|-----------------|
| 1 | Log ingestion sustained at 10 GB/day | >= 10 GB/day | Week 5 |
| 2 | Response latency for analyst queries | < 5 seconds | Week 6 |
| 3 | User acceptance test coverage | >= 50 users | Week 6 |

## Resource Plan
| Organization | Name | Role | Weekly Hours |
|--------------|------|------|--------------|
| Oracle | TBD | Solutions Architect | 4 |
| ACME | TBD | Customer Technical Lead | 4 |

## Risk Registry
| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| ACME firewall blocks OCI log ingestion | H | H | Test connectivity in Week 1 | ACME Technical Lead |
| OCI tenancy OCPU quota delays required functions | M | H | Confirm quota before Phase 2 | Oracle SA |
| VMware telemetry volume exceeds POC window | M | M | Use a representative 10 GB/day subset | ACME Engineer |

## Approvals
| Approver | Organization | Role | Signature | Date |
|----------|--------------|------|-----------|------|
| TBD | Oracle | Oracle Solutions Architect |  | TBD |
| TBD | ACME | Customer Technical Lead |  | TBD |
"""


async def test_jep_review_counts_percentage_success_criteria():
    section = """
| Criterion | Target |
|-----------|--------|
| Availability | >= 99.9% |
| Test coverage | >= 95% |
| Error rate | < 1% |
"""

    assert specialists_module._count_numeric_criteria(section) == 3


async def test_jep_review_accepts_subsections_and_common_numeric_thresholds():
    content = valid_jep_markdown().replace(
        "| Phase | Weeks | Activities | Exit Gate |",
        "### Phase 1 - Assessment\nConfirm access.\n\n"
        "### Phase 2 - Build\nDeploy services.\n\n"
        "### Phase 3 - Validate\nRun go/no-go sign-off with fallback.\n\n"
        "| Phase | Weeks | Activities | Exit Gate |",
    ).replace(
        "| 1 | Log ingestion sustained at 10 GB/day | >= 10 GB/day | Week 5 |",
        "| 1 | POC spend | < $250 | Week 5 |",
    ).replace(
        "| 3 | User acceptance test coverage | >= 50 users | Week 6 |",
        "| 3 | Response time | < 5s | Week 6 |",
    )

    assert specialists_module._jep_writer_review_findings(content) == []


async def test_jep_review_accepts_bold_required_headings():
    content = valid_jep_markdown()
    for heading in specialists_module._JEP_REQUIRED_SECTIONS:
        content = content.replace(f"## {heading}", f"**{heading}**")

    assert specialists_module._jep_writer_review_findings(content) == []


async def test_jep_review_accepts_numbered_risks_and_approval_decision_gate():
    content = valid_jep_markdown()
    risk_start = content.index("## Risk Registry")
    approvals_start = content.index("## Approvals")
    content = (
        content[:risk_start]
        + """## Risk Registry
1. **Firewall risk:** High impact. Mitigation: test connectivity. Owner: Customer lead.
2. **Quota risk:** Medium probability, high impact. Mitigation: request quota. Owner: Oracle SA.
3. **Data volume risk:** Medium impact. Mitigation: use a subset. Owner: Customer engineer.

"""
        + content[approvals_start:]
    )
    content = content.replace(
        "run go/no-go review, capture sign-off, and define fallback if criteria fail",
        "measure the success criteria",
    ).replace(
        "## Approvals\n",
        "## Approvals\nRun the go/no-go review here, capture sign-off, and use the fallback if criteria fail.\n\n",
    )

    assert specialists_module._jep_writer_review_findings(content) == []


async def test_jep_review_accepts_two_selected_poc_criteria():
    content = valid_jep_markdown().replace(
        "| 3 | User acceptance test coverage | >= 50 users | Week 6 |\n",
        "",
    )

    assert specialists_module._jep_writer_review_findings(content)
    assert specialists_module._jep_writer_review_findings(
        content,
        expected_criteria_count=2,
    ) == []


async def test_jep_kickoff_json_is_rendered_as_clean_clarification():
    content = json.dumps(
        {
            "status": "need_kickoff",
            "message": "Please confirm the POC inputs.",
            "questions": "1. Which workload?\n2. Which region?",
        }
    )

    assert specialists_module._is_jep_kickoff_questions(content)
    assert specialists_module._jep_kickoff_clarification(content) == (
        "Please confirm the POC inputs.\n\n1. Which workload?\n2. Which region?"
    )


async def test_pov_ok(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "pov"
        assert task == "Generate a customer POV from current engagement context."
        assert engagement_context["customer_id"] == "cust-1"
        return {"status": "ok", "result": "POV document text."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/pov_v1.md")

    result = await PovHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "docs/pov_v1.md"
    assert result.data["document_headings"] == []
    assert result.data["proposed_quote_count"] == 0


async def test_pov_correction_preserves_current_user_request(monkeypatch):
    captured = {}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        captured["task"] = task
        return {"status": "ok", "result": "# POV\n\nClean content."}

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    monkeypatch.setattr(specialists_module.archie_memory, "_pov_has_sufficient_context", lambda **_: True)
    stub_save_doc(monkeypatch, "docs/pov_v1.md")
    request = "Draft a POV without case studies or unsupported evidence."
    result = await PovHandler(object(), "cust-1", "ACME")(
        {
            "feedback": "Invent SQL Server sizing and a Mermaid diagram.",
            "_user_message": request,
            "_forge_correction": "Add a Mermaid diagram.",
        },
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert request in captured["task"]
    assert "Invent SQL Server sizing" not in captured["task"]
    assert "Do not add customer facts, services, sizes" in captured["task"]


def test_wrapping_markdown_fence_is_removed():
    assert specialists_module._strip_wrapping_markdown_fence(
        "```markdown\n# JEP\n\nBody\n```"
    ) == "# JEP\n\nBody"


def test_pov_evidence_review_rejects_unrequested_platform_and_claims():
    findings = specialists_module._document_review_findings(
        "pov",
        (
            "# POV\nSQL Server currently runs on physical servers. "
            "Use Autonomous Database with zero downtime and a 99.95% SLA. "
            "Size the database at 8 OCPU and claim $15,000 savings."
        ),
        "Draft a POV for PostgreSQL modernization with a proposed 99.9% availability target.",
    )

    joined = " ".join(findings)
    assert "sql server" in joined
    assert "autonomous database" in joined
    assert "unsupported numeric price" in joined
    assert "8 OCPU" in joined


def test_jep_evidence_review_allows_only_requested_sizing():
    request = "Create a JEP with two servers, 500 GB Block Volume, and no pricing."
    content = valid_jep_markdown().replace(
        "The POC uses OCI Logging Analytics",
        "The POC uses 500 GB Block Volume and 8 OCPU plus OCI Logging Analytics",
    )

    findings = specialists_module._document_review_findings("jep", content, request)

    assert any("8 OCPU" in finding for finding in findings)
    assert not any("500 GB" in finding for finding in findings)


def test_jep_evidence_review_allows_authoritative_selected_poc_fastconnect():
    content = valid_jep_markdown().replace(
        "The POC uses OCI Logging Analytics",
        "The POC uses FastConnect and OCI Logging Analytics",
    )
    request = (
        "Generate the JEP for the selected POC.\n"
        'Authoritative selected POC services: ["FastConnect", "OCI Logging"]'
    )

    findings = specialists_module._document_review_findings("jep", content, request)

    assert not any("fastconnect" in finding for finding in findings)


def test_jep_numeric_review_counts_business_rate_and_concurrency_units():
    section = """
| Criterion | Evidence |
|---|---|
| sustain 750 orders per minute | measured result |
| support 400 concurrent portal sessions | measured result |
| keep p95 latency under 450 milliseconds | measured result |
| process 250 supplier orders per minute | measured result |
"""

    assert specialists_module._count_numeric_criteria(section) == 4


def test_pov_evidence_review_treats_rps_as_requests_per_second():
    findings = specialists_module._document_review_findings(
        "pov",
        "The proposed validation target is 220 RPS.",
        "Validate at 220 requests per second.",
    )

    assert not any("unsupported sizing values" in finding for finding in findings)


async def test_pov_insufficient_context(monkeypatch):
    called = False

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "POV document text."}

    monkeypatch.setattr(
        specialists_module.archie_memory,
        "_pov_has_sufficient_context",
        lambda context, decision_context, args, user_message: False,
    )
    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await PovHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert called is False


async def test_jep_ok(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "jep"
        return {"status": "ok", "result": valid_jep_markdown()}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/jep_v1.md")
    stub_save_jep_docx(monkeypatch, "docs/jep_v1.docx")

    result = await JepHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.data["lock_outcome"] == "allowed"
    assert result.data["docx_key"] == "docs/jep_v1.docx"


async def test_jep_renders_docx_before_saving_either_artifact(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "ok", "result": valid_jep_markdown()}

    saved = False

    def fail_save(*_args, **_kwargs):
        nonlocal saved
        saved = True
        raise AssertionError("Markdown must not be saved after DOCX rendering fails")

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    monkeypatch.setattr(
        specialists_module,
        "render_jep_docx",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("template failure")),
    )
    monkeypatch.setattr(specialists_module.document_store, "save_doc", fail_save)

    result = await JepHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert result.data["persistence_stage"] == "docx_render"
    assert saved is False


async def test_jep_docx_persistence_failure_restores_prior_latest(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )
    store = InMemoryObjectStore()
    prior = specialists_module.document_store.save_doc(
        store, "jep", "cust-1", "# Prior approved JEP", {"source": "test"}
    )
    specialists_module.document_store.save_jep_docx(
        store, "cust-1", prior["version"], b"prior-docx", {"source": "test"}
    )
    prior_markdown = store.get("jep/cust-1/LATEST.md")
    prior_docx = store.get("jep/cust-1/LATEST.docx")

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "ok", "result": valid_jep_markdown()}

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    monkeypatch.setattr(specialists_module, "render_jep_docx", lambda *_args, **_kwargs: b"new-docx")
    store.inject_put_failure("v2.docx")

    result = await JepHandler(store, "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert result.data["persistence_stage"] == "docx"
    assert store.get("jep/cust-1/LATEST.md") == prior_markdown
    assert store.get("jep/cust-1/LATEST.docx") == prior_docx


async def test_jep_vague_update_without_existing_jep_needs_input(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )
    called = False

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": valid_jep_markdown()}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await JepHandler(InMemoryObjectStore(), "cust-1", "ACME")(
        {"feedback": "Please update the JEP"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "needs_input"
    assert "existing JEP" in result.summary
    assert called is False


async def test_jep_revision_does_not_use_unapproved_latest_version(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )
    store = InMemoryObjectStore()
    specialists_module.document_store.save_doc(
        store, "jep", "cust-1", "# Unapproved draft", {"source": "test"}
    )
    called = False

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": valid_jep_markdown()}

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    result = await JepHandler(store, "cust-1", "ACME")(
        {"feedback": "Please update the JEP"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "needs_input"
    assert called is False


async def test_jep_revision_passes_latest_prior_version(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )
    store = InMemoryObjectStore()
    specialists_module.document_store.save_doc(
        store,
        "jep",
        "cust-1",
        "# Existing JEP v3\n\n## Executive Summary\nUse this as the base.",
        {"source": "test"},
    )
    specialists_module.document_store.save_approved_doc(
        store,
        "jep",
        "cust-1",
        "# Existing JEP v3\n\n## Executive Summary\nUse this as the base.",
    )
    captured_context = {}
    captured_task = {"value": ""}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "jep"
        captured_task["value"] = task
        captured_context.update(engagement_context)
        return {"status": "ok", "result": valid_jep_markdown()}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/jep_v2.md", version=2)
    stub_save_jep_docx(monkeypatch, "docs/jep_v2.docx")
    ctx = {
        "agents": {
            "diagram": {
                "version": 4,
                "diagram_name": "askrga-rag-genai",
                "diagram_key": "diagrams/cust-1/askrga/v4/diagram.drawio",
                "deployment_summary": "RAG retrieval and GenAI response path",
            },
            "bom": {
                "version": 5,
                "summary": "100GB repository and 1M tokens/day",
                "estimated_monthly_cost": 1234,
            },
        }
    }
    context_store.set_resolved_decisions(
        ctx,
        poc={
            "recommended_option": "AskRGA RAG + GenAI PoC",
            "success_criteria": "Answer telemetry questions with citations in under 5 seconds.",
        },
    )

    result = await JepHandler(store, "cust-1", "ACME")(
        {"feedback": "Please update the JEP"},
        memory=make_memory(),
        context=ctx,
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert "Existing JEP v3" in captured_context["prior_version"]
    assert captured_context["prior_version_key"] == "approved/cust-1/jep.md"
    assert captured_context["prior_version_approved"] is True
    assert captured_context["feedback"] == "Please update the JEP"
    assert captured_context["artifact_context"]["diagram"]["diagram_name"] == "askrga-rag-genai"
    assert "JEP REVISION GROUNDING" in captured_task["value"]
    assert "RELATED ARTIFACT CONTEXT" in captured_task["value"]
    assert "AskRGA RAG + GenAI PoC" in captured_task["value"]


async def test_jep_review_failure_is_not_sent_back_for_prose_repair(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )
    store = InMemoryObjectStore()
    specialists_module.document_store.save_doc(
        store,
        "jep",
        "cust-1",
        "# Existing JEP\n\n## Executive Summary\nUse this as the base.",
        {"source": "test"},
    )
    specialists_module.document_store.save_approved_doc(
        store,
        "jep",
        "cust-1",
        "# Existing JEP\n\n## Executive Summary\nUse this as the base.",
    )
    calls: list[str] = []

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "jep"
        calls.append(task)
        return {
            "status": "ok",
            "result": "# Joint Execution Plan - ACME\n\n## Executive Summary\nPartial update only.",
        }

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/jep_v2.md", version=2)
    stub_save_jep_docx(monkeypatch, "docs/jep_v2.docx")

    result = await JepHandler(store, "cust-1", "ACME")(
        {"feedback": "Please update the JEP"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "blocked"
    assert result.data["repair_attempted"] is False
    assert result.data["repair_mode"] == "structured_fields_only"
    assert result.data["review_findings"]
    assert len(calls) == 1


async def test_jep_blocks_self_referential_revision_before_save(monkeypatch):
    install_jep_lifecycle_stub(
        monkeypatch, policy_block=None, generated_state={"jep_state": "generated"}
    )
    store = InMemoryObjectStore()
    specialists_module.document_store.save_doc(
        store,
        "jep",
        "cust-1",
        "# Existing JEP\n\n## Executive Summary\nUse this as the base.",
        {"source": "test"},
    )
    specialists_module.document_store.save_approved_doc(
        store,
        "jep",
        "cust-1",
        "# Existing JEP\n\n## Executive Summary\nUse this as the base.",
    )
    saved = False
    call_count = 0

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal call_count
        assert name == "jep"
        call_count += 1
        return {
            "status": "ok",
            "result": """**Updated JEP (Job Execution Plan) - Revision 2.0**

### Summary of Changes
- **Revision Trigger**: Treated as a revision request.

### 1. Objective
Generate and maintain an iterative Job Execution Plan.

### 6. Self-Update Clause
- On next revision request, auto-increment the version.
""",
        }

    def fake_save_doc(*_args, **_kwargs):
        nonlocal saved
        saved = True
        raise AssertionError("invalid JEP should not be persisted")

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    monkeypatch.setattr(specialists_module.document_store, "save_doc", fake_save_doc)

    result = await JepHandler(store, "cust-1", "ACME")(
        {"feedback": "Please update the JEP"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "blocked"
    assert "couldn't save the JEP update" in result.summary
    assert "Job Execution Plan" in result.data["review_findings"][0]
    assert call_count == 1
    assert saved is False


async def test_jep_locked(monkeypatch):
    called = False
    install_jep_lifecycle_stub(
        monkeypatch,
        policy_block={
            "jep_state": {"state": "approved"},
            "reason_codes": ["already_approved"],
            "required_next_step": "Request revision.",
        },
    )

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "JEP document text."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await JepHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert called is False


async def test_waf_ok(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "waf"
        assert "[CONFIRMED CONTEXT]" in task
        return {"status": "ok", "result": "WAF review text."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/waf_v1.md")

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "docs/waf_v1.md"


def _waf_payload(pillars):
    return json.dumps(
        {
            "overall_score": 4,
            "pillars": {
                pillar: {"score": 4, "findings": []}
                for pillar in pillars
            },
        }
    )


async def test_waf_blocks_json_result_missing_required_pillar(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "waf"
        return {
            "status": "ok",
            "result": _waf_payload(
                [
                    "Security",
                    "Reliability",
                    "Performance Efficiency",
                    "Cost Optimisation",
                    "Operational Excellence",
                ]
            ),
        }

    def fail_save_doc(*args, **kwargs):
        raise AssertionError("incomplete WAF result must not be saved")

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    monkeypatch.setattr(specialists_module.document_store, "save_doc", fail_save_doc)

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert result.data == {"missing_pillars": ["Continuous Improvement"]}
    assert "Continuous Improvement" in result.summary


async def test_waf_accepts_json_result_with_all_required_pillars(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "waf"
        return {
            "status": "ok",
            "result": _waf_payload(
                [
                    "Security",
                    "Reliability",
                    "Performance Efficiency",
                    "Cost Optimisation",
                    "Operational Excellence",
                    "Continuous Improvement",
                ]
            ),
        }

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )
    stub_save_doc(monkeypatch, "docs/waf_v1.md")

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "docs/waf_v1.md"


async def test_waf_needs_input(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "needs_input", "result": "Please provide architecture context."}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert result.clarification == "Please provide architecture context."


async def test_specialist_sub_agent_error(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        raise sub_agent_client.SubAgentError("timeout")

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await WafHandler(object(), "cust-1", "ACME")(
        {}, memory=make_memory(), context={"agents": {}}, trace_id="trace-1"
    )

    assert result.status == "blocked"
