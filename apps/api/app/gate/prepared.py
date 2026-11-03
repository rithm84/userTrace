"""The Implementation Gate's evaluation, prepared while the agent proposes a change.

Contract: docs/arch-components/implementation.md#prepared-draft

The gate's proposal model reads the case's whole bundle, and its answer cites only
evidence and code findings, never criterion identifiers. So while the voice agent
proposes a change, the bundle the agreement would produce can be predicted from
today's rows with the proposed criteria in place of the agreed ones, the flow
classification confirmed, and the proposal asked for over that prediction.

Nothing here decides. The prediction is recorded on the proposal's prepared draft
with a digest of every input the decision depends on (`decision_inputs_digest`).
Once the participant agrees, the gate assembles the real bundle as it always does;
only when the agreement records that draft and the two digests are equal does it
reuse the prepared proposal instead of asking again, and the deterministic
controller then validates and commits it exactly as it would a fresh one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from utrace_contracts.canonical import payload_digest
from utrace_contracts.domain.lifecycle import ProductSignalState

from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.gate.bundle import GateBundle, assemble_gate_bundle
from app.gate.classification import establish_flow_classification
from app.gate.proposal import GateProposal, request_gate_proposal
from app.gate.scope import AcceptanceCriterion
from app.interview.settings import get_jev_settings

__all__ = [
    "PREPARED_PROPOSAL_MAX_OUTPUT_TOKENS",
    "decision_inputs_digest",
    "predicted_bundle",
    "prepare_gate_proposal",
    "prepared_gate_proposal",
]

_log = structlog.get_logger(__name__)

PREPARED_PROPOSAL_MAX_OUTPUT_TOKENS: Final[int] = 16_000


def decision_inputs_digest(bundle: GateBundle) -> str:
    """One digest over everything a gate decision depends on, and nothing that moves.

    Revisions, the case's state, and when the bundle was assembled change between a
    proposal and its agreement without changing what the gate decides on; the
    criteria count by their text, so a predicted bundle's stand-in identifiers do not
    matter.
    """
    return "sha256:" + payload_digest(
        {
            "pinned_configuration_release_id": str(bundle.pinned_configuration_release_id),
            "policy": bundle.policy.model_dump(mode="json"),
            "initial_flow_type": bundle.initial_flow_type.value,
            "established_flow_type": (
                None if bundle.established_flow_type is None else bundle.established_flow_type.value
            ),
            "criteria": [
                [criterion.ordinal, criterion.statement, criterion.observability]
                for criterion in bundle.acceptance_criteria
            ],
            "confirmed_evidence": [
                [str(item.evidence_id), item.claim_state, item.summary]
                for item in bundle.confirmed_evidence
            ],
            "telemetry_citations": [str(item.evidence_id) for item in bundle.telemetry_citations],
            "code_findings": [
                [str(item.finding_id), item.path, item.first_line, item.last_line, item.excerpt_digest]
                for item in bundle.code_findings
            ],
            "readiness": bundle.readiness.model_dump(mode="json"),
            "implementation_count": bundle.implementation_count,
        }
    )


def predicted_bundle(
    bundle: GateBundle, *, criteria: tuple[tuple[str, str], ...]
) -> GateBundle:
    """The bundle an agreement to these criteria would leave the gate with."""
    return bundle.model_copy(
        update={
            "product_signal_state": ProductSignalState.GATE_PENDING,
            "acceptance_criteria_revision": bundle.acceptance_criteria_revision + 1,
            "concern_revision": bundle.concern_revision + 1,
            "acceptance_criteria": tuple(
                AcceptanceCriterion(
                    criterion_id=f"proposed-{ordinal + 1}",
                    ordinal=ordinal,
                    statement=statement,
                    observability=observability,
                )
                for ordinal, (statement, observability) in enumerate(criteria)
            ),
        }
    )


async def prepare_gate_proposal(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    prepared_draft_id: UUID,
    criteria: tuple[tuple[str, str], ...],
) -> None:
    """Confirm the flow, then ask for the gate's proposal over the predicted bundle."""
    jev_settings = get_jev_settings()
    async with JevClient(
        api_key=jev_settings.api_key,
        model_id=jev_settings.model_id,
        timeout_seconds=jev_settings.timeout_seconds,
    ) as jev:
        await establish_flow_classification(
            database, jev, client_id=client_id, product_signal_id=product_signal_id
        )
    bundle = predicted_bundle(
        await assemble_gate_bundle(
            database,
            client_id=client_id,
            product_signal_id=product_signal_id,
            now=datetime.now(tz=UTC),
        ),
        criteria=criteria,
    )
    async with openai_gateway_for(OpenAIRole.GATE_PROPOSAL) as gateway:
        proposal = await request_gate_proposal(
            gateway, bundle=bundle, max_output_tokens=PREPARED_PROPOSAL_MAX_OUTPUT_TOKENS
        )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_drafts
            set gate_proposal = %s, gate_inputs_digest = %s, updated_at = now()
            where id = %s
            """,
            (
                Jsonb(proposal.model_dump(mode="json")),
                decision_inputs_digest(bundle),
                prepared_draft_id,
            ),
        )
    _log.info(
        "gate.proposal_prepared",
        product_signal_id=str(product_signal_id),
        prepared_draft_id=str(prepared_draft_id),
        recommended=proposal.recommended_decision,
        blocker=None if proposal.blocker is None else proposal.blocker.value,
        blocker_detail=proposal.blocker_detail,
    )


async def prepared_gate_proposal(database: Database, *, bundle: GateBundle) -> GateProposal | None:
    """The proposal prepared for exactly this agreement, when nothing it read has changed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select drafts.gate_proposal, drafts.gate_inputs_digest
            from public.acceptance_criteria_agreements as agreements
            join public.prepared_drafts as drafts on drafts.id = agreements.prepared_draft_id
            where agreements.product_signal_id = %s
              and agreements.acceptance_criteria_revision = %s
            """,
            (bundle.product_signal_id, bundle.acceptance_criteria_revision),
        )
        row = await cur.fetchone()
    if row is None or not isinstance(row["gate_proposal"], dict):
        return None
    if row["gate_inputs_digest"] != decision_inputs_digest(bundle):
        _log.info(
            "gate.prepared_proposal_stale",
            product_signal_id=str(bundle.product_signal_id),
        )
        return None
    return GateProposal.model_validate(row["gate_proposal"])

