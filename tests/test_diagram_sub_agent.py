import json

import pytest

from sub_agents.diagram.server import handle
from sub_agents.models import A2ARequest


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_diagram_sub_agent_returns_needs_input_when_request_has_no_services():
    response = await handle(
        A2ARequest(
            task="I need the diagram updated to include the correct OCI Icons",
            trace_id="trace-1",
        )
    )

    assert response.status == "needs_input"
    questions = json.loads(response.result)
    assert [question["id"] for question in questions] == ["diagram.source", "workload.components"]
    assert response.trace["stage"] == "freeform_input_parsing"
