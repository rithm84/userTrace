"""Read projections.

Durable Postgres projections are authoritative for every view. These queries are
tenant-scoped at the SQL level as well as by the caller's resolved authority, so
a missing predicate cannot leak another client's rows.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    ClientMemberRole,
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
    MembershipStatus,
)
from utrace_contracts.domain.records import (
    CaseFileRecord,
    ClientMemberRecord,
    ClientRecord,
    GateDecisionRecord,
    ImplementationAttemptRecord,
    InterviewSessionRecord,
    OutreachOfferRecord,
    ProductSignalRecord,
    SessionControlsState,
    SessionReadiness,
    ValidationAttemptRecord,
)

from app.db.pool import Database
from app.db.rows import (
    as_bool,
    as_datetime,
    as_int,
    as_optional_str,
    as_str,
    as_uuid,
    present,
)
from app.errors import AggregateNotFoundError, AuthorizationError

__all__ = [
    "CustomerAccountRecord",
    "OfferAllowance",
    "ParticipantAccount",
    "ParticipantInterview",
    "StaffClientMembership",
    "fetch_all",
    "fetch_one",
    "load_case",
    "load_case_file",
    "load_case_files",
    "load_cases",
    "load_client",
    "load_customer_accounts",
    "load_members",
    "load_offer_allowance",
    "load_offers",
    "load_participant_account",
    "load_session",
    "load_staff_clients",
    "participant_user_id",
]


async def fetch_all(
    database: Database, statement: str, params: Sequence[object]
) -> list[DictRow]:
    """Run one read statement and return every row."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(statement, tuple(params))
        return list(await cur.fetchall())


async def fetch_one(
    database: Database, statement: str, params: Sequence[object]
) -> DictRow | None:
    """Run one read statement and return its first row, if any."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(statement, tuple(params))
        return await cur.fetchone()


async def load_client(database: Database, *, client_id: UUID) -> ClientRecord:
    """Return one client, or raise when it does not exist."""
    row = await fetch_one(
        database,
        "select id, name, product_name, revision, created_at "
        "from public.clients where id = %s",
        (client_id,),
    )
    if row is None:
        raise AggregateNotFoundError(table="clients", row_id=client_id)
    return ClientRecord.model_validate(present(row))


async def load_members(
    database: Database, *, client_id: UUID
) -> list[ClientMemberRecord]:
    """Return the client's staff membership rows."""
    rows = await fetch_all(
        database,
        "select id, client_id, role, status, created_at "
        "from public.client_members where client_id = %s order by role, created_at",
        (client_id,),
    )
    return [ClientMemberRecord.model_validate(present(row)) for row in rows]


_SIGNAL_COLUMNS = """
    id, client_id, state, revision, machine_version, initial_flow_type,
    established_flow_type, classification_revision, concern_revision,
    acceptance_criteria_revision,
    originating_client_user_id, originating_customer_account_id,
    pinned_configuration_release_id,
    return_deadline_at, closure_reason, created_at, updated_at
"""


async def load_cases(
    database: Database, *, client_id: UUID
) -> list[ProductSignalRecord]:
    """Return every case for one client, newest first."""
    rows = await fetch_all(
        database,
        f"select {_SIGNAL_COLUMNS} from public.product_signals "
        "where client_id = %s order by created_at desc",
        (client_id,),
    )
    return [ProductSignalRecord.model_validate(present(row)) for row in rows]


async def load_case(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> ProductSignalRecord:
    """Return one case scoped to its client."""
    row = await fetch_one(
        database,
        f"select {_SIGNAL_COLUMNS} from public.product_signals "
        "where id = %s and client_id = %s",
        (product_signal_id, client_id),
    )
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    return ProductSignalRecord.model_validate(present(row))


async def load_gate_decisions(
    database: Database, *, product_signal_id: UUID
) -> list[GateDecisionRecord]:
    """Return the immutable gate decisions for one case."""
    rows = await fetch_all(
        database,
        """
        select id, client_id, product_signal_id, decision, blocker_reason,
               classification_revision, acceptance_criteria_revision,
               pinned_configuration_release_id, decided_at
        from public.gate_decisions
        where product_signal_id = %s order by decided_at
        """,
        (product_signal_id,),
    )
    return [GateDecisionRecord.model_validate(present(row)) for row in rows]


async def load_attempts(
    database: Database, *, product_signal_id: UUID
) -> list[ImplementationAttemptRecord]:
    """Return the case's Implementation Attempt; the demo grants at most one."""
    rows = await fetch_all(
        database,
        """
        select id, client_id, product_signal_id, gate_decision_id, state, revision,
               repository_ref, base_revision, outcome_reason, created_at, updated_at
        from public.implementation_attempts
        where product_signal_id = %s order by created_at
        """,
        (product_signal_id,),
    )
    return [ImplementationAttemptRecord.model_validate(present(row)) for row in rows]


async def load_validation_attempts(
    database: Database, *, product_signal_id: UUID
) -> list[ValidationAttemptRecord]:
    """Return the Validation Attempts for one case."""
    rows = await fetch_all(
        database,
        """
        select id, client_id, product_signal_id, implementation_attempt_id,
               preview_version_id, interview_session_id, client_user_id, state,
               revision, dataset_manifest_digest, acceptance_criteria_revision,
               outcome, completed_at, created_at
        from public.validation_attempts
        where product_signal_id = %s order by created_at
        """,
        (product_signal_id,),
    )
    return [ValidationAttemptRecord.model_validate(present(row)) for row in rows]


_CASE_FILE_COLUMNS = """
    id, client_id, product_signal_id, variant, stage, revision, core_digest,
    delivery_id, sealed_at, published_at, created_at
"""


async def load_case_files(
    database: Database, *, client_id: UUID
) -> list[CaseFileRecord]:
    """Return published Case Files for one client."""
    rows = await fetch_all(
        database,
        f"select {_CASE_FILE_COLUMNS} from public.case_files "
        "where client_id = %s and stage = 'published' order by published_at desc",
        (client_id,),
    )
    return [CaseFileRecord.model_validate(present(row)) for row in rows]


async def load_case_file_for_case(
    database: Database, *, product_signal_id: UUID
) -> CaseFileRecord | None:
    """Return the Case File of one case, at whatever stage it has reached."""
    row = await fetch_one(
        database,
        f"select {_CASE_FILE_COLUMNS} from public.case_files where product_signal_id = %s",
        (product_signal_id,),
    )
    return None if row is None else CaseFileRecord.model_validate(present(row))


async def load_case_file(
    database: Database, *, client_id: UUID, case_file_id: UUID
) -> tuple[CaseFileRecord, Mapping[str, object] | None]:
    """Return one published Case File and its immutable core."""
    row = await fetch_one(
        database,
        f"select {_CASE_FILE_COLUMNS}, core from public.case_files "
        "where id = %s and client_id = %s and stage = 'published'",
        (case_file_id, client_id),
    )
    if row is None:
        raise AggregateNotFoundError(table="case_files", row_id=case_file_id)
    record = CaseFileRecord.model_validate(
        present({key: row[key] for key in row if key != "core"})
    )
    core = row["core"]
    return record, core if isinstance(core, dict) else None


async def load_offers(
    database: Database, *, client_id: UUID
) -> list[OutreachOfferRecord]:
    """Return every Outreach Offer for one client with its issuance state."""
    rows = await fetch_all(
        database,
        """
        select offers.id, offers.client_id, offers.product_signal_id,
               offers.customer_account_id, offers.state, offers.revision,
               offers.currency, offers.amount_minor_units, offers.created_at,
               issuance.state as issuance_state
        from public.outreach_offers as offers
        left join public.offer_entitlements as entitlements
          on entitlements.outreach_offer_id = offers.id
        left join public.offer_issuance_operations as issuance
          on issuance.offer_entitlement_id = entitlements.id
        where offers.client_id = %s
        order by offers.created_at desc
        """,
        (client_id,),
    )
    return [OutreachOfferRecord.model_validate(present(row)) for row in rows]


@dataclass(frozen=True, slots=True)
class OfferAllowance:
    """The client's authorized, reserved, earned, and remaining issuance allowance."""

    currency: str | None
    authorized: int
    reserved: int
    earned: int

    @property
    def remaining(self) -> int:
        """Allowance still available for a new rewarded invitation."""
        return self.authorized - self.reserved - self.earned


async def load_offer_allowance(
    database: Database, *, client_id: UUID
) -> OfferAllowance:
    """Compute the client's remaining issuance allowance from the ledger."""
    budget = await fetch_one(
        database,
        """
        select currency, authorized_minor_units
        from public.offer_budgets
        where client_id = %s
        order by sequence_number desc
        limit 1
        """,
        (client_id,),
    )
    if budget is None:
        return OfferAllowance(currency=None, authorized=0, reserved=0, earned=0)
    currency = as_str(budget, "currency")
    totals = await fetch_one(
        database,
        """
        select
          coalesce(sum(reservations.amount_minor_units)
            filter (where reservations.released_at is null
                      and reservations.converted_at is null), 0)::bigint as reserved,
          coalesce((
            select sum(amount_minor_units) from public.offer_entitlements
            where client_id = %s and currency = %s
          ), 0)::bigint as earned
        from public.offer_reservations as reservations
        where reservations.client_id = %s and reservations.currency = %s
        """,
        (client_id, currency, client_id, currency),
    )
    reserved = as_int(totals, "reserved") if totals is not None else 0
    earned = as_int(totals, "earned") if totals is not None else 0
    return OfferAllowance(
        currency=currency,
        authorized=as_int(budget, "authorized_minor_units"),
        reserved=reserved,
        earned=earned,
    )


async def load_session(
    database: Database, *, session_id: UUID
) -> InterviewSessionRecord:
    """Return the durable Interview Session projection."""
    row = await fetch_one(
        database,
        """
        select sessions.id, sessions.client_id, sessions.product_signal_id,
               sessions.state, sessions.revision, sessions.phase,
               coalesce(readiness.conversational_ready, false) as conversational_ready,
               coalesce(readiness.observation_ready, false) as observation_ready,
               sessions.character_state, sessions.character_revision,
               sessions.implementation_progress, sessions.muted, sessions.paused,
               sessions.closure_reason, sessions.created_at, sessions.updated_at
        from public.interview_sessions as sessions
        left join public.session_readiness as readiness
          on readiness.interview_session_id = sessions.id
        where sessions.id = %s
        """,
        (session_id,),
    )
    if row is None:
        raise AggregateNotFoundError(table="interview_sessions", row_id=session_id)
    closure_reason = as_optional_str(row, "closure_reason")
    return InterviewSessionRecord(
        id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        state=InterviewSessionState(as_str(row, "state")),
        revision=as_int(row, "revision"),
        phase=InterviewPhase(as_str(row, "phase")),
        readiness=SessionReadiness(
            conversational=as_bool(row, "conversational_ready"),
            observation=as_bool(row, "observation_ready"),
        ),
        character_state=AgentCharacterState(as_str(row, "character_state")),
        character_revision=as_int(row, "character_revision"),
        implementation_progress=ImplementationProgress(
            as_str(row, "implementation_progress")
        ),
        controls=SessionControlsState(
            muted=as_bool(row, "muted"), paused=as_bool(row, "paused")
        ),
        **({} if closure_reason is None else {"closure_reason": closure_reason}),
        created_at=as_datetime(row, "created_at"),
        updated_at=as_datetime(row, "updated_at"),
    )


class StaffClientMembership(BaseModel):
    """One client the authenticated staff identity actively belongs to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client: ClientRecord
    membership: ClientMemberRecord


async def load_staff_clients(
    database: Database, *, auth_user_id: UUID
) -> list[StaffClientMembership]:
    """Return every client one authenticated identity holds active staff access to.

    This is the only staff read that is not scoped by a path ``client_id``: a
    caller who knows no client identifier still needs somewhere to start. It
    leaks nothing, because the membership join is the same one every other staff
    route resolves before it reads.
    """
    rows = await fetch_all(
        database,
        """
        select clients.id, clients.name, clients.product_name, clients.revision,
               clients.created_at,
               members.id as member_id, members.role, members.status,
               members.created_at as member_created_at
        from public.client_members as members
        join public.clients on clients.id = members.client_id
        where members.auth_user_id = %s and members.status = %s
        order by clients.name
        """,
        (auth_user_id, MembershipStatus.ACTIVE.value),
    )
    return [
        StaffClientMembership(
            client=ClientRecord(
                id=as_uuid(row, "id"),
                name=as_str(row, "name"),
                product_name=as_str(row, "product_name"),
                revision=as_int(row, "revision"),
                created_at=as_datetime(row, "created_at"),
            ),
            membership=ClientMemberRecord(
                id=as_uuid(row, "member_id"),
                client_id=as_uuid(row, "id"),
                role=ClientMemberRole(as_str(row, "role")),
                status=MembershipStatus(as_str(row, "status")),
                created_at=as_datetime(row, "member_created_at"),
            ),
        )
        for row in rows
    ]


class CustomerAccountRecord(BaseModel):
    """One customer organization and its billing mapping.

    ``stripe_customer_ref`` is the mapped Stripe Customer from the client's
    verified provider references, not an inferred or participant-supplied
    identifier. It is absent when no mapping has been recorded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    client_id: UUID
    external_account_key: str
    name: str
    stripe_customer_ref: str | None = None


async def load_customer_accounts(
    database: Database, *, client_id: UUID
) -> list[CustomerAccountRecord]:
    """Return the client's customer accounts with their billing mapping."""
    rows = await fetch_all(
        database,
        """
        select accounts.id, accounts.client_id, accounts.external_account_key,
               accounts.name,
               refs.provider_resource_id as stripe_customer_ref
        from public.customer_accounts as accounts
        left join public.provider_refs as refs
          on refs.client_id = accounts.client_id
         and refs.owner_id = accounts.id
         and refs.resource_type = 'stripe_customer'
        where accounts.client_id = %s
        order by accounts.external_account_key
        """,
        (client_id,),
    )
    return [
        CustomerAccountRecord(
            id=as_uuid(row, "id"),
            client_id=as_uuid(row, "client_id"),
            external_account_key=as_str(row, "external_account_key"),
            name=as_str(row, "name"),
            stripe_customer_ref=as_optional_str(row, "stripe_customer_ref"),
        )
        for row in rows
    ]


_PARTICIPANT_INTERVIEW_LIMIT = 50


class ParticipantInterview(BaseModel):
    """One Interview Session the participant took part in, as its tab last showed it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: UUID
    client_name: str
    topic: str
    """The originating message the participant wrote, in their own words."""
    purpose: Literal["interview", "validation"]
    state: Literal["participant_setup", "interviewing", "paused", "reconnecting", "ended"]
    end_reason: (
        Literal[
            "finish_later",
            "ended_by_participant",
            "client_stopped",
            "completed",
            "no_implementation",
            "blocked",
            "not_validated",
        ]
        | None
    )
    updated_at: datetime


class ParticipantAccount(BaseModel):
    """The signed-in uTrace User's own participation across every client."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    utrace_user_id: UUID
    participation_count: int
    interviews: tuple[ParticipantInterview, ...]


async def load_participant_account(
    database: Database, *, auth_user_id: UUID
) -> ParticipantAccount:
    """Return one uTrace User's participation, newest session first.

    Each session is read from its committed projection, so the account shows the
    same state and end reason the session tab showed. Only sessions of this
    identity's own client users are selected.
    """
    user = await _participant_user(database, auth_user_id=auth_user_id)
    rows = await fetch_all(
        database,
        """
        select sessions.id as session_id, clients.name as client_name,
               reports.source_excerpt as topic, projections.projection,
               projections.updated_at
        from public.client_users
        join public.interview_sessions as sessions
          on sessions.client_user_id = client_users.id
        join public.clients on clients.id = sessions.client_id
        join public.source_reports as reports
          on reports.product_signal_id = sessions.product_signal_id
        join public.session_projections as projections
          on projections.session_id = sessions.id
        where client_users.utrace_user_id = %s
        order by projections.updated_at desc
        limit %s
        """,
        (as_uuid(user, "id"), _PARTICIPANT_INTERVIEW_LIMIT),
    )
    return ParticipantAccount(
        utrace_user_id=as_uuid(user, "id"),
        participation_count=as_int(user, "participation_count"),
        interviews=tuple(_participant_interview(row) for row in rows),
    )


async def participant_user_id(database: Database, *, auth_user_id: UUID) -> UUID:
    """Return the uTrace User behind one authenticated identity."""
    return as_uuid(await _participant_user(database, auth_user_id=auth_user_id), "id")


async def _participant_user(database: Database, *, auth_user_id: UUID) -> DictRow:
    user = await fetch_one(
        database,
        "select id, participation_count from public.users where auth_user_id = %s",
        (auth_user_id,),
    )
    if user is None:
        raise AuthorizationError(
            subject="participant account",
            reason="this identity has no uTrace User; it has never claimed an invitation",
        )
    return user


def _participant_interview(row: DictRow) -> ParticipantInterview:
    """Validate one session row and its committed projection."""
    projection = row["projection"]
    if not isinstance(projection, Mapping):
        raise TypeError("session_projections.projection is not an object")
    return ParticipantInterview.model_validate(
        {
            "session_id": as_uuid(row, "session_id"),
            "client_name": as_str(row, "client_name"),
            "topic": as_str(row, "topic"),
            "purpose": projection["purpose"],
            "state": projection["state"],
            "end_reason": projection["end_reason"],
            "updated_at": as_datetime(row, "updated_at"),
        }
    )
