"""Typed control-plane errors.

Every failure names what was attempted, on which aggregate, and why it was
refused. Nothing is swallowed and nothing degrades silently: a caller either gets
the committed outcome or one of these errors.
"""

from uuid import UUID

__all__ = [
    "AggregateNotFoundError",
    "AuthorizationError",
    "BudgetExhaustedError",
    "ControlPlaneError",
    "GuardRejectedError",
    "IllegalTransitionError",
    "LaneCredentialError",
    "LeaseLostError",
    "NotYetWiredError",
    "ReferenceFormatError",
    "RevisionConflictError",
    "StaleContextError",
]


class ControlPlaneError(Exception):
    """Base class for refusals the control plane raises deliberately."""


class AggregateNotFoundError(ControlPlaneError):
    """The aggregate row named by a transition does not exist."""

    def __init__(self, table: str, row_id: UUID) -> None:
        super().__init__(f"aggregate row {table}.{row_id} does not exist")
        self.table = table
        self.row_id = row_id


class RevisionConflictError(ControlPlaneError):
    """A concurrent transition already advanced the aggregate's revision."""

    def __init__(self, table: str, row_id: UUID, expected: int, actual: int) -> None:
        super().__init__(
            f"revision conflict on {table}.{row_id}: "
            f"expected revision {expected}, found {actual}"
        )
        self.table = table
        self.row_id = row_id
        self.expected = expected
        self.actual = actual


class IllegalTransitionError(ControlPlaneError):
    """The machine has no transition for this trigger from the current state."""

    def __init__(self, machine: str, machine_version: int, state: str, trigger: str) -> None:
        super().__init__(
            f"machine {machine} v{machine_version} has no transition "
            f"'{trigger}' from state '{state}'"
        )
        self.machine = machine
        self.machine_version = machine_version
        self.state = state
        self.trigger = trigger


class GuardRejectedError(ControlPlaneError):
    """A transition guard refused the move on the supplied facts."""

    def __init__(self, machine: str, trigger: str, reason: str) -> None:
        super().__init__(f"machine {machine} rejected trigger '{trigger}': {reason}")
        self.machine = machine
        self.trigger = trigger
        self.reason = reason


class StaleContextError(GuardRejectedError):
    """A result's inputs moved while it was produced, so it cannot be applied.

    The refused result stays diagnostic evidence; the controller that owns the
    decision may rebuild its context and decide again.
    """

    def __init__(self, trigger: str, reason: str) -> None:
        super().__init__(machine="product_signal", trigger=trigger, reason=reason)


class BudgetExhaustedError(ControlPlaneError):
    """A pinned budget leaves no allowance for the requested work."""

    def __init__(self, budget: str, consumed: int, allowance: int) -> None:
        super().__init__(
            f"{budget} budget exhausted: consumed {consumed} of {allowance}"
        )
        self.budget = budget
        self.consumed = consumed
        self.allowance = allowance


class LeaseLostError(ControlPlaneError):
    """The lease this worker holds is no longer current, so its write is refused."""

    def __init__(self, lease_kind: str, lease_id: UUID, fencing_token: int) -> None:
        super().__init__(
            f"{lease_kind} lease {lease_id} with fencing token {fencing_token} "
            "is no longer the current lease"
        )
        self.lease_kind = lease_kind
        self.lease_id = lease_id
        self.fencing_token = fencing_token


class AuthorizationError(ControlPlaneError):
    """Authentication succeeded but the caller holds no authority for the subject."""

    def __init__(self, subject: str, reason: str) -> None:
        super().__init__(f"not authorized for {subject}: {reason}")
        self.subject = subject
        self.reason = reason


class LaneCredentialError(ControlPlaneError):
    """A lane credential failed audience, binding, expiry, or revocation checks."""

    def __init__(self, audience: str, reason: str) -> None:
        super().__init__(f"lane credential for audience '{audience}' rejected: {reason}")
        self.audience = audience
        self.reason = reason


class ReferenceFormatError(ControlPlaneError):
    """A caller asked to resolve something that is not an opaque reference."""

    def __init__(self, ref: str) -> None:
        super().__init__(
            f"'{ref}' is not a client-scoped reference; expected "
            "'client_user:<uuid>' or 'customer_account:<uuid>'"
        )
        self.ref = ref


class NotYetWiredError(ControlPlaneError):
    """The route exists and is authorized, but its owning workstream is not wired.

    Raised from one helper so unwired behaviour is always an explicit 501 with the
    owning workstream named, never a silent stub that returns plausible data.
    """

    def __init__(self, capability: str, owning_workstream: str) -> None:
        super().__init__(
            f"capability '{capability}' is not yet wired; "
            f"it is owned by the {owning_workstream} workstream"
        )
        self.capability = capability
        self.owning_workstream = owning_workstream
