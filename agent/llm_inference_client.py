"""
agent/llm_inference_client.py
------------------------------
OCI GenAI Inference backend for the Drawing Agent.

Uses GenerativeAiInferenceClient (direct SDK, not ADK Agent Endpoint).
Auth: Instance Principal only — no ~/.oci/config.

Public function:
    run_inference(prompt, *, endpoint, model_id, compartment_id,
                  max_tokens, temperature, top_p, top_k) -> str

Returns the raw LLM text string; callers are responsible for JSON parsing.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def run_inference(
    prompt: str,
    *,
    endpoint: str,
    model_id: str,
    compartment_id: str,
    max_tokens: int = 2000,
    temperature: float = 0.0,
    top_p: float = 0.9,
    top_k: int = 0,
    system_message: str = "",
) -> str:
    """
    Send *prompt* to OCI GenAI Inference and return the raw generated text.

    Parameters match config.yaml ``inference:`` block exactly.

    Memory model
    ------------
    This is intentionally stateless — each call is an independent single-turn
    request.  There is no session continuation across calls.  The drawing agent
    achieves multi-turn clarification by rebuilding the full prompt from scratch
    (pending["prompt"] + answers) before each call; no in-model history is needed.

    system_message
    --------------
    Passed as ``GenericChatRequest.system``.  Sets the model's behavioural
    contract (JSON-only output, role, format rules) before the user prompt.
    Configure in config.yaml under ``inference.system_message``.

    Raises:
        RuntimeError: if oci SDK is not importable
        oci.exceptions.ServiceError: for any OCI API error
    """
    try:
        import oci  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "oci SDK not available. Install with: pip install oci"
        ) from exc

    # ── Auth: Instance Principal ─────────────────────────────────────────────
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()

    # ── Client ───────────────────────────────────────────────────────────────
    client = oci.generative_ai_inference.GenerativeAiInferenceClient(
        config={},
        signer=signer,
        service_endpoint=endpoint,
    )

    # ── Request objects (canonical structure from reference snippet) ─────────
    content = oci.generative_ai_inference.models.TextContent()
    content.text = prompt

    message = oci.generative_ai_inference.models.Message()
    message.role = "USER"
    message.content = [content]

    chat_request = oci.generative_ai_inference.models.GenericChatRequest()
    chat_request.api_format = (
        oci.generative_ai_inference.models.BaseChatRequest.API_FORMAT_GENERIC
    )
    chat_request.messages = [message]
    chat_request.max_tokens = max_tokens
    chat_request.temperature = temperature
    chat_request.top_p = top_p
    chat_request.top_k = top_k
    # System prompt: sets behavioural contract before the user message.
    # Empty string → field omitted (OCI API treats absent == no system message).
    if system_message:
        chat_request.system = system_message

    chat_detail = oci.generative_ai_inference.models.ChatDetails()
    chat_detail.serving_mode = (
        oci.generative_ai_inference.models.OnDemandServingMode(model_id=model_id)
    )
    chat_detail.chat_request = chat_request
    chat_detail.compartment_id = compartment_id

    # ── Call ─────────────────────────────────────────────────────────────────
    logger.info(
        "OCI inference request: model=%s prompt_len=%d",
        model_id,
        len(prompt),
    )
    chat_response = client.chat(chat_detail)

    # ── Extract text ─────────────────────────────────────────────────────────
    text = _extract_text(chat_response)
    logger.info(
        "OCI inference response: len=%d",
        len(text),
    )
    return text


def _extract_text(response) -> str:
    """
    Extract generated text from a GenerativeAiInferenceClient.chat() response.

    The SDK response shape (GenericChatResponse):
      response.data.chat_response.choices[0].message.content[0].text
    Falls back progressively to avoid AttributeError on unexpected shapes.
    """
    try:
        choices = response.data.chat_response.choices
        if choices:
            content_list = choices[0].message.content
            if content_list:
                return content_list[0].text or ""
    except AttributeError:
        pass

    # Secondary fallback: look for finish_reason or raw string
    try:
        return str(response.data.chat_response.choices[0].message.content)
    except (AttributeError, IndexError, TypeError):
        pass

    return str(response)
