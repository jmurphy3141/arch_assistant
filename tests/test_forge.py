import warnings

import pytest

from skillforge import Forge, MemorySnapshot, ToolResult, TurnResult
from skillforge.forge import _append_result_with_evidence, _clean_simple_question_claims


warnings.filterwarnings(
    "ignore", "Unknown pytest.mark.asyncio", pytest.PytestUnknownMarkWarning
)
pytestmark = [pytest.mark.asyncio, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeHatEngine:
    def __init__(self):
        self.apply_calls = []

    def get_hat_tool_definitions(self):
        return []

    def apply_hat(self, active, name):
        self.apply_calls.append((active, name))
        return active + [name]

    def drop_hat(self, active, name):
        return [h for h in active if h != name]

    def warn_stale_hats(self, active, rounds, max_rounds=5):
        return []

    def inject_hats(self, prompt, active):
        return prompt


class FakeMemory:
    def __init__(self):
        self.update_calls = []

    def assemble(self, *, session_id, context, user_message):
        return MemorySnapshot(session_id="s1")

    def update(self, *, session_id, tool_name, result, context):
        self.update_calls.append(
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "result": result,
                "context": context,
            }
        )
        return context


class QueueTextRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, prompt, system_message, label):
        self.calls.append(
            {"prompt": prompt, "system_message": system_message, "label": label}
        )
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


async def ok_handler(args, *, memory, context, trace_id):
    return ToolResult(summary="done", status="ok", artifact_key="key/1")


def make_forge(text_runner, **kwargs):
    forge = Forge(
        base_system_prompt="test",
        hat_engine=kwargs.pop("hat_engine", FakeHatEngine()),
        memory=kwargs.pop("memory", FakeMemory()),
        text_runner=text_runner,
        **kwargs,
    )
    forge.register_tool("test_tool", ok_handler)
    return forge


@pytest.mark.asyncio
async def test_plain_reply():
    forge = make_forge(QueueTextRunner("Here is my answer"))

    result = await forge.run_turn(session_id="s1", user_message="Hi", context={})

    assert result.reply == "Here is my answer"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_tool_call_ok():
    forge = make_forge(
        QueueTextRunner('{"tool": "test_tool", "args": {}}', "Done.")
    )

    result = await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert result.artifacts == {"test_tool": "key/1"}
    assert len(result.tool_calls) == 1


@pytest.mark.asyncio
async def test_step3_poc_override_does_not_hijack_artifact_request():
    planning = (
        "STEP 1 - UNDERSTAND:\n"
        "The user wants a POC BOM and architecture diagram.\n"
        "STEP 2 - MEMORY ASSESSMENT:\n"
        "Enough context is available for a first pass.\n"
        "STEP 3 - PLAN + HAT SELECTION:\n"
        "Evaluate the POC and generate requested deliverables."
    )
    runner = QueueTextRunner(planning, "Done.")
    forge = make_forge(runner, step3_planning=True)

    await forge.run_turn(
        session_id="s1",
        user_message="generate the BOM and diagram for the AskRGA POC",
        context={},
    )

    assert "FORGE OVERRIDE: poc_recommendation absent" not in runner.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_needs_input_surfaces():
    async def needs_input_handler(args, *, memory, context, trace_id):
        return ToolResult(
            summary="x",
            status="needs_input",
            clarification="Please provide region.",
        )

    forge = Forge(
        base_system_prompt="test",
        hat_engine=FakeHatEngine(),
        memory=FakeMemory(),
        text_runner=QueueTextRunner('{"tool": "needs_tool", "args": {}}'),
    )
    forge.register_tool("needs_tool", needs_input_handler)

    result = await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert result.reply == "Please provide region."


@pytest.mark.asyncio
async def test_needs_input_retry():
    async def needs_input_handler(args, *, memory, context, trace_id):
        return ToolResult(
            summary="x",
            status="needs_input",
            clarification="Please provide region.",
        )

    forge = Forge(
        base_system_prompt="test",
        hat_engine=FakeHatEngine(),
        memory=FakeMemory(),
        text_runner=QueueTextRunner('{"tool": "needs_tool", "args": {}}', "Got it."),
    )
    forge.register_tool(
        "needs_tool", needs_input_handler, retry_on_needs_input=True
    )

    result = await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert result.reply == "Got it."


@pytest.mark.asyncio
async def test_blocked_removes_artifact():
    async def blocked_handler(args, *, memory, context, trace_id):
        return ToolResult(summary="blocked", status="blocked")

    forge = Forge(
        base_system_prompt="test",
        hat_engine=FakeHatEngine(),
        memory=FakeMemory(),
        text_runner=QueueTextRunner('{"tool": "blocked_tool", "args": {}}', "Done."),
    )
    forge.register_tool("blocked_tool", blocked_handler)

    result = await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert result.artifacts == {}


@pytest.mark.asyncio
async def test_safety_check_blocks():
    async def artifact_handler(args, *, memory, context, trace_id):
        return ToolResult(summary="ok", status="ok", artifact_key="k")

    forge = Forge(
        base_system_prompt="test",
        hat_engine=FakeHatEngine(),
        memory=FakeMemory(),
        text_runner=QueueTextRunner('{"tool": "safe_tool", "args": {}}', "Done."),
    )
    forge.register_tool(
        "safe_tool",
        artifact_handler,
        safety_checker=lambda name, result: (False, "too expensive"),
    )

    result = await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert result.artifacts == {}
    assert "too expensive" in result.tool_calls[-1].result.summary


@pytest.mark.asyncio
async def test_hat_activation():
    hat_engine = FakeHatEngine()
    forge = Forge(
        base_system_prompt="test",
        hat_engine=hat_engine,
        memory=FakeMemory(),
        text_runner=QueueTextRunner(
            '{"tool": "use_hat_critic", "args": {}}', "Done."
        ),
    )

    await forge.run_turn(session_id="s1", user_message="Review it", context={})

    assert hat_engine.apply_calls == [([], "critic")]


@pytest.mark.asyncio
async def test_skill_guidance_injected():
    captured = {}

    async def guided_handler(args, *, memory, context, trace_id):
        captured["args"] = args
        return ToolResult(summary="ok", status="ok")

    forge = Forge(
        base_system_prompt="test",
        hat_engine=FakeHatEngine(),
        memory=FakeMemory(),
        text_runner=QueueTextRunner(
            '{"tool": "guided_tool", "args": {"prompt": "original"}}', "Done."
        ),
    )
    forge.register_tool(
        "guided_tool",
        guided_handler,
        skill_guidance="## Guidance\nBe precise.",
        memory_contract=False,
    )

    await forge.run_turn(session_id="s1", user_message="Run it", context={})

    injected = captured["args"].get("prompt", "") or captured["args"].get("task", "")
    assert injected.startswith("## Guidance")


@pytest.mark.asyncio
async def test_memory_refreshed_after_tool():
    memory = FakeMemory()

    async def memory_handler(args, *, memory, context, trace_id):
        return ToolResult(summary="ok", status="ok")

    forge = Forge(
        base_system_prompt="test",
        hat_engine=FakeHatEngine(),
        memory=memory,
        text_runner=QueueTextRunner('{"tool": "memory_tool", "args": {}}', "Done."),
    )
    forge.register_tool("memory_tool", memory_handler, memory_contract=True)

    await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert len(memory.update_calls) == 1


@pytest.mark.asyncio
async def test_unknown_tool_continues():
    forge = make_forge(
        QueueTextRunner('{"tool": "nonexistent", "args": {}}', "Fallback reply.")
    )

    result = await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert result.reply == "Fallback reply."


def test_system_prompt_cached():
    forge = make_forge(QueueTextRunner("Done."))

    first = forge._get_system_msg()
    second = forge._get_system_msg()

    assert first is second


@pytest.mark.asyncio
async def test_max_iterations_exhausted():
    forge = make_forge(
        QueueTextRunner('{"tool": "test_tool", "args": {}}'), max_iterations=3
    )

    result = await forge.run_turn(session_id="s1", user_message="Run it", context={})

    assert isinstance(result, TurnResult)
    assert len(result.tool_calls) == 3
    assert result.reply == "Done — the test tool is ready: `key/1`."
    assert "done" in result.reply.lower()
    assert "key/1" in result.reply


@pytest.mark.asyncio
async def test_final_synthesis_cleans_overstructured_simple_question():
    runner = QueueTextRunner(
        (
            "## Recommendation\n"
            "- First point.\n"
            "- Second point.\n"
            "- Third point.\n"
            "- Fourth point."
        ),
        "OCI is the better fit here because the workload is Oracle Database-heavy and cost-sensitive.",
    )
    forge = make_forge(runner)

    result = await forge.run_turn(
        session_id="s1",
        user_message="Why would we pick OCI here?",
        context={},
    )

    assert result.reply == (
        "OCI is the better fit here because the workload is Oracle Database-heavy and cost-sensitive."
    )
    assert "Recommendation" not in result.reply
    assert "- First point" not in result.reply


@pytest.mark.asyncio
async def test_artifact_tool_uses_clean_deterministic_completion_reply():
    forge = make_forge(
        QueueTextRunner(
            '{"tool": "generate_pov", "args": {}}',
            "A long promotional response with unsupported claims and internal details.",
        )
    )

    async def handler(args, *, memory, context, trace_id):
        return ToolResult(summary="POV v1 saved.", status="ok", artifact_key="pov/acme/v1.md")

    forge.register_tool("generate_pov", handler)
    result = await forge.run_turn(
        session_id="s1",
        user_message="Generate the POV.",
        context={},
    )

    assert result.reply == "Done — the POV is ready: `pov/acme/v1.md`."
    assert "unsupported claims" not in result.reply


def test_simple_question_claim_cleaner_drops_unsupported_precision():
    reply = (
        "I recommend this design because it isolates each tier and uses managed services.\n\n"
        "It cuts operations by 70% and costs $1,200 per month. Competitors are higher.\n\n"
        "The WAF and private subnets reduce exposure, while autoscaling handles retail peaks."
    )

    cleaned = _clean_simple_question_claims("Why this architecture?", reply)

    assert "isolates each tier" in cleaned
    assert "WAF and private subnets" in cleaned
    assert "70%" not in cleaned
    assert "$1,200" not in cleaned
    assert "Competitors are higher" not in cleaned


def test_response_cleaner_splits_inline_markdown_bullets():
    from skillforge.forge import _strip_internal_response_artifacts

    cleaned = _strip_internal_response_artifacts(
        "Traffic flow:\n- **Ingress**: WAF. - **Web**: Compute. - **Data**: Database."
    )

    assert "WAF.\n- **Web**" in cleaned
    assert "Compute.\n- **Data**" in cleaned


def test_diagram_review_evidence_includes_xml_presence_and_labels():
    result = ToolResult(
        summary="Diagram generated. Key: diagram.drawio",
        status="ok",
        artifact_key="diagram.drawio",
        data={
            "drawio_xml": (
                '<mxfile><diagram><mxCell value="OCI WAF"/>'
                '<mxCell value="Private Database"/></diagram></mxfile>'
            ),
            "node_count": 2,
        },
    )

    prompt = _append_result_with_evidence("prompt", "generate_diagram", result)

    assert '"drawio_xml_present":true' in prompt
    assert "OCI WAF" in prompt
    assert "Private Database" in prompt


def test_format_instruction_in_system_prompt():
    forge = make_forge(QueueTextRunner("Done."))

    system_msg = forge._get_system_msg()

    assert '{"tool"' in system_msg
    assert "Tool call format" in system_msg


def test_critique_enabled_stored():
    async def handler(args, *, memory, context, trace_id):
        return ToolResult(summary="ok", status="ok")

    forge = Forge(
        base_system_prompt="test",
        hat_engine=FakeHatEngine(),
        memory=FakeMemory(),
        text_runner=QueueTextRunner("Done."),
    )
    forge.register_tool("tool_name", handler, critique_enabled=True)

    assert forge._registry.get("tool_name").critique_enabled is True
