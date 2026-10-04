"""The private invitation and client-stop notice text.

Source-linked templates, not generated prose: the invitation states the credit
amount and currency, the recipient organization, the billing use, the completion
conditions, and the uTrace Session Link. The agent cannot negotiate or increase
the amount, so nothing here is derived from a model output.

Contract: docs/PRD.md#outreach-offers
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["OfferTerms", "invitation_text", "stop_notice_text"]

_MINOR_UNITS_PER_MAJOR = 100


@dataclass(frozen=True, slots=True)
class OfferTerms:
    """The reserved reward one invitation states."""

    currency: str
    amount_minor_units: int
    organization_name: str
    billing_use: str
    conditions: tuple[str, ...]

    def amount_text(self) -> str:
        """Return the amount as the participant reads it, from minor units only."""
        major, minor = divmod(self.amount_minor_units, _MINOR_UNITS_PER_MAJOR)
        return f"{major}.{minor:02d} {self.currency}"


def invitation_text(
    *,
    client_name: str,
    product_name: str,
    concern_excerpt: str,
    session_link: str,
    terms: OfferTerms | None,
    expires_in_hours: int,
) -> str:
    """Compose the private initial invitation."""
    lines = [
        f"Hi — I am a uTrace product agent working with the {client_name} team.",
        "",
        f"You wrote this in the {product_name} community:",
        f"> {concern_excerpt}",
        "",
        (
            "I would like to go through it with you in a short voice session, in a "
            f"private copy of {product_name} with your own workspace data. I will ask "
            "short questions while you show me the workflow."
        ),
    ]
    if terms is not None:
        lines.extend(
            [
                "",
                (
                    f"For completing that participation, {terms.organization_name} "
                    f"receives {terms.amount_text()} of {terms.billing_use}."
                ),
                "What completing it means:",
                *[f"- {condition}" for condition in terms.conditions],
            ]
        )
    lines.extend(
        [
            "",
            f"Start here: {session_link}",
            f"The link is personal to you and expires in {expires_in_hours} hours.",
            "",
            (
                "If you would rather not take part, reply `no thanks`. Reply `stop` "
                "and I will not contact you again."
            ),
        ]
    )
    return "\n".join(lines)


def stop_notice_text(*, client_name: str, product_name: str) -> str:
    """Compose the private notice that the client stopped the originating user's case.

    It says what the session tab says for a client stop, phrased for a direct message.
    """
    return (
        f"The {client_name} team stopped the case about your {product_name} request. "
        "Nothing further will run, and anything you had already completed still counts."
    )
