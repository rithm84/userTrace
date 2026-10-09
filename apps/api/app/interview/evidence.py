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

from collections.abc import Mapping
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
from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "CorrectionOutcome",
    "TranscriptFragment",
    "accept_correction",
    "implementation_hold_active",
    "record_claim",
    "record_transcript_fragment",
]

_log = get_logger(__name__)

_CORRECTION_REASON = "superseded_by_accepted_correction"


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

    A rejoined call can record a turn again, so the event and its exact span are
    the natural idempotency boundary.
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


