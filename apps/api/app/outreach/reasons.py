"""The closure and cancellation reasons private outreach records.

`canceled` covers both an unanswered invitation and an explicit withdrawal; the
recorded reason says which.

Contract: docs/arch-components/signal-and-outreach.md#originating-user-and-invitation-lifecycle
"""

from typing import Final

__all__ = [
    "CONTACT_BUDGET_EXHAUSTED",
    "DECLINED",
    "DO_NOT_CONTACT",
    "INVITATION_EXPIRED",
    "OPTED_OUT",
    "RETURN_WINDOW_EXPIRED",
    "UNREACHABLE",
]

DECLINED: Final[str] = "declined_initial_invitation"
OPTED_OUT: Final[str] = "participant_opted_out"
DO_NOT_CONTACT: Final[str] = "participant_do_not_contact"
UNREACHABLE: Final[str] = "participant_unreachable"
INVITATION_EXPIRED: Final[str] = "invitation_response_deadline_expired"
CONTACT_BUDGET_EXHAUSTED: Final[str] = "contact_budget_exhausted"
RETURN_WINDOW_EXPIRED: Final[str] = "return_window_expired"
