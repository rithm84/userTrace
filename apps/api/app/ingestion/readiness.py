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

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind

from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.ids import uuid7
from app.logging import get_logger

__all__ = [
    "READINESS_KINDS",
    "READINESS_LIFETIME_SECONDS",
    "KindReadiness",
    "OpenPageReadiness",
    "ReadinessFact",
    "ReadinessProjection",
    "current_readiness",
    "open_page_readiness",
    "publish_readiness",
    "withdraw_readiness",
]

_log = get_logger(__name__)

#: A readiness projection expires so a stale one can never authorize probing.
READINESS_LIFETIME_SECONDS = 60

#: The kinds each readiness evaluation records, in report order.
READINESS_KINDS: tuple[str, ...] = ("browser", "server", "correlation")

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


@dataclass(frozen=True, slots=True)
class KindReadiness:
    """One kind's newest projection, judged for the page the participant has open."""

    kind: str
    ready: bool
    #: Why it is not ready; empty when it is.
    reason: str


@dataclass(frozen=True, slots=True)
class OpenPageReadiness:
    """Observation readiness for the page the participant has open.

    Each kind's newest projection counts only while it is unexpired and was
    evaluated for that page: the page's own browser stream, the server streams of
    the preview it is on (the session's own preview before a page opens), and a
    trace correlated under it. Every evaluation reads the newest page, so between a
    new page opening and its first evaluation the newest projections still describe
    the page before, and they are not readiness.
    """

    #: The preview the open page is on; None before the participant opens one.
    preview_runtime_allocation_id: UUID | None
    kinds: tuple[KindReadiness, ...]

    @property
    def ready(self) -> bool:
        """Whether every kind is ready for the open page."""
        return all(kind.ready for kind in self.kinds)


async def open_page_readiness(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    interview_session_id: UUID,
    observed_at: datetime,
) -> OpenPageReadiness:
    """Judge each kind's newest projection for the page the participant has open."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select preview_runtime_allocation_id from public.telemetry_activations
            where interview_session_id = %s and kind = 'browser'
            order by created_at desc
            limit 1
            """,
            (interview_session_id,),
        )
        page = await cur.fetchone()
        await cur.execute(
            """
            with page as (
                select id, preview_runtime_allocation_id
                from public.telemetry_activations
                where interview_session_id = %(session)s and kind = 'browser'
                order by created_at desc
                limit 1
            ),
            preview as (
                select coalesce(
                    (select preview_runtime_allocation_id from page),
                    sessions.preview_runtime_allocation_id
                ) as allocation_id
                from public.interview_sessions as sessions
                where sessions.id = %(session)s
            )
            select distinct on (projections.kind)
                   projections.kind, projections.ready, projections.expires_at,
                   projections.failures,
                   case projections.kind
                     when 'browser' then exists (
                       select 1 from public.browser_streams as streams, page
                       where streams.activation_id = page.id
                         and projections.contributing_resource_ids ? streams.id::text
                     )
                     when 'server' then exists (
                       select 1
                       from public.server_streams as streams
                       join public.telemetry_activations as activations
                         on activations.id = streams.activation_id,
                       preview
                       where activations.interview_session_id = %(session)s
                         and activations.preview_runtime_allocation_id = preview.allocation_id
                         and projections.contributing_resource_ids ? streams.id::text
                     )
                     else exists (
                       select 1 from public.correlation_projections as correlations, page
                       where correlations.activation_id = page.id
                         and projections.contributing_resource_ids ? correlations.id::text
                     )
                   end as covers_page
            from public.telemetry_readiness_projections as projections
            where projections.client_id = %(client)s
              and projections.interview_session_id = %(session)s
            order by projections.kind, projections.checked_at desc, projections.revision desc
            """,
            {"client": client_id, "session": interview_session_id},
        )
        newest = {str(row["kind"]): row for row in await cur.fetchall()}
    kinds: list[KindReadiness] = []
    for kind in READINESS_KINDS:
        row = newest.get(kind)
        if row is None:
            kinds.append(KindReadiness(kind=kind, ready=False, reason="no readiness projection"))
            continue
        expires_at = row["expires_at"]
        if not isinstance(expires_at, datetime):
            raise TypeError("telemetry_readiness_projections.expires_at is not a timestamp")
        if expires_at <= observed_at:
            reason = f"the projection expired at {expires_at.isoformat()}"
        elif not as_bool(row, "ready"):
            reason = _failure_codes(row["failures"])
        elif not as_bool(row, "covers_page"):
            reason = "evaluated for a page the participant has since left"
        else:
            reason = ""
        kinds.append(KindReadiness(kind=kind, ready=reason == "", reason=reason))
    return OpenPageReadiness(
        preview_runtime_allocation_id=(
            None if page is None else as_uuid(page, "preview_runtime_allocation_id")
        ),
        kinds=tuple(kinds),
    )


def _failure_codes(failures: object) -> str:
    """Render one projection's typed failure codes, or say it recorded none."""
    if not isinstance(failures, list) or len(failures) == 0:
        return "not ready, with no failure recorded"
    return ", ".join(
        str(failure["code"]) for failure in failures if isinstance(failure, Mapping)
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
