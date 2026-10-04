"""Drafting the Case File's prose, and checking every citation in code.

GPT-6.1 Sol drafts typed fields from the frozen, source-linked bundle. Application code
then checks citation presence and reference validity, exactly as
[Case File assembly](../../../../docs/arch-components/client-outputs.md#case-file-identity-and-assembly)
requires: a confidence score alone cannot establish that a claim is supported, so
nothing here trusts the model's own assurance that it cited something.

``deterministic_narrative`` is the other named source. It derives the same typed
fields from the durable records alone. Which source produced a Case File is
recorded in its metadata; assembly never silently swaps one for the other.

Transcript fragments carry ``{"field": ..., "summary": ...}``. The recognised
fields are ``expected_behavior``, ``actual_behavior``, ``impact``, ``workaround``
and ``excerpt``; the interview stream writes them, and a field with no fragment
becomes explicit uncertainty rather than an invented claim.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.connectors.openai_models import OpenAIResponsesGateway
from app.errors import GuardRejectedError
from app.logging import get_logger
from app.outputs.bundle import BundleEvidence, DraftingBundle
from app.outputs.sections import CitedStatement

__all__ = [
    "CaseFileDrafter",
    "DraftedClaim",
    "DraftedNarrative",
    "cited_statement",
    "deterministic_narrative",
    "verify_narrative",
]

_log = get_logger(__name__)

_MAX_OUTPUT_TOKENS: Final[int] = 16_000

_INSTRUCTIONS: Final[str] = (
    "You draft one uTrace Case File for a client product team. Write only from the "
    "supplied evidence slices. Every claim must name the evidence_record_id values "
    "that support it, and you may only name ids present in the supplied list. Never "
    "include a customer name, email address, Discord handle, transcript quotation, "
    "preview credential, telemetry identifier, or internal evaluation. If the "
    "evidence does not establish something, say so plainly instead of inferring it."
)


class DraftedClaim(BaseModel):
    """One drafted claim and the evidence identifiers it rests on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str
    evidence_record_ids: list[str]


class DraftedNarrative(BaseModel):
    """The typed prose fields a Case File needs, with their citations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    concern: DraftedClaim
    expected_behavior: DraftedClaim
    actual_behavior: DraftedClaim
    impact: DraftedClaim
    workaround: DraftedClaim | None
    interview_excerpts: list[DraftedClaim]
    blast_radius: DraftedClaim
    assumptions: list[str]
    follow_up_checks: list[str]
    next_action_detail: str
    coverage_limits: list[str]


def _claim(statement: str, sources: Sequence[BundleEvidence]) -> DraftedClaim:
    return DraftedClaim(
        statement=statement,
        evidence_record_ids=[str(item.evidence_record_id) for item in sources],
    )


def _fragments(bundle: DraftingBundle, field: str) -> tuple[BundleEvidence, ...]:
    """Return the transcript fragments the interview tagged with one field."""
    return tuple(
        item for item in bundle.transcript_fragments if item.body.get("field") == field
    )


def _from_fragments(
    bundle: DraftingBundle, *, field: str, absent: str
) -> DraftedClaim:
    """Build one claim from its tagged fragments, or state that it was not established."""
    fragments = _fragments(bundle, field)
    if not fragments:
        return _claim(absent, (bundle.source_message,))
    return _claim(
        " ".join(fragment.summary for fragment in fragments),
        fragments,
    )


def deterministic_narrative(bundle: DraftingBundle) -> DraftedNarrative:
    """Derive every prose field from the durable records, with no model involved."""
    workaround_fragments = _fragments(bundle, "workaround")
    observations = bundle.observations
    blast_radius_sources = (
        (bundle.source_message,) if not observations else observations[:3]
    )
    implementation = bundle.implementation
    if implementation is None:
        blast_radius = _claim(
            "No code changed for this Product Signal, so the delivered blast radius "
            "is none.",
            blast_radius_sources,
        )
        next_action = (
            "Assess the recorded blocker; no pull request was opened."
            if bundle.blocker_reason is not None
            else "Review the recorded gate decision; no implementation was attempted."
        )
    else:
        blast_radius = _claim(
            "The change touches "
            f"{len(implementation.files_touched)} file(s) in "
            f"{implementation.repository_ref} on top of {implementation.base_revision}.",
            blast_radius_sources,
        )
        next_action = (
            "Review and merge the draft pull request; it carries the exact validated "
            f"commit {implementation.source_commit}."
        )
    return DraftedNarrative(
        title=bundle.title_hint,
        concern=_claim(bundle.source_message.summary, (bundle.source_message,)),
        expected_behavior=_from_fragments(
            bundle,
            field="expected_behavior",
            absent="The interview did not establish an explicit expected behaviour.",
        ),
        actual_behavior=_from_fragments(
            bundle,
            field="actual_behavior",
            absent="The interview did not establish an explicit current behaviour.",
        ),
        impact=_from_fragments(
            bundle,
            field="impact",
            absent="The interview did not establish a quantified impact.",
        ),
        workaround=(
            None
            if not workaround_fragments
            else _claim(
                " ".join(item.summary for item in workaround_fragments),
                workaround_fragments,
            )
        ),
        interview_excerpts=[
            _claim(item.summary, (item,)) for item in _fragments(bundle, "excerpt")
        ],
        blast_radius=blast_radius,
        assumptions=[
            "The validated candidate and the delivered commit are the same source.",
            "The prepared dataset matches the agreed seeding contract.",
        ],
        follow_up_checks=[check.name for check in implementation.checks]
        if implementation is not None
        else [],
        next_action_detail=next_action,
        coverage_limits=[],
    )


class CaseFileDrafter:
    """Draft the Case File's typed fields with GPT-6.1 Sol over one frozen bundle."""

    def __init__(self, gateway: OpenAIResponsesGateway) -> None:
        self._gateway = gateway

    async def draft(self, bundle: DraftingBundle) -> DraftedNarrative:
        """Run one structured-output request and return the drafted narrative."""
        narrative = await self._gateway.respond_with_schema(
            instructions=_INSTRUCTIONS,
            input_messages=[{"role": "user", "content": _bundle_prompt(bundle)}],
            schema=DraftedNarrative,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        _log.info(
            "case_file_drafted",
            product_signal_id=str(bundle.product_signal_id),
            model=self._gateway.configuration.model_id,
        )
        return narrative


def _bundle_prompt(bundle: DraftingBundle) -> str:
    """Render the frozen bundle as the one input the drafter may draft from."""
    lines = [
        f"Product Signal: {bundle.product_signal_id}",
        f"Flow: {bundle.flow_type.value}",
        f"Case File variant: {bundle.variant.value}",
        "Validation outcome: "
        + (bundle.validation_outcome.value if bundle.validation_outcome else "none"),
        "",
        "Evidence slices you may cite:",
    ]
    for item in bundle.evidence:
        field = item.body.get("field")
        tag = f" field={field}" if isinstance(field, str) else ""
        lines.append(
            f"- {item.evidence_record_id} [{item.evidence_class.value}{tag}] "
            f"{item.summary}"
        )
    lines.append("")
    lines.append("Acceptance criteria and their recorded results:")
    for proof in bundle.proofs:
        lines.append(
            f"- {proof.criterion.statement} -> {proof.result} ({proof.reason})"
        )
    if bundle.blocker_reason is not None:
        lines.append("")
        lines.append(f"Recorded blocker: {bundle.blocker_reason}")
    return "\n".join(lines)


def cited_statement(claim: DraftedClaim, bundle: DraftingBundle) -> CitedStatement:
    """Convert one drafted claim into a Case File statement with real citations."""
    if not claim.evidence_record_ids:
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason=f"the claim '{claim.statement[:80]}' cites no evidence",
        )
    citations = tuple(
        bundle.by_id(_as_uuid(identifier)).citation()
        for identifier in claim.evidence_record_ids
    )
    return CitedStatement(statement=claim.statement, citations=citations)


def _as_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason=f"citation '{value}' is not an evidence identifier",
        ) from error


def verify_narrative(narrative: DraftedNarrative, bundle: DraftingBundle) -> None:
    """Refuse a narrative whose citations are absent, invalid, or out of bundle."""
    claims = [
        narrative.concern,
        narrative.expected_behavior,
        narrative.actual_behavior,
        narrative.impact,
        narrative.blast_radius,
        *narrative.interview_excerpts,
    ]
    if narrative.workaround is not None:
        claims.append(narrative.workaround)
    for claim in claims:
        cited_statement(claim, bundle)
    if not narrative.title.strip():
        raise GuardRejectedError(
            machine="case_file", trigger="verify", reason="the Case File has no title"
        )
    if not narrative.next_action_detail.strip():
        raise GuardRejectedError(
            machine="case_file",
            trigger="verify",
            reason="the Case File states no next action",
        )
