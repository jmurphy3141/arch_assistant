from collections.abc import Awaitable, Callable

from fastapi import FastAPI

from sub_agents.models import A2ARequest, A2AResponse, AgentCard


def make_agent_app(
    card: AgentCard,
    handler: Callable[[A2ARequest], Awaitable[A2AResponse]],
) -> FastAPI:
    """
    Returns a FastAPI app with:
      GET  /a2a/card  -> returns card as JSON
      POST /a2a       -> calls handler(req: A2ARequest) -> A2AResponse
      GET  /health    -> {"status": "ok", "agent": card.name}
    """
    app = FastAPI(title=f"{card.name} A2A sub-agent")

    @app.get("/a2a/card")
    async def get_card() -> dict:
        return card.model_dump()

    @app.post("/a2a", response_model=A2AResponse)
    async def post_a2a(req: A2ARequest) -> A2AResponse:
        return await handler(req)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "agent": card.name}

    return app
