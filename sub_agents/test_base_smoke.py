from fastapi.testclient import TestClient

from sub_agents.base import make_agent_app
from sub_agents.models import A2ARequest, A2AResponse, AgentCard


card = AgentCard(
    name="test",
    description="test agent",
    inputs={"required": ["task"], "optional": []},
    output="text",
    llm_model_id="mock",
)


async def handler(req: A2ARequest) -> A2AResponse:
    return A2AResponse(result="ok", status="ok")


app = make_agent_app(card, handler)
client = TestClient(app)


def test_card():
    r = client.get("/a2a/card")
    assert r.status_code == 200
    assert r.json()["name"] == "test"


def test_a2a():
    r = client.post("/a2a", json={"task": "hello"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
