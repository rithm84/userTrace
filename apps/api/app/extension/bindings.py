"""Binding one Interview Session to one preview document.

Three independent proofs meet here and all three must hold:

- the session tab's single-use, short-lived binding offer, scoped to the client,
  user, session, preview runtime allocation, extension installation, and
  protocol version;
- the installation's proof of possession, established when the socket opened;
- the embedded SDK's signed page proof, which is the authority for the client and
  environment, so client product teams configure no domain allowlist.

A session ID, a Chrome tab ID, an origin, or SDK presence establishes no trust on
its own. One session has one authoritative preview-tab binding, and focusing
another valid tab of the same session transfers that binding rather than
replaying a consumed offer.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import ValidationError
from utrace_contracts.domain.lifecycle import InterviewSessionState

from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.extension.protocol import (
    BindingOfferFrame,
    PageProof,
    PageProofClaims,
    ProtocolError,
)
from app.ids import uuid7
from app.ingestion.errors import SourceValidationError
from app.ingestion.signing import SignedEnvelope, Signer, payload_digest
from app.logging import get_logger

__all__ = [
    "BindingOffer",
    "BoundDocument",
    "PageProofVerifier",
    "apply_binding_request",
    "current_binding",
    "mint_binding_offer",
    "offer_digest",
    "revoke_binding",
    "served_allocation",
]

_log = get_logger(__name__)

_OFFER_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class BindingOffer:
    """One minted single-use offer, as the session tab receives it."""

    offer_token: str
    expires_at: datetime
    protocol_version: str
    extension_installation_id: UUID
    preview_runtime_allocation_id: UUID


def offer_digest(token: str) -> str:
    """Return the stored digest of an opaque binding offer."""
    return sha256(token.encode("utf-8")).hexdigest()


async def mint_binding_offer(
    database: Database,
    *,
    client_id: UUID,
    client_user_id: UUID,
    interview_session_id: UUID,
    extension_installation_id: UUID,
    preview_runtime_allocation_id: UUID,
    protocol_version: str,
    lifetime_seconds: int,
) -> BindingOffer:
    """Mint one short-lived offer scoped to exactly one installation and document.

    The token is opaque to the session tab and only its digest is stored, so a
    database read cannot reconstruct a usable offer.
    """
    token = secrets.token_urlsafe(_OFFER_TOKEN_BYTES)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=lifetime_seconds)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.extension_binding_offers (
                id, client_id, interview_session_id, client_user_id,
                extension_installation_id, preview_runtime_allocation_id,
                protocol_version, offer_digest, expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                interview_session_id,
                client_user_id,
                extension_installation_id,
                preview_runtime_allocation_id,
                protocol_version,
                offer_digest(token),
                expires_at,
            ),
        )
    _log.info(
        "extension_binding_offer_minted",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        extension_installation_id=str(extension_installation_id),
        expires_at=expires_at.isoformat(),
    )
    return BindingOffer(
        offer_token=token,
        expires_at=expires_at,
        protocol_version=protocol_version,
        extension_installation_id=extension_installation_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
    )


@dataclass(frozen=True, slots=True)
class BoundDocument:
    """The authoritative binding after a request is accepted or refused."""

    status: str
    binding_revision: int
    interview_session_id: UUID
    client_id: UUID | None
    extension_binding_id: UUID | None
    document_attachment_id: UUID | None
    preview_runtime_allocation_id: UUID | None
    reason: str | None


def _page_proof_envelope(proof: PageProof) -> SignedEnvelope:
    """Return the signed envelope a forwarded page proof carries.

    The extension forwards the envelope field by field, so the control plane
    rebuilds it verbatim: the payload bytes it verifies are the bytes ingestion
    signed, and the digest it stores is the digest of those same bytes.
    """
    return SignedEnvelope(
        algorithm=proof.algorithm,
        key_id=proof.key_id,
        payload_b64url=proof.payload_b64url,
        signature_b64url=proof.signature_b64url,
    )


class PageProofVerifier:
    """Verifies the embedded SDK's page proof and returns its signed claims.

    The proof is an Ed25519 envelope this control plane issued at document
    activation, so the same signer verifies it. The signed payload bytes are the
    authority; nothing the extension says alongside them is.
    """

    def __init__(self, *, signer: Signer) -> None:
        self._signer = signer

    def verify(self, proof: PageProof, *, now: datetime) -> PageProofClaims:
        """Return the signed claims, or refuse the proof with a closed reason."""
        try:
            raw = self._signer.verify(_page_proof_envelope(proof))
        except SourceValidationError as error:
            raise ProtocolError(
                "invalid_page_proof",
                f"the page proof's {error.field} {error.reason}",
            ) from error
        try:
            claims = PageProofClaims.model_validate_json(raw)
        except ValidationError as error:
            raise ProtocolError(
                "malformed_page_proof",
                f"the signed payload failed validation with {error.error_count()} errors",
            ) from error
        if claims.expires_at <= now:
            raise ProtocolError("expired_page_proof", "the page proof has expired")
        return claims


async def apply_binding_request(
    conn: AsyncConnection[DictRow],
    *,
    frame: BindingOfferFrame,
    extension_installation_id: UUID,
    verifier: PageProofVerifier,
    supported_protocol_versions: tuple[str, ...],
) -> BoundDocument:
    """Validate one binding request and commit the authoritative binding."""
    now = datetime.now(tz=UTC)
    request = frame.payload.request
    claims = verifier.verify(frame.payload.page_proof, now=now)

    if request.kind == "offer":
        offer = request.offer
        if offer.protocol_version not in supported_protocol_versions:
            return _rejected(offer.session_id, "unsupported_protocol_version")
        stored = await _claim_offer(conn, token=offer.offer_token, now=now)
        if stored is None:
            return _rejected(offer.session_id, "offer_unknown_or_consumed")
        if as_uuid(stored, "extension_installation_id") != extension_installation_id:
            return _rejected(offer.session_id, "offer_installation_mismatch")
        if as_uuid(stored, "interview_session_id") != offer.session_id:
            return _rejected(offer.session_id, "offer_session_mismatch")
        if as_uuid(stored, "client_id") != offer.client_id:
            return _rejected(offer.session_id, "offer_client_mismatch")
        allocation = as_uuid(stored, "preview_runtime_allocation_id")
        if allocation != offer.preview_runtime_allocation_id:
            return _rejected(offer.session_id, "offer_allocation_mismatch")
        if as_str(stored, "protocol_version") != offer.protocol_version:
            return _rejected(offer.session_id, "offer_protocol_mismatch")
        session_id = offer.session_id
        client_id = offer.client_id
        offer_token_id: UUID | None = as_uuid(stored, "id")
    else:
        session_id = request.session_id
        existing = await current_binding(conn, interview_session_id=session_id)
        if existing is None or existing != request.extension_binding_id:
            return _rejected(session_id, "unknown_binding_for_rebind")
        previous = await _load_binding(conn, binding_id=request.extension_binding_id)
        if as_uuid(previous, "extension_installation_id") != extension_installation_id:
            return _rejected(session_id, "rebind_installation_mismatch")
        client_id = as_uuid(previous, "client_id")
        # A rebind follows the session origin: after it advances to a candidate, the
        # reloaded document belongs to the candidate's runtime, not the bound one's.
        allocation = await served_allocation(conn, interview_session_id=session_id)
        offer_token_id = None

    if claims.session_id != session_id or claims.client_id != client_id:
        return _rejected(session_id, "page_proof_session_mismatch")
    if claims.preview_runtime_allocation_id != allocation:
        return _rejected(session_id, "page_proof_allocation_mismatch")
    if claims.origin != frame.payload.document_origin:
        return _rejected(session_id, "page_proof_origin_mismatch")
    if claims.sdk_protocol_version not in supported_protocol_versions:
        return _rejected(session_id, "unsupported_sdk_protocol_version")

    session = await _load_session(conn, interview_session_id=session_id)
    if as_str(session, "state") == InterviewSessionState.CLOSED.value:
        return _rejected(session_id, "session_closed")
    if as_uuid(session, "client_id") != client_id:
        return _rejected(session_id, "client_scope_mismatch")
    await _require_allocation(conn, allocation_id=allocation, session_id=session_id)

    await revoke_binding(
        conn,
        interview_session_id=session_id,
        reason="replaced_by_new_document",
    )
    binding_id = uuid7()
    # The attachment identity comes from the signed proof: ingestion issued it
    # when the document activated, and every surface names that same one.
    document_attachment_id = claims.document_attachment_id
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.extension_bindings (
                id, client_id, interview_session_id, extension_installation_id,
                preview_runtime_allocation_id, document_attachment_id, status,
                binding_revision, document_origin, tab_id, document_nonce,
                sdk_protocol_version, page_proof_digest, offer_token_id
            )
            values (%s, %s, %s, %s, %s, %s, 'bound', %s, %s, %s, %s, %s, %s, %s)
            returning binding_revision
            """,
            (
                binding_id,
                client_id,
                session_id,
                extension_installation_id,
                allocation,
                document_attachment_id,
                await _next_binding_revision(conn, interview_session_id=session_id),
                frame.payload.document_origin,
                frame.payload.tab_locator.tab_id,
                claims.document_nonce,
                claims.sdk_protocol_version,
                payload_digest(_page_proof_envelope(frame.payload.page_proof)),
                offer_token_id,
            ),
        )
        written = await cur.fetchone()
    if written is None:
        raise AggregateNotFoundError(table="extension_bindings", row_id=binding_id)
    if offer_token_id is not None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.extension_binding_offers
                set consumed_at = now(), consumed_by_binding_id = %s
                where id = %s
                """,
                (binding_id, offer_token_id),
            )
    _log.info(
        "extension_binding_bound",
        client_id=str(client_id),
        interview_session_id=str(session_id),
        extension_installation_id=str(extension_installation_id),
        extension_binding_id=str(binding_id),
        document_attachment_id=str(document_attachment_id),
        request_kind=request.kind,
    )
    return BoundDocument(
        status="bound",
        binding_revision=as_int(written, "binding_revision"),
        interview_session_id=session_id,
        client_id=client_id,
        extension_binding_id=binding_id,
        document_attachment_id=document_attachment_id,
        preview_runtime_allocation_id=allocation,
        reason=None,
    )


async def served_allocation(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> UUID:
    """The Preview Runtime Allocation the session's origin serves.

    That is the allocation of the session's active route revision, which a candidate
    takes over when the origin advances to it, or the session's claimed base allocation
    while its first route is still being committed.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select coalesce(
                     routes.preview_runtime_allocation_id,
                     sessions.preview_runtime_allocation_id
                   ) as preview_runtime_allocation_id
            from public.interview_sessions as sessions
            left join public.preview_routes as routes
              on routes.interview_session_id = sessions.id and routes.state = 'active'
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None or row["preview_runtime_allocation_id"] is None:
        raise GuardRejectedError(
            machine="interview_session",
            trigger="extension_binding",
            reason="the session has no Preview Runtime Allocation yet",
        )
    return as_uuid(row, "preview_runtime_allocation_id")


async def revoke_binding(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID, reason: str
) -> UUID | None:
    """Revoke the session's current binding, returning the binding it revoked."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.extension_bindings
            set status = 'revoked', reason = %s, revoked_at = now(),
                binding_revision = binding_revision + 1, updated_at = now()
            where interview_session_id = %s and status = 'bound'
            returning id
            """,
            (reason, interview_session_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    revoked = as_uuid(row, "id")
    _log.info(
        "extension_binding_revoked",
        interview_session_id=str(interview_session_id),
        extension_binding_id=str(revoked),
        reason=reason,
    )
    return revoked


async def current_binding(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> UUID | None:
    """Return the session's current binding identifier, if it has one."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.extension_bindings
            where interview_session_id = %s and status = 'bound'
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    return as_uuid(row, "id") if row is not None else None


async def _claim_offer(
    conn: AsyncConnection[DictRow], *, token: str, now: datetime
) -> DictRow | None:
    """Lock one unexpired, unconsumed offer, or return ``None`` when there is none.

    The row lock is what makes the offer single use: a concurrent binding waits
    and then finds it consumed.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, interview_session_id, extension_installation_id,
                   preview_runtime_allocation_id, protocol_version, expires_at
            from public.extension_binding_offers
            where offer_digest = %s and consumed_at is null and expires_at > %s
            for update
            """,
            (offer_digest(token), now),
        )
        return await cur.fetchone()


async def _next_binding_revision(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> int:
    """Return the next monotonic binding revision for one session."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select coalesce(max(binding_revision), 0) + 1 as next_revision
            from public.extension_bindings
            where interview_session_id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    return as_int(row, "next_revision") if row is not None else 1


async def _load_binding(
    conn: AsyncConnection[DictRow], *, binding_id: UUID
) -> DictRow:
    """Read one binding row."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, interview_session_id, extension_installation_id,
                   preview_runtime_allocation_id, status
            from public.extension_bindings
            where id = %s
            """,
            (binding_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="extension_bindings", row_id=binding_id)
    return row


async def _load_session(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> DictRow:
    """Read the Interview Session a binding is being made against."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, state, preview_runtime_allocation_id
            from public.interview_sessions
            where id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    return row


async def _require_allocation(
    conn: AsyncConnection[DictRow], *, allocation_id: UUID, session_id: UUID
) -> None:
    """Refuse a page proof naming an allocation this session does not hold."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select interview_session_id, state
            from public.preview_runtime_allocations
            where id = %s
            """,
            (allocation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="preview_runtime_allocations", row_id=allocation_id
        )
    bound_session = row["interview_session_id"]
    if bound_session is not None and bound_session != session_id:
        raise ProtocolError(
            "allocation_bound_elsewhere",
            "the page proof names an allocation held by another Interview Session",
        )


def _rejected(session_id: UUID, reason: str) -> BoundDocument:
    """Build the refusal the extension renders, naming its reason code."""
    _log.info(
        "extension_binding_rejected",
        interview_session_id=str(session_id),
        reason=reason,
    )
    return BoundDocument(
        status="rejected",
        binding_revision=0,
        interview_session_id=session_id,
        client_id=None,
        extension_binding_id=None,
        document_attachment_id=None,
        preview_runtime_allocation_id=None,
        reason=reason,
    )
