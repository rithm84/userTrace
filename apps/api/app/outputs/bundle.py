"""The frozen, source-linked bundle a Case File is drafted and assembled from.

Assembly reads durable rows once, into one immutable bundle, and everything after
that works from it: the deterministic assembler, GPT-6.1 Sol's drafting, and the citation
check all see the same source set. A citation that is not in the bundle's index
is not an authorized evidence slice, so it cannot reach a published Case File.

Two evidence-body conventions the implementation stream writes and this module
reads, alongside the criterion fields in ``app.validation.evidence``:

- ``check_result``: ``{"name": <check>, "outcome": "passed"|"failed"|"skipped"}``
- ``implementation_artifact``: ``{"files_touched": [...], "cleanup_state": <text>}``
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    EvidenceClaimState,
    EvidenceClass,
    FlowType,
    GateDecisionKind,
    ValidationOutcome,
)

from app.db.rows import as_datetime, as_int, as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.outputs.sections import CheckResult, Citation, opaque_ref
from app.validation.records import CriterionRow, load_criteria_on_connection

__all__ = [
    "BundleEvidence",
    "CriterionProofRow",
    "DraftingBundle",
    "GateRecord",
    "ImplementationRecord",
    "build_bundle",
]


class _Frozen(BaseModel):
    """Immutable, closed bundle shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class BundleEvidence(_Frozen):
    """One authorized evidence slice, with everything a citation needs."""

    evidence_record_id: UUID
    evidence_class: EvidenceClass
    claim_state: EvidenceClaimState
    source_ref: str
    summary: str
    recorded_at: datetime
    body: Mapping[str, object]

    def citation(self) -> Citation:
        """Return this slice as a Case File citation."""
        return Citation(
            evidence_record_id=self.evidence_record_id,
            evidence_class=self.evidence_class,
            source_ref=self.source_ref,
        )


class CriterionProofRow(_Frozen):
    """One recorded criterion-level proof, read back from the durable row."""

    criterion: CriterionRow
    result: str
    user_confirmed: bool
    supporting_evidence_record_ids: tuple[UUID, ...]
    contradicting_evidence_record_ids: tuple[UUID, ...]
    reason: str

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class GateRecord(_Frozen):
    """The Implementation Gate's frozen decision for this case."""

    gate_decision_id: UUID
    decision: GateDecisionKind
    blocker_reason: str | None
    decided_at: datetime
    approved_scope: tuple[str, ...]
    excluded_scope: tuple[str, ...]
    required_checks: tuple[str, ...]


class ImplementationRecord(_Frozen):
    """The exact identities of the attempt whose candidate was validated."""

    implementation_attempt_id: UUID
    repository_ref: str
    base_revision: str
    preview_version_id: UUID
    source_commit: str
    source_digest: str
    build_digest: str
    dataset_manifest_digest: str
    files_touched: tuple[str, ...]
    cleanup_state: str
    checks: tuple[CheckResult, ...]


class DraftingBundle(_Frozen):
    """Everything one Case File may be built from, frozen at assembly time."""

    client_id: UUID
    product_signal_id: UUID
    variant: CaseFileVariant
    title_hint: str
    flow_type: FlowType
    context_revision: int
    evidence_window_id: UUID
    originating_user_ref: str
    customer_account_ref: str
    workflow_ref: str
    source_message: BundleEvidence
    transcript_fragments: tuple[BundleEvidence, ...]
    observations: tuple[BundleEvidence, ...]
    evidence: tuple[BundleEvidence, ...]
    criteria: tuple[CriterionRow, ...]
    proofs: tuple[CriterionProofRow, ...]
    validation_attempt_id: UUID | None
    validation_outcome: ValidationOutcome | None
    implementation_attempt_ids: tuple[UUID, ...]
    gate: GateRecord | None
    implementation: ImplementationRecord | None
    blocker_reason: str | None

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    def citation_index(self) -> frozenset[UUID]:
        """Every evidence identifier a claim in this Case File may cite."""
        return frozenset(item.evidence_record_id for item in self.evidence)

    def by_id(self, evidence_record_id: UUID) -> BundleEvidence:
        """Return one authorized slice, refusing an identifier outside the bundle."""
        for item in self.evidence:
            if item.evidence_record_id == evidence_record_id:
                return item
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason=(
                f"citation {evidence_record_id} is not in this Case File's frozen "
                "evidence bundle"
            ),
        )

    def of_class(self, evidence_class: EvidenceClass) -> tuple[BundleEvidence, ...]:
        """Return the bundle's slices of one evidence class, in recorded order."""
        return tuple(
            item for item in self.evidence if item.evidence_class is evidence_class
        )


#: Where each evidence class keeps its readable text: intake keeps the reporter's
#: own words, the interview keeps the participant's statement, and every derived
#: class carries a summary.
_TEXT_FIELD: Mapping[EvidenceClass, str] = {
    EvidenceClass.INTAKE_SOURCE: "content",
    EvidenceClass.TRANSCRIPT_FRAGMENT: "statement",
}
_SUMMARY_FIELD = "summary"


def _text(evidence_class: EvidenceClass, body: Mapping[str, object]) -> str:
    """Read the readable text an evidence body of this class carries."""
    key = _TEXT_FIELD.get(evidence_class, _SUMMARY_FIELD)
    value = body.get(key)
    if not isinstance(value, str):
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason=f"a {evidence_class.value} evidence body carries no '{key}'",
        )
    return value


def _body(row: Mapping[str, object]) -> Mapping[str, object]:
    body = row["body"]
    if not isinstance(body, Mapping):
        raise TypeError("evidence body is not an object")
    return body


def _to_evidence(row: DictRow) -> BundleEvidence:
    body = _body(row)
    evidence_class = EvidenceClass(as_str(row, "evidence_class"))
    return BundleEvidence(
        evidence_record_id=as_uuid(row, "id"),
        evidence_class=evidence_class,
        claim_state=EvidenceClaimState(as_str(row, "claim_state")),
        source_ref=as_str(row, "source_ref"),
        summary=_text(evidence_class, body),
        recorded_at=as_datetime(row, "recorded_at"),
        body=body,
    )


async def _load_evidence(
    conn: AsyncConnection[DictRow], *, client_id: UUID, product_signal_id: UUID
) -> tuple[BundleEvidence, ...]:
    """Read every non-withdrawn, non-superseded evidence slice for one case."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select records.id, records.evidence_class, records.claim_state,
                   records.source_ref, records.body, records.recorded_at
            from public.evidence_records as records
            where records.client_id = %s
              and records.product_signal_id = %s
              and records.claim_state <> %s
              and not exists (
                select 1 from public.evidence_records as newer
                where newer.supersedes_id = records.id
              )
            order by records.recorded_at, records.id
            """,
            (client_id, product_signal_id, EvidenceClaimState.WITHDRAWN.value),
        )
        rows = list(await cur.fetchall())
    return tuple(_to_evidence(row) for row in rows)


async def _load_attempt_ids(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> tuple[UUID, ...]:
    """Return the case's Implementation Attempt; the demo grants at most one."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.implementation_attempts
            where product_signal_id = %s order by created_at
            """,
            (product_signal_id,),
        )
        rows = list(await cur.fetchall())
    return tuple(as_uuid(row, "id") for row in rows)


async def _load_gate(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID
) -> GateRecord | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id, decision, blocker_reason, decided_at, approved_scope,
                   required_checks
            from public.gate_decisions
            where product_signal_id = %s
            order by decided_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    decision = GateDecisionKind(as_str(row, "decision"))
    approved = decision is GateDecisionKind.APPROVED_LIVE
    scope = _ApprovedScope.model_validate(row["approved_scope"]) if approved else None
    checks = _RequiredChecks.model_validate(row["required_checks"])
    return GateRecord(
        gate_decision_id=as_uuid(row, "id"),
        decision=decision,
        blocker_reason=as_optional_str(row, "blocker_reason"),
        decided_at=as_datetime(row, "decided_at"),
        approved_scope=() if scope is None else tuple(scope.in_scope_changes),
        excluded_scope=() if scope is None else tuple(scope.excluded_changes),
        required_checks=tuple(check.command for check in checks.checks),
    )


class _ApprovedScope(BaseModel):
    """The parts of an approved gate scope the Case File states."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    in_scope_changes: list[str]
    excluded_changes: list[str]


class _RequiredCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    command: str


class _RequiredChecks(BaseModel):
    """The checks the gate pinned; a declined case pins none."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    checks: list[_RequiredCheck]


#: What an attempt's newest lease says about its workspace, as the Case File states it.
_CLEANUP_BY_LEASE: Mapping[str, str] = {
    "released": "workspace destroyed",
    "active": "workspace still running",
    "fenced": "workspace superseded by a later run",
    "expired": "workspace lease expired without release",
}


async def _load_implementation(
    conn: AsyncConnection[DictRow], *, validation_attempt_id: UUID
) -> ImplementationRecord:
    """The validated candidate's identities, changed files, checks, and cleanup.

    Each comes from the record that owns it: the files from the frozen Preview
    Version's source manifest, the checks from the attempt's candidate check runs
    (the last cycle of each), and cleanup from the attempt's newest lease.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select attempts.dataset_manifest_digest,
                   implementations.id as implementation_attempt_id,
                   implementations.repository_ref, implementations.base_revision,
                   versions.id as preview_version_id, versions.source_commit,
                   versions.source_digest, versions.build_digest, versions.source_manifest,
                   (select leases.state::text from public.environment_leases as leases
                     where leases.implementation_attempt_id = implementations.id
                     order by leases.created_at desc limit 1) as lease_state
            from public.validation_attempts as attempts
            join public.implementation_attempts as implementations
              on implementations.id = attempts.implementation_attempt_id
            join public.preview_versions as versions
              on versions.id = attempts.preview_version_id
            where attempts.id = %s
            """,
            (validation_attempt_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise AggregateNotFoundError(
                table="validation_attempts", row_id=validation_attempt_id
            )
        await cur.execute(
            """
            select distinct on (check_name) check_name, exit_code
            from public.implementation_check_runs
            where implementation_attempt_id = %s and phase = 'candidate'
            order by check_name, cycle_index desc, created_at desc
            """,
            (as_uuid(row, "implementation_attempt_id"),),
        )
        runs = await cur.fetchall()
    manifest = row["source_manifest"]
    if not isinstance(manifest, Mapping):
        raise TypeError("the validated Preview Version carries no frozen source manifest")
    lease_state = as_optional_str(row, "lease_state")
    if lease_state is None:
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason="the validated candidate's Implementation Attempt holds no environment lease",
        )
    return ImplementationRecord(
        implementation_attempt_id=as_uuid(row, "implementation_attempt_id"),
        repository_ref=as_str(row, "repository_ref"),
        base_revision=as_str(row, "base_revision"),
        preview_version_id=as_uuid(row, "preview_version_id"),
        source_commit=as_str(row, "source_commit"),
        source_digest=as_str(row, "source_digest"),
        build_digest=as_str(row, "build_digest"),
        dataset_manifest_digest=as_str(row, "dataset_manifest_digest"),
        files_touched=tuple(sorted(str(path) for path in manifest)),
        cleanup_state=_CLEANUP_BY_LEASE[lease_state],
        checks=tuple(
            CheckResult(
                name=as_str(run, "check_name"),
                outcome="passed" if as_int(run, "exit_code") == 0 else "failed",
            )
            for run in runs
        ),
    )


async def _load_proofs(
    conn: AsyncConnection[DictRow],
    *,
    validation_attempt_id: UUID,
    criteria: Sequence[CriterionRow],
) -> tuple[CriterionProofRow, ...]:
    by_id = {criterion.id: criterion for criterion in criteria}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select validation_criterion_id, result, user_confirmed,
                   evidence_record_ids, contradicting_evidence_record_ids, reason
            from public.validation_outcomes
            where validation_attempt_id = %s
            """,
            (validation_attempt_id,),
        )
        rows = list(await cur.fetchall())
    proofs: list[CriterionProofRow] = []
    for row in rows:
        criterion = by_id.get(as_uuid(row, "validation_criterion_id"))
        if criterion is None:
            continue
        proofs.append(
            CriterionProofRow(
                criterion=criterion,
                result=as_str(row, "result"),
                user_confirmed=bool(row["user_confirmed"]),
                supporting_evidence_record_ids=_uuid_list(row["evidence_record_ids"]),
                contradicting_evidence_record_ids=_uuid_list(
                    row["contradicting_evidence_record_ids"]
                ),
                reason=as_str(row, "reason"),
            )
        )
    proofs.sort(key=lambda proof: proof.criterion.ordinal)
    return tuple(proofs)


def _uuid_list(value: object) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise TypeError("expected a uuid array column")
    return tuple(item if isinstance(item, UUID) else UUID(str(item)) for item in value)


async def build_bundle(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    variant: CaseFileVariant,
    evidence_window_id: UUID,
    validation_attempt_id: UUID | None,
    blocker_reason: str | None,
) -> DraftingBundle:
    """Read every durable record this Case File may cite, once, into one bundle."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select signals.initial_flow_type, signals.established_flow_type,
                   signals.concern_revision, signals.acceptance_criteria_revision,
                   signals.originating_client_user_id,
                   signals.originating_customer_account_id
            from public.product_signals as signals
            where signals.id = %s and signals.client_id = %s
            """,
            (product_signal_id, client_id),
        )
        signal = await cur.fetchone()
    if signal is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)

    evidence = await _load_evidence(
        conn, client_id=client_id, product_signal_id=product_signal_id
    )
    source_messages = [
        item for item in evidence if item.evidence_class is EvidenceClass.INTAKE_SOURCE
    ]
    if not source_messages:
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason="the case has no intake source evidence to cite as its source message",
        )

    criteria = await load_criteria_on_connection(
        conn,
        product_signal_id=product_signal_id,
        criteria_revision=as_int(signal, "acceptance_criteria_revision"),
    )
    proofs: tuple[CriterionProofRow, ...] = ()
    outcome: ValidationOutcome | None = None
    implementation: ImplementationRecord | None = None
    workflow_ref = ""
    if validation_attempt_id is not None:
        async with conn.cursor() as cur:
            await cur.execute(
                "select outcome, workflow_ref from public.validation_attempts where id = %s",
                (validation_attempt_id,),
            )
            attempt = await cur.fetchone()
        if attempt is None:
            raise AggregateNotFoundError(
                table="validation_attempts", row_id=validation_attempt_id
            )
        recorded = as_optional_str(attempt, "outcome")
        outcome = None if recorded is None else ValidationOutcome(recorded)
        workflow_ref = as_str(attempt, "workflow_ref")
        proofs = await _load_proofs(
            conn, validation_attempt_id=validation_attempt_id, criteria=criteria
        )
        if variant is CaseFileVariant.VALIDATED_IMPLEMENTATION:
            implementation = await _load_implementation(
                conn, validation_attempt_id=validation_attempt_id
            )

    established = as_optional_str(signal, "established_flow_type")
    flow_type = FlowType(established or as_str(signal, "initial_flow_type"))
    return DraftingBundle(
        client_id=client_id,
        product_signal_id=product_signal_id,
        variant=variant,
        title_hint=source_messages[0].summary,
        flow_type=flow_type,
        context_revision=as_int(signal, "concern_revision"),
        evidence_window_id=evidence_window_id,
        originating_user_ref=opaque_ref(
            kind="client_user", identifier=as_uuid(signal, "originating_client_user_id")
        ),
        customer_account_ref=opaque_ref(
            kind="customer_account",
            identifier=as_uuid(signal, "originating_customer_account_id"),
        ),
        workflow_ref=workflow_ref,
        source_message=source_messages[0],
        transcript_fragments=tuple(
            item
            for item in evidence
            if item.evidence_class is EvidenceClass.TRANSCRIPT_FRAGMENT
        ),
        observations=tuple(
            item
            for item in evidence
            if item.evidence_class
            in {
                EvidenceClass.BROWSER_OBSERVATION,
                EvidenceClass.SERVER_SPAN,
                EvidenceClass.VALIDATION_OBSERVATION,
            }
        ),
        evidence=evidence,
        criteria=criteria,
        proofs=proofs,
        validation_attempt_id=validation_attempt_id,
        validation_outcome=outcome,
        implementation_attempt_ids=await _load_attempt_ids(
            conn, product_signal_id=product_signal_id
        ),
        gate=await _load_gate(conn, product_signal_id=product_signal_id),
        implementation=implementation,
        blocker_reason=blocker_reason,
    )
