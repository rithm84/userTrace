"""Installation identity, account linking, and installation revocation.

Setup happens once for the individual uTrace User, not once per client or
interview. The web account-link page, which the person reaches already signed
in, asks for a short-lived single-use link code; the extension exchanges that
code together with its device public key and a proof of possession of the
matching private key. The control plane maps the authenticated person to a
global `utrace_user_id` and never hands the extension the identity graph.

Signing out or revoking an installation blocks new bindings and revokes active
extension authority. A reinstall gets a new installation identity because the
device key it proves possession of is new.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field

from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import AggregateNotFoundError, AuthorizationError
from app.extension.proofs import (
    PossessionChallenge,
    PossessionError,
    claimed_installation_id,
    decode_forwarded_proof,
    verify_possession,
)
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "InstallationRecord",
    "InstallationRegistrationRequest",
    "LinkCode",
    "ProofOfPossession",
    "consume_possession_proof",
    "link_code_digest",
    "load_installation",
    "mint_link_code",
    "register_installation",
    "revoke_installation",
    "validate_extension_redirect_uri",
    "verify_session_preflight_proof",
]

_log = get_logger(__name__)

_LINK_CODE_BYTES = 32
_BASE64URL = r"^[A-Za-z0-9_-]{16,4096}$"
_EXTENSION_VERSION = r"^[0-9]+(\.[0-9]+){0,3}$"
_PROTOCOL_VERSION = r"^1\.[0-9]{1,3}$"

_LINKED = "linked"
_REVOKED = "revoked"


class ProofOfPossession(BaseModel):
    """A detached ES256 signature over the canonical challenge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    algorithm: Literal["ES256"]
    challenge_b64u: str = Field(pattern=_BASE64URL)
    signature_b64u: str = Field(pattern=_BASE64URL)


class InstallationRegistrationRequest(BaseModel):
    """`installation-registration.v1` request, as the extension sends it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    kind: Literal["installation_registration_request"]
    link_code: str = Field(pattern=_BASE64URL)
    device_public_key_spki_b64u: str = Field(pattern=_BASE64URL)
    extension_version: str = Field(pattern=_EXTENSION_VERSION)
    supported_protocol_versions: tuple[str, ...] = Field(min_length=1, max_length=4)
    proof_of_possession: ProofOfPossession


@dataclass(frozen=True, slots=True)
class InstallationRecord:
    """One registered installation as the control plane holds it."""

    installation_id: UUID
    utrace_user_id: UUID
    account_link_state: str
    negotiated_protocol_version: str
    registered_at: datetime


@dataclass(frozen=True, slots=True)
class LinkCode:
    """The one-time code the account-link redirect carries to the extension."""

    code: str
    expires_at: datetime


_EXTENSION_REDIRECT_HOST = re.compile(r"^[a-p]{32}\.chromiumapp\.org$")


def validate_extension_redirect_uri(redirect_uri: str) -> str:
    """Return the redirect URI once it is provably an extension's own origin.

    `chrome.identity.launchWebAuthFlow` only ever hands the extension a URI of
    the form `https://<extension id>.chromiumapp.org/...`, so anything else would
    carry the link code to a third party. Query and fragment are refused because
    the redirect appends `link_code` itself.
    """
    parts = urlsplit(redirect_uri)
    if parts.scheme != "https":
        raise AuthorizationError(
            subject="extension account link",
            reason="the extension redirect URI must be an https URI",
        )
    if _EXTENSION_REDIRECT_HOST.match(parts.netloc) is None:
        raise AuthorizationError(
            subject="extension account link",
            reason="the extension redirect URI must be the extension's chromiumapp.org origin",
        )
    if parts.query or parts.fragment:
        raise AuthorizationError(
            subject="extension account link",
            reason="the extension redirect URI must carry no query or fragment",
        )
    return redirect_uri


def link_code_digest(code: str) -> str:
    """Return the stored digest of an opaque link code."""
    return sha256(code.encode("utf-8")).hexdigest()


async def mint_link_code(
    database: Database,
    *,
    utrace_user_id: UUID,
    redirect_uri: str,
    extension_version: str,
    protocol_version: str,
    lifetime_seconds: int,
) -> LinkCode:
    """Mint one short-lived, single-use link code for the signed-in person.

    Only the digest is stored, exactly as an invitation token is, so a database
    read cannot reconstruct a usable code.
    """
    code = secrets.token_urlsafe(_LINK_CODE_BYTES)
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=lifetime_seconds)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.extension_link_codes (
                id, code_digest, utrace_user_id, redirect_uri, extension_version,
                protocol_version, expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                link_code_digest(code),
                utrace_user_id,
                redirect_uri,
                extension_version,
                protocol_version,
                expires_at,
            ),
        )
    _log.info(
        "extension_link_code_minted",
        utrace_user_id=str(utrace_user_id),
        protocol_version=protocol_version,
        expires_at=expires_at.isoformat(),
    )
    return LinkCode(code=code, expires_at=expires_at)


async def register_installation(
    database: Database,
    *,
    request: InstallationRegistrationRequest,
    audience: str,
    supported_protocol_versions: tuple[str, ...],
    possession_max_age_seconds: int,
) -> InstallationRecord:
    """Consume a link code, register the device key, and link the installation."""
    negotiated = _negotiate(
        offered=request.supported_protocol_versions,
        supported=supported_protocol_versions,
    )
    now = datetime.now(tz=UTC)
    challenge = verify_possession(
        device_public_key_spki_b64u=request.device_public_key_spki_b64u,
        challenge_b64u=request.proof_of_possession.challenge_b64u,
        signature_b64u=request.proof_of_possession.signature_b64u,
        expected_audience=audience,
        expected_purpose="installation_registration",
        expected_installation_id=None,
        now=now,
        max_age_seconds=possession_max_age_seconds,
    )

    async with database.transaction() as conn:
        link_code_id, utrace_user_id = await _claim_link_code(
            conn, code=request.link_code, now=now
        )
        installation_id = await _upsert_installation(
            conn,
            utrace_user_id=utrace_user_id,
            request=request,
            negotiated=negotiated,
            now=now,
        )
        await consume_possession_proof(
            conn,
            challenge=challenge,
            installation_id=installation_id,
            device_public_key_spki_b64u=request.device_public_key_spki_b64u,
        )
        async with conn.cursor() as cur:
            # Both columns move together, which is what the row's own check
            # constraint requires: a code is consumed by exactly one installation.
            await cur.execute(
                """
                update public.extension_link_codes
                set consumed_at = now(), consumed_by_installation_id = %s
                where id = %s
                """,
                (installation_id, link_code_id),
            )
            await cur.execute(
                """
                insert into public.extension_preferences (utrace_user_id)
                values (%s)
                on conflict (utrace_user_id) do nothing
                """,
                (utrace_user_id,),
            )
    _log.info(
        "extension_installation_registered",
        utrace_user_id=str(utrace_user_id),
        extension_installation_id=str(installation_id),
        negotiated_protocol_version=negotiated,
        extension_version=request.extension_version,
    )
    return InstallationRecord(
        installation_id=installation_id,
        utrace_user_id=utrace_user_id,
        account_link_state=_LINKED,
        negotiated_protocol_version=negotiated,
        registered_at=now,
    )


async def revoke_installation(
    database: Database,
    *,
    installation_id: UUID,
    proof: ProofOfPossession,
    audience: str,
    possession_max_age_seconds: int,
) -> None:
    """Revoke one installation and every binding it currently holds."""
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        installation = await load_installation(conn, installation_id=installation_id)
        challenge = verify_possession(
            device_public_key_spki_b64u=as_str(
                installation, "device_public_key_spki_b64u"
            ),
            challenge_b64u=proof.challenge_b64u,
            signature_b64u=proof.signature_b64u,
            expected_audience=audience,
            expected_purpose="installation_revocation",
            expected_installation_id=installation_id,
            now=now,
            max_age_seconds=possession_max_age_seconds,
        )
        await consume_possession_proof(
            conn,
            challenge=challenge,
            installation_id=installation_id,
            device_public_key_spki_b64u=as_str(
                installation, "device_public_key_spki_b64u"
            ),
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.extension_installations
                set account_link_state = %s, revoked_at = now(),
                    revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (_REVOKED, installation_id),
            )
            await cur.execute(
                """
                update public.extension_bindings
                set status = 'revoked', reason = 'installation_revoked',
                    revoked_at = now(), binding_revision = binding_revision + 1,
                    updated_at = now()
                where extension_installation_id = %s and status = 'bound'
                """,
                (installation_id,),
            )
    _log.info(
        "extension_installation_revoked",
        extension_installation_id=str(installation_id),
    )


async def consume_possession_proof(
    conn: AsyncConnection[DictRow],
    *,
    challenge: PossessionChallenge,
    installation_id: UUID | None,
    device_public_key_spki_b64u: str,
) -> None:
    """Record a challenge's ``jti`` as spent, refusing a replay.

    Single use is durable. The unique primary key is the enforcement, so two
    concurrent connections cannot both accept the same challenge.
    """
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.extension_possession_proofs (
                    jti, extension_installation_id, device_public_key_spki_b64u,
                    purpose, audience, issued_at
                )
                values (%s, %s, %s, %s, %s, %s)
                """,
                (
                    challenge.jti,
                    installation_id,
                    device_public_key_spki_b64u,
                    challenge.purpose,
                    challenge.audience,
                    challenge.issued_at,
                ),
            )
    except UniqueViolation as error:
        raise PossessionError(
            "replayed_challenge", "this challenge was already used"
        ) from error


async def verify_session_preflight_proof(
    conn: AsyncConnection[DictRow],
    *,
    encoded_proof: str,
    utrace_user_id: UUID,
    audience: str,
    max_age_seconds: int,
) -> UUID:
    """Verify the proof the session tab forwarded and return the installation it proves.

    The extension signs a `session_preflight` challenge naming its installation
    and hands the page `base64url(JSON(proof_of_possession))`. The proof counts
    only when that installation belongs to this participant, is linked, and its
    registered device key signed the challenge; its `jti` is then spent so the
    page cannot replay it.
    """
    proof = decode_forwarded_proof(encoded_proof)
    installation_id = claimed_installation_id(proof.challenge_b64u)
    installation = await load_installation(conn, installation_id=installation_id)
    if as_uuid(installation, "utrace_user_id") != utrace_user_id:
        raise PossessionError(
            "installation_not_owned",
            f"installation {installation_id} belongs to another uTrace User",
        )
    if as_str(installation, "account_link_state") != _LINKED:
        raise PossessionError(
            "installation_not_linked", f"installation {installation_id} is not linked"
        )
    device_key = as_str(installation, "device_public_key_spki_b64u")
    challenge = verify_possession(
        device_public_key_spki_b64u=device_key,
        challenge_b64u=proof.challenge_b64u,
        signature_b64u=proof.signature_b64u,
        expected_audience=audience,
        expected_purpose="session_preflight",
        expected_installation_id=installation_id,
        now=datetime.now(tz=UTC),
        max_age_seconds=max_age_seconds,
    )
    await consume_possession_proof(
        conn,
        challenge=challenge,
        installation_id=installation_id,
        device_public_key_spki_b64u=device_key,
    )
    return installation_id


async def load_installation(
    conn: AsyncConnection[DictRow], *, installation_id: UUID
) -> DictRow:
    """Read one installation, refusing a revoked one."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, utrace_user_id, device_public_key_spki_b64u,
                   account_link_state, negotiated_protocol_version, extension_version
            from public.extension_installations
            where id = %s
            """,
            (installation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="extension_installations", row_id=installation_id
        )
    return row


async def _claim_link_code(
    conn: AsyncConnection[DictRow], *, code: str, now: datetime
) -> tuple[UUID, UUID]:
    """Lock one unexpired, unconsumed link code and return it with its owner.

    The row lock is what makes the code single use: a concurrent registration
    waits and then finds it consumed.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, utrace_user_id
            from public.extension_link_codes
            where code_digest = %s and consumed_at is null and expires_at > %s
            for update
            """,
            (link_code_digest(code), now),
        )
        row = await cur.fetchone()
    if row is None:
        raise AuthorizationError(
            subject="extension installation",
            reason="the account-link code is unknown, expired, or already used",
        )
    return as_uuid(row, "id"), as_uuid(row, "utrace_user_id")


async def _upsert_installation(
    conn: AsyncConnection[DictRow],
    *,
    utrace_user_id: UUID,
    request: InstallationRegistrationRequest,
    negotiated: str,
    now: datetime,
) -> UUID:
    """Insert the installation, or re-link the one holding this device key.

    A device key identifies one browser profile's installation. Linking the same
    key again is a re-link of that installation, not a second identity.
    """
    installation_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.extension_installations (
                id, utrace_user_id, device_public_key_spki_b64u, account_link_state,
                extension_version, supported_protocol_versions,
                negotiated_protocol_version, registered_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (device_public_key_spki_b64u) do update
            set utrace_user_id = excluded.utrace_user_id,
                account_link_state = excluded.account_link_state,
                extension_version = excluded.extension_version,
                supported_protocol_versions = excluded.supported_protocol_versions,
                negotiated_protocol_version = excluded.negotiated_protocol_version,
                revoked_at = null,
                revision = public.extension_installations.revision + 1,
                updated_at = now()
            returning id
            """,
            (
                installation_id,
                utrace_user_id,
                request.device_public_key_spki_b64u,
                _LINKED,
                request.extension_version,
                list(request.supported_protocol_versions),
                negotiated,
                now,
            ),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="extension_installations", row_id=installation_id
        )
    return as_uuid(row, "id")


def _negotiate(*, offered: tuple[str, ...], supported: tuple[str, ...]) -> str:
    """Return the highest protocol both sides implement, or refuse the install.

    The control plane accepts the current and previous supported major protocol
    for new sessions; an obsolete installation cannot start a session.
    """
    common = [version for version in supported if version in offered]
    if not common:
        raise AuthorizationError(
            subject="extension installation",
            reason=(
                "this build implements no protocol version the control plane "
                f"supports; it offered {', '.join(offered)}"
            ),
        )
    return max(common, key=_protocol_sort_key)


def _protocol_sort_key(version: str) -> tuple[int, int]:
    """Order protocol versions numerically rather than lexically."""
    major, _, minor = version.partition(".")
    return int(major), int(minor)


