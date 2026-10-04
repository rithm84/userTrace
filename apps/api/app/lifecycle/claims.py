"""Invitation claim.

The invitation is opaque and expiring: only its digest is stored, and the row is
bound server-side to the invited provider identity, client, Product Signal, and
originating user. First claim proves control of that identity through the
configured provider binding; the same identity may reconnect, another person is
rejected. Claiming is idempotent and returns the same Interview Session.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AgentCharacterState,
    AggregateKind,
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
)

from app.db.kernel import apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_optional_uuid, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AuthorizationError, GuardRejectedError
from app.ids import uuid7
from app.interview.projection import publish_projection_on_connection
from app.lifecycle.interview import INTERVIEW_SESSION_MACHINE, InterviewSessionFacts
from app.lifecycle.machine import plan_transition
from app.lifecycle.outreach import OUTREACH_ATTEMPT_MACHINE, OutreachAttemptFacts
from app.logging import get_logger

__all__ = ["ClaimOutcome", "claim_invitation", "invitation_token_digest"]

_log = get_logger(__name__)


def invitation_token_digest(token: str) -> str:
    """Return the stored digest of an opaque invitation token."""
    return sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """The Interview Session the claim opened, and whether it was already open."""

    interview_session_id: UUID
    already_claimed: bool


async def _load_invitation(conn: AsyncConnection[DictRow], digest: str) -> DictRow:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, product_signal_id, client_user_id,
                   outreach_attempt_id, bound_provider, bound_provider_identity,
                   expires_at, claimed_at, revoked_at, interview_session_id
            from public.invitations
            where token_digest = %s
            for update
            """,
            (digest,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AuthorizationError(
            subject="invitation", reason="no invitation matches this token"
        )
    return row


async def _proves_bound_identity(
    conn: AsyncConnection[DictRow], auth_user_id: UUID, provider: str, identity: str
) -> bool:
    """Prove the signed-in account controls the invited provider identity."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select 1 from auth.identities
            where user_id = %s and provider = %s and provider_id = %s
            """,
            (auth_user_id, provider, identity),
        )
        return await cur.fetchone() is not None


async def _bind_account(
    conn: AsyncConnection[DictRow], *, client_user_id: UUID, auth_user_id: UUID
) -> None:
    """Bind the invited uTrace User to the account that proved the invited identity.

    Intake and the demo seed create the uTrace User before the person has ever
    signed in, so the first claim writes the binding and a later claim by the
    same account finds it in place. A uTrace User already bound to another
    account belongs to someone else.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.users
            set auth_user_id = %s, revision = revision + 1, updated_at = now()
            where id = (select utrace_user_id from public.client_users where id = %s)
              and auth_user_id is null
            """,
            (auth_user_id, client_user_id),
        )
        await cur.execute(
            """
            select users.auth_user_id
            from public.client_users
            join public.users on users.id = client_users.utrace_user_id
            where client_users.id = %s
            """,
            (client_user_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"client user {client_user_id} has no uTrace User")
    if as_optional_uuid(row, "auth_user_id") != auth_user_id:
        raise AuthorizationError(
            subject=f"client user {client_user_id}",
            reason="the invited uTrace User is bound to a different account",
        )


async def claim_invitation(
    database: Database, *, token: str, auth_user_id: UUID
) -> ClaimOutcome:
    """Claim one invitation and return its Interview Session."""
    digest = invitation_token_digest(token)
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn:
        invitation = await _load_invitation(conn, digest)
        invitation_id = as_uuid(invitation, "id")
        client_id = as_uuid(invitation, "client_id")

        if invitation["revoked_at"] is not None:
            raise AuthorizationError(
                subject=f"invitation {invitation_id}", reason="the invitation is revoked"
            )
        if as_datetime(invitation, "expires_at") <= now:
            raise AuthorizationError(
                subject=f"invitation {invitation_id}", reason="the invitation has expired"
            )

        bound_provider = as_str(invitation, "bound_provider")
        bound_identity = as_str(invitation, "bound_provider_identity")
        if not await _proves_bound_identity(
            conn, auth_user_id, bound_provider, bound_identity
        ):
            raise AuthorizationError(
                subject=f"invitation {invitation_id}",
                reason=(
                    f"the signed-in account does not control the invited "
                    f"{bound_provider} identity"
                ),
            )
        await _bind_account(
            conn,
            client_user_id=as_uuid(invitation, "client_user_id"),
            auth_user_id=auth_user_id,
        )

        existing_session = as_optional_uuid(invitation, "interview_session_id")
        if existing_session is not None:
            return ClaimOutcome(
                interview_session_id=existing_session, already_claimed=True
            )

        session_id = uuid7()
        actor = Actor(kind=ActorKind.CUSTOMER_PARTICIPANT, ref=str(auth_user_id))
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.interview_sessions (
                    id, client_id, product_signal_id, client_user_id, state, phase,
                    character_state, implementation_progress
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    client_id,
                    as_uuid(invitation, "product_signal_id"),
                    as_uuid(invitation, "client_user_id"),
                    InterviewSessionState.INVITED.value,
                    InterviewPhase.PARTICIPANT_SETUP.value,
                    AgentCharacterState.IDLE.value,
                    ImplementationProgress.NOT_STARTED.value,
                ),
            )
            await cur.execute(
                """
                update public.invitations
                set claimed_at = now(), interview_session_id = %s, updated_at = now()
                where id = %s
                """,
                (session_id, invitation_id),
            )
            await cur.execute(
                "select state, revision from public.outreach_attempts where id = %s "
                "for update",
                (as_uuid(invitation, "outreach_attempt_id"),),
            )
            attempt = await cur.fetchone()
        if attempt is None:
            raise GuardRejectedError(
                machine="outreach_attempt",
                trigger="claim",
                reason="the invitation has no Outreach Attempt",
            )

        await apply_transition_on_connection(
            conn,
            plan_transition(
                OUTREACH_ATTEMPT_MACHINE,
                OutreachAttemptFacts(
                    bound_identity_matches=True, expired=False, contact_budget_remaining=0
                ),
                trigger="claim",
                current_state=as_str(attempt, "state"),
                client_id=client_id,
                aggregate_kind=AggregateKind.OUTREACH_ATTEMPT,
                aggregate_id=as_uuid(invitation, "outreach_attempt_id"),
                expected_revision=as_int(attempt, "revision"),
                state_column="state",
                mutation={"responded_at": now},
                event=EventAppend(
                    message_id=uuid7(),
                    stream=StreamRef(
                        kind=AggregateKind.OUTREACH_ATTEMPT,
                        id=as_uuid(invitation, "outreach_attempt_id"),
                    ),
                    event_type="invitation_claimed",
                    payload={
                        "invitation_id": str(invitation_id),
                        "interview_session_id": str(session_id),
                    },
                    actor=actor,
                    causation_id=invitation_id,
                    correlation_id=as_uuid(invitation, "product_signal_id"),
                    occurred_at=now,
                ),
                jobs=(),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=StreamRef(kind=AggregateKind.INVITATION, id=invitation_id),
                    action="claim_invitation",
                    outcome="claimed",
                    reason_code=None,
                    actor=actor,
                    pinned_configuration_release_id=None,
                    detail={"interview_session_id": str(session_id)},
                ),
                additional_locks=(),
            ),
        )

        await apply_transition_on_connection(
            conn,
            plan_transition(
                INTERVIEW_SESSION_MACHINE,
                InterviewSessionFacts(
                    identity_matches=True,
                    participant_setup_passed=False,
                    conversational_ready=False,
                    observation_ready=False,
                ),
                trigger="claim",
                current_state=InterviewSessionState.INVITED.value,
                client_id=client_id,
                aggregate_kind=AggregateKind.INTERVIEW_SESSION,
                aggregate_id=session_id,
                expected_revision=0,
                state_column="state",
                mutation={},
                event=EventAppend(
                    message_id=uuid7(),
                    stream=StreamRef(
                        kind=AggregateKind.INTERVIEW_SESSION, id=session_id
                    ),
                    event_type="interview_session_claimed",
                    payload={"invitation_id": str(invitation_id)},
                    actor=actor,
                    causation_id=invitation_id,
                    correlation_id=as_uuid(invitation, "product_signal_id"),
                    occurred_at=now,
                ),
                jobs=(),
                commands=(),
                audit=AuditIntent(
                    audit_id=uuid7(),
                    subject=StreamRef(
                        kind=AggregateKind.INTERVIEW_SESSION, id=session_id
                    ),
                    action="claim_invitation",
                    outcome="session_claimed",
                    reason_code=None,
                    actor=actor,
                    pinned_configuration_release_id=None,
                    detail={"invitation_id": str(invitation_id)},
                ),
                additional_locks=(),
            ),
        )
        # The session tab renders from this projection, so the session opens with one.
        await publish_projection_on_connection(conn, interview_session_id=session_id)

    _log.info(
        "invitation_claimed",
        client_id=str(client_id),
        invitation_id=str(invitation_id),
        interview_session_id=str(session_id),
    )
    return ClaimOutcome(interview_session_id=session_id, already_claimed=False)
