import warnings

import pytest

from skillforge import Forge, MemorySnapshot, ToolResult


warnings.filterwarnings(
    "ignore", "Unknown pytest.mark.asyncio", pytest.PytestUnknownMarkWarning
)
pytestmark = [pytest.mark.asyncio, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class AWSMemory:
    def assemble(self, *, session_id, context, user_message):
        return MemorySnapshot(
            session_id=session_id,
            facts={"architecture_defined": True},
            raw=context,
        )

    def update(self, *, session_id, tool_name, result, context):
        return context


async def ec2_handler(args, *, memory, context, trace_id):
    return ToolResult(summary="EC2 sized", status="ok")


async def cfn_handler(args, *, memory, context, trace_id):
    if not memory.facts.get("architecture_defined"):
        return ToolResult(
            summary="needs arch",
            status="needs_input",
            clarification="Define architecture first.",
        )
    return ToolResult(summary="CloudFormation ready", status="ok")


class FakeHatEngine:
    def get_hat_tool_definitions(self):
        return []

    def apply_hat(self, active, name):
        return active + [name]

    def drop_hat(self, active, name):
        return [h for h in active if h != name]

    def warn_stale_hats(self, active, rounds, max_rounds=5):
        return []

    def inject_hats(self, prompt, active):
        return prompt


class FakeTextRunner:
    def __init__(self):
        self.responses = ['{"tool": "size_ec2", "args": {}}', "EC2 sizing complete."]

    async def __call__(self, prompt, system_message, label):
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_aws_forge_end_to_end():
    forge = Forge(
        base_system_prompt="You are an AWS solutions architect.",
        hat_engine=FakeHatEngine(),
        memory=AWSMemory(),
        text_runner=FakeTextRunner(),
    )
    forge.register_tool("size_ec2", ec2_handler, memory_contract=True)
    forge.register_tool("generate_cloudformation", cfn_handler, memory_contract=True)

    result = await forge.run_turn(
        session_id="aws-1", user_message="Size my app", context={}
    )

    assert result.reply == "EC2 sizing complete."
    assert "size_ec2" in result.artifacts or len(result.tool_calls) == 1
