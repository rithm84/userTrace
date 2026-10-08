"""What each kind of context bundle is for, and which revisions its result depends on.

A bundle pins every revision it was assembled against, as provenance. Its result
is stale only when a revision that result depends on has moved: a purpose
declares those here, from what its applied result rests on, so a revision the
result does not use cannot refuse it. Corrections that supersede evidence a
bundle cited invalidate it whatever its purpose.

Contract: docs/arch-components/context-assembly.md#provenance-and-concurrent-results
"""

from enum import StrEnum

__all__ = ["BundleDependency", "BundlePurpose"]


class BundleDependency(StrEnum):
    """One revision a bundle pins; the names are `BundleDependencies`' fields."""

    PRODUCT_SIGNAL_REVISION = "product_signal_revision"
    INTERVIEW_SESSION_REVISION = "interview_session_revision"
    PINNED_CONFIGURATION_RELEASE_ID = "pinned_configuration_release_id"
    CLASSIFICATION_REVISION = "classification_revision"
    ACCEPTANCE_CRITERIA_REVISION = "acceptance_criteria_revision"
    CONCERN_REVISION = "concern_revision"
    READINESS_REVISION = "readiness_revision"


_CASE: frozenset[BundleDependency] = frozenset(
    {
        BundleDependency.PRODUCT_SIGNAL_REVISION,
        BundleDependency.PINNED_CONFIGURATION_RELEASE_ID,
        BundleDependency.CLASSIFICATION_REVISION,
        BundleDependency.ACCEPTANCE_CRITERIA_REVISION,
        BundleDependency.CONCERN_REVISION,
    }
)
"""The case's own lifecycle, policy, classification, agreed scope, and concern."""


class BundlePurpose(StrEnum):
    """The closed set of bundle purposes."""

    VOICE_TOOLS = "voice_delegation"
    """A voice agent tool call's working context; its tools record agreements.

    The stored value predates the voice agent, and stays so the column's enum is unchanged.
    """
    FLOW_CLASSIFICATION = "flow_classification"
    """The interview-established flow classification the gate screens against."""
    JEV_ATTENTION = "jev_attention"
    """Which agreed checks the person's activity in a new version showed."""

    @property
    def dependencies(self) -> frozenset[BundleDependency]:
        """The revisions a result of this purpose depends on.

        An agreement or a classification rests on the case itself. A shown-checks
        verdict rests on the criteria it judged. No result rests on telemetry
        readiness or on the session's own revision, which moves with Mute, Pause,
        phase, and implementation progress.
        """
        match self:
            case BundlePurpose.VOICE_TOOLS | BundlePurpose.FLOW_CLASSIFICATION:
                return _CASE
            case BundlePurpose.JEV_ATTENTION:
                return frozenset({BundleDependency.ACCEPTANCE_CRITERIA_REVISION})
