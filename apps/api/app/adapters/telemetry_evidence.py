"""``EvidenceQueryPort`` over ingestion's frozen windows and promoted items.

The validation controller and the Case File assembler read criterion evidence
from the control plane's own evidence ledger. Ingestion owns the telemetry those
citations point at, so this adapter joins the two halves:

- ``freeze_window`` freezes a telemetry evidence window at the same watermarks it
  writes the control-plane window row at, and links them, so a decision's frozen
  selection and the ingestion state it saw are one record.
- ``promote_observation`` writes both halves of one citation: the telemetry
  evidence item, which keeps the source record, policy and schema versions,
  correlation revision, and watermarks; and the ledger row the validation
  queries read, whose body is built by ``evidence_body`` so the field names the
  queries select on have one definition.
- the two read shapes delegate to ``DurableEvidenceQuery``, which is already the
  implementation over that ledger. Wrapping rather than reimplementing keeps one
  query per shape.

A promotion never decides which criterion an observation supports. The control
plane does that and passes it in; ingestion supplies the provenance and refuses a
source record outside the window's client scope or source classes.

Contract: docs/arch-components/telemetry-and-evidence.md#evidence-windows-and-promotion
"""

from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import EvidenceClaimState, EvidenceClass

from app.db.pool import Database
from app.db.rows import as_uuid
from app.evidence.promotion import PromotionRequest, promote_items
from app.evidence.windows import (
    EvidenceWindow,
    open_rolling_window,
)
from app.evidence.windows import (
    freeze_window as freeze_telemetry_window,
)
from app.ids import uuid7
from app.ingestion.errors import QueryAuthorizationError
from app.logging import get_logger
from app.validation.evidence import (
    BeforeAfterComparison,
    CriterionEvidenceItem,
    DurableEvidenceQuery,
    EvidenceStance,
    FrozenEvidenceWindow,
    evidence_body,
)

__all__ = ["TelemetryEvidenceQuery"]

_log = get_logger(__name__)

#: The source classes a telemetry window admits and the ledger class each maps to.
_LEDGER_CLASS = {
    "browser_observation": EvidenceClass.BROWSER_OBSERVATION,
    "server_span": EvidenceClass.SERVER_SPAN,
}

_ALLOWED_SOURCE_CLASSES = tuple(sorted(_LEDGER_CLASS))

_AUTHORIZED_CALLER = "control_plane.validation"


class TelemetryEvidenceQuery:
    """``EvidenceQueryPort`` whose windows and citations are ingestion's own."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._ledger = DurableEvidenceQuery(database)

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
        """Freeze one selection in both the ledger and the ingestion stream.

        A window with no Interview Session cites no telemetry, so only the ledger
        row is written. That is the Case File of a case that never reached a
        session, not a telemetry window with nothing in it.
        """
        window = await self._ledger.freeze_window(
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            purpose=purpose,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        if interview_session_id is None:
            return window
        frozen = await self._freeze_telemetry(
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            purpose=purpose,
        )
        await self._link(
            telemetry_window_id=frozen.evidence_window_id,
            control_plane_window_id=window.evidence_window_id,
        )
        _log.info(
            "telemetry_evidence_window_frozen",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            evidence_window_id=str(window.evidence_window_id),
            telemetry_evidence_window_id=str(frozen.evidence_window_id),
            purpose=purpose,
            browser_accepted_through=frozen.watermarks.browser_accepted_through,
            server_accepted_through=frozen.watermarks.server_accepted_through,
            correlation_revision=frozen.watermarks.correlation_revision,
        )
        return window

    async def criterion_evidence(
        self,
        *,
        client_id: UUID,
        evidence_window_id: UUID,
        validation_criterion_id: UUID,
        allowed_source_classes: frozenset[EvidenceClass],
        limit: int,
    ) -> tuple[CriterionEvidenceItem, ...]:
        """Return the promoted items citing one criterion inside the window."""
        return await self._ledger.criterion_evidence(
            client_id=client_id,
            evidence_window_id=evidence_window_id,
            validation_criterion_id=validation_criterion_id,
            allowed_source_classes=allowed_source_classes,
            limit=limit,
        )

    async def before_after_comparison(
        self,
        *,
        client_id: UUID,
        evidence_window_id: UUID,
        workflow_ref: str,
        base_preview_version_id: UUID | None,
        candidate_preview_version_id: UUID,
    ) -> BeforeAfterComparison:
        """Return the workflow's base and candidate observations."""
        return await self._ledger.before_after_comparison(
            client_id=client_id,
            evidence_window_id=evidence_window_id,
            workflow_ref=workflow_ref,
            base_preview_version_id=base_preview_version_id,
            candidate_preview_version_id=candidate_preview_version_id,
        )

    async def promote_observation(
        self,
        *,
        client_id: UUID,
        product_signal_id: UUID,
        interview_session_id: UUID,
        telemetry_window_id: UUID,
        source_class: str,
        source_record_id: UUID,
        criterion_id: UUID,
        stance: EvidenceStance,
        summary: str,
        workflow_ref: str | None,
        milestone: str | None,
        preview_version_id: UUID | None,
        coverage_limit: str | None,
        observed_at: datetime | None,
    ) -> UUID:
        """Promote one telemetry record into the window and the ledger.

        The telemetry item carries the provenance; the ledger row carries the
        claim. Both are written before this returns, so a citation can never name
        a ledger row whose telemetry provenance was not recorded.
        """
        ledger_class = _LEDGER_CLASS.get(source_class)
        if ledger_class is None:
            raise QueryAuthorizationError(
                subject=f"evidence window {telemetry_window_id}",
                reason=f"'{source_class}' is not a telemetry source class",
            )
        promoted = await promote_items(
            self._database,
            evidence_window_id=telemetry_window_id,
            requests=(
                PromotionRequest(
                    source_class=source_class,
                    source_record_id=source_record_id,
                    claim_ref=str(criterion_id),
                    supports=stance is EvidenceStance.SUPPORTS,
                ),
            ),
        )
        item = promoted[0]
        record_id = uuid7()
        async with self._database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.evidence_records (
                    id, client_id, product_signal_id, interview_session_id,
                    evidence_class, claim_state, source_ref, context_revision, body
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id,
                    client_id,
                    product_signal_id,
                    interview_session_id,
                    ledger_class.value,
                    EvidenceClaimState.CONFIRMED.value,
                    f"telemetry:{source_class}/{source_record_id}",
                    0,
                    Jsonb(
                        evidence_body(
                            criterion_id=criterion_id,
                            stance=stance,
                            summary=summary,
                            workflow_ref=workflow_ref,
                            milestone=milestone,
                            preview_version_id=preview_version_id,
                            coverage_limit=coverage_limit,
                            observed_at=observed_at,
                        )
                    ),
                ),
            )
        _log.info(
            "telemetry_evidence_promoted_to_ledger",
            client_id=str(client_id),
            product_signal_id=str(product_signal_id),
            evidence_record_id=str(record_id),
            telemetry_evidence_item_id=str(item.evidence_item_id),
            source_class=source_class,
            criterion_id=str(criterion_id),
            stance=stance.value,
        )
        return record_id

    async def open_window(
        self,
        *,
        client_id: UUID,
        product_signal_id: UUID,
        interview_session_id: UUID,
        purpose: str,
    ) -> EvidenceWindow:
        """Open the rolling live window promotions attach to during a session."""
        return await open_rolling_window(
            self._database,
            client_id=client_id,
            interview_session_id=interview_session_id,
            product_signal_id=product_signal_id,
            activation_id=None,
            purpose=purpose,
            authorized_caller=_AUTHORIZED_CALLER,
            allowed_source_classes=_ALLOWED_SOURCE_CLASSES,
            query_contract={"shape": "session_timeline"},
            domain_identities={
                "product_signal_id": str(product_signal_id),
                "interview_session_id": str(interview_session_id),
            },
        )

    async def _freeze_telemetry(
        self,
        *,
        client_id: UUID,
        product_signal_id: UUID,
        interview_session_id: UUID,
        purpose: str,
    ) -> EvidenceWindow:
        """Freeze the session's current ingestion state as an immutable window."""
        rolling = await self.open_window(
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            purpose=purpose,
        )
        return await freeze_telemetry_window(
            self._database,
            source_window_id=rolling.evidence_window_id,
            purpose=purpose,
            authorized_caller=_AUTHORIZED_CALLER,
            completeness={"state": "frozen_at_decision"},
        )

    async def _link(
        self, *, telemetry_window_id: UUID, control_plane_window_id: UUID
    ) -> None:
        """Record which control-plane window cites this telemetry selection."""
        async with self._database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                update public.telemetry_evidence_windows
                set control_plane_evidence_window_id = %s, updated_at = now()
                where id = %s
                returning id
                """,
                (control_plane_window_id, telemetry_window_id),
            )
            row = await cur.fetchone()
        if row is None or as_uuid(row, "id") != telemetry_window_id:
            raise QueryAuthorizationError(
                subject=f"evidence window {telemetry_window_id}",
                reason="the frozen telemetry window could not be linked",
            )
