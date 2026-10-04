"""Case File assembly: reserve, verify, resolve delivery, seal, publish, close.

Assembly runs the three stages the
[output contract](../../../../docs/arch-components/client-outputs.md#case-file-identity-and-assembly)
defines, then closes the Product Signal, because the lifecycle refuses every
``closed_*`` transition until the Case File is published.

Sealing computes the immutable core digest over the RFC 8785 canonical bytes and
stores those exact bytes, so a publication retry reuses them rather than
re-serializing. Delivery never edits an already sealed core.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts import payload_digest
from utrace_contracts.domain.lifecycle import (
    ActorKind,
    AggregateKind,
    CaseFileStage,
    CaseFileVariant,
    ProductSignalState,
)

from app.db.kernel import ColumnValue, apply_transition_on_connection
from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_optional_uuid, as_str, as_uuid
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.ids import uuid7
from app.lifecycle.case_file import CASE_FILE_MACHINE, CaseFileFacts
from app.lifecycle.case_stop import close_open_sessions
from app.lifecycle.machine import plan_transition
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from app.logging import get_logger
from app.outputs.core_builder import canonical_core
from app.outputs.sections import CaseFileCore
from app.validation.completion import UNSUCCESSFUL_VALIDATION
from app.validation.records import load_budgets

__all__ = [
    "CaseFileRow",
    "existing_case_file_id",
    "forbidden_identifiers",
    "load_case_file_row",
    "pinned_dashboard_base_url",
    "publish_and_close",
    "reserve_case_file",
    "resolve_delivery_stage",
    "seal_case_file",
    "verify_case_file",
]

_log = get_logger(__name__)

_SERVICE_ACTOR = Actor(kind=ActorKind.SERVICE, ref="outputs.assembly")
#: How a blocked closure ends the participant's session, where its cause has its own
#: wording; any other blocked closure ends it as ``blocked``.
_SESSION_CLOSURE_BY_TRIGGER: Mapping[str, str] = {UNSUCCESSFUL_VALIDATION: "not_validated"}


@dataclass(frozen=True, slots=True)
class CaseFileRow:
    """One Case File record at whatever stage it has reached."""

    id: UUID
    client_id: UUID
    product_signal_id: UUID
    variant: CaseFileVariant
    stage: CaseFileStage
    revision: int
    core_digest: str | None
    core_canonical: str | None
    delivery_id: UUID | None
    authenticated_url: str | None


async def load_case_file_row(
    conn: AsyncConnection[DictRow], *, case_file_id: UUID
) -> CaseFileRow:
    """Return one Case File row by its identity."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, product_signal_id, variant, stage, revision,
                   core_digest, core_canonical, delivery_id, authenticated_url
            from public.case_files where id = %s
            """,
            (case_file_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="case_files", row_id=case_file_id)
    return CaseFileRow(
        id=as_uuid(row, "id"),
        client_id=as_uuid(row, "client_id"),
        product_signal_id=as_uuid(row, "product_signal_id"),
        variant=CaseFileVariant(as_str(row, "variant")),
        stage=CaseFileStage(as_str(row, "stage")),
        revision=as_int(row, "revision"),
        core_digest=as_optional_str(row, "core_digest"),
        core_canonical=as_optional_str(row, "core_canonical"),
        delivery_id=as_optional_uuid(row, "delivery_id"),
        authenticated_url=as_optional_str(row, "authenticated_url"),
    )


async def existing_case_file_id(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> UUID | None:
    """Return the case's one Case File identity, if it already has one."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from public.case_files where product_signal_id = %s",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return None if row is None else as_uuid(row, "id")


async def pinned_dashboard_base_url(
    conn: AsyncConnection[DictRow], *, configuration_release_id: UUID
) -> str:
    """Read the authenticated dashboard origin from the pinned configuration.

    The Case File URL a pull-request body links has to be stable and reserved
    before delivery, and the origin belongs to the client's pinned configuration
    release rather than to a worker's environment.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select policy from public.configuration_releases where id = %s",
            (configuration_release_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="configuration_releases", row_id=configuration_release_id
        )
    policy = row["policy"]
    if not isinstance(policy, Mapping):
        raise TypeError("the pinned configuration release carries no policy object")
    base = policy.get("dashboard_base_url")
    if not isinstance(base, str) or not base:
        raise GuardRejectedError(
            machine="case_file",
            trigger="reserve",
            reason=(
                "the pinned configuration release declares no dashboard_base_url, so "
                "no authenticated Case File URL can be reserved"
            ),
        )
    return base.rstrip("/")


async def forbidden_identifiers(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> tuple[str, ...]:
    """Return the direct personal identifiers a published core must not contain."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select users.display_name
            from public.product_signals as signals
            join public.client_users on client_users.id = signals.originating_client_user_id
            join public.users on users.id = client_users.utrace_user_id
            where signals.id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return ()
    name = row["display_name"]
    return (name,) if isinstance(name, str) and name else ()


async def reserve_case_file(
    conn: AsyncConnection[DictRow],
    *,
    case_file_id: UUID,
    client_id: UUID,
    product_signal_id: UUID,
    variant: CaseFileVariant,
    authenticated_url: str,
) -> None:
    """Reserve the Case File identity and its stable authenticated URL."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.case_files (
                id, client_id, product_signal_id, variant, stage, authenticated_url
            )
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                case_file_id,
                client_id,
                product_signal_id,
                variant.value,
                CaseFileStage.DRAFT.value,
                authenticated_url,
            ),
        )


async def _transition(
    conn: AsyncConnection[DictRow],
    case_file: CaseFileRow,
    *,
    trigger: str,
    facts: CaseFileFacts,
    mutation: Mapping[str, ColumnValue],
    payload: Mapping[str, JsonValue],
) -> None:
    stream = StreamRef(kind=AggregateKind.CASE_FILE, id=case_file.id)
    request = plan_transition(
        CASE_FILE_MACHINE,
        facts,
        trigger=trigger,
        current_state=case_file.stage.value,
        client_id=case_file.client_id,
        aggregate_kind=AggregateKind.CASE_FILE,
        aggregate_id=case_file.id,
        expected_revision=case_file.revision,
        state_column="stage",
        mutation=mutation,
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type=f"case_file_{trigger}",
            payload=payload,
            actor=_SERVICE_ACTOR,
            causation_id=None,
            correlation_id=case_file.product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=(),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action=f"case_file_{trigger}",
            outcome=trigger,
            reason_code=None,
            actor=_SERVICE_ACTOR,
            pinned_configuration_release_id=None,
            detail=payload,
        ),
        additional_locks=(),
    )
    await apply_transition_on_connection(conn, request)


async def verify_case_file(
    conn: AsyncConnection[DictRow],
    *,
    case_file_id: UUID,
    core: CaseFileCore,
    narrative_source: str,
    blocker_reason: str | None,
) -> None:
    """Freeze the verified findings and evidence draft onto the Case File row."""
    case_file = await load_case_file_row(conn, case_file_id=case_file_id)
    metadata = core.metadata
    await _transition(
        conn,
        case_file,
        trigger="verify",
        facts=CaseFileFacts(
            variant=core.variant,
            citations_verified=True,
            mandatory_evidence_present=True,
            delivery_identity_verified=False,
            core_digest_present=False,
        ),
        mutation={
            "core": Jsonb(core.model_dump(mode="json")),
            "title": core.summary.title,
            "context_revision": metadata.context_revision,
            "narrative_source": narrative_source,
            "blocker_reason": blocker_reason,
            "validation_attempt_id": metadata.validation_attempt_id,
            "implementation_attempt_id": (
                metadata.implementation_attempt_ids[-1]
                if metadata.implementation_attempt_ids
                else None
            ),
            "preview_version_id": metadata.preview_version_id,
            "gate_decision_id": metadata.gate_decision_id,
            "evidence_window_id": metadata.evidence_window_id,
            "originating_user_ref": metadata.originating_user_ref,
            "customer_account_ref": metadata.customer_account_ref,
        },
        payload={"variant": core.variant.value, "narrative_source": narrative_source},
    )


async def resolve_delivery_stage(
    conn: AsyncConnection[DictRow],
    *,
    case_file_id: UUID,
    delivery_id: UUID | None,
    delivery_identity_verified: bool,
    core: CaseFileCore,
    blocker_reason: str | None,
) -> None:
    """Append the verified GitHub identities, or record that none are needed.

    A definitive delivery failure after successful validation reaches this stage
    as a ``blocked`` core: the variant and blocker move with the core, which is
    still unsealed, so nothing already frozen is edited.
    """
    case_file = await load_case_file_row(conn, case_file_id=case_file_id)
    await _transition(
        conn,
        case_file,
        trigger="resolve_delivery",
        facts=CaseFileFacts(
            variant=core.variant,
            citations_verified=True,
            mandatory_evidence_present=True,
            delivery_identity_verified=delivery_identity_verified,
            core_digest_present=False,
        ),
        mutation={
            "delivery_id": delivery_id,
            "variant": core.variant.value,
            "blocker_reason": blocker_reason,
            "core": Jsonb(core.model_dump(mode="json")),
        },
        payload={
            "delivery_id": None if delivery_id is None else str(delivery_id),
            "delivery_identity_verified": delivery_identity_verified,
            "variant": core.variant.value,
        },
    )


async def seal_case_file(
    conn: AsyncConnection[DictRow], *, case_file_id: UUID, core: CaseFileCore
) -> str:
    """Compute the immutable core digest over the canonical bytes and freeze them."""
    case_file = await load_case_file_row(conn, case_file_id=case_file_id)
    canonical = canonical_core(core)
    digest = payload_digest(core.model_dump(mode="json"))
    await _transition(
        conn,
        case_file,
        trigger="seal",
        facts=CaseFileFacts(
            variant=case_file.variant,
            citations_verified=True,
            mandatory_evidence_present=True,
            delivery_identity_verified=True,
            core_digest_present=True,
        ),
        mutation={
            "core": Jsonb(core.model_dump(mode="json")),
            "core_canonical": canonical.decode("utf-8"),
            "core_digest": digest,
            "sealed_at": datetime.now(tz=UTC),
        },
        payload={"core_digest": digest, "core_bytes": len(canonical)},
    )
    _log.info(
        "case_file_sealed",
        case_file_id=str(case_file_id),
        product_signal_id=str(case_file.product_signal_id),
        variant=case_file.variant.value,
        core_digest=digest,
    )
    return digest


async def publish_and_close(
    database: Database,
    *,
    case_file_id: UUID,
    closure_trigger: str,
    closure_reason: str,
) -> str:
    """Publish the sealed Case File and close the Product Signal in one commit."""
    async with database.transaction() as conn:
        case_file = await load_case_file_row(conn, case_file_id=case_file_id)
        if case_file.stage is CaseFileStage.PUBLISHED:
            return case_file.core_digest or ""
        if case_file.core_digest is None:
            raise GuardRejectedError(
                machine="case_file",
                trigger="publish",
                reason="publication requires the sealed immutable core digest",
            )
        await _transition(
            conn,
            case_file,
            trigger="publish",
            facts=CaseFileFacts(
                variant=case_file.variant,
                citations_verified=True,
                mandatory_evidence_present=True,
                delivery_identity_verified=True,
                core_digest_present=True,
            ),
            mutation={"published_at": datetime.now(tz=UTC)},
            payload={"core_digest": case_file.core_digest},
        )
        await _close_signal(
            conn,
            product_signal_id=case_file.product_signal_id,
            closure_trigger=closure_trigger,
            closure_reason=closure_reason,
            case_file_id=case_file.id,
        )
    _log.info(
        "case_file_published",
        case_file_id=str(case_file_id),
        product_signal_id=str(case_file.product_signal_id),
        closure_trigger=closure_trigger,
        core_digest=case_file.core_digest,
    )
    return case_file.core_digest


async def _participation_occurred(
    conn: AsyncConnection[DictRow], product_signal_id: UUID
) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
                select 1 from public.interview_sessions as sessions
                join public.session_readiness as readiness
                  on readiness.interview_session_id = sessions.id
                where sessions.product_signal_id = %s
                  and readiness.conversational_ready
            ) as participated
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return bool(row is not None and row["participated"])


async def _close_signal(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    closure_trigger: str,
    closure_reason: str,
    case_file_id: UUID,
) -> None:
    """Commit the case's closed outcome now that its Case File is published."""
    budgets = await load_budgets(conn, product_signal_id=product_signal_id)
    participated = await _participation_occurred(conn, product_signal_id)
    stream = StreamRef(kind=AggregateKind.PRODUCT_SIGNAL, id=product_signal_id)
    payload: Mapping[str, JsonValue] = {
        "case_file_id": str(case_file_id),
        "closure_reason": closure_reason,
    }
    request = plan_transition(
        PRODUCT_SIGNAL_MACHINE,
        ProductSignalFacts(
            participation_occurred=participated,
            case_file_published=True,
        ),
        trigger=closure_trigger,
        current_state=budgets.state,
        client_id=budgets.client_id,
        aggregate_kind=AggregateKind.PRODUCT_SIGNAL,
        aggregate_id=product_signal_id,
        expected_revision=budgets.revision,
        state_column="state",
        mutation={"closure_reason": closure_reason},
        event=EventAppend(
            message_id=uuid7(),
            stream=stream,
            event_type="product_signal_closed",
            payload=payload,
            actor=_SERVICE_ACTOR,
            causation_id=None,
            correlation_id=product_signal_id,
            occurred_at=datetime.now(tz=UTC),
        ),
        jobs=(),
        commands=(),
        audit=AuditIntent(
            audit_id=uuid7(),
            subject=stream,
            action="close_product_signal",
            outcome=closure_trigger,
            reason_code=closure_reason,
            actor=_SERVICE_ACTOR,
            pinned_configuration_release_id=budgets.pinned_configuration_release_id,
            detail=payload,
        ),
        additional_locks=(),
    )
    await apply_transition_on_connection(conn, request)
    await close_open_sessions(
        conn,
        product_signal_id=product_signal_id,
        client_id=budgets.client_id,
        actor=_SERVICE_ACTOR,
        now=datetime.now(tz=UTC),
        closure_reason=(
            _SESSION_CLOSURE_BY_TRIGGER.get(closure_trigger, "blocked")
            if request.to_state == ProductSignalState.CLOSED_BLOCKED.value
            else "completed"
        ),
        event_type="interview_session_closed_by_case_closure",
        audit_action="close_product_signal",
    )
