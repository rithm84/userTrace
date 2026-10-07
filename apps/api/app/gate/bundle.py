"""Deterministic assembly of the Implementation Gate bundle.

The gate contract names exactly what the decision reads: the Product Signal,
the interview-established classification and its revision, the acceptance
criteria, confirmed user evidence, correlated telemetry, exact-revision code
findings, prepared-environment readiness, the pinned policy, and whether the
Product Signal already holds its one Implementation Attempt.

Every field here comes from a durable row. No model call happens in this module:
a proposal is asked for only after the bundle exists, and it may cite nothing
that is not already in it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field
from utrace_contracts.domain.lifecycle import FlowType, ProductSignalState

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.gate.policy import ImplementationPolicy, read_implementation_policy
from app.gate.scope import AcceptanceCriterion

__all__ = [
    "CodeFinding",
    "EnvironmentReadiness",
    "EvidenceCitation",
    "GateBundle",
    "assemble_gate_bundle",
]

_TELEMETRY_CLASSES = ("browser_observation", "server_span")


class BundleModel(BaseModel):
    """Base for every bundle part: immutable and closed to extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceCitation(BundleModel):
    """One admissible evidence record the gate may cite."""

    evidence_id: UUID
    evidence_class: str
    claim_state: str
    source_ref: str
    context_revision: int
    recorded_at: datetime
    summary: str
    """The record's own `body.summary`, or an empty string when it carries none."""


class CodeFinding(BundleModel):
    """One exact-revision code finding from the read-only pre-gate investigation."""

    finding_id: UUID
    repository_ref: str
    base_revision: str
    path: str
    first_line: Annotated[int, Field(ge=1)]
    last_line: Annotated[int, Field(ge=1)]
    excerpt_digest: str
    note: str


class EnvironmentReadiness(BundleModel):
    """Whether a prepared base environment exists for this Product Signal."""

    base_allocation_id: UUID | None
    base_allocation_state: str | None
    seed_manifest_digest: str | None

    @property
    def prepared(self) -> bool:
        """Readiness means a base allocation that reached `ready` or `bound`."""
        return self.base_allocation_state in ("ready", "bound")


class GateBundle(BundleModel):
    """Everything the deterministic gate controller reads before it decides."""

    client_id: UUID
    product_signal_id: UUID
    product_signal_state: ProductSignalState
    product_signal_revision: int
    initial_flow_type: FlowType
    established_flow_type: FlowType | None
    classification_revision: int
    acceptance_criteria_revision: int
    concern_revision: int
    pinned_configuration_release_id: UUID
    policy: ImplementationPolicy
    customer_account_id: UUID
    originating_client_user_id: UUID
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    confirmed_evidence: tuple[EvidenceCitation, ...]
    telemetry_citations: tuple[EvidenceCitation, ...]
    code_findings: tuple[CodeFinding, ...]
    readiness: EnvironmentReadiness
    implementation_count: int
    """How many Implementation Attempts the Product Signal has already used."""
    assembled_at: datetime

    def evidence_ids(self) -> frozenset[UUID]:
        """Every evidence identifier a proposal is allowed to cite."""
        return frozenset(
            item.evidence_id for item in (*self.confirmed_evidence, *self.telemetry_citations)
        )

    def finding_ids(self) -> frozenset[UUID]:
        """Every code-finding identifier a proposal is allowed to cite."""
        return frozenset(item.finding_id for item in self.code_findings)

    def finding_by_id(self, finding_id: UUID) -> CodeFinding | None:
        """Return one cited finding, or `None` when the bundle does not carry it."""
        for finding in self.code_findings:
            if finding.finding_id == finding_id:
                return finding
        return None


async def assemble_gate_bundle(
    database: Database, *, client_id: UUID, product_signal_id: UUID, now: datetime
) -> GateBundle:
    """Read one complete gate bundle from durable rows."""
    async with database.connection() as conn:
        signal = await _load_signal(conn, client_id=client_id, product_signal_id=product_signal_id)
        release_id = as_uuid(signal, "pinned_configuration_release_id")
        policy = read_implementation_policy(
            configuration_release_id=release_id,
            policy=await _load_policy_document(conn, release_id),
        )
        criteria_revision = as_int(signal, "acceptance_criteria_revision")
        return GateBundle(
            client_id=client_id,
            product_signal_id=product_signal_id,
            product_signal_state=ProductSignalState(as_str(signal, "state")),
            product_signal_revision=as_int(signal, "revision"),
            initial_flow_type=FlowType(as_str(signal, "initial_flow_type")),
            established_flow_type=_optional_flow(signal),
            classification_revision=as_int(signal, "classification_revision"),
            acceptance_criteria_revision=criteria_revision,
            concern_revision=as_int(signal, "concern_revision"),
            pinned_configuration_release_id=release_id,
            policy=policy,
            customer_account_id=as_uuid(signal, "originating_customer_account_id"),
            originating_client_user_id=as_uuid(signal, "originating_client_user_id"),
            acceptance_criteria=await _load_criteria(conn, product_signal_id, criteria_revision),
            confirmed_evidence=await _load_evidence(conn, product_signal_id, confirmed=True),
            telemetry_citations=await _load_evidence(conn, product_signal_id, confirmed=False),
            code_findings=await _load_findings(conn, product_signal_id),
            readiness=await _load_readiness(conn, product_signal_id),
            implementation_count=as_int(signal, "implementation_count"),
            assembled_at=now,
        )


def _optional_flow(row: Mapping[str, object]) -> FlowType | None:
    value = row["established_flow_type"]
    return None if value is None else FlowType(str(value))


async def _load_signal(
    conn: AsyncConnection[DictRow], *, client_id: UUID, product_signal_id: UUID
) -> DictRow:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select signals.state, signals.revision, signals.initial_flow_type,
                   signals.established_flow_type, signals.classification_revision,
                   signals.acceptance_criteria_revision, signals.concern_revision,
                   signals.pinned_configuration_release_id,
                   signals.originating_client_user_id,
                   signals.originating_customer_account_id,
                   (select count(*) from public.implementation_attempts as attempts
                     where attempts.product_signal_id = signals.id) as implementation_count
            from public.product_signals as signals
            where signals.id = %s and signals.client_id = %s
            """,
            (product_signal_id, client_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    return row


async def _load_policy_document(
    conn: AsyncConnection[DictRow], release_id: UUID
) -> Mapping[str, object]:
    async with conn.cursor() as cur:
        await cur.execute(
            "select policy from public.configuration_releases where id = %s", (release_id,)
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="configuration_releases", row_id=release_id)
    document = row["policy"]
    if not isinstance(document, Mapping):
        raise AggregateNotFoundError(table="configuration_releases", row_id=release_id)
    return document


async def _load_criteria(
    conn: AsyncConnection[DictRow], product_signal_id: UUID, criteria_revision: int
) -> tuple[AcceptanceCriterion, ...]:
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
        rows = await cur.fetchall()
    return tuple(
        AcceptanceCriterion(
            criterion_id=str(as_uuid(row, "id")),
            ordinal=as_int(row, "ordinal"),
            statement=as_str(row, "statement"),
            observability=as_str(row, "observability"),
        )
        for row in rows
    )


async def _load_evidence(
    conn: AsyncConnection[DictRow], product_signal_id: UUID, *, confirmed: bool
) -> tuple[EvidenceCitation, ...]:
    """Load current evidence: confirmed user claims, or correlated telemetry.

    A correction appends a superseding row rather than overwriting, so a record
    that another row supersedes is history and is never admissible.
    """
    predicate = (
        "records.claim_state = 'confirmed' and records.evidence_class <> all(%s)"
        if confirmed
        else "records.evidence_class = any(%s)"
    )
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            select records.id, records.evidence_class, records.claim_state,
                   records.source_ref, records.context_revision, records.recorded_at,
                   coalesce(records.body ->> 'summary', '') as summary
            from public.evidence_records as records
            where records.product_signal_id = %s
              and {predicate}
              and not exists (
                select 1 from public.evidence_records as later
                where later.supersedes_id = records.id
              )
            order by records.recorded_at
            """,
            (product_signal_id, list(_TELEMETRY_CLASSES)),
        )
        rows = await cur.fetchall()
    return tuple(
        EvidenceCitation(
            evidence_id=as_uuid(row, "id"),
            evidence_class=as_str(row, "evidence_class"),
            claim_state=as_str(row, "claim_state"),
            source_ref=as_str(row, "source_ref"),
            context_revision=as_int(row, "context_revision"),
            recorded_at=as_datetime(row, "recorded_at"),
            summary=as_str(row, "summary"),
        )
        for row in rows
    )


async def _load_findings(
    conn: AsyncConnection[DictRow], product_signal_id: UUID
) -> tuple[CodeFinding, ...]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, repository_ref, base_revision, path, first_line, last_line,
                   excerpt_digest, note
            from public.code_investigation_findings
            where product_signal_id = %s
            order by path, first_line
            """,
            (product_signal_id,),
        )
        rows = await cur.fetchall()
    return tuple(
        CodeFinding(
            finding_id=as_uuid(row, "id"),
            repository_ref=as_str(row, "repository_ref"),
            base_revision=as_str(row, "base_revision"),
            path=as_str(row, "path"),
            first_line=as_int(row, "first_line"),
            last_line=as_int(row, "last_line"),
            excerpt_digest=as_str(row, "excerpt_digest"),
            note=as_str(row, "note"),
        )
        for row in rows
    )


async def _load_readiness(
    conn: AsyncConnection[DictRow], product_signal_id: UUID
) -> EnvironmentReadiness:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, state, seed_manifest_digest
            from public.preview_runtime_allocations
            where product_signal_id = %s and allocation_kind = 'base'
            order by created_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return EnvironmentReadiness(
            base_allocation_id=None, base_allocation_state=None, seed_manifest_digest=None
        )
    return EnvironmentReadiness(
        base_allocation_id=as_uuid(row, "id"),
        base_allocation_state=as_str(row, "state"),
        seed_manifest_digest=as_optional_str(row, "seed_manifest_digest"),
    )


