"""The two readiness boundaries.

Conversational readiness admits greeting and problem discussion: an
authenticated participant, working voice transport and controls, the voice agent
in the call (recorded as `sideband_attached`), and a current backend session
context.

Observation readiness additionally requires the exact preview allocation, the
extension binding, and the three observation kinds telemetry ingestion projects:
the embedded browser SDK's path, the Server SDK's span path, and the
browser-to-server correlation that joins them. Product-activity claims,
observation-dependent probing, visual commands, and validation require that
complete path. Connected sockets alone establish neither, so every component is
recorded separately and a withdrawal names exactly what is missing.

The three observation kinds are read through `app.ports.PortRegistry.observation`,
which projects the ingestion stream's own readiness rows. An expired projection
is not readiness, and that rule lives with the adapter rather than here.

`public.session_readiness` is the one place readiness lives. It carries the
component facts, the typed blockers that explain a refusal, and one monotonic
revision that only advances when a value actually changes, so an unchanged
heartbeat cannot make a running worker's context bundle stale. Every reader —
the session projection, the staff case summary, the context bundle, validation,
and participation — reads it there.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, InterviewSessionState

from app.db.kernel import apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_bool, as_int, as_str
from app.envelope import Actor, AuditIntent, EventAppend, StreamRef
from app.errors import AggregateNotFoundError
from app.ids import uuid7
from app.lifecycle.interview import INTERVIEW_SESSION_MACHINE, InterviewSessionFacts
from app.lifecycle.machine import plan_transition
from app.logging import get_logger
from app.preview.ports import ObservationReadinessPort

__all__ = [
    "ExtensionReadiness",
    "ReadinessRecord",
    "load_readiness",
    "record_conversational_facts",
    "record_observation_facts",
    "refresh_observation_readiness",
    "refresh_telemetry_readiness",
    "withdraw_observation_readiness",
]

_log = get_logger(__name__)
_READINESS_ACTOR = Actor(kind=ActorKind.SERVICE, ref="interview.readiness")

_BLOCKER_BY_COLUMN: Mapping[str, str] = {
    "participant_authenticated": "participant_not_authenticated",
    "voice_transport_ready": "voice_transport_unavailable",
    "sideband_attached": "sideband_not_attached",
    "backend_context_current": "backend_context_stale",
    "preview_allocation_ready": "preview_allocation_not_ready",
    "browser_sdk_ready": "browser_sdk_unavailable",
    "server_sdk_ready": "server_sdk_unavailable",
    "trace_correlation_ready": "trace_correlation_incomplete",
}

_CONVERSATIONAL_COLUMNS: tuple[str, ...] = (
    "participant_authenticated",
    "voice_transport_ready",
    "sideband_attached",
    "backend_context_current",
)

_OBSERVATION_COLUMNS: tuple[str, ...] = (
    "preview_allocation_ready",
    "browser_sdk_ready",
    "server_sdk_ready",
    "trace_correlation_ready",
)

_EXTENSION_BLOCKER = "extension_readiness_withdrawn"
_ALL_COLUMNS: tuple[str, ...] = (*_CONVERSATIONAL_COLUMNS, *_OBSERVATION_COLUMNS)


class ExtensionReadiness(StrEnum):
    """The extension half of the combined record, owned by the control plane."""

    READY = "ready"
    RECOVERING = "recovering"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class ReadinessRecord:
    """One session's readiness at one revision."""

    interview_session_id: UUID
    client_id: UUID
    readiness_revision: int
    components: Mapping[str, bool]
    extension_readiness: ExtensionReadiness
    conversational: bool
    observation: bool
    blockers: tuple[str, ...]


async def load_readiness(
    database: Database, *, interview_session_id: UUID
) -> ReadinessRecord:
    """Return the current readiness record, creating it on first read.

    A read takes no lock. The extension's projection, a tool call, and every other
    reader see the last committed record at once instead of queueing behind a
    writer that holds the row, so a slow writer can never hold back the agent's
    cursor or character.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(_READINESS_QUERY, (interview_session_id,))
        row = await cur.fetchone()
    if row is not None:
        return _to_record(interview_session_id, row)
    async with database.transaction() as conn:
        return await _ensure(conn, interview_session_id=interview_session_id)


async def record_conversational_facts(
    database: Database,
    *,
    interview_session_id: UUID,
    participant_authenticated: bool,
    voice_transport_ready: bool,
    sideband_attached: bool,
    backend_context_current: bool,
) -> ReadinessRecord:
    """Record the conversational half. Every fact is supplied explicitly."""
    return await _update(
        database,
        interview_session_id=interview_session_id,
        components={
            "participant_authenticated": participant_authenticated,
            "voice_transport_ready": voice_transport_ready,
            "sideband_attached": sideband_attached,
            "backend_context_current": backend_context_current,
        },
        extension_readiness=None,
    )


async def record_observation_facts(
    database: Database,
    *,
    interview_session_id: UUID,
    preview_allocation_ready: bool,
    extension_readiness: ExtensionReadiness,
    browser_sdk_ready: bool,
    server_sdk_ready: bool,
    trace_correlation_ready: bool,
) -> ReadinessRecord:
    """Record the observation half from already-observed component facts."""
    return await _update(
        database,
        interview_session_id=interview_session_id,
        components={
            "preview_allocation_ready": preview_allocation_ready,
            "browser_sdk_ready": browser_sdk_ready,
            "server_sdk_ready": server_sdk_ready,
            "trace_correlation_ready": trace_correlation_ready,
        },
        extension_readiness=extension_readiness,
    )


async def refresh_observation_readiness(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    observation: ObservationReadinessPort,
) -> ReadinessRecord:
    """Recombine extension binding, preview allocation, and ingestion facts.

    Resume and reconnect both call this: the contract requires fresh readiness
    rather than the readiness a previous connection left behind.
    """
    components = await _observed_components(
        database,
        client_id=client_id,
        interview_session_id=interview_session_id,
        observation=observation,
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.extension_bindings
            where interview_session_id = %s and status = 'bound'
            """,
            (interview_session_id,),
        )
        binding = await cur.fetchone()
    return await _update(
        database,
        interview_session_id=interview_session_id,
        components=components,
        extension_readiness=(
            ExtensionReadiness.READY if binding is not None else ExtensionReadiness.WITHDRAWN
        ),
    )


async def refresh_telemetry_readiness(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    observation: ObservationReadinessPort,
) -> ReadinessRecord:
    """Recombine the preview allocation and ingestion facts alone.

    Telemetry ingestion calls this whenever it publishes new readiness. The
    extension half stays as the extension's own connection, binding, and the
    participant's Pause last left it, so a healthy telemetry path can never
    restore readiness that a lost connection or a Pause withdrew.
    """
    return await _update(
        database,
        interview_session_id=interview_session_id,
        components=await _observed_components(
            database,
            client_id=client_id,
            interview_session_id=interview_session_id,
            observation=observation,
        ),
        extension_readiness=None,
    )


async def _observed_components(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    observation: ObservationReadinessPort,
) -> Mapping[str, bool]:
    """Read the preview allocation and the three observation kinds ingestion projects."""
    facts = await observation.readiness(
        client_id=client_id, interview_session_id=interview_session_id
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select allocations.state
            from public.interview_sessions as sessions
            join public.preview_runtime_allocations as allocations
              on allocations.id = sessions.preview_runtime_allocation_id
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        allocation = await cur.fetchone()
    return {
        "preview_allocation_ready": allocation is not None
        and as_str(allocation, "state") in {"ready", "bound"},
        "browser_sdk_ready": facts.browser_ready,
        "server_sdk_ready": facts.server_ready,
        "trace_correlation_ready": facts.correlation_ready,
    }


async def withdraw_observation_readiness(
    database: Database, *, interview_session_id: UUID, reason: str
) -> ReadinessRecord:
    """Withdraw the observation half immediately, naming the cause.

    Pause, a lost binding, a lost document proof, and a lost correlated path all
    land here: dependent claims, probing, commands, and validation stop at once.
    """
    record = await _update(
        database,
        interview_session_id=interview_session_id,
        components=dict.fromkeys(_OBSERVATION_COLUMNS, False),
        extension_readiness=ExtensionReadiness.WITHDRAWN,
        extra_blocker=reason,
    )
    _log.info(
        "observation_readiness_withdrawn",
        interview_session_id=str(interview_session_id),
        reason=reason,
        readiness_revision=record.readiness_revision,
    )
    return record


async def _ensure(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> ReadinessRecord:
    """Lock the readiness row, creating it from the session on first use."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select client_id from public.interview_sessions where id = %s",
            (interview_session_id,),
        )
        session = await cur.fetchone()
        if session is None:
            raise AggregateNotFoundError(
                table="interview_sessions", row_id=interview_session_id
            )
        await cur.execute(
            """
            insert into public.session_readiness (interview_session_id, client_id)
            values (%s, %s)
            on conflict (interview_session_id) do nothing
            """,
            (interview_session_id, session["client_id"]),
        )
        await cur.execute(_SELECT_READINESS, (interview_session_id,))
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="session_readiness", row_id=interview_session_id
        )
    return _to_record(interview_session_id, row)


async def _update(
    database: Database,
    *,
    interview_session_id: UUID,
    components: Mapping[str, bool],
    extension_readiness: ExtensionReadiness | None,
    extra_blocker: str | None = None,
) -> ReadinessRecord:
    """Apply component facts, recompute both boundaries, and advance the revision."""
    async with database.transaction() as conn:
        current = await _ensure(conn, interview_session_id=interview_session_id)
        merged = {**current.components, **components}
        merged_extension = extension_readiness or current.extension_readiness
        candidate = _combine(
            interview_session_id=interview_session_id,
            client_id=current.client_id,
            readiness_revision=current.readiness_revision,
            components=merged,
            extension_readiness=merged_extension,
            extra_blocker=extra_blocker,
        )
        if _same(current, candidate):
            return current
        advanced = replace(
            candidate, readiness_revision=current.readiness_revision + 1
        )
        await _write(conn, advanced)
        if advanced.conversational and not current.conversational:
            await _admit_when_preparing(
                conn, client_id=current.client_id, interview_session_id=interview_session_id
            )
    _log.info(
        "session_readiness_recorded",
        interview_session_id=str(interview_session_id),
        readiness_revision=advanced.readiness_revision,
        conversational=advanced.conversational,
        observation=advanced.observation,
        blockers=list(advanced.blockers),
    )
    return advanced


async def _admit_when_preparing(
    conn: AsyncConnection[DictRow], *, client_id: UUID, interview_session_id: UUID
) -> None:
    """Admit a preparing session to `live` the moment conversation is ready.

    Greeting and problem discussion may begin while the preview is still being
    prepared, so admission needs conversational readiness only; observation-
    dependent work keeps its own boundary.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select state, revision from public.interview_sessions where id = %s for update",
            (interview_session_id,),
        )
        session = await cur.fetchone()
    if session is None:
        raise AggregateNotFoundError(table="interview_sessions", row_id=interview_session_id)
    if as_str(session, "state") != InterviewSessionState.PREPARING.value:
        return
    stream = StreamRef(kind=AggregateKind.INTERVIEW_SESSION, id=interview_session_id)
    await apply_transition_on_connection(
        conn,
        plan_transition(
            INTERVIEW_SESSION_MACHINE,
            InterviewSessionFacts(
                identity_matches=True,
                participant_setup_passed=True,
                conversational_ready=True,
                observation_ready=False,
            ),
            trigger="admit",
            current_state=InterviewSessionState.PREPARING.value,
            client_id=client_id,
            aggregate_kind=AggregateKind.INTERVIEW_SESSION,
            aggregate_id=interview_session_id,
            expected_revision=as_int(session, "revision"),
            state_column="state",
            mutation={},
            event=EventAppend(
                message_id=uuid7(),
                stream=stream,
                event_type="interview_session_admitted",
                payload={},
                actor=_READINESS_ACTOR,
                causation_id=None,
                correlation_id=interview_session_id,
                occurred_at=datetime.now(tz=UTC),
            ),
            jobs=(),
            commands=(),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=stream,
                action="admit_interview_session",
                outcome="live",
                reason_code=None,
                actor=_READINESS_ACTOR,
                pinned_configuration_release_id=None,
                detail={},
            ),
            additional_locks=(),
        ),
    )


def _combine(
    *,
    interview_session_id: UUID,
    client_id: UUID,
    readiness_revision: int,
    components: Mapping[str, bool],
    extension_readiness: ExtensionReadiness,
    extra_blocker: str | None,
) -> ReadinessRecord:
    """Compute both boundaries and the blockers that explain a refusal."""
    blockers: list[str] = []
    for column in _ALL_COLUMNS:
        if not components[column]:
            blockers.append(_BLOCKER_BY_COLUMN[column])
    if extension_readiness is not ExtensionReadiness.READY:
        blockers.append(_EXTENSION_BLOCKER)
    if extra_blocker is not None and extra_blocker not in blockers:
        blockers.append(extra_blocker)
    conversational = all(components[column] for column in _CONVERSATIONAL_COLUMNS)
    observation = (
        conversational
        and extension_readiness is ExtensionReadiness.READY
        and all(components[column] for column in _OBSERVATION_COLUMNS)
    )
    return ReadinessRecord(
        interview_session_id=interview_session_id,
        client_id=client_id,
        readiness_revision=readiness_revision,
        components=dict(components),
        extension_readiness=extension_readiness,
        conversational=conversational,
        observation=observation,
        blockers=tuple(blockers),
    )


def _same(current: ReadinessRecord, candidate: ReadinessRecord) -> bool:
    """True when nothing observable changed, so the revision must not advance."""
    return (
        current.components == candidate.components
        and current.extension_readiness is candidate.extension_readiness
        and current.blockers == candidate.blockers
    )


async def _write(conn: AsyncConnection[DictRow], record: ReadinessRecord) -> None:
    """Persist the combined record at its advanced revision."""
    assignments = ", ".join(f"{column} = %s" for column in _ALL_COLUMNS)
    parameters: list[object] = [record.components[column] for column in _ALL_COLUMNS]
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            update public.session_readiness
            set {assignments},
                extension_readiness = %s,
                conversational_ready = %s,
                observation_ready = %s,
                blockers = %s,
                readiness_revision = %s,
                updated_at = now()
            where interview_session_id = %s
            """,  # the column names come from a fixed internal tuple
            (
                *parameters,
                record.extension_readiness.value,
                record.conversational,
                record.observation,
                list(record.blockers),
                record.readiness_revision,
                record.interview_session_id,
            ),
        )


_READINESS_QUERY = """
    select client_id, readiness_revision, extension_readiness, blockers,
           participant_authenticated, voice_transport_ready, sideband_attached,
           backend_context_current, preview_allocation_ready, browser_sdk_ready,
           server_sdk_ready, trace_correlation_ready
    from public.session_readiness
    where interview_session_id = %s
"""
#: The same read for a writer, which holds the row until it commits.
_SELECT_READINESS = _READINESS_QUERY + "    for update\n"


def _to_record(interview_session_id: UUID, row: DictRow) -> ReadinessRecord:
    """Project one readiness row into its record."""
    components = {column: as_bool(row, column) for column in _ALL_COLUMNS}
    blockers = row["blockers"]
    if not isinstance(blockers, Sequence) or isinstance(blockers, str):
        raise TypeError("session_readiness.blockers is not an array")
    extension_readiness = ExtensionReadiness(as_str(row, "extension_readiness"))
    conversational = all(components[column] for column in _CONVERSATIONAL_COLUMNS)
    observation = (
        conversational
        and extension_readiness is ExtensionReadiness.READY
        and all(components[column] for column in _OBSERVATION_COLUMNS)
    )
    return ReadinessRecord(
        interview_session_id=interview_session_id,
        client_id=row["client_id"],
        readiness_revision=as_int(row, "readiness_revision"),
        components=components,
        extension_readiness=extension_readiness,
        conversational=conversational,
        observation=observation,
        blockers=tuple(str(blocker) for blocker in blockers),
    )
