from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class A2ARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    engagement_context: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""


class A2AResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: str
    status: str
    trace: dict[str, Any] = Field(default_factory=dict)


class AgentCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    inputs: dict[str, list[str]]
    output: str
    llm_model_id: str
