"""Job handlers for Case File assembly, delivery, and closure.

``assemble_case_file`` runs the whole ordered flow for one case: freeze the
bundle, draft or derive the narrative, verify, resolve delivery, seal, publish,
and close the Product Signal. It is idempotent by stage, so a retry resumes where
the previous run committed rather than starting a second Case File.

Every run opens the GPT-6.1 Sol drafter that writes the Case File's prose, and a
``validated_implementation`` also opens the GitHub App connector for the case's
authorized repository, which delivers the draft PR.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from utrace_contracts.domain.lifecycle import CaseFileStage, CaseFileVariant, DeliveryState

from app.connectors.github_delivery import (
    FilePatch,
    github_app_from_environment,
    idempotency_marker,
)
from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.db.pool import Database
from app.db.rows import as_datetime
from app.errors import AggregateNotFoundError, GuardRejectedError
from app.execution.jobs import ClaimedJob
from app.gate.policy import read_implementation_policy
from app.ids import uuid7
from app.logging import get_logger
from app.offers.entitlement import CaseStopReason, evaluate_entitlement
from app.outputs.assembly import (
    existing_case_file_id,
    forbidden_identifiers,
    load_case_file_row,
    pinned_dashboard_base_url,
    publish_and_close,
    reserve_case_file,
    resolve_delivery_stage,
    seal_case_file,
    verify_case_file,
)
from app.outputs.bundle import DraftingBundle, build_bundle
from app.outputs.closure_jobs import NARRATIVE_SOURCE
from app.outputs.core_builder import build_core, verify_core
from app.outputs.delivery import (
    DeliveryRepositoryPort,
    DeliveryRow,
    delivery_branch,
    load_delivery,
    reserve_delivery,
)
from app.outputs.delivery_runner import DeliveryRequest, run_delivery
from app.outputs.drafting import (
    CaseFileDrafter,
    DraftedNarrative,
    deterministic_narrative,
    verify_narrative,
)
from app.outputs.sections import CaseFileCore
from app.preview.artifacts import load_artifact
from app.validation.evidence import (
    BeforeAfterComparison,
    DurableEvidenceQuery,
    EvidenceQueryPort,
)
from app.validation.records import load_budgets

__all__ = [
    "CLOSE_CASE_AFTER_STOP",
    "AssemblyResult",
    "assemble",
    "assemble_case_file",
    "close_case_after_stop",
]

_log = get_logger(__name__)

#: Git's mode for a regular, non-executable file.
_REGULAR_FILE = "100644"
_GITHUB_TIMEOUT_SECONDS = 30.0

CLOSE_CASE_AFTER_STOP = "control_plane.close_case_after_stop"
"""The kernel's job type for a client Stop that happened after participation."""

type JobHandler = Callable[[Database, ClaimedJob], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    """What one assembly run committed."""

    case_file_id: UUID
    variant: CaseFileVariant
    stage: CaseFileStage
    core_digest: str | None
    delivery_id: UUID | None
    delivery_state: DeliveryState | None


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return UUID(str(value))


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


async def delivery_patches(
    database: Database, *, preview_version_id: UUID
) -> tuple[FilePatch, ...]:
    """The exact files the validated Preview Version installs over its base.

    Delivery commits what the originating user validated: the Preview Version's
    frozen artifact, each file rechecked against its digest. It never re-derives
    a patch against a moving branch.
    """
    artifact = await load_artifact(database, preview_version_id=preview_version_id)
    patches: list[FilePatch] = []
    for candidate in artifact.files:
        content = candidate.content.encode("utf-8")
        if f"sha256:{hashlib.sha256(content).hexdigest()}" != candidate.digest:
            raise GuardRejectedError(
                machine="delivery",
                trigger="create",
                reason=(
                    f"{candidate.path} in Preview Version {preview_version_id} does not "
                    f"match its frozen digest {candidate.digest}"
                ),
            )
        patches.append(FilePatch(path=candidate.path, content=content, mode=_REGULAR_FILE))
    return tuple(patches)


async def _freeze_window(
    database: Database, evidence: EvidenceQueryPort, *, product_signal_id: UUID
) -> UUID:
    """Freeze one immutable evidence window for this Case File."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select client_id, created_at from public.product_signals where id = %s",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    window = await evidence.freeze_window(
        client_id=UUID(str(row["client_id"])),
        product_signal_id=product_signal_id,
        interview_session_id=None,
        purpose="case_file",
        starts_at=as_datetime(row, "created_at"),
        ends_at=await database.now(),
    )
    return window.evidence_window_id


async def _comparison(
    evidence: EvidenceQueryPort, bundle: DraftingBundle
) -> BeforeAfterComparison | None:
    """Read the agreed workflow's base and candidate observations."""
    if bundle.implementation is None or not bundle.workflow_ref:
        return None
    return await evidence.before_after_comparison(
        client_id=bundle.client_id,
        evidence_window_id=bundle.evidence_window_id,
        workflow_ref=bundle.workflow_ref,
        base_preview_version_id=None,
        candidate_preview_version_id=bundle.implementation.preview_version_id,
    )


async def _narrative(
    bundle: DraftingBundle, *, narrative_source: str, drafter: CaseFileDrafter | None
) -> DraftedNarrative:
    """Produce the typed prose fields from the named source."""
    if narrative_source == NARRATIVE_SOURCE:
        if drafter is None:
            raise ValueError("a model-drafted Case File needs a drafter; none was supplied")
        narrative = await drafter.draft(bundle)
    elif narrative_source == "evidence_derived":
        narrative = deterministic_narrative(bundle)
    else:
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason=f"unknown narrative source '{narrative_source}'",
        )
    verify_narrative(narrative, bundle)
    return narrative


async def _reserve_identity(
    database: Database, *, product_signal_id: UUID, variant: CaseFileVariant
) -> UUID:
    """Reserve the Case File identity and its stable authenticated URL."""
    case_file_id = uuid7()
    async with database.transaction() as conn:
        existing = await existing_case_file_id(conn, product_signal_id=product_signal_id)
        if existing is not None:
            return existing
        budgets = await load_budgets(conn, product_signal_id=product_signal_id)
        base = await pinned_dashboard_base_url(
            conn, configuration_release_id=budgets.pinned_configuration_release_id
        )
        await reserve_case_file(
            conn,
            case_file_id=case_file_id,
            client_id=budgets.client_id,
            product_signal_id=product_signal_id,
            variant=variant,
            authenticated_url=(
                f"{base}/clients/{budgets.client_id}/case-files/{case_file_id}"
            ),
        )
    return case_file_id


async def _deliver(
    database: Database,
    repository: DeliveryRepositoryPort | None,
    *,
    case_file_id: UUID,
    bundle: DraftingBundle,
    core: CaseFileCore,
    narrative: DraftedNarrative,
) -> tuple[DeliveryRow | None, str | None]:
    """Run the delivery protocol, returning its row and any blocker reason."""
    implementation = bundle.implementation
    if implementation is None or bundle.validation_attempt_id is None:
        raise GuardRejectedError(
            machine="case_file",
            trigger="resolve_delivery",
            reason="a validated implementation must carry its attempt and candidate",
        )
    if repository is None:
        raise ValueError(
            "a validated implementation delivers a draft PR, so it needs the authorized "
            "repository connector; none was supplied"
        )
    async with database.transaction() as conn:
        case_file = await load_case_file_row(conn, case_file_id=case_file_id)
        if case_file.delivery_id is not None:
            delivery_id = case_file.delivery_id
        else:
            delivery_id = uuid7()
            command_id = uuid7()
            await reserve_delivery(
                conn,
                delivery_id=delivery_id,
                client_id=bundle.client_id,
                product_signal_id=bundle.product_signal_id,
                case_file_id=case_file_id,
                validation_attempt_id=bundle.validation_attempt_id,
                command_id=command_id,
                repository_ref=implementation.repository_ref,
                branch_ref=delivery_branch(product_signal_id=bundle.product_signal_id),
                base_branch=await repository.read_default_branch(),
                head_commit=implementation.source_commit,
                base_revision=implementation.base_revision,
                idempotency_marker=idempotency_marker(command_id=str(command_id)),
                case_file_url=case_file.authenticated_url or "",
            )
    outcome = await run_delivery(
        database,
        repository,
        request=DeliveryRequest(
            delivery_id=delivery_id,
            title=narrative.title,
            problem_summary=core.summary.concern.statement,
            implementation_summary=core.risk.blast_radius.statement,
            risks=tuple(narrative.assumptions) + tuple(narrative.follow_up_checks),
            implementation=implementation,
            patches=await delivery_patches(
                database, preview_version_id=implementation.preview_version_id
            ),
        ),
    )
    async with database.connection() as conn:
        row = await load_delivery(conn, delivery_id=delivery_id)
    if outcome.state is DeliveryState.DELIVERED:
        return row, None
    return row, outcome.blocker_reason


async def assemble(
    database: Database,
    evidence: EvidenceQueryPort,
    *,
    product_signal_id: UUID,
    variant: CaseFileVariant,
    validation_attempt_id: UUID | None,
    blocker_reason: str | None,
    closure_trigger: str,
    closure_reason: str,
    narrative_source: str,
    drafter: CaseFileDrafter | None,
    repository: DeliveryRepositoryPort | None,
) -> AssemblyResult:
    """Assemble, seal, publish, and close one case's Case File."""
    case_file_id = await _reserve_identity(
        database, product_signal_id=product_signal_id, variant=variant
    )
    async with database.connection() as conn:
        current = await load_case_file_row(conn, case_file_id=case_file_id)
    if current.stage is CaseFileStage.PUBLISHED:
        return AssemblyResult(
            case_file_id=case_file_id,
            variant=current.variant,
            stage=current.stage,
            core_digest=current.core_digest,
            delivery_id=current.delivery_id,
            delivery_state=None,
        )

    window_id = await _freeze_window(
        database, evidence, product_signal_id=product_signal_id
    )
    async with database.connection() as conn:
        bundle = await build_bundle(
            conn,
            client_id=current.client_id,
            product_signal_id=product_signal_id,
            variant=variant,
            evidence_window_id=window_id,
            validation_attempt_id=validation_attempt_id,
            blocker_reason=blocker_reason,
        )
        forbidden = await forbidden_identifiers(
            conn, product_signal_id=product_signal_id
        )
    narrative = await _narrative(
        bundle, narrative_source=narrative_source, drafter=drafter
    )
    comparison = await _comparison(evidence, bundle)
    core = build_core(
        bundle=bundle,
        narrative=narrative,
        comparison=comparison,
        delivery=None,
        narrative_source=narrative_source,
    )
    verify_core(core, bundle=bundle, forbidden_identifiers=forbidden)

    if current.stage is CaseFileStage.DRAFT:
        async with database.transaction() as conn:
            await verify_case_file(
                conn,
                case_file_id=case_file_id,
                core=core,
                narrative_source=narrative_source,
                blocker_reason=blocker_reason,
            )

    delivery: DeliveryRow | None = None
    delivery_blocker: str | None = None
    final_variant = variant
    final_trigger = closure_trigger
    final_reason = closure_reason
    if variant is CaseFileVariant.VALIDATED_IMPLEMENTATION:
        delivery, delivery_blocker = await _deliver(
            database,
            repository,
            case_file_id=case_file_id,
            bundle=bundle,
            core=core,
            narrative=narrative,
        )
        if delivery_blocker is not None:
            final_variant = CaseFileVariant.BLOCKED
            final_trigger = "close_blocked"
            final_reason = delivery_blocker
            async with database.connection() as conn:
                bundle = await build_bundle(
                    conn,
                    client_id=current.client_id,
                    product_signal_id=product_signal_id,
                    variant=final_variant,
                    evidence_window_id=window_id,
                    validation_attempt_id=validation_attempt_id,
                    blocker_reason=delivery_blocker,
                )
            core = build_core(
                bundle=bundle,
                narrative=narrative,
                comparison=comparison,
                delivery=delivery,
                narrative_source=narrative_source,
            )
        else:
            core = build_core(
                bundle=bundle,
                narrative=narrative,
                comparison=comparison,
                delivery=delivery,
                narrative_source=narrative_source,
            )
        verify_core(core, bundle=bundle, forbidden_identifiers=forbidden)

    async with database.transaction() as conn:
        case_file = await load_case_file_row(conn, case_file_id=case_file_id)
        if case_file.stage is CaseFileStage.VERIFIED:
            await resolve_delivery_stage(
                conn,
                case_file_id=case_file_id,
                delivery_id=None if delivery is None else delivery.id,
                delivery_identity_verified=delivery_blocker is None
                and delivery is not None,
                core=core,
                blocker_reason=delivery_blocker or blocker_reason,
            )
    async with database.transaction() as conn:
        case_file = await load_case_file_row(conn, case_file_id=case_file_id)
        if case_file.stage is CaseFileStage.DELIVERY_RESOLVED:
            await seal_case_file(conn, case_file_id=case_file_id, core=core)
    digest = await publish_and_close(
        database,
        case_file_id=case_file_id,
        closure_trigger=final_trigger,
        closure_reason=final_reason,
    )
    _log.info(
        "case_file_assembled",
        product_signal_id=str(product_signal_id),
        case_file_id=str(case_file_id),
        variant=final_variant.value,
        narrative_source=narrative_source,
        core_digest=digest,
        delivery_id=None if delivery is None else str(delivery.id),
    )
    return AssemblyResult(
        case_file_id=case_file_id,
        variant=final_variant,
        stage=CaseFileStage.PUBLISHED,
        core_digest=digest,
        delivery_id=None if delivery is None else delivery.id,
        delivery_state=None if delivery is None else delivery.state,
    )


async def _run_from_job(
    database: Database,
    claimed: ClaimedJob,
    *,
    drafter: CaseFileDrafter | None,
    repository: DeliveryRepositoryPort | None,
) -> None:
    """Assemble one Case File from a claimed Job's frozen input."""
    await assemble(
        database,
        DurableEvidenceQuery(database),
        product_signal_id=UUID(str(claimed.input["product_signal_id"])),
        variant=CaseFileVariant(str(claimed.input["variant"])),
        validation_attempt_id=_optional_uuid(claimed.input["validation_attempt_id"]),
        blocker_reason=_optional_text(claimed.input["blocker_reason"]),
        closure_trigger=str(claimed.input["closure_trigger"]),
        closure_reason=str(claimed.input["closure_reason"]),
        narrative_source=str(claimed.input["narrative_source"]),
        drafter=drafter,
        repository=repository,
    )


async def assemble_case_file(database: Database, claimed: ClaimedJob) -> None:
    """Assemble a Case File with GPT-6.1 Sol drafting its prose.

    A validated implementation also delivers its draft PR, so only that variant
    opens the GitHub App connector for the case's authorized repository.
    """
    variant = CaseFileVariant(str(claimed.input["variant"]))
    async with openai_gateway_for(OpenAIRole.CASE_FILE_SYNTHESIS) as gateway:
        drafter = CaseFileDrafter(gateway)
        if variant is not CaseFileVariant.VALIDATED_IMPLEMENTATION:
            await _run_from_job(database, claimed, drafter=drafter, repository=None)
            return
        repository_ref = await _authorized_repository(
            database, product_signal_id=UUID(str(claimed.input["product_signal_id"]))
        )
        async with github_app_from_environment(
            repository_ref=repository_ref, request_timeout_seconds=_GITHUB_TIMEOUT_SECONDS
        ) as github:
            await _run_from_job(database, claimed, drafter=drafter, repository=github)


async def close_case_after_stop(database: Database, claimed: ClaimedJob) -> None:
    """Publish the blocker Case File a client Stop after participation obliges."""
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    reason = str(claimed.input["reason"])
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.validation_attempts
            where product_signal_id = %s and state = 'completed'
            order by completed_at desc limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    async with openai_gateway_for(OpenAIRole.CASE_FILE_SYNTHESIS) as gateway:
        await assemble(
            database,
            DurableEvidenceQuery(database),
            product_signal_id=product_signal_id,
            variant=CaseFileVariant.BLOCKED,
            validation_attempt_id=None if row is None else UUID(str(row["id"])),
            blocker_reason=f"the client stopped this case after participation: {reason}",
            closure_trigger="client_stop_after_participation",
            closure_reason="client_stop_after_participation",
            narrative_source=NARRATIVE_SOURCE,
            drafter=CaseFileDrafter(gateway),
            repository=None,
        )
    await evaluate_entitlement(
        database,
        product_signal_id=product_signal_id,
        stop_reason=CaseStopReason.CLIENT_STOP,
    )


async def _authorized_repository(database: Database, *, product_signal_id: UUID) -> str:
    """Return the `owner/name` the Product Signal's pinned policy authorizes."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select releases.id, releases.policy
            from public.product_signals as signals
            join public.configuration_releases as releases
              on releases.id = signals.pinned_configuration_release_id
            where signals.id = %s
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    document = row["policy"]
    if not isinstance(document, Mapping):
        raise TypeError("configuration_releases.policy is not a JSON object")
    return read_implementation_policy(
        configuration_release_id=UUID(str(row["id"])), policy=document
    ).repository_ref
