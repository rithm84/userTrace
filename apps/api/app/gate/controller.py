"""The deterministic Implementation Gate controller.

The model proposes; this module decides. It validates every citation against the
assembled bundle, runs the security screen and the scope checks, and commits one
immutable decision: `approved_live`, or `no_implementation` with one typed
blocker.

The proposal takes seconds while the interview carries on, so the decision
commits only if the case has not moved since its bundle was assembled: a changed
state, classification, agreed scope, or concern refuses it with a
`StaleContextError`, and the gate decides again on a fresh bundle.

An approval freezes the approved-scope object. The Product Signal stays
`gate_pending` in every case: an approval is consumed by attempt creation under
the [attempt protocol](../../../../docs/arch-components/implementation.md#attempt-protocol),
and a denial is closed by the Case File that explains it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import ActorKind, AggregateKind, GateDecisionKind

from app.db.kernel import append_fact
from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.envelope import Actor, AuditIntent, EventAppend, JsonValue, StreamRef
from app.errors import AggregateNotFoundError, StaleContextError
from app.gate.bundle import GateBundle
from app.gate.proposal import GateBlocker, GateProposal
from app.gate.scope import ApprovedScope, ResourceBudgets
from app.gate.screen import ScreenFinding, check_live_fit, screen_proposal, validate_citations
from app.ids import uuid7
from app.logging import get_logger

__all__ = ["GateOutcome", "decide_gate"]

_log = get_logger(__name__)

_GATE_ACTOR = Actor(kind=ActorKind.SERVICE, ref="gate.controller")
_EVIDENCE_WINDOW_KIND = "implementation_gate"


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """The committed decision and everything a caller needs to act on it."""

    gate_decision_id: UUID
    decision: GateDecisionKind
    approved_scope: ApprovedScope | None
    blocker: GateBlocker | None
    blocker_detail: str | None
    findings: tuple[ScreenFinding, ...]
    evidence_window_id: UUID


async def decide_gate(
    database: Database,
    *,
    bundle: GateBundle,
    proposal: GateProposal,
    base_revision: str,
    now: datetime,
) -> GateOutcome:
    """Validate, screen, and commit one Implementation Gate decision."""
    findings = (
        *validate_citations(bundle, proposal),
        *screen_proposal(bundle, proposal, base_revision=base_revision),
    )
    if findings:
        return await _commit(
            database,
            bundle=bundle,
            decision=GateDecisionKind.NO_IMPLEMENTATION,
            scope=None,
            findings=findings,
            proposal=proposal,
            now=now,
        )

    refusal = check_live_fit(bundle, proposal)
    if refusal is not None:
        return await _commit(
            database,
            bundle=bundle,
            decision=GateDecisionKind.NO_IMPLEMENTATION,
            scope=None,
            findings=(refusal,),
            proposal=proposal,
            now=now,
        )

    scope = _freeze_scope(bundle, proposal, base_revision=base_revision)
    return await _commit(
        database,
        bundle=bundle,
        decision=GateDecisionKind.APPROVED_LIVE,
        scope=scope,
        findings=(),
        proposal=proposal,
        now=now,
    )


def _freeze_scope(
    bundle: GateBundle,
    proposal: GateProposal,
    *,
    base_revision: str,
) -> ApprovedScope:
    """Narrow the pinned policy into the scope this one live attempt is bound to."""
    policy = bundle.policy
    budgets = ResourceBudgets(
        **{
            **policy.budgets.model_dump(),
            "elapsed_seconds": min(policy.budgets.elapsed_seconds, policy.live_window_seconds),
        }
    )
    return ApprovedScope(
        intended_outcome=proposal.intended_outcome,
        acceptance_criteria=bundle.acceptance_criteria,
        acceptance_criteria_revision=bundle.acceptance_criteria_revision,
        classification_revision=bundle.classification_revision,
        evidence_revision=bundle.concern_revision,
        repository_ref=policy.repository_ref,
        base_revision=base_revision,
        in_scope_changes=tuple(proposal.in_scope_changes),
        excluded_changes=tuple(proposal.excluded_changes),
        permitted_paths=policy.permitted_paths,
        blocked_paths=policy.blocked_paths,
        required_checks=policy.required_checks,
        dependency_policy=policy.dependency_policy,
        permitted_dependencies=policy.permitted_dependencies,
        dependency_manifest_paths=policy.dependency_manifest_paths,
        budgets=budgets,
    )


async def _commit(
    database: Database,
    *,
    bundle: GateBundle,
    decision: GateDecisionKind,
    scope: ApprovedScope | None,
    findings: tuple[ScreenFinding, ...],
    proposal: GateProposal,
    now: datetime,
) -> GateOutcome:
    """Write the evidence window and the immutable decision."""
    blocker = findings[0].blocker if findings else None
    blocker_reason = _blocker_reason(findings) if findings else None
    window_id = uuid7()
    decision_id = uuid7()

    async with database.transaction() as conn:
        await _require_unmoved(conn, bundle=bundle)
        await _insert_evidence_window(conn, bundle=bundle, window_id=window_id)
        await _insert_decision(
            conn,
            bundle=bundle,
            decision_id=decision_id,
            decision=decision,
            blocker_reason=blocker_reason,
            scope=scope,
            window_id=window_id,
            proposal=proposal,
            now=now,
        )
        await append_fact(
            conn,
            client_id=bundle.client_id,
            event=EventAppend(
                message_id=uuid7(),
                stream=StreamRef(
                    kind=AggregateKind.PRODUCT_SIGNAL, id=bundle.product_signal_id
                ),
                event_type="implementation_gate_decided",
                payload=_event_payload(
                    decision_id=decision_id,
                    decision=decision,
                    scope=scope,
                    blocker=blocker,
                ),
                actor=_GATE_ACTOR,
                causation_id=None,
                correlation_id=bundle.product_signal_id,
                occurred_at=now,
            ),
            audit=AuditIntent(
                audit_id=uuid7(),
                subject=StreamRef(
                    kind=AggregateKind.PRODUCT_SIGNAL, id=bundle.product_signal_id
                ),
                action="implementation_gate_decision",
                outcome=decision.value,
                reason_code=blocker.value if blocker is not None else None,
                actor=_GATE_ACTOR,
                pinned_configuration_release_id=bundle.pinned_configuration_release_id,
                detail={
                    "gate_decision_id": str(decision_id),
                    "findings": [item.model_dump(mode="json") for item in findings],
                    "recommended_decision": proposal.recommended_decision,
                },
            ),
            jobs=(),
        )

    _log.info(
        "implementation_gate_decided",
        client_id=str(bundle.client_id),
        product_signal_id=str(bundle.product_signal_id),
        gate_decision_id=str(decision_id),
        decision=decision.value,
        blocker=blocker.value if blocker is not None else None,
        finding_count=len(findings),
        scope_digest=scope.digest() if scope is not None else None,
    )
    return GateOutcome(
        gate_decision_id=decision_id,
        decision=decision,
        approved_scope=scope,
        blocker=blocker,
        blocker_detail=blocker_reason,
        findings=findings,
        evidence_window_id=window_id,
    )


async def _require_unmoved(conn: AsyncConnection[DictRow], *, bundle: GateBundle) -> None:
    """Lock the Product Signal and refuse a decision whose case moved since its bundle."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select state::text as state, revision, classification_revision,
                   acceptance_criteria_revision, concern_revision
            from public.product_signals
            where id = %s
            for update
            """,
            (bundle.product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=bundle.product_signal_id)
    pinned: tuple[tuple[str, object, object], ...] = (
        ("state", as_str(row, "state"), bundle.product_signal_state.value),
        ("product_signal_revision", as_int(row, "revision"), bundle.product_signal_revision),
        (
            "classification_revision",
            as_int(row, "classification_revision"),
            bundle.classification_revision,
        ),
        (
            "acceptance_criteria_revision",
            as_int(row, "acceptance_criteria_revision"),
            bundle.acceptance_criteria_revision,
        ),
        ("concern_revision", as_int(row, "concern_revision"), bundle.concern_revision),
    )
    moved = [name for name, current, assembled in pinned if current != assembled]
    if moved:
        raise StaleContextError(
            trigger="implementation_gate_decision", reason=f"moved: {', '.join(moved)}"
        )


def _blocker_reason(findings: tuple[ScreenFinding, ...]) -> str:
    """Render every finding into the one stored blocker reason."""
    return "; ".join(
        f"{item.blocker.value}({item.subject}): {item.detail}" for item in findings
    )


def _event_payload(
    *,
    decision_id: UUID,
    decision: GateDecisionKind,
    scope: ApprovedScope | None,
    blocker: GateBlocker | None,
) -> dict[str, JsonValue]:
    return {
        "gate_decision_id": str(decision_id),
        "decision": decision.value,
        "scope_digest": scope.digest() if scope is not None else None,
        "blocker": blocker.value if blocker is not None else None,
    }


async def _insert_evidence_window(
    conn: AsyncConnection[DictRow], *, bundle: GateBundle, window_id: UUID
) -> None:
    """Record the admissible evidence range this one decision read.

    The evidence was stamped by the database's clock, so the window ends on that
    clock too: a worker clock even milliseconds behind it would end the window
    before its own evidence.
    """
    recorded = [item.recorded_at for item in bundle.confirmed_evidence]
    recorded.extend(item.recorded_at for item in bundle.telemetry_citations)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_windows (
                id, client_id, product_signal_id, window_kind, starts_at, ends_at
            )
            values (%s, %s, %s, %s, coalesce(%s, now()), now())
            """,
            (
                window_id,
                bundle.client_id,
                bundle.product_signal_id,
                _EVIDENCE_WINDOW_KIND,
                min(recorded) if recorded else None,
            ),
        )


async def _insert_decision(
    conn: AsyncConnection[DictRow],
    *,
    bundle: GateBundle,
    decision_id: UUID,
    decision: GateDecisionKind,
    blocker_reason: str | None,
    scope: ApprovedScope | None,
    window_id: UUID,
    proposal: GateProposal,
    now: datetime,
) -> None:
    """Insert the immutable decision row across its three scope columns."""
    columns = scope.to_columns() if scope is not None else None
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.gate_decisions (
                id, client_id, product_signal_id, decision, blocker_reason,
                classification_revision, acceptance_criteria_revision,
                pinned_configuration_release_id, evidence_window_id, approved_scope,
                required_checks, budgets, cited_evidence, decided_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id,
                bundle.client_id,
                bundle.product_signal_id,
                decision.value,
                blocker_reason,
                bundle.classification_revision,
                bundle.acceptance_criteria_revision,
                bundle.pinned_configuration_release_id,
                window_id,
                Jsonb(dict(columns.approved_scope) if columns is not None else {}),
                Jsonb(dict(columns.required_checks) if columns is not None else {"checks": []}),
                Jsonb(dict(columns.budgets) if columns is not None else {}),
                Jsonb(
                    {
                        "evidence_ids": sorted(proposal.cited_evidence_ids),
                        "code_finding_ids": sorted(proposal.cited_finding_ids),
                        "scope_digest": scope.digest() if scope is not None else None,
                    }
                ),
                now,
            ),
        )


