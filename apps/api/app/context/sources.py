"""Collecting the durable facts a context bundle is assembled from.

Every function here reads committed Postgres rows and returns source-linked
items. Nothing is summarised by a model and nothing is invented: a summary is
derived and cannot replace a user's correction or a canonical record.

Each item carries the source's own identity and revision so a Case File citation
resolves back to the record, and so a later correction can find and invalidate
the bundles that cited it.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import EvidenceClaimState

from app.clients.overview import ActiveProductOverview
from app.db.rows import (
    as_bool,
    as_int,
    as_optional_str,
    as_str,
    as_uuid,
)
from app.envelope import JsonValue
from app.interview.observations import ObservedActivity

__all__ = [
    "ContextItem",
    "ContextSection",
    "acceptance_criteria_items",
    "agreed_scope_items",
    "contradiction_items",
    "implementation_status_items",
    "lifecycle_and_policy_items",
    "observation_items",
    "open_question_items",
    "prior_command_items",
    "product_overview_items",
    "recent_conversation_items",
]

# The enum values are fixed by public.context_section in the migration. They are
# spelled here rather than imported because the shared contract package does not
# own this projection.
type ContextSection = str

SECTION_RECENT_CONVERSATION: ContextSection = "recent_conversation"
SECTION_AGREED_SCOPE: ContextSection = "agreed_scope"
SECTION_ACCEPTANCE_CRITERIA: ContextSection = "acceptance_criteria"
SECTION_OPEN_QUESTIONS: ContextSection = "open_questions"
SECTION_CONTRADICTIONS: ContextSection = "contradictions"
SECTION_SELECTED_OBSERVATIONS: ContextSection = "selected_observations"
SECTION_IMPLEMENTATION_STATUS: ContextSection = "implementation_status"
SECTION_LIFECYCLE_AND_POLICY: ContextSection = "lifecycle_and_policy"
SECTION_PRIOR_COMMANDS_AND_RESULTS: ContextSection = "prior_commands_and_results"
SECTION_PRODUCT_KNOWLEDGE: ContextSection = "product_knowledge"

# An open question is an evidence record whose body declares this kind, so the
# append-only evidence ledger stays the single home for interview facts.
OPEN_QUESTION_BODY_KIND = "open_question"


class ContextItem(BaseModel):
    """One source-linked fact offered to a bundle, before the budget is applied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: ContextSection
    source_kind: str
    source_id: str
    source_revision: str
    weight: int
    body: Mapping[str, JsonValue]


async def recent_conversation_items(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID, limit: int
) -> tuple[ContextItem, ...]:
    """Return what was said in the last `limit` transcript fragments, as turns, oldest first.

    A turn can arrive as several fragments, so consecutive fragments of one
    speaker are joined into the turn they belong to: one item per turn carries the
    exchange in a fraction of the items and tokens, so the window can reach back
    over the conversation rather than its last few seconds. A turn cites its first
    fragment and spans the time range of all of them. Each connection's fragments
    are timed on its own clock, so a session's calls come in the order they opened.

    Fragments are provisional transport artifacts. They travel as what the
    speaker said, never as a completed turn or an established agreement.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select fragments.id, fragments.speaker, fragments.start_ms, fragments.end_ms,
                   fragments.fragment_text
            from public.voice_transcript_fragments as fragments
            join public.voice_connections as connections
              on connections.id = fragments.voice_connection_id
            where fragments.interview_session_id = %s
            order by connections.created_at desc, fragments.start_ms desc,
                     fragments.recorded_at desc
            limit %s
            """,
            (interview_session_id, limit),
        )
        rows = list(await cur.fetchall())
    rows.reverse()
    turns: list[list[DictRow]] = []
    for row in rows:
        if turns and as_str(turns[-1][0], "speaker") == as_str(row, "speaker"):
            turns[-1].append(row)
        else:
            turns.append([row])
    return tuple(_turn_item(fragments) for fragments in turns)


def _turn_item(fragments: Sequence[DictRow]) -> ContextItem:
    """One speaker's consecutive fragments as a single conversation item."""
    start_ms = as_int(fragments[0], "start_ms")
    end_ms = max(as_int(fragment, "end_ms") for fragment in fragments)
    return ContextItem(
        section=SECTION_RECENT_CONVERSATION,
        source_kind="voice_transcript_turn",
        source_id=str(as_uuid(fragments[0], "id")),
        source_revision=f"{start_ms}-{end_ms}",
        weight=1,
        body={
            "speaker": as_str(fragments[0], "speaker"),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "fragments": len(fragments),
            "text": "".join(as_str(fragment, "fragment_text") for fragment in fragments),
        },
    )


async def agreed_scope_items(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> tuple[ContextItem, ...]:
    """Return the agreed scope from the current acceptance-criteria agreement."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, acceptance_criteria_revision, agreed_scope
            from public.acceptance_criteria_agreements
            where product_signal_id = %s
            order by acceptance_criteria_revision desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return ()
    scope = row["agreed_scope"]
    if not isinstance(scope, dict):
        raise TypeError("acceptance_criteria_agreements.agreed_scope is not an object")
    return (
        ContextItem(
            section=SECTION_AGREED_SCOPE,
            source_kind="acceptance_criteria_agreement",
            source_id=str(as_uuid(row, "id")),
            source_revision=str(as_int(row, "acceptance_criteria_revision")),
            weight=1,
            body=dict(scope),
        ),
    )


async def acceptance_criteria_items(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID, criteria_revision: int
) -> tuple[ContextItem, ...]:
    """Return the frozen acceptance criteria at one criteria revision."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, ordinal, statement, observability
            from public.validation_criteria
            where product_signal_id = %s and criteria_revision = %s
            order by ordinal
            """,
            (product_signal_id, criteria_revision),
        )
        rows = list(await cur.fetchall())
    return tuple(
        ContextItem(
            section=SECTION_ACCEPTANCE_CRITERIA,
            source_kind="validation_criterion",
            source_id=str(as_uuid(row, "id")),
            source_revision=str(criteria_revision),
            weight=1,
            body={
                "ordinal": as_int(row, "ordinal"),
                "statement": as_str(row, "statement"),
                "observability": as_str(row, "observability"),
            },
        )
        for row in rows
    )


async def open_question_items(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> tuple[ContextItem, ...]:
    """Return the questions the interview has raised and not yet resolved."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, context_revision, body, source_ref
            from public.evidence_records
            where product_signal_id = %s
              and claim_state = %s
              and body ->> 'kind' = %s
              and not exists (
                select 1 from public.evidence_records superseding
                where superseding.supersedes_id = evidence_records.id
              )
            order by recorded_at
            """,
            (product_signal_id, EvidenceClaimState.PROPOSED.value, OPEN_QUESTION_BODY_KIND),
        )
        rows = list(await cur.fetchall())
    return tuple(_evidence_item(row, SECTION_OPEN_QUESTIONS) for row in rows)


async def contradiction_items(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> tuple[ContextItem, ...]:
    """Return contradicted claims and the holds an unresolved flag raised."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, context_revision, body, source_ref
            from public.evidence_records
            where product_signal_id = %s and claim_state = %s
            order by recorded_at
            """,
            (product_signal_id, EvidenceClaimState.CONTRADICTED.value),
        )
        claims = [_evidence_item(row, SECTION_CONTRADICTIONS) for row in await cur.fetchall()]
        await cur.execute(
            """
            select id, hold_kind, reason, raised_by_evidence_id, affected_evidence_id,
                   blocks_new_implementation_steps
            from public.dependency_holds
            where product_signal_id = %s and released_at is null
            order by created_at
            """,
            (product_signal_id,),
        )
        holds = [
            ContextItem(
                section=SECTION_CONTRADICTIONS,
                source_kind="dependency_hold",
                source_id=str(as_uuid(row, "id")),
                source_revision=as_str(row, "hold_kind"),
                weight=1,
                body={
                    "hold_kind": as_str(row, "hold_kind"),
                    "reason": as_str(row, "reason"),
                    "raised_by_evidence_id": str(as_uuid(row, "raised_by_evidence_id")),
                    "affected_evidence_id": (
                        str(row["affected_evidence_id"])
                        if row["affected_evidence_id"] is not None
                        else None
                    ),
                    "blocks_new_implementation_steps": as_bool(
                        row, "blocks_new_implementation_steps"
                    ),
                },
            )
            for row in await cur.fetchall()
        ]
    return tuple(claims + holds)


def observation_items(
    observations: Sequence[ObservedActivity],
) -> tuple[ContextItem, ...]:
    """Render bounded observation summaries as source-linked items.

    The summaries come from the telemetry query surface with an explicit field
    projection, so nothing here adds raw product content to the bundle.
    """
    return tuple(
        ContextItem(
            section=SECTION_SELECTED_OBSERVATIONS,
            source_kind="observation",
            source_id=observation.observation_id,
            source_revision=observation.occurred_at,
            weight=1,
            body={
                "observation_type": observation.observation_type,
                "occurred_at": observation.occurred_at,
                "target_id": observation.target_id,
                "control": observation.control,
                "milestone": observation.milestone,
                "milestone_attributes": dict(observation.milestone_attributes),
            },
        )
        for observation in observations
    )


async def implementation_status_items(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    interview_session_id: UUID,
) -> tuple[ContextItem, ...]:
    """Return the current attempt state and the user-facing progress projection."""
    items: list[ContextItem] = []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, state, revision, outcome_reason
            from public.implementation_attempts
            where product_signal_id = %s
            order by created_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        attempt = await cur.fetchone()
        if attempt is not None:
            items.append(
                ContextItem(
                    section=SECTION_IMPLEMENTATION_STATUS,
                    source_kind="implementation_attempt",
                    source_id=str(as_uuid(attempt, "id")),
                    source_revision=str(as_int(attempt, "revision")),
                    weight=1,
                    body={
                        "state": as_str(attempt, "state"),
                        "outcome_reason": as_optional_str(attempt, "outcome_reason"),
                    },
                )
            )
        await cur.execute(
            """
            select id, revision, implementation_progress, phase
            from public.interview_sessions
            where id = %s
            """,
            (interview_session_id,),
        )
        session = await cur.fetchone()
    if session is not None:
        items.append(
            ContextItem(
                section=SECTION_IMPLEMENTATION_STATUS,
                source_kind="interview_session_progress",
                source_id=str(as_uuid(session, "id")),
                source_revision=str(as_int(session, "revision")),
                weight=1,
                body={
                    "implementation_progress": as_str(session, "implementation_progress"),
                    "phase": as_str(session, "phase"),
                },
            )
        )
    return tuple(items)


async def lifecycle_and_policy_items(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    interview_session_id: UUID,
) -> tuple[ContextItem, ...]:
    """Return the lifecycle and pinned-policy revisions every result must cite."""
    items: list[ContextItem] = []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select signals.id, signals.state, signals.revision,
                   signals.initial_flow_type, signals.established_flow_type,
                   signals.classification_revision, signals.concern_revision,
                   signals.acceptance_criteria_revision, signals.return_deadline_at,
                   releases.id as release_id, releases.release_number,
                   releases.digest, releases.policy
            from public.product_signals as signals
            join public.configuration_releases as releases
              on releases.id = signals.pinned_configuration_release_id
            where signals.id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"product signal {product_signal_id} does not exist")
        deadline = row["return_deadline_at"]
        policy = row["policy"]
        if not isinstance(policy, dict):
            raise TypeError("configuration_releases.policy is not an object")
        items.append(
            ContextItem(
                section=SECTION_LIFECYCLE_AND_POLICY,
                source_kind="product_signal",
                source_id=str(as_uuid(row, "id")),
                source_revision=str(as_int(row, "revision")),
                weight=1,
                body={
                    "state": as_str(row, "state"),
                    "initial_flow_type": as_str(row, "initial_flow_type"),
                    "established_flow_type": as_optional_str(row, "established_flow_type"),
                    "classification_revision": as_int(row, "classification_revision"),
                    "concern_revision": as_int(row, "concern_revision"),
                    "acceptance_criteria_revision": as_int(
                        row, "acceptance_criteria_revision"
                    ),
                    "return_deadline_at": (
                        deadline.isoformat() if isinstance(deadline, datetime) else None
                    ),
                },
            )
        )
        items.append(
            ContextItem(
                section=SECTION_LIFECYCLE_AND_POLICY,
                source_kind="configuration_release",
                source_id=str(as_uuid(row, "release_id")),
                source_revision=as_str(row, "digest"),
                weight=1,
                body={"release_number": as_int(row, "release_number"), "policy": dict(policy)},
            )
        )
        await cur.execute(
            """
            select readiness_revision, conversational_ready, observation_ready, blockers
            from public.session_readiness
            where interview_session_id = %s
            """,
            (interview_session_id,),
        )
        readiness = await cur.fetchone()
    if readiness is not None:
        blockers = readiness["blockers"]
        if not isinstance(blockers, list):
            raise TypeError("session_readiness.blockers is not an array")
        items.append(
            ContextItem(
                section=SECTION_LIFECYCLE_AND_POLICY,
                source_kind="session_readiness",
                source_id=str(interview_session_id),
                source_revision=str(as_int(readiness, "readiness_revision")),
                weight=1,
                body={
                    "conversational": as_bool(readiness, "conversational_ready"),
                    "observation": as_bool(readiness, "observation_ready"),
                    "blockers": [str(blocker) for blocker in blockers],
                },
            )
        )
    return tuple(items)


async def prior_command_items(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID, limit: int
) -> tuple[ContextItem, ...]:
    """Return prior Commands against this session with their terminal Results."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select commands.id, commands.command_type, commands.state,
                   commands.revision, results.disposition, results.effect_state,
                   results.technical_category
            from public.commands
            left join public.results
              on results.command_id = commands.id and results.kind = 'terminal'
            where commands.target_kind = 'interview_session'
              and commands.target_id = %s
            order by commands.created_at desc
            limit %s
            """,
            (interview_session_id, limit),
        )
        rows = list(await cur.fetchall())
    rows.reverse()
    return tuple(
        ContextItem(
            section=SECTION_PRIOR_COMMANDS_AND_RESULTS,
            source_kind="command",
            source_id=str(as_uuid(row, "id")),
            source_revision=str(as_int(row, "revision")),
            weight=1,
            body={
                "command_type": as_str(row, "command_type"),
                "state": as_str(row, "state"),
                "disposition": as_optional_str(row, "disposition"),
                "effect_state": as_optional_str(row, "effect_state"),
                "technical_category": as_optional_str(row, "technical_category"),
            },
        )
        for row in rows
    )


def _evidence_item(row: DictRow, section: ContextSection) -> ContextItem:
    """Render one evidence record as a source-linked item."""
    body = row["body"]
    if not isinstance(body, dict):
        raise TypeError("evidence_records.body is not an object")
    return ContextItem(
        section=section,
        source_kind="evidence_record",
        source_id=str(as_uuid(row, "id")),
        source_revision=str(as_int(row, "context_revision")),
        weight=1,
        body={"source_ref": as_str(row, "source_ref"), **dict(body)},
    )


def product_overview_items(overview: ActiveProductOverview) -> tuple[ContextItem, ...]:
    """Render the client's active Product Overview, one section per item.

    The whole overview is small enough to travel with every bundle that needs
    product knowledge, so nothing has to be looked up while a participant waits.
    Each section cites the overview version and its own content digest.
    """
    return tuple(
        ContextItem(
            section=SECTION_PRODUCT_KNOWLEDGE,
            source_kind="product_overview_section",
            source_id=f"{overview.id}/{section.key}",
            source_revision=section.content_digest(),
            weight=1,
            body={
                "title": section.title,
                "text": section.body,
                "inferred": section.inferred,
            },
        )
        for section in overview.overview.sections()
    )