"""Server SDK observation activations.

One activation binds the server streams of one prepared preview to the exact
client, client user, customer account, Interview Session, preview environment,
runtime allocation, and optional candidate Preview Version. Each service process
creates a fresh ``server_stream_id``; a process restart creates a new stream, and
the SDK reconnects and rotates its credential rather than re-activating, so the
activation outlives a dropped control connection.

The activation also issues the preview entry token. A remote ``traceparent`` is
accepted as a parent only on a request that already entered the authenticated
active preview, which the preview front door proves by attaching that token in
the ``x-utrace-preview-entry`` header.

The control WebSocket itself belongs to the server-SDK workstream. This module is
the durable half it calls: creating an activation, rotating a credential, and
verifying the signed instrumentation manifest.

Contract: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
"""

import secrets
from base64 import b64decode, b64encode
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.db.rows import as_optional_str, as_optional_uuid, as_str, as_uuid
from app.envelope import JsonValue
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.ingestion.credentials import (
    SERVER_CREDENTIAL_LIFETIME_SECONDS,
    IssuedCredential,
    issue_credential,
)
from app.ingestion.errors import (
    IntegrityViolationError,
    SourceValidationError,
    TelemetryAuthenticationError,
)
from app.ingestion.limits import ServerLimits, default_server_limits
from app.ingestion.preview_environments import resolve_preview_environment_id
from app.ingestion.scheduling import schedule_readiness
from app.ingestion.signing import verify_canonical_signature
from app.logging import get_logger

__all__ = [
    "ServerActivation",
    "ServerActivationRequest",
    "create_server_activation",
    "load_server_activation",
    "rotate_server_credential",
    "sign_server_manifest",
    "verify_signed_manifest",
]

_log = get_logger(__name__)


_PREVIEW_ENTRY_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class ServerActivationRequest:
    """What one service process presents when it activates.

    `preview_runtime_allocation_id` and `public_preview_host` come from the
    bootstrap credential (its subject and binding), never from the service.
    """

    preview_runtime_allocation_id: UUID
    public_preview_host: str
    server_stream_id: UUID
    service_instance_id: UUID
    service_key: str
    signed_manifest: Mapping[str, JsonValue]
    required_hops: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServerActivation:
    """The durable activation a server credential resolves against."""

    activation_id: UUID
    client_id: UUID
    client_user_id: UUID
    customer_account_id: UUID
    interview_session_id: UUID
    preview_environment_id: UUID
    preview_runtime_allocation_id: UUID
    preview_version_id: UUID | None
    server_stream_id: UUID
    service_instance_id: UUID
    service_key: str
    policy_version: str
    source_schema_version: str
    semantic_convention_version: str
    manifest_version: int
    probe_id: UUID
    required_hops: tuple[str, ...]
    limits: ServerLimits


def sign_server_manifest(
    manifest: Mapping[str, JsonValue], *, private_key: Ed25519PrivateKey, key_id: str
) -> dict[str, JsonValue]:
    """Sign one manifest over its RFC 8785 canonical form, as activation verifies it.

    uTrace holds the release key: onboarding signs each client release's manifest,
    and ``verify_signed_manifest`` checks it against the configured trust anchor.
    """
    signature = private_key.sign(rfc8785.dumps(dict(manifest)))
    return {
        "manifest": dict(manifest),
        "signature": {
            "algorithm": "ed25519",
            "key_id": key_id,
            "value": b64encode(signature).decode("ascii"),
        },
    }


def verify_signed_manifest(
    signed: Mapping[str, JsonValue], *, trust_anchor: bytes, trust_anchor_key_id: str
) -> Mapping[str, JsonValue]:
    """Verify the Ed25519 signature over the manifest's RFC 8785 canonical form.

    Verification is mandatory: an unsigned, wrongly signed, or wrongly keyed
    manifest fails activation rather than being accepted at lower fidelity.
    """
    manifest = signed.get("manifest")
    signature = signed.get("signature")
    if not isinstance(manifest, dict) or not isinstance(signature, dict):
        raise SourceValidationError(
            field="signed_manifest",
            reason="must carry a manifest object and a signature object",
        )
    key_id = signature.get("key_id")
    value = signature.get("value")
    if not isinstance(key_id, str) or not isinstance(value, str):
        raise SourceValidationError(
            field="signed_manifest.signature", reason="must carry a key_id and a value"
        )
    if key_id != trust_anchor_key_id:
        raise SourceValidationError(
            field="signed_manifest.signature.key_id",
            reason="is not the configured manifest trust anchor",
        )
    verify_canonical_signature(
        payload=manifest,
        signature=b64decode(value),
        public_key=trust_anchor,
        field="signed_manifest.signature.value",
    )
    return manifest


def _manifest_versions(manifest: Mapping[str, JsonValue]) -> tuple[int, str, str, str]:
    """Return the manifest, policy, uTrace schema, and semantic-convention versions."""
    manifest_version = manifest.get("manifest_version")
    policy_version = manifest.get("policy_version")
    schema_version = manifest.get("utrace_schema_version")
    convention_version = manifest.get("semantic_convention_version")
    if (
        not isinstance(manifest_version, int)
        or not isinstance(policy_version, str)
        or not isinstance(schema_version, str)
        or not isinstance(convention_version, str)
    ):
        raise SourceValidationError(
            field="signed_manifest.manifest",
            reason=(
                "must carry manifest_version, policy_version, utrace_schema_version, "
                "and semantic_convention_version"
            ),
        )
    return manifest_version, policy_version, schema_version, convention_version


async def create_server_activation(
    database: Database,
    *,
    request: ServerActivationRequest,
    trust_anchor: bytes,
    trust_anchor_key_id: str,
) -> tuple[ServerActivation, IssuedCredential, str]:
    """Create one server activation and return it with its credential and entry token.

    The bootstrap credential names the allocation and the session origin it is
    bound to. The allocation is resolved by identity, because a base runtime and
    its candidate share one session origin; the origin only has to agree once the
    allocation's route has assigned it. The client, user, customer account, and
    session identities come from the committed allocation, never from the
    service's own claims.
    """
    manifest = verify_signed_manifest(
        request.signed_manifest,
        trust_anchor=trust_anchor,
        trust_anchor_key_id=trust_anchor_key_id,
    )
    manifest_version, policy_version, schema_version, convention_version = (
        _manifest_versions(manifest)
    )
    limits = default_server_limits()
    preview_entry_token = secrets.token_urlsafe(_PREVIEW_ENTRY_TOKEN_BYTES)
    activation_id = uuid7()
    probe_id = uuid7()

    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select allocations.id as preview_runtime_allocation_id,
                       allocations.client_id,
                       allocations.preview_version_id,
                       allocations.origin_host,
                       sessions.id as interview_session_id,
                       sessions.state as session_state,
                       sessions.client_user_id,
                       signals.originating_customer_account_id
                from public.preview_runtime_allocations as allocations
                join public.interview_sessions as sessions
                  on sessions.id = allocations.interview_session_id
                join public.product_signals as signals
                  on signals.id = allocations.product_signal_id
                where allocations.id = %s
                """,
                (request.preview_runtime_allocation_id,),
            )
            allocation = await cur.fetchone()
        if allocation is None:
            raise AggregateNotFoundError(
                table="preview_runtime_allocations",
                row_id=request.preview_runtime_allocation_id,
            )
        if as_str(allocation, "session_state") == "closed":
            raise TelemetryAuthenticationError(
                "the Interview Session bound to that preview origin is closed"
            )
        assigned_origin = as_optional_str(allocation, "origin_host")
        if assigned_origin is not None and assigned_origin != request.public_preview_host:
            raise IntegrityViolationError(
                identity_kind="public_preview_host",
                identity_value=request.public_preview_host,
                reason="is not the session origin this allocation's route serves",
            )
        client_id = as_uuid(allocation, "client_id")
        preview_environment_id = await resolve_preview_environment_id(
            conn,
            preview_runtime_allocation_id=as_uuid(
                allocation, "preview_runtime_allocation_id"
            ),
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.telemetry_activations (
                    id, client_id, kind, client_user_id, customer_account_id,
                    interview_session_id, preview_environment_id,
                    preview_runtime_allocation_id, preview_version_id,
                    protocol_version, source_schema_version, policy_version,
                    redaction_version, semantic_convention_version,
                    annotation_manifest_version, annotation_manifest_digest, limits,
                    server_stream_id, service_instance_id, service_key,
                    preview_entry_token_digest, probe_id, required_hops
                )
                values (
                    %s, %s, 'server', %s, %s, %s, %s, %s, %s, '1', %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    activation_id,
                    client_id,
                    as_uuid(allocation, "client_user_id"),
                    as_uuid(allocation, "originating_customer_account_id"),
                    as_uuid(allocation, "interview_session_id"),
                    preview_environment_id,
                    as_uuid(allocation, "preview_runtime_allocation_id"),
                    as_optional_uuid(allocation, "preview_version_id"),
                    schema_version,
                    policy_version,
                    policy_version,
                    convention_version,
                    str(manifest_version),
                    f"sha256:{sha256(rfc8785.dumps(manifest)).hexdigest()}",
                    Jsonb(limits.model_dump()),
                    request.server_stream_id,
                    request.service_instance_id,
                    request.service_key,
                    sha256(preview_entry_token.encode("ascii")).hexdigest(),
                    probe_id,
                    list(request.required_hops),
                ),
            )
            await cur.execute(
                """
                insert into public.server_streams (
                    id, client_id, activation_id, service_instance_id, service_key
                )
                values (%s, %s, %s, %s, %s)
                """,
                (
                    request.server_stream_id,
                    client_id,
                    activation_id,
                    request.service_instance_id,
                    request.service_key,
                ),
            )
            # The entry token belongs to the runtime, not to a route revision, so
            # every revision pointing at this allocation resolves the same token
            # and a later activation is visible to the next proxied request.
            await cur.execute(
                """
                update public.preview_runtime_allocations
                set preview_entry_token = %s, revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (
                    preview_entry_token,
                    as_uuid(allocation, "preview_runtime_allocation_id"),
                ),
            )
        await schedule_readiness(
            conn,
            client_id=client_id,
            activation_id=activation_id,
            interview_session_id=as_uuid(allocation, "interview_session_id"),
            trigger="server_activation_created",
        )
        credential = await issue_credential(
            conn,
            client_id=client_id,
            activation_id=activation_id,
            kind="server",
            lifetime_seconds=SERVER_CREDENTIAL_LIFETIME_SECONDS,
        )

    _log.info(
        "server_activation_created",
        client_id=str(client_id),
        activation_id=str(activation_id),
        server_stream_id=str(request.server_stream_id),
        service_key=request.service_key,
        manifest_version=manifest_version,
    )
    activation = ServerActivation(
        activation_id=activation_id,
        client_id=client_id,
        client_user_id=as_uuid(allocation, "client_user_id"),
        customer_account_id=as_uuid(allocation, "originating_customer_account_id"),
        interview_session_id=as_uuid(allocation, "interview_session_id"),
        preview_environment_id=preview_environment_id,
        preview_runtime_allocation_id=as_uuid(
            allocation, "preview_runtime_allocation_id"
        ),
        preview_version_id=as_optional_uuid(allocation, "preview_version_id"),
        server_stream_id=request.server_stream_id,
        service_instance_id=request.service_instance_id,
        service_key=request.service_key,
        policy_version=policy_version,
        source_schema_version=schema_version,
        semantic_convention_version=convention_version,
        manifest_version=manifest_version,
        probe_id=probe_id,
        required_hops=request.required_hops,
        limits=limits,
    )
    return activation, credential, preview_entry_token


async def rotate_server_credential(
    database: Database, *, activation_id: UUID, server_stream_id: UUID
) -> IssuedCredential:
    """Issue a fresh credential for a live activation after a control reconnect.

    Rotation keeps the activation, so the stream's identity, sequence, and
    correlation outcome survive a dropped control connection.
    """
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select client_id, state, server_stream_id
                from public.telemetry_activations
                where id = %s and kind = 'server'
                for update
                """,
                (activation_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise AggregateNotFoundError(
                table="telemetry_activations", row_id=activation_id
            )
        if as_str(row, "state") != "active":
            raise TelemetryAuthenticationError(
                f"the observation activation is {as_str(row, 'state')}"
            )
        if as_uuid(row, "server_stream_id") != server_stream_id:
            raise IntegrityViolationError(
                identity_kind="server_stream_id",
                identity_value=str(server_stream_id),
                reason="is not the stream this activation was created for",
            )
        return await issue_credential(
            conn,
            client_id=as_uuid(row, "client_id"),
            activation_id=activation_id,
            kind="server",
            lifetime_seconds=SERVER_CREDENTIAL_LIFETIME_SECONDS,
        )


async def load_server_activation(
    database: Database, *, activation_id: UUID
) -> ServerActivation:
    """Load the authenticated scope one server credential resolves to."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, client_user_id, customer_account_id,
                   interview_session_id, preview_environment_id,
                   preview_runtime_allocation_id, preview_version_id,
                   server_stream_id, service_instance_id, service_key,
                   policy_version, source_schema_version, semantic_convention_version,
                   annotation_manifest_version, probe_id, required_hops, limits
            from public.telemetry_activations
            where id = %s and kind = 'server'
            """,
            (activation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="telemetry_activations", row_id=activation_id)
    limits_column = row["limits"]
    hops = row["required_hops"]
    if not isinstance(limits_column, dict) or not isinstance(hops, list):
        raise SourceValidationError(
            field="telemetry_activations", reason="limits or required_hops are malformed"
        )
    return ServerActivation(
        activation_id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        client_user_id=as_uuid(row, "client_user_id"),
        customer_account_id=as_uuid(row, "customer_account_id"),
        interview_session_id=as_uuid(row, "interview_session_id"),
        preview_environment_id=as_uuid(row, "preview_environment_id"),
        preview_runtime_allocation_id=as_uuid(row, "preview_runtime_allocation_id"),
        preview_version_id=as_optional_uuid(row, "preview_version_id"),
        server_stream_id=as_uuid(row, "server_stream_id"),
        service_instance_id=as_uuid(row, "service_instance_id"),
        service_key=as_str(row, "service_key"),
        policy_version=as_str(row, "policy_version"),
        source_schema_version=as_str(row, "source_schema_version"),
        semantic_convention_version=as_str(row, "semantic_convention_version"),
        manifest_version=int(as_str(row, "annotation_manifest_version")),
        probe_id=as_uuid(row, "probe_id"),
        required_hops=tuple(str(hop) for hop in hops),
        limits=ServerLimits.model_validate(limits_column),
    )
