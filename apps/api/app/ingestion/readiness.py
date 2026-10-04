"""Readiness projections and the typed facts the control plane receives.

Ingestion owns source validation, stream continuity, trace completeness, and
correlation readiness evidence. The control plane remains the authority that
combines these facts with voice, preview, extension, SDK, environment, policy,
and session readiness.

Raw telemetry never enters the domain-message system. Only derived readiness,
gap, correlation, evidence-availability, and failure facts reach the control
plane, and they go through the kernel's ``append_fact`` so each one gets its
sequenced Event and audit record.

A projection's revision is monotonic by construction: every revision is a new
append-only row, so a replaced stream or resource invalidates the previous
projection rather than overwriting it.

Contract: docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "READINESS_LIFETIME_SECONDS",
    "ReadinessFact",
    "ReadinessProjection",
    "current_readiness",
    "publish_readiness",
    "withdraw_readiness",
]

_log = get_logger(__name__)

#: A readiness projection expires so a stale one can never authorize probing.
READINESS_LIFETIME_SECONDS = 60

_INGESTION_ACTOR = Actor(kind=ActorKind.SERVICE, ref="telemetry.ingestion")

_EVENT_TYPES: Mapping[str, str] = {
    "browser": "telemetry_browser_readiness_reported",
    "server": "telemetry_server_readiness_reported",
    "correlation": "telemetry_correlation_readiness_reported",
}


@dataclass(frozen=True, slots=True)
class ReadinessFact:
    """One readiness evaluation, before it is committed."""

    kind: str
    ready: bool
    contributing_resource_ids: Sequence[str]
    accepted_watermarks: Mapping[str, JsonValue]
    probe_id: UUID | None
    failures: Sequence[Mapping[str, JsonValue]]


@dataclass(frozen=True, slots=True)
class ReadinessProjection:
    """A committed readiness projection and its monotonic revision."""

    readiness_id: UUID
    kind: str
    revision: int
    ready: bool
    checked_at: datetime
    expires_at: datetime


async def publish_readiness(
    database: Database,
    *,
    client_id: UUID,
    activation_id: UUID,
    interview_session_id: UUID,
    fact: ReadinessFact,
) -> ReadinessProjection:
    """Commit one readiness projection and append its control-plane fact.

    The projection row and the Event are written in the same transaction, so the
    control plane never learns of readiness that ingestion did not record.
    """
    if fact.kind not in _EVENT_TYPES:
        raise ValueError(f"'{fact.kind}' is not a telemetry readiness kind")
    checked_at = datetime.now(tz=UTC)
    expires_at = checked_at + timedelta(seconds=READINESS_LIFETIME_SECONDS)
    readiness_id = uuid7()

    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.telemetry_readiness_projections (
                    id, client_id, activation_id, interview_session_id, kind, revision,
                    ready, contributing_resource_ids, accepted_watermarks, probe_id,
                    failures, checked_at, expires_at
                )
                select %s, %s, %s, %s, %s,
                       coalesce(max(existing.revision), 0) + 1,
                       %s, %s, %s, %s, %s, %s, %s
                from public.telemetry_readiness_projections as existing
                where existing.client_id = %s
                  and existing.activation_id = %s
                  and existing.kind = %s
                returning revision
                """,
                (
                    readiness_id,
                    client_id,
                    activation_id,
                    interview_session_id,
                    fact.kind,
                    fact.ready,
                    Jsonb(list(fact.contributing_resource_ids)),
                    Jsonb(dict(fact.accepted_watermarks)),
                    fact.probe_id,
                    Jsonb([dict(failure) for failure in fact.failures]),
                    checked_at,
                    expires_at,
                    client_id,
                    activation_id,
                    fact.kind,
                ),
            )
            row = await cur.fetchone()
        if row is None:
            raise ValueError("the readiness projection insert returned no revision")
        revision = as_int(row, "revision")
        await append_fact(
            conn,
            client_id=client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=StreamRef(
                    kind=AggregateKind.INTERVIEW_SESSION, id=interview_session_id
                ),
                event_type=_EVENT_TYPES[fact.kind],
                payload={
                    "telemetry_readiness_id": str(readiness_id),
                    "activation_id": str(activation_id),
                    "kind": fact.kind,
                    "revision": revision,
                    "ready": fact.ready,
                    "contributing_resource_ids": list(fact.contributing_resource_ids),
                    "accepted_watermarks": dict(fact.accepted_watermarks),
                    "probe_id": str(fact.probe_id) if fact.probe_id is not None else None,
                    "failures": [dict(failure) for failure in fact.failures],
                    "expires_at": expires_at.isoformat(),
                },
                actor=_INGESTION_ACTOR,
                causation_id=None,
                correlation_id=activation_id,
                occurred_at=checked_at,
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=StreamRef(
                    kind=AggregateKind.INTERVIEW_SESSION, id=interview_session_id
                ),
                action=f"report_{fact.kind}_readiness",
                outcome="ready" if fact.ready else "withdrawn",
                reason_code=None if fact.ready else "telemetry_readiness_withdrawn",
                actor=_INGESTION_ACTOR,
                pinned_configuration_release_id=None,
                detail={"activation_id": str(activation_id), "revision": revision},
            ),
            jobs=(),
        )
    _log.info(
        "telemetry_readiness_published",
        client_id=str(client_id),
        activation_id=str(activation_id),
        kind=fact.kind,
        revision=revision,
        ready=fact.ready,
    )
    return ReadinessProjection(
        readiness_id=readiness_id,
        kind=fact.kind,
        revision=revision,
        ready=fact.ready,
        checked_at=checked_at,
        expires_at=expires_at,
    )


async def withdraw_readiness(
    database: Database,
    *,
    client_id: UUID,
    activation_id: UUID,
    interview_session_id: UUID,
    kind: str,
    failure_code: str,
    detail: Mapping[str, JsonValue],
) -> ReadinessProjection:
    """Remove readiness immediately and say exactly which failure removed it."""
    return await publish_readiness(
        database,
        client_id=client_id,
        activation_id=activation_id,
        interview_session_id=interview_session_id,
        fact=ReadinessFact(
            kind=kind,
            ready=False,
            contributing_resource_ids=(),
            accepted_watermarks={},
            probe_id=None,
            failures=({"code": failure_code, "detail": dict(detail)},),
        ),
    )


async def current_readiness(
    database: Database, *, activation_id: UUID, kind: str
) -> ReadinessProjection | None:
    """Return the highest-revision projection for one activation and kind."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, revision, ready, checked_at, expires_at
            from public.telemetry_readiness_projections
            where activation_id = %s and kind = %s
            order by revision desc
            limit 1
            """,
            (activation_id, kind),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    checked_at = row["checked_at"]
    expires_at = row["expires_at"]
    if not isinstance(checked_at, datetime) or not isinstance(expires_at, datetime):
        raise TypeError("a readiness projection has non-timestamp times")
    return ReadinessProjection(
        readiness_id=as_uuid(row, "id"),
        kind=kind,
        revision=as_int(row, "revision"),
        ready=as_bool(row, "ready"),
        checked_at=checked_at,
        expires_at=expires_at,
    )
