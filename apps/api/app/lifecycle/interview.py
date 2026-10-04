"""Interview Session and voice connection machines.

Session: ``invited -> claimed -> preparing -> live -> closed``.
Voice: ``connecting -> active -> reconnecting -> closed``.

The two readiness boundaries are separate. Conversational readiness admits
greeting and problem discussion; observation readiness additionally gates
product-activity claims, visual commands, and validation. Pause is a durable
control flag on the live session, not a state: it withdraws authority for voice,
observation, and new live implementation steps without closing the encounter.
"""

from dataclasses import dataclass

from utrace_contracts.domain.lifecycle import InterviewSessionState, VoiceConnectionState

from app.lifecycle.machine import Machine, Transition

__all__ = [
    "INTERVIEW_SESSION_MACHINE",
    "VOICE_CONNECTION_MACHINE",
    "InterviewSessionFacts",
    "VoiceConnectionFacts",
]

MACHINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class InterviewSessionFacts:
    """Facts the Interview Session guards read."""

    identity_matches: bool
    participant_setup_passed: bool
    conversational_ready: bool
    observation_ready: bool


def _claimable(facts: InterviewSessionFacts) -> str | None:
    if not facts.identity_matches:
        return "only the invited originating user may claim the session"
    return None


def _preparable(facts: InterviewSessionFacts) -> str | None:
    if not facts.participant_setup_passed:
        return (
            "participant compatibility preflight must pass before interview activity"
        )
    return None


def _admittable(facts: InterviewSessionFacts) -> str | None:
    if not facts.conversational_ready:
        return (
            "conversational readiness requires the authenticated participant, working "
            "voice transport and controls, and an attached backend sideband"
        )
    return None


INTERVIEW_SESSION_MACHINE: Machine[InterviewSessionFacts] = Machine(
    name="interview_session",
    version=MACHINE_VERSION,
    initial=InterviewSessionState.INVITED.value,
    terminal=frozenset({InterviewSessionState.CLOSED.value}),
    transitions=(
        Transition(
            trigger="claim",
            sources=frozenset({InterviewSessionState.INVITED.value}),
            target=InterviewSessionState.CLAIMED.value,
            guard=_claimable,
        ),
        Transition(
            trigger="begin_preparation",
            sources=frozenset({InterviewSessionState.CLAIMED.value}),
            target=InterviewSessionState.PREPARING.value,
            guard=_preparable,
        ),
        Transition(
            trigger="admit",
            sources=frozenset({InterviewSessionState.PREPARING.value}),
            target=InterviewSessionState.LIVE.value,
            guard=_admittable,
        ),
        Transition(
            trigger="close",
            sources=frozenset(
                {
                    InterviewSessionState.INVITED.value,
                    InterviewSessionState.CLAIMED.value,
                    InterviewSessionState.PREPARING.value,
                    InterviewSessionState.LIVE.value,
                }
            ),
            target=InterviewSessionState.CLOSED.value,
            guard=None,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class VoiceConnectionFacts:
    """Facts the voice connection guards read."""

    provider_session_bound: bool
    superseded: bool


def _activatable(facts: VoiceConnectionFacts) -> str | None:
    if not facts.provider_session_bound:
        return "the provider session must be bound to the current voice run"
    if facts.superseded:
        return "a superseded connection cannot become current"
    return None


def _restorable(facts: VoiceConnectionFacts) -> str | None:
    if facts.superseded:
        return "an authenticated takeover already revoked this connection"
    return None


VOICE_CONNECTION_MACHINE: Machine[VoiceConnectionFacts] = Machine(
    name="voice_connection",
    version=MACHINE_VERSION,
    initial=VoiceConnectionState.CONNECTING.value,
    terminal=frozenset({VoiceConnectionState.CLOSED.value}),
    transitions=(
        Transition(
            trigger="activate",
            sources=frozenset({VoiceConnectionState.CONNECTING.value}),
            target=VoiceConnectionState.ACTIVE.value,
            guard=_activatable,
        ),
        Transition(
            trigger="lose_transport",
            sources=frozenset({VoiceConnectionState.ACTIVE.value}),
            target=VoiceConnectionState.RECONNECTING.value,
            guard=None,
        ),
        Transition(
            trigger="restore",
            sources=frozenset({VoiceConnectionState.RECONNECTING.value}),
            target=VoiceConnectionState.ACTIVE.value,
            guard=_restorable,
        ),
        Transition(
            trigger="close",
            sources=frozenset(
                {
                    VoiceConnectionState.CONNECTING.value,
                    VoiceConnectionState.ACTIVE.value,
                    VoiceConnectionState.RECONNECTING.value,
                }
            ),
            target=VoiceConnectionState.CLOSED.value,
            guard=None,
        ),
    ),
)
