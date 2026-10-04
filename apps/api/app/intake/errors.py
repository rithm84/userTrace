"""Typed refusals the intake path raises.

Each one names the surface, the author, or the unresolved fact, so a skipped
message or a blocked analysis is diagnosable from the error alone.
"""

from uuid import UUID

from app.errors import ControlPlaneError

__all__ = [
    "RoutingBlockedError",
    "UnattributedAuthorError",
    "UnwatchedSurfaceError",
]


class UnwatchedSurfaceError(ControlPlaneError):
    """The message arrived on a channel the client's release does not watch."""

    def __init__(self, client_id: UUID, channel_ref: str) -> None:
        super().__init__(
            f"channel {channel_ref} is not a watched intake surface for client "
            f"{client_id}"
        )
        self.client_id = client_id
        self.channel_ref = channel_ref


class UnattributedAuthorError(ControlPlaneError):
    """A bot-authored message has no client-scoped reporter attribution.

    uTrace's own posts must never become customer reports, so a bot author is
    refused unless an explicit attribution row names the reporting client user.
    """

    def __init__(self, client_id: UUID, author_provider_identity: str) -> None:
        super().__init__(
            f"bot-authored message from {author_provider_identity} has no reporter "
            f"attribution for client {client_id}; it cannot become a customer report"
        )
        self.client_id = client_id
        self.author_provider_identity = author_provider_identity


class RoutingBlockedError(ControlPlaneError):
    """Routing could not resolve a required fact, so no outcome may be committed.

    The Intake Item is preserved in ``routing_blocked`` with this reason. It is
    never converted into an invented noise verdict.
    """

    def __init__(self, intake_item_id: UUID, reason: str) -> None:
        super().__init__(f"routing blocked for Intake Item {intake_item_id}: {reason}")
        self.intake_item_id = intake_item_id
        self.reason = reason
