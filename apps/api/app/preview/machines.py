"""Preview Runtime Allocation and Preview Version machines.

A warm runtime provisions, seeds, and waits unclaimed. A claim binds it to one
Interview Session and nothing else may use it. When it is no longer needed it is
released — routes and credentials revoked, minimized evidence extracted — and
only then terminated, which destroys the sandbox. A data-bearing runtime is never
reset back into the pool, so there is no transition from ``released`` to
``ready``.

A Preview Version is immutable. It builds, becomes ready once its candidate
runtime passes every gating probe, and is retired when a newer revision replaces
it. A correction is a new Preview Version, never a patch of this one.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import (
    PreviewRuntimeAllocationState,
)

from app.lifecycle.machine import Machine, Transition

__all__ = [
    "ALLOCATION_MACHINE_VERSION",
    "PREVIEW_RUNTIME_ALLOCATION_MACHINE",
    "PreviewRuntimeAllocationFacts",
]

ALLOCATION_MACHINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class PreviewRuntimeAllocationFacts:
    """Facts the Preview Runtime Allocation guards read."""

    runtime_reachable: bool
    seed_manifest_matches: bool
    readiness_admits_traffic: bool
    routes_revoked: bool
    evidence_extracted: bool


def _may_seed(facts: PreviewRuntimeAllocationFacts) -> str | None:
    if not facts.runtime_reachable:
        return "the runtime sandbox is not reachable, so no dataset may be installed"
    return None


def _may_become_ready(facts: PreviewRuntimeAllocationFacts) -> str | None:
    if not facts.seed_manifest_matches:
        return "the installed dataset does not reproduce the approved seed manifest"
    if not facts.readiness_admits_traffic:
        return "a gating readiness probe did not pass"
    return None


def _may_claim(facts: PreviewRuntimeAllocationFacts) -> str | None:
    if not facts.readiness_admits_traffic:
        return "the runtime is no longer admitting traffic"
    return None


def _may_release(facts: PreviewRuntimeAllocationFacts) -> str | None:
    if not facts.routes_revoked:
        return "every route revision pointing at the runtime must be revoked first"
    if not facts.evidence_extracted:
        return "required minimized evidence has not been extracted yet"
    return None


PREVIEW_RUNTIME_ALLOCATION_MACHINE: Machine[PreviewRuntimeAllocationFacts] = Machine(
    name="preview_runtime_allocation",
    version=ALLOCATION_MACHINE_VERSION,
    initial=PreviewRuntimeAllocationState.PENDING.value,
    terminal=frozenset({PreviewRuntimeAllocationState.TERMINATED.value}),
    transitions=(
        Transition(
            trigger="begin_seeding",
            sources=frozenset({PreviewRuntimeAllocationState.PENDING.value}),
            target=PreviewRuntimeAllocationState.SEEDING.value,
            guard=_may_seed,
        ),
        Transition(
            trigger="mark_ready",
            sources=frozenset({PreviewRuntimeAllocationState.SEEDING.value}),
            target=PreviewRuntimeAllocationState.READY.value,
            guard=_may_become_ready,
        ),
        Transition(
            trigger="claim",
            sources=frozenset({PreviewRuntimeAllocationState.READY.value}),
            target=PreviewRuntimeAllocationState.BOUND.value,
            guard=_may_claim,
        ),
        # A candidate runtime is built for one Preview Version and binds to the
        # session directly; it never waits unclaimed in the pool.
        Transition(
            trigger="bind_candidate",
            sources=frozenset({PreviewRuntimeAllocationState.SEEDING.value}),
            target=PreviewRuntimeAllocationState.BOUND.value,
            guard=_may_become_ready,
        ),
        Transition(
            trigger="release",
            sources=frozenset(
                {
                    PreviewRuntimeAllocationState.BOUND.value,
                    PreviewRuntimeAllocationState.READY.value,
                }
            ),
            target=PreviewRuntimeAllocationState.RELEASED.value,
            guard=_may_release,
        ),
        # Provisioning or seeding that never reached a usable runtime is destroyed
        # rather than repaired in place: a partial dataset is not evidence.
        Transition(
            trigger="abandon",
            sources=frozenset(
                {
                    PreviewRuntimeAllocationState.PENDING.value,
                    PreviewRuntimeAllocationState.SEEDING.value,
                }
            ),
            target=PreviewRuntimeAllocationState.TERMINATED.value,
            guard=None,
        ),
        Transition(
            trigger="terminate",
            sources=frozenset({PreviewRuntimeAllocationState.RELEASED.value}),
            target=PreviewRuntimeAllocationState.TERMINATED.value,
            guard=None,
        ),
    ),
)


