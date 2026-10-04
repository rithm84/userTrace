"""Pure lifecycle machine tests.

Machines are data and guards are pure functions, so their behaviour is proved
without a database. Every transition of every machine is exercised here; the
database tests then prove the same transitions commit correctly.
"""

from __future__ import annotations

import pytest
from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    ProductSignalState,
    RoutingOutcome,
    ValidationOutcome,
)

from app.errors import GuardRejectedError, IllegalTransitionError
from app.lifecycle.case_file import CASE_FILE_MACHINE, CaseFileFacts
from app.lifecycle.implementation import (
    IMPLEMENTATION_ATTEMPT_MACHINE,
    ImplementationAttemptFacts,
)
from app.lifecycle.intake import INTAKE_ITEM_MACHINE, IntakeItemFacts
from app.lifecycle.interview import (
    INTERVIEW_SESSION_MACHINE,
    VOICE_CONNECTION_MACHINE,
    InterviewSessionFacts,
    VoiceConnectionFacts,
)
from app.lifecycle.machine import Machine, resolve
from app.lifecycle.outreach import OUTREACH_ATTEMPT_MACHINE, OutreachAttemptFacts
from app.lifecycle.product_signal import PRODUCT_SIGNAL_MACHINE, ProductSignalFacts
from app.lifecycle.validation import VALIDATION_ATTEMPT_MACHINE, ValidationAttemptFacts

ALL_MACHINES = (
    INTAKE_ITEM_MACHINE,
    PRODUCT_SIGNAL_MACHINE,
    OUTREACH_ATTEMPT_MACHINE,
    INTERVIEW_SESSION_MACHINE,
    VOICE_CONNECTION_MACHINE,
    IMPLEMENTATION_ATTEMPT_MACHINE,
    VALIDATION_ATTEMPT_MACHINE,
    CASE_FILE_MACHINE,
)


def _permissive_signal_facts(**overrides: object) -> ProductSignalFacts:
    base: dict[str, object] = {
        "participation_occurred": True,
        "case_file_published": True,
    }
    base.update(overrides)
    return ProductSignalFacts(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("machine", ALL_MACHINES, ids=lambda m: m.name)
def test_every_machine_pins_a_version_and_reaches_only_declared_states(
    machine: Machine[object],
) -> None:
    """Targets and sources stay inside the machine's own declared state set."""
    assert machine.version >= 1
    declared = {machine.initial, *machine.terminal}
    for transition in machine.transitions:
        declared.update(transition.sources)
        declared.add(transition.target)
    assert machine.initial in declared
    for terminal in machine.terminal:
        assert all(
            terminal not in transition.sources for transition in machine.transitions
        ), f"{machine.name} allows a move out of terminal state {terminal}"


def test_intake_item_routes_and_recovers() -> None:
    """received -> routing -> routed, with routing_blocked recovery."""
    facts = IntakeItemFacts(
        routing_outcome=RoutingOutcome.CREATE_PRODUCT_SIGNAL,
        blocked_reason=None,
        route_committed=False,
    )
    assert resolve(INTAKE_ITEM_MACHINE, "received", "begin_routing", facts).target == "routing"
    assert resolve(INTAKE_ITEM_MACHINE, "routing", "commit_route", facts).target == "routed"
    blocked = IntakeItemFacts(
        routing_outcome=None, blocked_reason="required facts missing", route_committed=False
    )
    assert (
        resolve(INTAKE_ITEM_MACHINE, "routing", "block_routing", blocked).target
        == "routing_blocked"
    )
    assert (
        resolve(INTAKE_ITEM_MACHINE, "routing_blocked", "begin_routing", blocked).target
        == "routing"
    )


def test_intake_item_refuses_a_routed_commit_without_an_outcome() -> None:
    """A routed Intake Item must commit exactly one routing outcome."""
    facts = IntakeItemFacts(
        routing_outcome=None, blocked_reason=None, route_committed=False
    )
    with pytest.raises(GuardRejectedError, match="exactly one routing outcome"):
        resolve(INTAKE_ITEM_MACHINE, "routing", "commit_route", facts)


def test_intake_item_refuses_cancellation_after_commitment() -> None:
    """Cancellation is authorized only before the route is committed."""
    facts = IntakeItemFacts(
        routing_outcome=RoutingOutcome.CLOSE_AS_NOISE,
        blocked_reason=None,
        route_committed=True,
    )
    with pytest.raises(GuardRejectedError, match="before the route is committed"):
        resolve(INTAKE_ITEM_MACHINE, "routing", "cancel", facts)


def test_product_signal_happy_path_matches_the_lifecycle_diagram() -> None:
    """awaiting_interview through closed_validated, in the diagram's order."""
    facts = _permissive_signal_facts()
    path = [
        ("awaiting_interview", "setup_ready", "interviewing"),
        ("interviewing", "evidence_sufficient", "gate_pending"),
        ("gate_pending", "authorize_implementation", "implementing"),
        ("implementing", "candidate_ready", "awaiting_validation"),
        ("awaiting_validation", "participant_ready", "validating"),
        ("validating", "validated", "closed_validated"),
    ]
    for current, trigger, expected in path:
        assert resolve(PRODUCT_SIGNAL_MACHINE, current, trigger, facts).target == expected


def test_product_signal_unsuccessful_validation_closes_as_blocked() -> None:
    """An unvalidated outcome closes with its Case File."""
    facts = _permissive_signal_facts()
    assert (
        resolve(PRODUCT_SIGNAL_MACHINE, "validating", "validation_unsuccessful", facts).target
        == "closed_blocked"
    )
    unpublished = _permissive_signal_facts(case_file_published=False)
    with pytest.raises(GuardRejectedError, match="published Case File"):
        resolve(PRODUCT_SIGNAL_MACHINE, "validating", "validation_unsuccessful", unpublished)


def test_client_stop_splits_on_participation() -> None:
    """After participation the stop closes as a blocker; before it, as canceled."""
    after = _permissive_signal_facts(participation_occurred=True, case_file_published=True)
    assert (
        resolve(
            PRODUCT_SIGNAL_MACHINE, "implementing", "client_stop_after_participation", after
        ).target
        == "closed_blocked"
    )
    with pytest.raises(GuardRejectedError, match="participation occurred"):
        resolve(
            PRODUCT_SIGNAL_MACHINE,
            "implementing",
            "client_stop_before_participation",
            after,
        )

    before = _permissive_signal_facts(participation_occurred=False, case_file_published=False)
    assert (
        resolve(
            PRODUCT_SIGNAL_MACHINE,
            "awaiting_interview",
            "client_stop_before_participation",
            before,
        ).target
        == "canceled"
    )
    with pytest.raises(GuardRejectedError, match="requires recorded participation"):
        resolve(
            PRODUCT_SIGNAL_MACHINE,
            "awaiting_interview",
            "client_stop_after_participation",
            before,
        )


def test_closed_states_require_a_published_case_file() -> None:
    """Case File publication is required for the three closed_* states."""
    facts = _permissive_signal_facts(case_file_published=False)
    for current, trigger in (
        ("gate_pending", "no_implementation"),
        ("validating", "validated"),
        ("implementing", "close_blocked"),
    ):
        with pytest.raises(GuardRejectedError, match="published Case File"):
            resolve(PRODUCT_SIGNAL_MACHINE, current, trigger, facts)


def test_terminal_product_signal_states_accept_no_further_trigger() -> None:
    """A closed case cannot be resumed by any trigger the machine declares."""
    facts = _permissive_signal_facts()
    triggers = {transition.trigger for transition in PRODUCT_SIGNAL_MACHINE.transitions}
    for terminal in PRODUCT_SIGNAL_MACHINE.terminal:
        for trigger in triggers:
            with pytest.raises(IllegalTransitionError):
                resolve(PRODUCT_SIGNAL_MACHINE, terminal, trigger, facts)
    assert ProductSignalState.CLOSED_VALIDATED.value in PRODUCT_SIGNAL_MACHINE.terminal


def test_outreach_attempt_paths() -> None:
    """scheduled -> dispatching -> awaiting_response -> claimed and its alternates."""
    ready = OutreachAttemptFacts(
        bound_identity_matches=True, expired=False, contact_budget_remaining=1
    )
    assert resolve(OUTREACH_ATTEMPT_MACHINE, "scheduled", "dispatch", ready).target == "dispatching"
    assert (
        resolve(OUTREACH_ATTEMPT_MACHINE, "dispatching", "delivery_confirmed", ready).target
        == "awaiting_response"
    )
    assert resolve(OUTREACH_ATTEMPT_MACHINE, "awaiting_response", "claim", ready).target == "claimed"
    assert (
        resolve(OUTREACH_ATTEMPT_MACHINE, "dispatching", "delivery_failed", ready).target
        == "unreached"
    )
    assert (
        resolve(OUTREACH_ATTEMPT_MACHINE, "awaiting_response", "decline", ready).target
        == "declined"
    )
    assert resolve(OUTREACH_ATTEMPT_MACHINE, "awaiting_response", "expire", ready).target == "expired"
    assert resolve(OUTREACH_ATTEMPT_MACHINE, "scheduled", "revoke", ready).target == "revoked"


def test_outreach_attempt_refuses_a_stranger_and_an_expired_invitation() -> None:
    """A claim must prove the invited identity and cannot beat the deadline."""
    stranger = OutreachAttemptFacts(
        bound_identity_matches=False, expired=False, contact_budget_remaining=1
    )
    with pytest.raises(GuardRejectedError, match="invited Discord identity"):
        resolve(OUTREACH_ATTEMPT_MACHINE, "awaiting_response", "claim", stranger)
    late = OutreachAttemptFacts(
        bound_identity_matches=True, expired=True, contact_budget_remaining=1
    )
    with pytest.raises(GuardRejectedError, match="expired"):
        resolve(OUTREACH_ATTEMPT_MACHINE, "awaiting_response", "claim", late)
    broke = OutreachAttemptFacts(
        bound_identity_matches=True, expired=False, contact_budget_remaining=0
    )
    with pytest.raises(GuardRejectedError, match="contact budget"):
        resolve(OUTREACH_ATTEMPT_MACHINE, "scheduled", "dispatch", broke)


def test_interview_session_and_voice_connection_paths() -> None:
    """Session and voice lifecycles advance independently of one another."""
    ready = InterviewSessionFacts(
        identity_matches=True,
        participant_setup_passed=True,
        conversational_ready=True,
        observation_ready=False,
    )
    assert resolve(INTERVIEW_SESSION_MACHINE, "invited", "claim", ready).target == "claimed"
    assert (
        resolve(INTERVIEW_SESSION_MACHINE, "claimed", "begin_preparation", ready).target
        == "preparing"
    )
    assert resolve(INTERVIEW_SESSION_MACHINE, "preparing", "admit", ready).target == "live"
    assert resolve(INTERVIEW_SESSION_MACHINE, "live", "close", ready).target == "closed"

    voice = VoiceConnectionFacts(provider_session_bound=True, superseded=False)
    assert resolve(VOICE_CONNECTION_MACHINE, "connecting", "activate", voice).target == "active"
    assert (
        resolve(VOICE_CONNECTION_MACHINE, "active", "lose_transport", voice).target
        == "reconnecting"
    )
    assert resolve(VOICE_CONNECTION_MACHINE, "reconnecting", "restore", voice).target == "active"
    assert resolve(VOICE_CONNECTION_MACHINE, "reconnecting", "close", voice).target == "closed"


def test_interview_session_readiness_boundaries_are_separate() -> None:
    """Preflight gates preparation; conversational readiness gates admission."""
    unprepared = InterviewSessionFacts(
        identity_matches=True,
        participant_setup_passed=False,
        conversational_ready=False,
        observation_ready=False,
    )
    with pytest.raises(GuardRejectedError, match="preflight must pass"):
        resolve(INTERVIEW_SESSION_MACHINE, "claimed", "begin_preparation", unprepared)
    not_conversational = InterviewSessionFacts(
        identity_matches=True,
        participant_setup_passed=True,
        conversational_ready=False,
        observation_ready=False,
    )
    with pytest.raises(GuardRejectedError, match="conversational readiness"):
        resolve(INTERVIEW_SESSION_MACHINE, "preparing", "admit", not_conversational)
    stranger = InterviewSessionFacts(
        identity_matches=False,
        participant_setup_passed=True,
        conversational_ready=True,
        observation_ready=True,
    )
    with pytest.raises(GuardRejectedError, match="invited originating user"):
        resolve(INTERVIEW_SESSION_MACHINE, "invited", "claim", stranger)


def test_superseded_voice_connection_cannot_become_current() -> None:
    """An authenticated takeover revokes the previous connection for good."""
    superseded = VoiceConnectionFacts(provider_session_bound=True, superseded=True)
    with pytest.raises(GuardRejectedError, match="superseded"):
        resolve(VOICE_CONNECTION_MACHINE, "connecting", "activate", superseded)
    with pytest.raises(GuardRejectedError, match="takeover"):
        resolve(VOICE_CONNECTION_MACHINE, "reconnecting", "restore", superseded)


def _attempt_facts(**overrides: object) -> ImplementationAttemptFacts:
    base: dict[str, object] = {
        "lease_active": True,
        "fencing_token_current": True,
        "execution_budget_remaining": True,
        "within_return_deadline": True,
        "readiness_fresh": True,
        "diff_within_approved_scope": True,
    }
    base.update(overrides)
    return ImplementationAttemptFacts(**base)  # type: ignore[arg-type]


def test_implementation_attempt_paths() -> None:
    """Authorized work reaches candidate_ready and its alternates are reachable."""
    facts = _attempt_facts()
    path = [
        ("authorized", "queue", "queued"),
        ("queued", "acquire_environment", "acquiring_environment"),
        ("acquiring_environment", "begin_execution", "executing"),
        ("executing", "run_checks", "checking"),
        ("checking", "correction_cycle", "executing"),
        ("checking", "checks_passed", "publishing_candidate"),
        ("publishing_candidate", "publish_candidate", "candidate_ready"),
        ("checking", "checks_failed", "checks_failed"),
        ("executing", "require_scope_change", "scope_change_required"),
        ("executing", "mark_unsafe", "unsafe"),
        ("executing", "pause", "paused"),
        ("paused", "resume", "executing"),
        ("executing", "block_infrastructure", "infrastructure_blocked"),
        ("infrastructure_blocked", "resume_after_repair", "executing"),
        ("executing", "exhaust_budget", "budget_exhausted"),
        ("executing", "cancel", "canceled"),
    ]
    for current, trigger, expected in path:
        assert (
            resolve(IMPLEMENTATION_ATTEMPT_MACHINE, current, trigger, facts).target == expected
        )


def test_implementation_attempt_requires_a_current_fencing_token() -> None:
    """A stale worker may log, but it cannot mutate or publish."""
    stale = _attempt_facts(fencing_token_current=False)
    with pytest.raises(GuardRejectedError, match="stale fencing token"):
        resolve(IMPLEMENTATION_ATTEMPT_MACHINE, "acquiring_environment", "begin_execution", stale)
    with pytest.raises(GuardRejectedError, match="stale fencing token"):
        resolve(IMPLEMENTATION_ATTEMPT_MACHINE, "publishing_candidate", "publish_candidate", stale)

    lost = _attempt_facts(lease_active=False)
    with pytest.raises(GuardRejectedError, match="active attempt-scoped Environment Lease"):
        resolve(IMPLEMENTATION_ATTEMPT_MACHINE, "executing", "run_checks", lost)


def test_implementation_attempt_scope_and_deadline_guards() -> None:
    """Scope and the case-level deadline are checked before mutation resumes."""
    out_of_scope = _attempt_facts(diff_within_approved_scope=False)
    with pytest.raises(GuardRejectedError, match="approved-scope object"):
        resolve(IMPLEMENTATION_ATTEMPT_MACHINE, "checking", "checks_passed", out_of_scope)

    expired = _attempt_facts(within_return_deadline=False)
    with pytest.raises(GuardRejectedError, match="return-and-closure deadline"):
        resolve(
            IMPLEMENTATION_ATTEMPT_MACHINE,
            "infrastructure_blocked",
            "resume_after_repair",
            expired,
        )

    stale_readiness = _attempt_facts(readiness_fresh=False)
    with pytest.raises(GuardRejectedError, match="fresh readiness"):
        resolve(IMPLEMENTATION_ATTEMPT_MACHINE, "paused", "resume", stale_readiness)


def _validation_facts(**overrides: object) -> ValidationAttemptFacts:
    base = {
        "originating_user_matches": True,
        "candidate_ready": True,
        "observation_ready": True,
        "same_candidate": True,
        "outcome": ValidationOutcome.VALIDATED,
        "criteria_total": 3,
        "criteria_affirmatively_proven": 3,
        "user_confirmed_every_criterion": True,
        "unresolved_contradiction": False,
    }
    base.update(overrides)
    return ValidationAttemptFacts(**base)  # type: ignore[arg-type]


def test_validation_attempt_paths() -> None:
    """Validation begins, may be interrupted and resumed, and completes."""
    facts = _validation_facts()
    assert resolve(VALIDATION_ATTEMPT_MACHINE, "pending", "begin", facts).target == "in_progress"
    assert (
        resolve(VALIDATION_ATTEMPT_MACHINE, "in_progress", "interrupt", facts).target
        == "interrupted"
    )
    assert (
        resolve(VALIDATION_ATTEMPT_MACHINE, "interrupted", "resume", facts).target == "in_progress"
    )
    assert (
        resolve(VALIDATION_ATTEMPT_MACHINE, "in_progress", "complete", facts).target == "completed"
    )
    assert (
        resolve(VALIDATION_ATTEMPT_MACHINE, "interrupted", "abandon", facts).target == "abandoned"
    )


def test_validation_requires_the_same_user_and_the_exact_candidate() -> None:
    """Only the originating user validates, and only the ready exact candidate."""
    stranger = _validation_facts(originating_user_matches=False)
    with pytest.raises(GuardRejectedError, match="immutable originating user"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "pending", "begin", stranger)

    not_ready = _validation_facts(candidate_ready=False)
    with pytest.raises(GuardRejectedError, match="exact ready candidate"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "pending", "begin", not_ready)

    unobserved = _validation_facts(observation_ready=False)
    with pytest.raises(GuardRejectedError, match="complete observation path"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "pending", "begin", unobserved)

    replaced = _validation_facts(same_candidate=False)
    with pytest.raises(GuardRejectedError, match="same exact candidate"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "interrupted", "resume", replaced)


def test_validated_needs_confirmation_and_proof_for_every_criterion() -> None:
    """Silence, partial proof, and unresolved contradictions cannot validate."""
    unproven = _validation_facts(criteria_affirmatively_proven=2)
    with pytest.raises(GuardRejectedError, match="affirmative proof for every criterion"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "in_progress", "complete", unproven)

    unconfirmed = _validation_facts(user_confirmed_every_criterion=False)
    with pytest.raises(GuardRejectedError, match="originating-user confirmation"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "in_progress", "complete", unconfirmed)

    contradicted = _validation_facts(unresolved_contradiction=True)
    with pytest.raises(GuardRejectedError, match="unresolved contradiction"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "in_progress", "complete", contradicted)

    missing = _validation_facts(outcome=None)
    with pytest.raises(GuardRejectedError, match="structured outcome"):
        resolve(VALIDATION_ATTEMPT_MACHINE, "in_progress", "complete", missing)


def test_non_validated_outcomes_complete_without_the_proof_guard() -> None:
    """A negative or unclear verdict is recorded, not blocked."""
    for outcome in (
        ValidationOutcome.PARTIALLY_VALIDATED,
        ValidationOutcome.NOT_VALIDATED,
        ValidationOutcome.UNCLEAR,
    ):
        facts = _validation_facts(
            outcome=outcome,
            criteria_affirmatively_proven=0,
            user_confirmed_every_criterion=False,
        )
        assert (
            resolve(VALIDATION_ATTEMPT_MACHINE, "in_progress", "complete", facts).target
            == "completed"
        )


def test_case_file_assembly_stages() -> None:
    """draft -> verified -> delivery_resolved -> ready -> published."""
    facts = CaseFileFacts(
        variant=CaseFileVariant.VALIDATED_IMPLEMENTATION,
        citations_verified=True,
        mandatory_evidence_present=True,
        delivery_identity_verified=True,
        core_digest_present=True,
    )
    path = [
        ("draft", "verify", "verified"),
        ("verified", "resolve_delivery", "delivery_resolved"),
        ("delivery_resolved", "seal", "ready"),
        ("ready", "publish", "published"),
    ]
    for current, trigger, expected in path:
        assert resolve(CASE_FILE_MACHINE, current, trigger, facts).target == expected


def test_case_file_guards_citations_delivery_and_digest() -> None:
    """A validated implementation cannot seal without verified identities."""
    uncited = CaseFileFacts(
        variant=CaseFileVariant.BLOCKED,
        citations_verified=False,
        mandatory_evidence_present=True,
        delivery_identity_verified=False,
        core_digest_present=True,
    )
    with pytest.raises(GuardRejectedError, match="authorized evidence slice"):
        resolve(CASE_FILE_MACHINE, "draft", "verify", uncited)

    undelivered = CaseFileFacts(
        variant=CaseFileVariant.VALIDATED_IMPLEMENTATION,
        citations_verified=True,
        mandatory_evidence_present=True,
        delivery_identity_verified=False,
        core_digest_present=True,
    )
    with pytest.raises(GuardRejectedError, match="verified GitHub identities"):
        resolve(CASE_FILE_MACHINE, "verified", "resolve_delivery", undelivered)

    # A blocker Case File resolves delivery with no GitHub identity at all.
    blocked = CaseFileFacts(
        variant=CaseFileVariant.BLOCKED,
        citations_verified=True,
        mandatory_evidence_present=True,
        delivery_identity_verified=False,
        core_digest_present=False,
    )
    assert (
        resolve(CASE_FILE_MACHINE, "verified", "resolve_delivery", blocked).target
        == "delivery_resolved"
    )
    with pytest.raises(GuardRejectedError, match="immutable core digest"):
        resolve(CASE_FILE_MACHINE, "delivery_resolved", "seal", blocked)


def test_unknown_trigger_is_an_illegal_transition() -> None:
    """A trigger the machine does not declare is refused by name."""
    with pytest.raises(IllegalTransitionError, match="no transition 'teleport'"):
        resolve(PRODUCT_SIGNAL_MACHINE, "interviewing", "teleport", _permissive_signal_facts())
