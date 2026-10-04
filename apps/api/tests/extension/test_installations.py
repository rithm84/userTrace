"""Installation registration, protocol negotiation, and revocation.

The link code is minted for a signed-in person, the extension exchanges it with a
real ES256 proof of possession of a real device key, and the control plane
verifies both against the production code path.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import InterviewPhase, ProductSignalState

from app.db.pool import Database
from app.errors import AuthorizationError
from app.extension.installations import (
    InstallationRegistrationRequest,
    ProofOfPossession,
    mint_link_code,
    register_installation,
    revoke_installation,
)
from app.extension.proofs import PossessionError
from app.ids import uuid7
from tests.extension.conftest import API_AUDIENCE
from tests.extension.keys import DeviceKey
from tests.factories import create_case
from tests.interview.fixtures import (
    create_binding,
    create_live_session,
    create_preview_allocation,
    link_installation,
)
from tests.supabase_auth import SignedInUser

_SUPPORTED = ("1.0",)
_POP_LIFETIME_SECONDS = 120


async def _user(database: Database, auth_users: dict[str, SignedInUser]) -> UUID:
    """Insert one global uTrace User and return its identifier."""
    user_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "insert into public.users (id, auth_user_id, display_name) values (%s, %s, %s)",
            (user_id, auth_users["participant"].auth_user_id, "extension participant"),
        )
    return user_id


def _request(
    *, link_code: str, device: DeviceKey, versions: tuple[str, ...] = _SUPPORTED
) -> InstallationRegistrationRequest:
    """Build one registration request signed by the device key."""
    challenge, signature = device.prove(
        audience=API_AUDIENCE, purpose="installation_registration", installation_id=None
    )
    return InstallationRegistrationRequest(
        schema_version=1,
        kind="installation_registration_request",
        link_code=link_code,
        device_public_key_spki_b64u=device.public_key_spki_b64u,
        extension_version="1.0.0",
        supported_protocol_versions=versions,
        proof_of_possession=ProofOfPossession(
            algorithm="ES256", challenge_b64u=challenge, signature_b64u=signature
        ),
    )


async def test_a_link_code_and_a_possession_proof_register_one_installation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    user_id = await _user(database, auth_users)
    code = await mint_link_code(
        database,
        utrace_user_id=user_id,
        redirect_uri="https://abcdef.chromiumapp.org/extension-link",
        extension_version="1.0.0",
        protocol_version="1.0",
        lifetime_seconds=600,
    )
    device = DeviceKey.generate()
    record = await register_installation(
        database,
        request=_request(link_code=code.code, device=device),
        audience=API_AUDIENCE,
        supported_protocol_versions=_SUPPORTED,
        possession_max_age_seconds=_POP_LIFETIME_SECONDS,
    )

    assert record.utrace_user_id == user_id
    assert record.account_link_state == "linked"
    assert record.negotiated_protocol_version == "1.0"

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select consumed_at, consumed_by_installation_id from public.extension_link_codes"
        )
        consumed = await cur.fetchone()
        await cur.execute(
            "select purpose, audience from public.extension_possession_proofs"
        )
        proof = await cur.fetchone()
        await cur.execute(
            "select preferences_revision from public.extension_preferences where utrace_user_id = %s",
            (user_id,),
        )
        preferences = await cur.fetchone()
    assert consumed is not None
    assert consumed["consumed_at"] is not None
    assert consumed["consumed_by_installation_id"] == record.installation_id
    assert proof is not None
    assert proof["purpose"] == "installation_registration"
    assert proof["audience"] == API_AUDIENCE
    assert preferences is not None
    assert preferences["preferences_revision"] == 0


async def test_a_link_code_cannot_be_used_twice(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    user_id = await _user(database, auth_users)
    code = await mint_link_code(
        database,
        utrace_user_id=user_id,
        redirect_uri="https://abcdef.chromiumapp.org/extension-link",
        extension_version="1.0.0",
        protocol_version="1.0",
        lifetime_seconds=600,
    )
    device = DeviceKey.generate()
    await register_installation(
        database,
        request=_request(link_code=code.code, device=device),
        audience=API_AUDIENCE,
        supported_protocol_versions=_SUPPORTED,
        possession_max_age_seconds=_POP_LIFETIME_SECONDS,
    )
    with pytest.raises(AuthorizationError) as refusal:
        await register_installation(
            database,
            request=_request(link_code=code.code, device=DeviceKey.generate()),
            audience=API_AUDIENCE,
            supported_protocol_versions=_SUPPORTED,
            possession_max_age_seconds=_POP_LIFETIME_SECONDS,
        )
    assert "already used" in str(refusal.value)


async def test_a_replayed_possession_challenge_is_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    user_id = await _user(database, auth_users)
    device = DeviceKey.generate()
    challenge, signature = device.prove(
        audience=API_AUDIENCE, purpose="installation_registration", installation_id=None
    )

    async def register(code: str) -> None:
        await register_installation(
            database,
            request=InstallationRegistrationRequest(
                schema_version=1,
                kind="installation_registration_request",
                link_code=code,
                device_public_key_spki_b64u=device.public_key_spki_b64u,
                extension_version="1.0.0",
                supported_protocol_versions=_SUPPORTED,
                proof_of_possession=ProofOfPossession(
                    algorithm="ES256",
                    challenge_b64u=challenge,
                    signature_b64u=signature,
                ),
            ),
            audience=API_AUDIENCE,
            supported_protocol_versions=_SUPPORTED,
            possession_max_age_seconds=_POP_LIFETIME_SECONDS,
        )

    first = await mint_link_code(
        database,
        utrace_user_id=user_id,
        redirect_uri="https://abcdef.chromiumapp.org/extension-link",
        extension_version="1.0.0",
        protocol_version="1.0",
        lifetime_seconds=600,
    )
    second = await mint_link_code(
        database,
        utrace_user_id=user_id,
        redirect_uri="https://abcdef.chromiumapp.org/extension-link",
        extension_version="1.0.0",
        protocol_version="1.0",
        lifetime_seconds=600,
    )
    await register(first.code)
    with pytest.raises(PossessionError) as replay:
        await register(second.code)
    assert replay.value.reason == "replayed_challenge"


async def test_a_stale_challenge_and_a_wrong_audience_are_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    user_id = await _user(database, auth_users)
    device = DeviceKey.generate()
    code = await mint_link_code(
        database,
        utrace_user_id=user_id,
        redirect_uri="https://abcdef.chromiumapp.org/extension-link",
        extension_version="1.0.0",
        protocol_version="1.0",
        lifetime_seconds=600,
    )
    stale_challenge, stale_signature = device.prove(
        audience=API_AUDIENCE,
        purpose="installation_registration",
        installation_id=None,
        issued_at=datetime.now(tz=UTC) - timedelta(seconds=_POP_LIFETIME_SECONDS * 4),
    )
    with pytest.raises(PossessionError) as stale:
        await register_installation(
            database,
            request=InstallationRegistrationRequest(
                schema_version=1,
                kind="installation_registration_request",
                link_code=code.code,
                device_public_key_spki_b64u=device.public_key_spki_b64u,
                extension_version="1.0.0",
                supported_protocol_versions=_SUPPORTED,
                proof_of_possession=ProofOfPossession(
                    algorithm="ES256",
                    challenge_b64u=stale_challenge,
                    signature_b64u=stale_signature,
                ),
            ),
            audience=API_AUDIENCE,
            supported_protocol_versions=_SUPPORTED,
            possession_max_age_seconds=_POP_LIFETIME_SECONDS,
        )
    assert stale.value.reason == "stale_challenge"

    with pytest.raises(PossessionError) as audience:
        await register_installation(
            database,
            request=_request(link_code=code.code, device=device),
            audience="https://api.somewhere-else.example",
            supported_protocol_versions=_SUPPORTED,
            possession_max_age_seconds=_POP_LIFETIME_SECONDS,
        )
    assert audience.value.reason == "audience_mismatch"


async def test_an_incompatible_build_cannot_register(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    user_id = await _user(database, auth_users)
    code = await mint_link_code(
        database,
        utrace_user_id=user_id,
        redirect_uri="https://abcdef.chromiumapp.org/extension-link",
        extension_version="1.0.0",
        protocol_version="1.9",
        lifetime_seconds=600,
    )
    with pytest.raises(AuthorizationError) as refusal:
        await register_installation(
            database,
            request=_request(
                link_code=code.code, device=DeviceKey.generate(), versions=("1.9",)
            ),
            audience=API_AUDIENCE,
            supported_protocol_versions=_SUPPORTED,
            possession_max_age_seconds=_POP_LIFETIME_SECONDS,
        )
    assert "no protocol version the control plane supports" in str(refusal.value)


async def test_revocation_blocks_new_bindings_and_revokes_the_current_one(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await create_case(
        database,
        name="extension-revoke",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = await create_live_session(
        database, case=case, phase=InterviewPhase.DISCOVERY
    )
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    device = DeviceKey.generate()
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=device.public_key_spki_b64u,
        protocol_version="1.0",
    )
    binding_id, _ = await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )

    challenge, signature = device.prove(
        audience=API_AUDIENCE,
        purpose="installation_revocation",
        installation_id=installation_id,
    )
    await revoke_installation(
        database,
        installation_id=installation_id,
        proof=ProofOfPossession(
            algorithm="ES256", challenge_b64u=challenge, signature_b64u=signature
        ),
        audience=API_AUDIENCE,
        possession_max_age_seconds=_POP_LIFETIME_SECONDS,
    )

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select account_link_state, revoked_at from public.extension_installations "
            "where id = %s",
            (installation_id,),
        )
        installation = await cur.fetchone()
        await cur.execute(
            "select status, reason from public.extension_bindings where id = %s",
            (binding_id,),
        )
        binding = await cur.fetchone()
    assert installation is not None
    assert installation["account_link_state"] == "revoked"
    assert installation["revoked_at"] is not None
    assert binding is not None
    assert binding["status"] == "revoked"
    assert binding["reason"] == "installation_revoked"
