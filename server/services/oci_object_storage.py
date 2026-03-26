"""
server/services/oci_object_storage.py
--------------------------------------
OCI Object Storage helper — server-side fetch via Instance Principals.

Exposes a single public function:
    fetch_object(bucket, object_name, namespace=None, version_id=None) -> bytes

Tests monkeypatch this module-level function to avoid real OCI calls:
    monkeypatch.setattr(
        "server.services.oci_object_storage.fetch_object",
        fake_fetch
    )
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def fetch_object(
    bucket: str,
    object_name: str,
    namespace: str | None = None,
    version_id: str | None = None,
) -> bytes:
    """
    Fetch an object from OCI Object Storage using Instance Principal auth.

    Raises:
        RuntimeError: if oci SDK is not importable
        oci.exceptions.ServiceError: for 404 / 403 from OCI
    """
    try:
        import oci  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "oci SDK not available; cannot fetch from bucket. "
            "Install with: pip install oci"
        ) from exc

    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)

    if namespace is None:
        namespace = client.get_namespace().data

    kwargs: dict = {}
    if version_id:
        kwargs["version_id"] = version_id

    response = client.get_object(namespace, bucket, object_name, **kwargs)
    # response.data is a urllib3.response.HTTPResponse-like object
    raw: bytes = response.data.content
    return raw


def list_objects(
    bucket: str,
    prefix: str | None = None,
    namespace: str | None = None,
    limit: int = 100,
    page: str | None = None,
) -> dict:
    """
    List objects in a bucket with optional prefix filter.

    Returns:
        {"objects": [{"name": ...}, ...], "next_page": <str|None>}
    """
    try:
        import oci  # type: ignore
    except ImportError as exc:
        raise RuntimeError("oci SDK not available") from exc

    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)

    if namespace is None:
        namespace = client.get_namespace().data

    kwargs: dict = {"limit": limit}
    if prefix:
        kwargs["prefix"] = prefix
    if page:
        kwargs["page"] = page

    response = client.list_objects(namespace, bucket, **kwargs)
    items = [{"name": obj.name} for obj in (response.data.objects or [])]
    next_page = response.data.next_start_with

    return {"objects": items, "next_page": next_page}
