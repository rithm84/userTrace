"""Immutable telemetry artifacts in Supabase Storage.

Large payloads follow the common artifact contract: UUIDv7 ``artifact_id``,
exact media and schema type, byte size, SHA-256 content hash, creation time, and
client scope. Baseline chunk manifests also record ordered chunk hashes and an
aggregate hash.

The relational row stores no bucket name, path, permanent URL, or credential.
The object key is derived from the client scope and the artifact identity at
resolution time, and resolution rechecks scope, type, size, and hash before
returning anything.

Contracts: docs/arch-components/telemetry-and-evidence.md#persistence-and-integrity
           docs/arch-components/cross-component-contracts.md#artifact-references
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

import httpx
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.envelope import JsonValue
from app.ids import uuid7
from app.ingestion.errors import ArtifactIntegrityError
from app.logging import get_logger

__all__ = ["ArtifactReference", "ArtifactStore", "ChunkManifest", "chunk_manifest"]

_log = get_logger(__name__)

_REQUEST_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """An opaque immutable reference. It contains no storage path or credential."""

    artifact_id: UUID
    client_id: UUID
    artifact_class: str
    media_type: str
    schema_type: str
    byte_size: int
    content_sha256: str
    retention_class: str


@dataclass(frozen=True, slots=True)
class ChunkManifest:
    """Ordered chunk hashes plus the aggregate hash over the whole baseline."""

    chunk_hashes: tuple[str, ...]
    aggregate_hash: str

    def as_json(self) -> Mapping[str, JsonValue]:
        """Return the manifest in its stored form."""
        return {
            "chunk_hashes": list(self.chunk_hashes),
            "aggregate_hash": self.aggregate_hash,
        }


def chunk_manifest(chunks: Sequence[bytes]) -> ChunkManifest:
    """Hash each chunk in order and derive the aggregate hash over the whole body.

    The aggregate hash is taken over the concatenated chunk bodies, which is what
    the exporter hashes, so a reordered or missing chunk cannot validate.
    """
    if len(chunks) == 0:
        raise ValueError("a chunk manifest needs at least one chunk")
    hashes = tuple(f"sha256:{sha256(chunk).hexdigest()}" for chunk in chunks)
    aggregate = sha256(b"".join(chunks)).hexdigest()
    return ChunkManifest(chunk_hashes=hashes, aggregate_hash=f"sha256:{aggregate}")


class ArtifactStore:
    """Writes and resolves immutable artifacts in one private Storage bucket.

    One instance owns one HTTP client against the Supabase Storage REST API. The
    service credential lives here and never reaches a relational row, a response
    body, or a log line.
    """

    def __init__(self, *, supabase_url: str, service_key: str, bucket: str) -> None:
        self._bucket = bucket
        self._client = httpx.AsyncClient(
            base_url=f"{supabase_url.rstrip('/')}/storage/v1",
            headers={"apikey": service_key, "authorization": f"Bearer {service_key}"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    def _object_key(self, client_id: UUID, artifact_class: str, artifact_id: UUID) -> str:
        """Derive the object key. Every read verifies the client scope first."""
        return f"{client_id}/{artifact_class}/{artifact_id}"

    async def put(
        self,
        conn: AsyncConnection[DictRow],
        *,
        client_id: UUID,
        artifact_class: str,
        media_type: str,
        schema_type: str,
        retention_class: str,
        body: bytes,
        manifest: ChunkManifest | None,
    ) -> ArtifactReference:
        """Store one immutable artifact and record its metadata.

        Content-addressed reuse is scoped to one client: identical bytes in a
        different client scope become a separate artifact, so a hash can never
        become a cross-tenant side channel.
        """
        digest = sha256(body).hexdigest()
        existing = await self._find(conn, client_id, artifact_class, digest)
        if existing is not None:
            return existing

        artifact_id = uuid7()
        key = self._object_key(client_id, artifact_class, artifact_id)
        response = await self._client.post(
            f"/object/{self._bucket}/{key}",
            content=body,
            headers={"content-type": media_type, "x-upsert": "false"},
        )
        if response.status_code >= 400:
            raise ArtifactIntegrityError(
                artifact_id=artifact_id,
                reason=(
                    f"Supabase Storage refused the upload with status "
                    f"{response.status_code}: {response.text[:200]}"
                ),
            )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.telemetry_artifacts (
                    id, client_id, artifact_class, media_type, schema_type,
                    byte_size, content_sha256, chunk_manifest, retention_class
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    artifact_id,
                    client_id,
                    artifact_class,
                    media_type,
                    schema_type,
                    len(body),
                    digest,
                    Jsonb(manifest.as_json()) if manifest is not None else None,
                    retention_class,
                ),
            )
        _log.info(
            "telemetry_artifact_stored",
            client_id=str(client_id),
            artifact_id=str(artifact_id),
            artifact_class=artifact_class,
            byte_size=len(body),
        )
        return ArtifactReference(
            artifact_id=artifact_id,
            client_id=client_id,
            artifact_class=artifact_class,
            media_type=media_type,
            schema_type=schema_type,
            byte_size=len(body),
            content_sha256=digest,
            retention_class=retention_class,
        )

    async def _find(
        self,
        conn: AsyncConnection[DictRow],
        client_id: UUID,
        artifact_class: str,
        digest: str,
    ) -> ArtifactReference | None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select id, media_type, schema_type, byte_size, retention_class
                from public.telemetry_artifacts
                where client_id = %s and artifact_class = %s and content_sha256 = %s
                """,
                (client_id, artifact_class, digest),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return ArtifactReference(
            artifact_id=UUID(str(row["id"])),
            client_id=client_id,
            artifact_class=artifact_class,
            media_type=as_str(row, "media_type"),
            schema_type=as_str(row, "schema_type"),
            byte_size=as_int(row, "byte_size"),
            content_sha256=digest,
            retention_class=as_str(row, "retention_class"),
        )

    async def resolve(
        self, database: Database, *, client_id: UUID, artifact_id: UUID, expected_class: str
    ) -> bytes:
        """Return one artifact's bytes after rechecking scope, type, size, and hash.

        The cross-client check runs before the fetch: services do not retrieve an
        object and then decide whether the caller should have seen it.
        """
        async with database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select artifact_class, byte_size, content_sha256
                from public.telemetry_artifacts
                where id = %s and client_id = %s
                """,
                (artifact_id, client_id),
            )
            row = await cur.fetchone()
        if row is None:
            raise ArtifactIntegrityError(
                artifact_id=artifact_id,
                reason="no artifact with that identity exists inside this client scope",
            )
        artifact_class = as_str(row, "artifact_class")
        if artifact_class != expected_class:
            raise ArtifactIntegrityError(
                artifact_id=artifact_id,
                reason=f"is a '{artifact_class}' artifact, not '{expected_class}'",
            )
        key = self._object_key(client_id, artifact_class, artifact_id)
        response = await self._client.get(f"/object/{self._bucket}/{key}")
        if response.status_code >= 400:
            raise ArtifactIntegrityError(
                artifact_id=artifact_id,
                reason=f"Supabase Storage returned status {response.status_code}",
            )
        body = response.content
        if len(body) != as_int(row, "byte_size"):
            raise ArtifactIntegrityError(
                artifact_id=artifact_id, reason="stored byte size does not match"
            )
        if sha256(body).hexdigest() != as_str(row, "content_sha256"):
            raise ArtifactIntegrityError(
                artifact_id=artifact_id, reason="stored content hash does not match"
            )
        return body
