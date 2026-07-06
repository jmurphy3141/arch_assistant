"""OCI Generative AI embeddings with an injectable, test-friendly callable."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import yaml


logger = logging.getLogger(__name__)
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

EmbedFn = Callable[..., list[list[float]]]


def load_embedding_settings(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Return the configured embedding endpoint, model, and compartment."""
    if config is None:
        try:
            config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            config = {}
    embedding = dict(config.get("embedding") or {})
    return {
        "endpoint": str(
            embedding.get("service_endpoint")
            or embedding.get("endpoint")
            or ""
        ),
        "model_id": str(embedding.get("model_id") or ""),
        "compartment_id": str(config.get("compartment_id") or ""),
    }


def build_embed_fn(config: dict[str, Any] | None = None) -> EmbedFn:
    """Build the swappable callable used by ingestion and semantic retrieval."""
    settings = load_embedding_settings(config)

    def embed(inputs: list[str], *, input_type: str = "SEARCH_DOCUMENT") -> list[list[float]]:
        return run_embeddings(inputs, input_type=input_type, **settings)

    return embed


def run_embeddings(
    inputs: list[str],
    *,
    endpoint: str,
    model_id: str,
    compartment_id: str,
    input_type: str = "SEARCH_DOCUMENT",
) -> list[list[float]]:
    """Embed text through OCI GenAI and return one float vector per input."""
    cleaned = [str(value or "") for value in inputs]
    if not cleaned:
        return []
    if not endpoint or not model_id or not compartment_id:
        raise RuntimeError("OCI embedding endpoint, model_id, and compartment_id are required")
    try:
        import oci  # type: ignore
    except ImportError as exc:
        raise RuntimeError("oci SDK not available. Install with: pip install oci") from exc

    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client = oci.generative_ai_inference.GenerativeAiInferenceClient(
        config={},
        signer=signer,
        service_endpoint=endpoint,
        timeout=(10, 180),
        retry_strategy=oci.retry.NoneRetryStrategy(),
    )
    details = oci.generative_ai_inference.models.EmbedTextDetails(
        inputs=cleaned,
        serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(
            model_id=model_id
        ),
        compartment_id=compartment_id,
        input_type=str(input_type or "SEARCH_DOCUMENT").upper(),
        truncate="END",
    )
    logger.info("OCI embedding request: model=%s inputs=%d", model_id, len(cleaned))
    response = client.embed_text(details)
    vectors = getattr(response.data, "embeddings", None)
    if not isinstance(vectors, list) or len(vectors) != len(cleaned):
        raise RuntimeError("OCI embedding response did not contain one vector per input")
    return [[float(value) for value in vector] for vector in vectors]
