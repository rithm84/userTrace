"""The user-facing implementation progress projection.

During live implementation the originating user stays on the session tab and
sees four concise states: reproduction captured, making changes, preparing the
preview, ready for validation. Async work exposes the same durable status
between sessions.

The projection is a durable column on the Interview Session, written through the
kernel in one transaction with its sequenced Event and audit record. A model
claim never moves it; only a committed step of the attempt does. An attempt with
no live session records the fact and changes nothing, because async progress is
read from the attempt itself.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, ImplementationProgress

from app.db.kernel import append_fact
from app.db.pool import Database
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.ids import uuid7

__all__ = ["record_progress"]

_logger = structlog.get_logger(__name__)

_ATTEMPT_ACTOR = Actor(kind=ActorKind.SERVICE, ref="implementation.controller")


async def record_progress(
    database: Database,
    *,
    client_id: UUID,
    implementation_attempt_id: UUID,
    interview_session_id: UUID | None,
    progress: ImplementationProgress,
    occurred_at: datetime,
) -> None:
    """Advance the durable progress projection for one attempt and its session."""
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update public.implementation_attempts
                set progress = %s, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (progress.value, implementation_attempt_id),
            )
            if interview_session_id is not None:
                await cur.execute(
                    """
                    update public.interview_sessions
                    set implementation_progress = %s, revision = revision + 1,
                        updated_at = now()
                    where id = %s
                    """,
                    (progress.value, interview_session_id),
                )
        stream = StreamRef(
            kind=AggregateKind.IMPLEMENTATION_ATTEMPT, id=implementation_attempt_id
        )
        await append_fact(
            conn,
            client_id=client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type="implementation_progress_recorded",
                payload={
                    "progress": progress.value,
                    "interview_session_id": (
                        str(interview_session_id) if interview_session_id is not None else None
                    ),
                },
                actor=_ATTEMPT_ACTOR,
                causation_id=None,
                correlation_id=implementation_attempt_id,
                occurred_at=occurred_at,
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action="record_implementation_progress",
                outcome=progress.value,
                reason_code=None,
                actor=_ATTEMPT_ACTOR,
                pinned_configuration_release_id=None,
                detail={},
            ),
            jobs=(),
        )
    _logger.info(
        "implementation.progress",
        attempt_id=str(implementation_attempt_id),
        interview_session_id=(
            str(interview_session_id) if interview_session_id is not None else None
        ),
        progress=progress.value,
    )
