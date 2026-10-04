"""Durable fixtures.

Helpers insert real rows through the same connector the control plane uses, so a
test exercises the production schema, constraints, and policies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    FlowType,
    ImplementationProgress,
    IntakeItemState,
    InterviewPhase,
    InterviewSessionState,
    OutreachAttemptState,
    OutreachPurpose,
    ProductSignalState,
)

from app.clients.overview import activate_overview
from app.clients.shopalytics_overview import SHOPALYTICS_OVERVIEW
from app.db.pool import Database
from app.ids import uuid7
from tests.helpers import policy_digest, record_session_readiness

__all__ = [
    "CaseFixture",
    "create_case",
    "create_client",
    "create_gate_decision",
    "create_interview_session",
    "create_invitation",
    "create_outreach_attempt",
]


@dataclass(frozen=True, slots=True)
class CaseFixture:
    """One client with a routed Product Signal and its originating participant."""

    client_id: UUID
    configuration_release_id: UUID
    utrace_user_id: UUID
    client_user_id: UUID
    customer_account_id: UUID
    intake_item_id: UUID
    product_signal_id: UUID
    cancellation_scope_id: UUID


async def create_client(database: Database, *, name: str) -> UUID:
    """Insert one client and return its identifier."""
    client_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "insert into public.clients (id, name, product_name) values (%s, %s, %s)",
            (client_id, name, f"{name} product"),
        )
    return client_id


async def create_case(
    database: Database,
    *,
    name: str,
    state: ProductSignalState,
    authorizing_auth_user_id: UUID,
    participant_auth_user_id: UUID | None,
) -> CaseFixture:
    """Insert a client, participant, customer account, Intake Item, and case.

    The client has an active Product Overview, as every configured client does.

    ``authorizing_auth_user_id`` is the staff identity that authorized the pinned
    configuration release; ``participant_auth_user_id`` is the separate Supabase
    Auth identity of the originating user, or ``None`` when the test does not
    need the participant to sign in.
    """
    client_id = await create_client(database, name=name)
    await activate_overview(database, client_id=client_id, overview=SHOPALYTICS_OVERVIEW)
    # The release fields the voice agent's instructions read.
    policy: dict[str, object] = {
        "data_seeding": {"fixture_clock": "2026-10-03T09:00:00+00:00"},
        "implementation": {"implementation_limit": 3},
    }
    release_id = uuid7()
    utrace_user_id = uuid7()
    client_user_id = uuid7()
    account_id = uuid7()
    membership_id = uuid7()
    intake_id = uuid7()
    signal_id = uuid7()
    scope_id = uuid7()

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, 1, %s, %s, %s)
            """,
            (
                release_id,
                client_id,
                Jsonb(policy),
                policy_digest(policy),
                authorizing_auth_user_id,
            ),
        )
        await cur.execute(
            "insert into public.users (id, auth_user_id, display_name) values (%s, %s, %s)",
            (utrace_user_id, participant_auth_user_id, "integration participant"),
        )
        await cur.execute(
            "insert into public.client_users (id, client_id, utrace_user_id) "
            "values (%s, %s, %s)",
            (client_user_id, client_id, utrace_user_id),
        )
        await cur.execute(
            """
            insert into public.customer_accounts (
                id, client_id, external_account_key, name
            )
            values (%s, %s, %s, %s)
            """,
            (account_id, client_id, f"{name}-account", f"{name} customer"),
        )
        await cur.execute(
            """
            insert into public.customer_memberships (
                id, client_id, customer_account_id, client_user_id
            )
            values (%s, %s, %s, %s)
            """,
            (membership_id, client_id, account_id, client_user_id),
        )
        await cur.execute(
            """
            insert into public.intake_items (
                id, client_id, state, source_provider, source_channel_ref,
                source_batch_digest, author_provider_identity, client_user_id,
                customer_account_id, payload
            )
            values (%s, %s, %s, 'discord', 'channel', %s, 'discord-user', %s, %s, %s)
            """,
            (
                intake_id,
                client_id,
                IntakeItemState.ROUTED.value,
                f"digest-{intake_id}",
                client_user_id,
                account_id,
                Jsonb({"messages": ["I keep rebuilding the same filters"]}),
            ),
        )
        await cur.execute(
            """
            insert into public.product_signals (
                id, client_id, state, intake_item_id,
                pinned_configuration_release_id, originating_client_user_id,
                originating_customer_account_id, initial_flow_type,
                cancellation_scope_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                signal_id,
                client_id,
                state.value,
                intake_id,
                release_id,
                client_user_id,
                account_id,
                FlowType.FEATURE_REQUEST_FLOW.value,
                scope_id,
            ),
        )
        await cur.execute(
            "update public.intake_items set product_signal_id = %s where id = %s",
            (signal_id, intake_id),
        )
    return CaseFixture(
        client_id=client_id,
        configuration_release_id=release_id,
        utrace_user_id=utrace_user_id,
        client_user_id=client_user_id,
        customer_account_id=account_id,
        intake_item_id=intake_id,
        product_signal_id=signal_id,
        cancellation_scope_id=scope_id,
    )


async def create_outreach_attempt(
    database: Database,
    *,
    case: CaseFixture,
    state: OutreachAttemptState,
    expires_in_seconds: int,
) -> UUID:
    """Insert one Outreach Attempt for a case."""
    attempt_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.outreach_attempts (
                id, client_id, product_signal_id, client_user_id, purpose, state,
                expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                attempt_id,
                case.client_id,
                case.product_signal_id,
                case.client_user_id,
                OutreachPurpose.INITIAL_INVITATION.value,
                state.value,
                datetime.now(tz=UTC) + timedelta(seconds=expires_in_seconds),
            ),
        )
    return attempt_id


async def create_interview_session(
    database: Database,
    *,
    case: CaseFixture,
    state: InterviewSessionState,
    conversational_ready: bool,
) -> UUID:
    """Insert one Interview Session projection and its readiness for a case.

    Readiness lives in `public.session_readiness`, so the requested boundary is
    recorded there with the component facts it is derived from.
    """
    session_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
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
                case.client_id,
                case.product_signal_id,
                case.client_user_id,
                state.value,
                InterviewPhase.DISCOVERY.value,
                AgentCharacterState.LISTENING.value,
                ImplementationProgress.NOT_STARTED.value,
            ),
        )
    await record_session_readiness(
        database,
        interview_session_id=session_id,
        conversational=conversational_ready,
        observation=False,
    )
    return session_id


async def create_gate_decision(
    database: Database,
    *,
    case: CaseFixture,
    decision: str,
    classification_revision: int,
    blocker_reason: str | None,
) -> UUID:
    """Insert one immutable gate decision and its evidence window."""
    window_id = uuid7()
    decision_id = uuid7()
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_windows (
                id, client_id, product_signal_id, window_kind, starts_at, ends_at
            )
            values (%s, %s, %s, 'gate', %s, %s)
            """,
            (window_id, case.client_id, case.product_signal_id, now - timedelta(hours=1), now),
        )
        await cur.execute(
            """
            insert into public.gate_decisions (
                id, client_id, product_signal_id, decision, blocker_reason,
                classification_revision, acceptance_criteria_revision,
                pinned_configuration_release_id, evidence_window_id, approved_scope,
                required_checks, budgets, cited_evidence
            )
            values (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id,
                case.client_id,
                case.product_signal_id,
                decision,
                blocker_reason,
                classification_revision,
                case.configuration_release_id,
                window_id,
                Jsonb({"paths": ["app/"], "excluded": ["billing/"]}),
                Jsonb({"commands": ["pytest"]}),
                Jsonb({"elapsed_seconds": 3600}),
                Jsonb({"evidence_ids": []}),
            ),
        )
    return decision_id


async def create_ready_version(database: Database, *, case: CaseFixture) -> UUID:
    """Give the case an approved attempt whose candidate is a ready Preview Version."""
    decision_id = await create_gate_decision(
        database,
        case=case,
        decision="approved_live",
        classification_revision=0,
        blocker_reason=None,
    )
    attempt_id = uuid7()
    version_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.implementation_attempts (
                id, client_id, product_signal_id, gate_decision_id, state,
                repository_ref, base_revision, consumed_budgets
            )
            values (%s, %s, %s, %s, 'candidate_ready', %s, %s, %s)
            """,
            (
                attempt_id,
                case.client_id,
                case.product_signal_id,
                decision_id,
                "acme/shopalytics",
                "base-revision",
                Jsonb({}),
            ),
        )
        await cur.execute(
            """
            insert into public.preview_versions (
                id, client_id, product_signal_id, implementation_attempt_id, state,
                source_commit, source_digest, build_digest, base_revision,
                seed_manifest_digest, ready_at
            )
            values (%s, %s, %s, %s, 'ready', %s, %s, %s, %s, %s, now())
            """,
            (
                version_id,
                case.client_id,
                case.product_signal_id,
                attempt_id,
                "base-revision",
                "sha256:source",
                "sha256:build",
                "base-revision",
                "sha256:seed",
            ),
        )
    return version_id


async def create_invitation(
    database: Database,
    *,
    case: CaseFixture,
    outreach_attempt_id: UUID,
    token: str,
    bound_auth_user_id: UUID,
    expires_in_seconds: int,
) -> UUID:
    """Insert one invitation bound to a real Supabase Auth provider identity.

    The binding is read from ``auth.identities`` so the claim path proves control
    of the invited identity exactly as it does for a Discord sign-in.
    """
    from app.lifecycle.claims import invitation_token_digest

    invitation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "select provider, provider_id from auth.identities where user_id = %s limit 1",
            (bound_auth_user_id,),
        )
        identity = await cur.fetchone()
        if identity is None:
            raise RuntimeError(
                f"Supabase Auth user {bound_auth_user_id} has no provider identity"
            )
        await cur.execute(
            """
            insert into public.invitations (
                id, client_id, outreach_attempt_id, product_signal_id, client_user_id,
                token_digest, bound_provider, bound_provider_identity, expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))
            """,
            (
                invitation_id,
                case.client_id,
                outreach_attempt_id,
                case.product_signal_id,
                case.client_user_id,
                invitation_token_digest(token),
                str(identity["provider"]),
                str(identity["provider_id"]),
                expires_in_seconds,
            ),
        )
    return invitation_id
