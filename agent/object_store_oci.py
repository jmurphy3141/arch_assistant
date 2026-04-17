"""
agent/object_store_oci.py
--------------------------
OCI Object Storage backend implementing ObjectStoreBase.

Auth: Instance Principal only — no ~/.oci/config.
All put/get/head calls go to the configured bucket/namespace.
Object contents are never logged; only key names and byte sizes.

Usage
-----
store = OciObjectStore(
    region="us-chicago-1",
    namespace="oraclejamescalise",
    bucket_name="agent_assistante",
)
store.put("agent3/c1/diag/req1/diagram.drawio", xml_bytes, "text/xml")
data = store.get("agent3/c1/diag/LATEST.json")
exists = store.head("agent3/c1/diag/LATEST.json")
"""
from __future__ import annotations

import logging
from typing import Optional

from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

_OCI_ENDPOINT_TPL = "https://objectstorage.{region}.oraclecloud.com"


class OciObjectStore(ObjectStoreBase):
    """
    Real OCI Object Storage backend via oci.object_storage.ObjectStorageClient.

    Thread-safe: a single client instance is reused across calls; the OCI SDK
    client is documented as thread-safe for concurrent requests.
    """

    def __init__(
        self,
        region: str,
        namespace: str,
        bucket_name: str,
        *,
        endpoint: Optional[str] = None,
    ) -> None:
        self._region      = region
        self._namespace   = namespace
        self._bucket_name = bucket_name
        self._endpoint    = endpoint or _OCI_ENDPOINT_TPL.format(region=region)
        self._client      = self._build_client()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_client(self):
        """Build an ObjectStorageClient with Instance Principal auth."""
        try:
            import oci  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "oci SDK not available; install with: pip install oci"
            ) from exc

        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        return oci.object_storage.ObjectStorageClient(
            config={},
            signer=signer,
            service_endpoint=self._endpoint,
        )

    # ── ObjectStoreBase interface ─────────────────────────────────────────────

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload *data* to the object at *key*."""
        import io

        logger.debug("OCI put  key=%s size=%d", key, len(data))
        self._client.put_object(
            namespace_name=self._namespace,
            bucket_name=self._bucket_name,
            object_name=key,
            put_object_body=io.BytesIO(data),
            content_type=content_type,
            content_length=len(data),
        )
        logger.info("OCI put  ok key=%s size=%d", key, len(data))

    def get(self, key: str) -> bytes:
        """Download and return the bytes stored at *key*.

        Raises:
            KeyError: if the object does not exist (404)
            PermissionError: if access is forbidden (403)
        """
        import oci  # type: ignore

        logger.debug("OCI get  key=%s", key)
        try:
            response = self._client.get_object(
                namespace_name=self._namespace,
                bucket_name=self._bucket_name,
                object_name=key,
            )
        except oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                raise KeyError(f"Object not found: {key}") from exc
            if exc.status == 403:
                raise PermissionError(f"Access forbidden: {key}") from exc
            raise

        data: bytes = response.data.content
        logger.info("OCI get  ok key=%s size=%d", key, len(data))
        return data

    def head(self, key: str) -> bool:
        """Return True if *key* exists, False if 404."""
        import oci  # type: ignore

        try:
            self._client.head_object(
                namespace_name=self._namespace,
                bucket_name=self._bucket_name,
                object_name=key,
            )
            return True
        except oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                return False
            raise

    def list(self, prefix: str = "") -> list[str]:
        """Return object names under prefix, transparently handling pagination."""
        start = None
        keys: list[str] = []
        while True:
            response = self._client.list_objects(
                namespace_name=self._namespace,
                bucket_name=self._bucket_name,
                prefix=prefix or None,
                start=start,
            )
            data = response.data
            keys.extend([obj.name for obj in data.objects])
            if not data.next_start_with:
                break
            start = data.next_start_with
        return keys

    # ── Repr ─────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"OciObjectStore(region={self._region!r}, "
            f"namespace={self._namespace!r}, "
            f"bucket={self._bucket_name!r})"
        )
