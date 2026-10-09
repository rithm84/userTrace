"""The narrow evidence seam validation and Case File assembly read through.

Telemetry ingestion owns normalization, correlation, promotion, and the frozen
[evidence windows](../../../../docs/arch-components/telemetry-and-evidence.md#evidence-windows-and-promotion).
This module names only the three read shapes the validation controller and the
Case File assembler need, so neither reaches into the ingestion stream's storage:

- ``freeze_window`` materializes one immutable window for a decision or output;
- ``criterion_evidence`` returns the promoted items that support or contradict
  one acceptance criterion inside a frozen window;
- ``before_after_comparison`` returns the base and candidate observations of one
  agreed workflow, with the coverage limits the Case File must state.

``DurableEvidenceQuery`` is the implementation over the control plane's own
evidence ledger: promoted items already land in ``public.evidence_records`` with
the claim they support or contradict, so validation reads the durable record
rather than a live provider stream. A query never mutates state, promotes
evidence, or broadens its own authorization.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import EvidenceClaimState, EvidenceClass

from app.db.pool import Database
from app.db.rows import as_datetime, as_str, as_uuid
from app.ids import uuid7

__all__ = [
    "ADMISSIBLE_OBSERVED_CLASSES",
    "BeforeAfterComparison",
    "CriterionEvidenceItem",
    "DurableEvidenceQuery",
    "EvidenceQueryPort",
    "EvidenceStance",
    "EvidenceWindowNotFoundError",
    "FrozenEvidenceWindow",
    "WorkflowObservation",
    "evidence_body",
]

ADMISSIBLE_OBSERVED_CLASSES: frozenset[EvidenceClass] = frozenset(
    {
        EvidenceClass.BROWSER_OBSERVATION,
        EvidenceClass.SERVER_SPAN,
        EvidenceClass.VALIDATION_OBSERVATION,
        EvidenceClass.CHECK_RESULT,
    }
)
"""Classes that can carry affirmative proof that the product did something.

A transcript fragment is the originating user's statement, not observed product
behaviour, so it is deliberately absent.
"""


class EvidenceStance(StrEnum):
    """Whether a promoted item supports or contradicts the claim it cites."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class _Frozen(BaseModel):
    """Immutable, closed result shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class FrozenEvidenceWindow(_Frozen):
    """One immutable selection of observations backing a decision or output."""

    evidence_window_id: UUID
    client_id: UUID
    product_signal_id: UUID
    interview_session_id: UUID | None
    purpose: str
    starts_at: datetime
    ends_at: datetime


class CriterionEvidenceItem(_Frozen):
    """One promoted observation cited for or against one acceptance criterion."""

    evidence_record_id: UUID
    evidence_class: EvidenceClass
    claim_state: EvidenceClaimState
    source_ref: str
    stance: EvidenceStance
    summary: str
    recorded_at: datetime
    observed_at: datetime | None
    """When the product did what this item cites; ``None`` when the source carries no time."""
    supersedes_id: UUID | None

    def is_observed_product_behaviour(self) -> bool:
        """Whether this item can serve as affirmative proof of product behaviour."""
        return self.evidence_class in ADMISSIBLE_OBSERVED_CLASSES


class WorkflowObservation(_Frozen):
    """One cited observation of the agreed workflow on a named Preview Version."""

    evidence_record_id: UUID
    evidence_class: EvidenceClass
    source_ref: str
    milestone: str | None
    """The product milestone it shows; ``None`` for one that is not a milestone, such
    as the page reload that shows a saved view survives a refresh."""
    summary: str
    preview_version_id: UUID | None
    recorded_at: datetime


class BeforeAfterComparison(_Frozen):
    """The base and candidate runs of one workflow, with their coverage limits."""

    workflow_ref: str
    base_preview_version_id: UUID | None
    candidate_preview_version_id: UUID
    base_observations: tuple[WorkflowObservation, ...]
    candidate_observations: tuple[WorkflowObservation, ...]
    coverage_limits: tuple[str, ...]


@runtime_checkable
class EvidenceQueryPort(Protocol):
    """Read-only, purpose-scoped access to promoted telemetry evidence."""

    async def freeze_window(
        self,
        *,
        client_id: UUID,
        product_signal_id: UUID,
        interview_session_id: UUID | None,
        purpose: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> FrozenEvidenceWindow:
        """Materialize one immutable evidence window and return its identity."""
        ...

    async def criterion_evidence(
        self,
        *,
        client_id: UUID,
        evidence_window_id: UUID,
        validation_criterion_id: UUID,
        allowed_source_classes: frozenset[EvidenceClass],
        limit: int,
    ) -> tuple[CriterionEvidenceItem, ...]:
        """Return the promoted items citing one criterion inside a frozen window."""
        ...

    async def before_after_comparison(
        self,
        *,
        client_id: UUID,
        evidence_window_id: UUID,
        workflow_ref: str,
        base_preview_version_id: UUID | None,
        candidate_preview_version_id: UUID,
    ) -> BeforeAfterComparison:
        """Return the workflow's base and candidate observations and their limits."""
        ...


class DurableEvidenceQuery:
    """``EvidenceQueryPort`` over the control plane's durable evidence ledger."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def freeze_window(
        self,
        *,
        client_id: UUID,
        product_signal_id: UUID,
        interview_session_id: UUID | None,
        purpose: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> FrozenEvidenceWindow:
        """Insert one immutable evidence window row and return it."""
        window_id = uuid7()
        async with self._database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.evidence_windows (
                    id, client_id, product_signal_id, interview_session_id,
                    window_kind, starts_at, ends_at
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    window_id,
                    client_id,
                    product_signal_id,
                    interview_session_id,
                    purpose,
                    starts_at,
                    ends_at,
                ),
            )
        return FrozenEvidenceWindow(
            evidence_window_id=window_id,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            purpose=purpose,
            starts_at=starts_at,
            ends_at=ends_at,
        )

    async def criterion_evidence(
        self,
        *,
        client_id: UUID,
        evidence_window_id: UUID,
        validation_criterion_id: UUID,
        allowed_source_classes: frozenset[EvidenceClass],
        limit: int,
    ) -> tuple[CriterionEvidenceItem, ...]:
        """Return confirmed items citing one criterion inside the frozen window.

        A withdrawn claim and a claim superseded by a later correction are both
        excluded: a corrected statement invalidates the affected earlier claim.
        """
        window = await self._read_window(client_id=client_id, window_id=evidence_window_id)
        async with self._database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select records.id, records.evidence_class, records.claim_state,
                       records.source_ref, records.body, records.recorded_at,
                       records.supersedes_id
                from public.evidence_records as records
                where records.client_id = %s
                  and records.product_signal_id = %s
                  and records.recorded_at >= %s
                  and records.recorded_at <= %s
                  and records.evidence_class = any(%s)
                  and records.claim_state <> %s
                  and records.body ->> 'criterion_id' = %s
                  and not exists (
                    select 1 from public.evidence_records as newer
                    where newer.supersedes_id = records.id
                  )
                order by records.recorded_at
                limit %s
                """,
                (
                    client_id,
                    window.product_signal_id,
                    window.starts_at,
                    window.ends_at,
                    [item.value for item in sorted(allowed_source_classes)],
                    EvidenceClaimState.WITHDRAWN.value,
                    str(validation_criterion_id),
                    limit,
                ),
            )
            rows = list(await cur.fetchall())
        return tuple(_to_criterion_item(row) for row in rows)

    async def before_after_comparison(
        self,
        *,
        client_id: UUID,
        evidence_window_id: UUID,
        workflow_ref: str,
        base_preview_version_id: UUID | None,
        candidate_preview_version_id: UUID,
    ) -> BeforeAfterComparison:
        """Return the workflow's observations on each Preview Version."""
        window = await self._read_window(client_id=client_id, window_id=evidence_window_id)
        async with self._database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select id, evidence_class, source_ref, body, recorded_at
                from public.evidence_records
                where client_id = %s
                  and product_signal_id = %s
                  and recorded_at >= %s
                  and recorded_at <= %s
                  and body ->> 'workflow_ref' = %s
                  and claim_state <> %s
                order by recorded_at
                """,
                (
                    client_id,
                    window.product_signal_id,
                    window.starts_at,
                    window.ends_at,
                    workflow_ref,
                    EvidenceClaimState.WITHDRAWN.value,
                ),
            )
            rows = list(await cur.fetchall())

        base: list[WorkflowObservation] = []
        candidate: list[WorkflowObservation] = []
        limits: list[str] = []
        for row in rows:
            observation = _to_workflow_observation(row)
            body = _body(row)
            limit = body.get("coverage_limit")
            if isinstance(limit, str) and limit not in limits:
                limits.append(limit)
            if observation.preview_version_id == candidate_preview_version_id:
                candidate.append(observation)
            elif observation.preview_version_id == base_preview_version_id:
                base.append(observation)
        return BeforeAfterComparison(
            workflow_ref=workflow_ref,
            base_preview_version_id=base_preview_version_id,
            candidate_preview_version_id=candidate_preview_version_id,
            base_observations=tuple(base),
            candidate_observations=tuple(candidate),
            coverage_limits=tuple(limits),
        )

    async def _read_window(
        self, *, client_id: UUID, window_id: UUID
    ) -> FrozenEvidenceWindow:
        async with self._database.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select id, client_id, product_signal_id, interview_session_id,
                       window_kind, starts_at, ends_at
                from public.evidence_windows
                where id = %s and client_id = %s
                """,
                (window_id, client_id),
            )
            row = await cur.fetchone()
        if row is None:
            raise EvidenceWindowNotFoundError(window_id=window_id, client_id=client_id)
        return FrozenEvidenceWindow(
            evidence_window_id=as_uuid(row, "id"),
            client_id=as_uuid(row, "client_id"),
            product_signal_id=as_uuid(row, "product_signal_id"),
            interview_session_id=(
                None
                if row["interview_session_id"] is None
                else as_uuid(row, "interview_session_id")
            ),
            purpose=as_str(row, "window_kind"),
            starts_at=as_datetime(row, "starts_at"),
            ends_at=as_datetime(row, "ends_at"),
        )


class EvidenceWindowNotFoundError(LookupError):
    """The named evidence window does not exist for that client."""

    def __init__(self, *, window_id: UUID, client_id: UUID) -> None:
        super().__init__(
            f"evidence window {window_id} does not exist for client {client_id}"
        )
        self.window_id = window_id
        self.client_id = client_id


def _body(row: Mapping[str, object]) -> Mapping[str, object]:
    """Return one evidence record's body, refusing a non-object payload."""
    body = row["body"]
    if not isinstance(body, Mapping):
        raise TypeError(f"evidence body is {type(body).__name__}, expected an object")
    return body


def _text(body: Mapping[str, object], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise TypeError(f"evidence body field '{key}' is not text")
    return value


def _optional_text(body: Mapping[str, object], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"evidence body field '{key}' is not text")
    return value


def _observed_at(body: Mapping[str, object]) -> datetime | None:
    """When the cited product behaviour happened, if the evidence recorded it."""
    value = body.get("observed_at")
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("evidence body field 'observed_at' is not text")
    return datetime.fromisoformat(value)


def _to_criterion_item(row: DictRow) -> CriterionEvidenceItem:
    body = _body(row)
    return CriterionEvidenceItem(
        evidence_record_id=as_uuid(row, "id"),
        evidence_class=EvidenceClass(as_str(row, "evidence_class")),
        claim_state=EvidenceClaimState(as_str(row, "claim_state")),
        source_ref=as_str(row, "source_ref"),
        stance=EvidenceStance(_text(body, "stance")),
        summary=_text(body, "summary"),
        recorded_at=as_datetime(row, "recorded_at"),
        observed_at=_observed_at(body),
        supersedes_id=(
            None if row["supersedes_id"] is None else as_uuid(row, "supersedes_id")
        ),
    )


def _to_workflow_observation(row: DictRow) -> WorkflowObservation:
    body = _body(row)
    preview_version = body.get("preview_version_id")
    return WorkflowObservation(
        evidence_record_id=as_uuid(row, "id"),
        evidence_class=EvidenceClass(as_str(row, "evidence_class")),
        source_ref=as_str(row, "source_ref"),
        milestone=_optional_text(body, "milestone"),
        summary=_text(body, "summary"),
        preview_version_id=(
            UUID(preview_version) if isinstance(preview_version, str) else None
        ),
        recorded_at=as_datetime(row, "recorded_at"),
    )


def evidence_body(
    *,
    criterion_id: UUID | None,
    stance: EvidenceStance | None,
    summary: str,
    workflow_ref: str | None,
    milestone: str | None,
    preview_version_id: UUID | None,
    coverage_limit: str | None,
    observed_at: datetime | None,
) -> dict[str, str]:
    """Build the promoted-evidence body this port reads.

    Exported so the telemetry stream and the fixtures that stand in for it write
    the same field names the queries above select on.
    """
    body: dict[str, str] = {"summary": summary}
    if criterion_id is not None:
        body["criterion_id"] = str(criterion_id)
    if stance is not None:
        body["stance"] = stance.value
    if workflow_ref is not None:
        body["workflow_ref"] = workflow_ref
    if milestone is not None:
        body["milestone"] = milestone
    if preview_version_id is not None:
        body["preview_version_id"] = str(preview_version_id)
    if coverage_limit is not None:
        body["coverage_limit"] = coverage_limit
    if observed_at is not None:
        body["observed_at"] = observed_at.isoformat()
    return body
