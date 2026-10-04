"""The interview's half of the append-only evidence ledger.

Transcript fragments are provisional transport artifacts: timestamped, attributed
to a speaker, tied to the provider event and the connection that produced them.
A fragment is not a completed turn, and a fragment, a pause, or an isolated "yes"
establishes no consent, scope, or validation outcome.

An application evidence claim is a separate record with its own identity and
context revision that cites its source fragments. Corrections append superseding
claims; nothing overwrites a source fragment. An accepted correction invalidates
the dependent results that cited the superseded claim and places a hold on new
incompatible implementation steps. A plausible contradiction flag raises the same
hold without being a confirmed correction: conversation and independent work
continue while interpretation resolves it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    EvidenceClaimState,
    EvidenceClass,
)

from app.context.freshness import invalidate_bundles_citing
from app.context.sources import OPEN_QUESTION_BODY_KIND
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "CorrectionOutcome",
    "PlaybackSegment",
    "TranscriptFragment",
    "accept_correction",
    "implementation_hold_active",
    "raise_contradiction_flag",
    "record_claim",
    "record_open_question",
    "record_playback_segments",
    "record_transcript_fragment",
    "release_hold",
]

_log = get_logger(__name__)

_CORRECTION_REASON = "superseded_by_accepted_correction"
_FLAG_REASON = "contradiction_flag_raised"


@dataclass(frozen=True, slots=True)
class TranscriptFragment:
    """One timed fragment exactly as the provider reported it."""

    interview_session_id: UUID
    voice_connection_id: UUID
    voice_run_id: UUID
    provider_session_ref: str
    speaker: str
    provider_event_type: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class CorrectionOutcome:
    """What an accepted correction superseded and what it stopped."""

    evidence_id: UUID
    superseded_evidence_id: UUID
    invalidated_bundle_ids: tuple[UUID, ...]
    hold_id: UUID


async def record_transcript_fragment(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    fragment: TranscriptFragment,
    provenance: Mapping[str, JsonValue],
) -> UUID | None:
    """Persist one fragment, or return ``None`` when it was already recorded.

    Sideband delivery is at least once, so the provider event and its exact span
    are the natural idempotency boundary.
    """
    fragment_id = uuid7()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.voice_transcript_fragments (
                    id, client_id, interview_session_id, voice_connection_id,
                    voice_run_id, provider_session_ref, speaker, provider_event_type,
                    start_ms, end_ms, fragment_text, provenance
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning id
                """,
                (
                    fragment_id,
                    client_id,
                    fragment.interview_session_id,
                    fragment.voice_connection_id,
                    fragment.voice_run_id,
                    fragment.provider_session_ref,
                    fragment.speaker,
                    fragment.provider_event_type,
                    fragment.start_ms,
                    fragment.end_ms,
                    fragment.text,
                    Jsonb(provenance),
                ),
            )
            row = await cur.fetchone()
    except UniqueViolation:
        return None
    if row is None:
        return None
    return as_uuid(row, "id")


@dataclass(frozen=True, slots=True)
class PlaybackSegment:
    """One span of generated audio on the provider session's timeline."""

    start_ms: int
    end_ms: int
    audio_byte_length: int


async def record_playback_segments(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    interview_session_id: UUID,
    voice_connection_id: UUID,
    segments: Sequence[PlaybackSegment],
    delivery: str,
) -> None:
    """Persist generated-audio segments where the transport exposes them, in one batch.

    Generated speech, transmitted audio, and heard speech are different facts.
    ``delivery`` never claims more than the transport proved.
    """
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            insert into public.voice_playback_segments (
                id, client_id, interview_session_id, voice_connection_id,
                start_ms, end_ms, audio_byte_length, delivery
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            """,
            [
                (
                    uuid7(),
                    client_id,
                    interview_session_id,
                    voice_connection_id,
                    segment.start_ms,
                    segment.end_ms,
                    segment.audio_byte_length,
                    delivery,
                )
                for segment in segments
            ],
        )


async def record_claim(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    evidence_class: EvidenceClass,
    claim_state: EvidenceClaimState,
    source_ref: str,
    context_revision: int,
    body: Mapping[str, JsonValue],
    supersedes_id: UUID | None,
) -> UUID:
    """Append one evidence claim. Claims are never overwritten."""
    evidence_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, interview_session_id,
                evidence_class, claim_state, source_ref, context_revision, body,
                supersedes_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                evidence_id,
                client_id,
                product_signal_id,
                interview_session_id,
                evidence_class.value,
                claim_state.value,
                source_ref,
                context_revision,
                Jsonb(body),
                supersedes_id,
            ),
        )
    return evidence_id


async def record_open_question(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    question: str,
    criterion_id: UUID | None,
    source_ref: str,
    context_revision: int,
) -> UUID:
    """Record a question the interview raised and has not answered.

    Open questions live in the evidence ledger so context assembly has one home
    for interview facts and a later answer supersedes the question in place.
    """
    async with database.transaction() as conn:
        return await record_claim(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
            claim_state=EvidenceClaimState.PROPOSED,
            source_ref=source_ref,
            context_revision=context_revision,
            body={
                "kind": OPEN_QUESTION_BODY_KIND,
                "question": question,
                "criterion_id": str(criterion_id) if criterion_id is not None else None,
            },
            supersedes_id=None,
        )


async def raise_contradiction_flag(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    affected_evidence_id: UUID,
    reason: str,
    semantic_judgement_id: UUID | None,
    source_ref: str,
    context_revision: int,
) -> UUID:
    """Flag a plausible contradiction and hold new dependent implementation steps.

    A flag is not a confirmed correction. Conversation and independent read-only
    work continue; dispatched operations settle or checkpoint with their outcomes
    recorded, and a dismissed flag releases the hold.
    """
    async with database.transaction() as conn:
        flag_id = await record_claim(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
            claim_state=EvidenceClaimState.CONTRADICTED,
            source_ref=source_ref,
            context_revision=context_revision,
            body={
                "kind": "contradiction_flag",
                "reason": reason,
                "affected_evidence_id": str(affected_evidence_id),
            },
            supersedes_id=None,
        )
        hold_id = await _raise_hold(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            hold_kind="contradiction_flag",
            reason=reason,
            raised_by_evidence_id=flag_id,
            affected_evidence_id=affected_evidence_id,
            semantic_judgement_id=semantic_judgement_id,
        )
    _log.info(
        "contradiction_flag_raised",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        evidence_id=str(flag_id),
        affected_evidence_id=str(affected_evidence_id),
        dependency_hold_id=str(hold_id),
    )
    return flag_id


async def accept_correction(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    supersedes_id: UUID,
    corrected_statement: str,
    source_ref: str,
    context_revision: int,
) -> CorrectionOutcome:
    """Supersede a claim, invalidate what depended on it, and hold new steps.

    The superseded record stays in the ledger: corrections are linked records, not
    edits. Dependent analysis is invalidated immediately, and authorized
    implementation stops taking new incompatible steps until the gate is
    re-evaluated against the corrected scope.
    """
    async with database.transaction() as conn:
        evidence_id = await record_claim(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
            claim_state=EvidenceClaimState.CONFIRMED,
            source_ref=source_ref,
            context_revision=context_revision,
            body={"kind": "correction", "statement": corrected_statement},
            supersedes_id=supersedes_id,
        )
        invalidated = await invalidate_bundles_citing(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            evidence_ids=(supersedes_id,),
            reason=_CORRECTION_REASON,
            caused_by_evidence_id=evidence_id,
        )
        hold_id = await _raise_hold(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            hold_kind="accepted_correction",
            reason=_CORRECTION_REASON,
            raised_by_evidence_id=evidence_id,
            affected_evidence_id=supersedes_id,
            semantic_judgement_id=None,
        )
        await append_fact(
            conn,
            client_id=client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=StreamRef(
                    kind=AggregateKind.INTERVIEW_SESSION, id=interview_session_id
                ),
                event_type="evidence_correction_accepted",
                payload={
                    "evidence_id": str(evidence_id),
                    "supersedes_id": str(supersedes_id),
                    "invalidated_context_bundles": [
                        str(bundle_id) for bundle_id in invalidated
                    ],
                    "dependency_hold_id": str(hold_id),
                },
                actor=Actor(kind=ActorKind.CUSTOMER_PARTICIPANT, ref=None),
                causation_id=None,
                correlation_id=product_signal_id,
                occurred_at=datetime.now(tz=UTC),
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=StreamRef(
                    kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id
                ),
                action="accept_evidence_correction",
                outcome="superseded",
                reason_code=_CORRECTION_REASON,
                actor=Actor(kind=ActorKind.CUSTOMER_PARTICIPANT, ref=None),
                pinned_configuration_release_id=None,
                detail={"invalidated_context_bundles": len(invalidated)},
            ),
            jobs=(),
        )
    _log.info(
        "evidence_correction_accepted",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        evidence_id=str(evidence_id),
        supersedes_id=str(supersedes_id),
        invalidated_context_bundles=len(invalidated),
    )
    return CorrectionOutcome(
        evidence_id=evidence_id,
        superseded_evidence_id=supersedes_id,
        invalidated_bundle_ids=invalidated,
        hold_id=hold_id,
    )


async def release_hold(database: Database, *, hold_id: UUID, reason: str) -> None:
    """Release one hold after its dependency check resolved it."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.dependency_holds
            set released_at = now(), release_reason = %s
            where id = %s and released_at is null
            """,
            (reason, hold_id),
        )
    _log.info("dependency_hold_released", dependency_hold_id=str(hold_id), reason=reason)


async def implementation_hold_active(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> bool:
    """True while an unresolved hold blocks new live implementation steps."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select 1 from public.dependency_holds
            where product_signal_id = %s
              and released_at is null
              and blocks_new_implementation_steps
            limit 1
            """,
            (product_signal_id,),
        )
        return await cur.fetchone() is not None


async def _raise_hold(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    hold_kind: str,
    reason: str,
    raised_by_evidence_id: UUID,
    affected_evidence_id: UUID,
    semantic_judgement_id: UUID | None,
) -> UUID:
    """Write one open hold against new dependent implementation steps."""
    hold_id = uuid7()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.dependency_holds (
                id, client_id, product_signal_id, interview_session_id, hold_kind,
                reason, raised_by_evidence_id, affected_evidence_id,
                semantic_judgement_id, blocks_new_implementation_steps
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, true)
            """,
            (
                hold_id,
                client_id,
                product_signal_id,
                interview_session_id,
                hold_kind,
                reason,
                raised_by_evidence_id,
                affected_evidence_id,
                semantic_judgement_id,
            ),
        )
    return hold_id


async def fragment_source_refs(
    conn: AsyncConnection[DictRow],
    *,
    interview_session_id: UUID,
    fragment_ids: Sequence[UUID],
) -> str:
    """Return the ``source_ref`` a claim uses to cite a span of fragments."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select min(start_ms) as first_ms, max(end_ms) as last_ms
            from public.voice_transcript_fragments
            where interview_session_id = %s and id = any(%s)
            """,
            (interview_session_id, list(fragment_ids)),
        )
        row = await cur.fetchone()
    if row is None or row["first_ms"] is None:
        raise LookupError("no transcript fragments matched the cited span")
    return f"transcript:{interview_session_id}:{row['first_ms']}-{row['last_ms']}"
