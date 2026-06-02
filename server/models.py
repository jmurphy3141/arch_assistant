from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    client_id: Optional[str] = "default"


class ClarifyRequest(BaseModel):
    answers: str
    client_id: Optional[str] = "default"
    diagram_name: Optional[str] = "oci_architecture"
    items_json: Optional[str] = None
    prompt: Optional[str] = None
    deployment_hints_json: Optional[str] = None
    auto_waf: Optional[bool] = False
    customer_id: Optional[str] = ""
    customer_name: Optional[str] = ""


class RefineRequest(BaseModel):
    """Request to refine an already-generated diagram based on free-text feedback."""

    feedback: str
    client_id: Optional[str] = "default"
    diagram_name: Optional[str] = "oci_architecture"
    items_json: Optional[str] = None
    prompt: Optional[str] = None
    prev_spec: Optional[str] = None
    deployment_hints_json: Optional[str] = None


class GenerateRequest(BaseModel):
    resources: List[Dict[str, Any]]
    context: Optional[str] = ""
    questionnaire: Optional[str] = ""
    notes: Optional[str] = ""
    diagram_name: Optional[str] = "oci_architecture"
    client_id: Optional[str] = "default"
    customer_id: Optional[str] = None
    customer_name: Optional[str] = ""
    deployment_hints: Optional[dict] = {}


class PovRequest(BaseModel):
    customer_id: str
    customer_name: str
    feedback: Optional[str] = None


class JepRequest(BaseModel):
    customer_id: str
    customer_name: str
    feedback: Optional[str] = None
    diagram_key: Optional[str] = None
    diagram_url: Optional[str] = None


class ApproveDocRequest(BaseModel):
    customer_id: str
    customer_name: str
    content: str


class JepKickoffRequest(BaseModel):
    customer_id: str
    customer_name: str


class JepAnswersRequest(BaseModel):
    customer_id: str
    answers: dict


class JepRevisionRequest(BaseModel):
    customer_id: str
    reason: Optional[str] = None


class WafRequest(BaseModel):
    customer_id: str
    customer_name: str
    feedback: Optional[str] = None


class TerraformGenerateRequest(BaseModel):
    customer_id: str
    customer_name: str
    prompt: Optional[str] = ""


class A2Av1Part(BaseModel):
    kind: str = "text"
    text: str = ""
    data: dict = {}
    mimeType: str = ""


class A2Av1Message(BaseModel):
    role: str = "user"
    parts: List[A2Av1Part] = []
    contextId: str = ""
    messageId: str = ""


class A2Av1JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str = ""
    method: str = ""
    params: dict = {}


class OrchestratorChatRequest(BaseModel):
    customer_id: str
    customer_name: str
    message: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None


class BomConversationTurn(BaseModel):
    role: str
    content: str


class BomChatRequest(BaseModel):
    message: str
    conversation: List[BomConversationTurn] = []
    model_id: Optional[str] = None


class BomXlsxRequest(BaseModel):
    bom_payload: Dict[str, Any]


class A2AObjectRef(BaseModel):
    """OCI Object Storage reference used in A2A task inputs."""

    namespace: Optional[str] = None
    bucket: str
    object: str
    version_id: Optional[str] = None


class A2ATask(BaseModel):
    """
    Incoming task from an orchestrator or peer agent.

    skill values:
      "generate_diagram"  - generate from a resource list (inline or bucket ref)
      "upload_bom"        - parse a BOM Excel from a bucket ref and generate
      "clarify_diagram"   - submit clarification answers for a pending request
    """

    task_id: str
    skill: str
    inputs: Dict[str, Any] = {}
    client_id: str = "default"


class A2AResponse(BaseModel):
    task_id: str
    agent_id: str
    status: str
    outputs: Dict[str, Any] = {}
    error_message: Optional[str] = None
