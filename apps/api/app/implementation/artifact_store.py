"""Content-addressed artifact storage over Supabase Storage and Postgres.

An artifact body goes to a private, tenant-scoped Storage bucket at a path
derived from its own SHA-256; its metadata — identity, type, media type, digest,
size, tenant scope, retention class, producer, and source revision — goes to
Postgres. The reference callers hold afterwards carries no bucket name, path, or
credential: resolution goes back through the control plane, which rechecks the
tenant and verifies type, size, and digest.

Storage is idempotent by tenant, artifact type, and digest together. Writing the
same evidence twice for one client keeps the first row and reuses the stored
object, so a retried extraction cannot produce a second identity for it; the
same bytes in a different role stay a separate artifact, because a final diff
and a revision patch are different evidence even when one cycle makes them
identical.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

import httpx
import structlog
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.envelope.artifacts import (
    ArtifactDigest,
    ArtifactReference,
    RetentionClass,
)
from utrace_contracts.envelope.values import ClientTenantScope

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_str, as_uuid
from app.ids import uuid7

__all__ = ["ARTIFACT_BUCKET", "ArtifactStore", "ArtifactStoreError", "digest_of"]

_logger = structlog.get_logger(__name__)

ARTIFACT_BUCKET = "utrace-artifacts"
"""The private bucket the implementation migration creates."""

_UPLOAD_TIMEOUT_SECONDS = 60.0


class ArtifactStoreError(RuntimeError):
    """Storage refused an artifact body. Extraction failure blocks cleanup."""

    def __init__(self, *, operation: str, status_code: int, body: str, path: str) -> None:
        super().__init__(
            f"artifact storage {operation} for '{path}' failed with HTTP {status_code}: {body}"
        )
        self.operation = operation
        self.status_code = status_code
        self.path = path


def digest_of(body: bytes) -> str:
    """The lowercase hex SHA-256 that addresses one artifact body."""
    return hashlib.sha256(body).hexdigest()


class ArtifactStore:
    """Writes artifact bodies to Supabase Storage and their metadata to Postgres."""

    def __init__(self, *, database: Database, supabase_url: str, service_key: str) -> None:
        self._database = database
        self._base_url = f"{supabase_url.rstrip('/')}/storage/v1"
        self._service_key = service_key

    async def put(
        self,
        *,
        client_id: UUID,
        artifact_type: str,
        media_type: str,
        retention_class: RetentionClass,
        producer: str,
        body: bytes,
        source_revision: str | None,
        implementation_attempt_id: UUID | None,
        environment_lease_id: UUID | None,
        product_signal_id: UUID | None,
    ) -> ArtifactReference:
        """Store one artifact body and return its opaque reference.

        A body this client already stored under the same digest is adopted: the
        existing row and object stand, and the returned reference is that one.
        """
        digest = digest_of(body)
        existing = await self._existing(
            client_id=client_id, artifact_type=artifact_type, digest=digest
        )
        if existing is not None:
            return existing

        path = f"{client_id}/{artifact_type}/{digest}"
        await self._upload(path=path, media_type=media_type, body=body)
        artifact_id = uuid7()
        created_at = datetime.now(tz=UTC)
        async with self._database.transaction() as conn:
            await self._insert(
                conn,
                artifact_id=artifact_id,
                client_id=client_id,
                artifact_type=artifact_type,
                media_type=media_type,
                digest=digest,
                byte_size=len(body),
                retention_class=retention_class,
                path=path,
                producer=producer,
                source_revision=source_revision,
                implementation_attempt_id=implementation_attempt_id,
                environment_lease_id=environment_lease_id,
                product_signal_id=product_signal_id,
            )
        _logger.info(
            "implementation.artifact.stored",
            client_id=str(client_id),
            artifact_id=str(artifact_id),
            artifact_type=artifact_type,
            digest=digest,
            byte_size=len(body),
        )
        return _reference(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            media_type=media_type,
            digest=digest,
            byte_size=len(body),
            retention_class=retention_class,
            client_id=client_id,
            created_at=created_at,
        )

    async def read(self, *, client_id: UUID, reference: ArtifactReference) -> bytes:
        """Download one artifact body and refuse it unless it matches its digest."""
        path = f"{client_id}/{reference.artifact_type}/{reference.digest.value}"
        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{self._base_url}/object/authenticated/{ARTIFACT_BUCKET}/{path}",
                headers={
                    "authorization": f"Bearer {self._service_key}",
                    "apikey": self._service_key,
                },
            )
        if response.status_code >= 400:
            raise ArtifactStoreError(
                operation="read",
                status_code=response.status_code,
                body=response.text[:500],
                path=path,
            )
        if digest_of(response.content) != reference.digest.value:
            raise ArtifactStoreError(
                operation="verify",
                status_code=response.status_code,
                body=f"the stored bytes do not match digest {reference.digest.value}",
                path=path,
            )
        return response.content

    async def _existing(
        self, *, client_id: UUID, artifact_type: str, digest: str
    ) -> ArtifactReference | None:
        """Return the artifact this client already stored for this type and digest.

        Identity is the tenant, the type, and the digest together: the same bytes
        in a different role are a different artifact.
        """
        async with self._database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select id, artifact_type, media_type, digest, byte_size, retention_class,
                       created_at
                from public.artifacts
                where client_id = %s and artifact_type = %s and digest = %s
                """,
                (client_id, artifact_type, digest),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return _reference(
            artifact_id=as_uuid(row, "id"),
            artifact_type=as_str(row, "artifact_type"),
            media_type=as_str(row, "media_type"),
            digest=as_str(row, "digest"),
            byte_size=as_int(row, "byte_size"),
            retention_class=_retention_class(as_str(row, "retention_class")),
            client_id=client_id,
            created_at=as_datetime(row, "created_at"),
        )

    async def _upload(self, *, path: str, media_type: str, body: bytes) -> None:
        """PUT the body into the private bucket, overwriting the identical object."""
        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS) as client:
            response = await client.put(
                f"{self._base_url}/object/{ARTIFACT_BUCKET}/{path}",
                content=body,
                headers={
                    "authorization": f"Bearer {self._service_key}",
                    "apikey": self._service_key,
                    "content-type": media_type,
                    "x-upsert": "true",
                },
            )
        if response.status_code >= 400:
            raise ArtifactStoreError(
                operation="upload",
                status_code=response.status_code,
                body=response.text[:500],
                path=path,
            )

    async def _insert(
        self,
        conn: AsyncConnection[DictRow],
        *,
        artifact_id: UUID,
        client_id: UUID,
        artifact_type: str,
        media_type: str,
        digest: str,
        byte_size: int,
        retention_class: RetentionClass,
        path: str,
        producer: str,
        source_revision: str | None,
        implementation_attempt_id: UUID | None,
        environment_lease_id: UUID | None,
        product_signal_id: UUID | None,
    ) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.artifacts (
                    id, client_id, artifact_type, media_type, digest, byte_size,
                    retention_class, storage_bucket, storage_path, producer,
                    source_revision, implementation_attempt_id, environment_lease_id,
                    product_signal_id
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    artifact_id,
                    client_id,
                    artifact_type,
                    media_type,
                    digest,
                    byte_size,
                    retention_class,
                    ARTIFACT_BUCKET,
                    path,
                    producer,
                    source_revision,
                    implementation_attempt_id,
                    environment_lease_id,
                    product_signal_id,
                ),
            )


def _reference(
    *,
    artifact_id: UUID,
    artifact_type: str,
    media_type: str,
    digest: str,
    byte_size: int,
    retention_class: RetentionClass,
    client_id: UUID,
    created_at: datetime,
) -> ArtifactReference:
    """Build the opaque reference other components hold."""
    return ArtifactReference(
        artifact_id=str(artifact_id),
        artifact_type=artifact_type,
        schema_version=1,
        media_type=media_type,
        digest=ArtifactDigest(algorithm="sha256", value=digest),
        byte_size=byte_size,
        created_at=created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        client_scope=ClientTenantScope(scope="client", client_id=str(client_id)),
        retention_class=retention_class,
    )


_RETENTION_CLASSES: tuple[RetentionClass, ...] = (
    "participant_personal",
    "client_evidence",
    "operational",
    "audit_minimized",
)


def _retention_class(value: str) -> RetentionClass:
    """Narrow a stored retention class to the contract's closed set."""
    for known in _RETENTION_CLASSES:
        if value == known:
            return known
    raise ValueError(f"'{value}' is not a known artifact retention class")
