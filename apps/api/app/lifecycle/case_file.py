"""Case File assembly machine.

``draft -> verified -> delivery_resolved -> ready -> published``. Verification
checks structure, scope, redaction, citations, mandatory evidence, and outcome
consistency. Only a ``validated_implementation`` resolves a GitHub delivery.
Sealing computes the immutable core digest; publication retries reuse those exact
bytes and never edit a sealed core.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import CaseFileStage, CaseFileVariant

from app.lifecycle.machine import Machine, Transition

__all__ = ["CASE_FILE_MACHINE", "CaseFileFacts"]

MACHINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class CaseFileFacts:
    """Facts the Case File guards read."""

    variant: CaseFileVariant
    citations_verified: bool
    mandatory_evidence_present: bool
    delivery_identity_verified: bool
    core_digest_present: bool


def _may_verify(facts: CaseFileFacts) -> str | None:
    if not facts.citations_verified:
        return "every material claim must cite an authorized evidence slice"
    if not facts.mandatory_evidence_present:
        return "the variant's mandatory evidence is incomplete"
    return None


def _may_resolve_delivery(facts: CaseFileFacts) -> str | None:
    if facts.variant is not CaseFileVariant.VALIDATED_IMPLEMENTATION:
        return None
    if not facts.delivery_identity_verified:
        return "a validated implementation must carry its verified GitHub identities"
    return None


def _may_seal(facts: CaseFileFacts) -> str | None:
    if not facts.core_digest_present:
        return "sealing requires the computed immutable core digest"
    return None


CASE_FILE_MACHINE: Machine[CaseFileFacts] = Machine(
    name="case_file",
    version=MACHINE_VERSION,
    initial=CaseFileStage.DRAFT.value,
    terminal=frozenset({CaseFileStage.PUBLISHED.value}),
    transitions=(
        Transition(
            trigger="verify",
            sources=frozenset({CaseFileStage.DRAFT.value}),
            target=CaseFileStage.VERIFIED.value,
            guard=_may_verify,
        ),
        Transition(
            trigger="resolve_delivery",
            sources=frozenset({CaseFileStage.VERIFIED.value}),
            target=CaseFileStage.DELIVERY_RESOLVED.value,
            guard=_may_resolve_delivery,
        ),
        Transition(
            trigger="seal",
            sources=frozenset({CaseFileStage.DELIVERY_RESOLVED.value}),
            target=CaseFileStage.READY.value,
            guard=_may_seal,
        ),
        Transition(
            trigger="publish",
            sources=frozenset({CaseFileStage.READY.value}),
            target=CaseFileStage.PUBLISHED.value,
            guard=None,
        ),
    ),
)
