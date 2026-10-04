"""``demo rehearse-intake``: prove the intake path, off the record.

This is a rehearsal. It posts the canonical Discord message into the real
watched channel, so it leaves a real message behind and must never be run
against the surface the demo is recorded on without clearing it afterwards. The
harness prints the posted message identifier for exactly that reason.

What it proves, from the control plane's own projections: one Intake Item routes
to exactly one Feature Request Product Signal, and the private invitation reserves
the fixed 25,000 minor unit reward.

The harness can only speak as the uTrace bot, and the deployed watched-channel
consumer drops the bot's own posts, so the rehearsal hands each posted message
to the staff re-ingestion route instead of waiting for the gateway to deliver
it. That route runs the consumer's own path, so what it commits is what a
customer's message commits. Two preconditions remain, and each raises with its
own name: the target's worker must be running, because routing happens in a
Job, and the target database must attribute the uTrace bot's provider identity
to the reporting client user, because intake refuses a bot author outright
otherwise.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import FlowType, OfferState, RoutingOutcome

from demo_harness import scenario
from demo_harness.checks import Finding, holds, violated
from demo_harness.control_plane import ControlPlane, ControlPlaneError
from demo_harness.discord_surface import PostedMessage, post_message
from demo_harness.models import IntakeItemSummary
from demo_harness.settings import DiscordSettings

__all__ = ["RehearsalBlockedError", "RehearsalTimeoutError", "rehearse_intake"]

_BANNER: Final[str] = (
    "REHEARSAL ONLY -- this posts a real message into the configured watched "
    "channel and routes it for real. Never run it as part of the demo journey."
)
_ROUTED: Final[str] = "routed"


class RehearsalTimeoutError(RuntimeError):
    """The ingested message never reached a committed routing outcome."""

    def __init__(self, *, intake_item_id: UUID, seconds: float) -> None:
        """Name the Intake Item and the process that has to be running."""
        super().__init__(
            f"Intake Item {intake_item_id} was created but committed no routing outcome "
            f"within {seconds:.0f}s. Routing runs in a Job, so the target's "
            f"`utrace-worker` has to be running against the same database; check its log "
            f"group for `worker_ready` and then `intake_route_job_completed`."
        )
        self.intake_item_id = intake_item_id


class RehearsalBlockedError(RuntimeError):
    """The control plane refused to ingest the message the rehearsal posted."""

    def __init__(self, *, message_id: str, refusal: ControlPlaneError) -> None:
        """Name the message, the refusal, and the one configuration it needs."""
        super().__init__(
            f"the control plane refused to ingest Discord message {message_id}: "
            f"{refusal.detail}. The harness can only post as the uTrace bot, and intake "
            f"never treats a bot-authored message as a customer report unless the target "
            f"database carries an intake_author_attributions row naming the reporting "
            f"client user for that provider identity. Seed that row, or rehearse with a "
            f"message the invited Discord identity posts by hand."
        )
        self.message_id = message_id
        self.refusal = refusal


async def rehearse_intake(
    control_plane: ControlPlane,
    *,
    client_id: UUID,
    discord_settings: DiscordSettings,
    poll_seconds: float,
    deadline_seconds: float,
) -> list[Finding]:
    """Post the canonical message once and check what the control plane did."""
    sys.stdout.write(f"{_BANNER}\n\n")

    signals_before = {case.id for case in (await control_plane.cases(client_id)).cases}

    first = await _post_and_ingest(control_plane, client_id=client_id, settings=discord_settings)
    routed = await _await_routing(
        control_plane,
        client_id=client_id,
        intake_item_id=first,
        poll_seconds=poll_seconds,
        deadline_seconds=deadline_seconds,
    )

    findings = [
        holds(
            "intake_item_routed",
            f"Intake Item {routed.id} routed {routed.routing_outcome} from channel "
            f"{routed.source_channel_ref} authored by {routed.author_provider_identity}",
        )
        if routed.routing_outcome == RoutingOutcome.CREATE_PRODUCT_SIGNAL.value
        else violated(
            "intake_item_routed",
            f"Intake Item {routed.id} committed {routed.routing_outcome} "
            f"(blocked: {routed.blocked_reason}); the scenario creates one Product Signal",
        )
    ]
    findings.extend(
        await _signal_findings(control_plane, client_id=client_id, known=signals_before)
    )
    findings.append(await _offer_finding(control_plane, client_id=client_id))
    return findings


async def _post_and_ingest(
    control_plane: ControlPlane, *, client_id: UUID, settings: DiscordSettings
) -> UUID:
    """Post the canonical message and hand it to intake, returning its Intake Item."""
    posted: PostedMessage = await post_message(
        settings, content=scenario.CANONICAL_DISCORD_MESSAGE
    )
    sys.stdout.write(
        f"posted the canonical message as {posted.id}; delete it after the rehearsal\n"
    )
    try:
        ingested = await control_plane.ingest_intake_message(
            client_id,
            channel_ref=posted.channel_id,
            provider_message_id=posted.id,
        )
    except ControlPlaneError as refusal:
        raise RehearsalBlockedError(message_id=posted.id, refusal=refusal) from refusal
    sys.stdout.write(
        f"intake recorded it as Intake Item {ingested.intake_item_id}"
        f"{' (duplicate)' if ingested.duplicate else ''}\n"
    )
    return ingested.intake_item_id


async def _await_routing(
    control_plane: ControlPlane,
    *,
    client_id: UUID,
    intake_item_id: UUID,
    poll_seconds: float,
    deadline_seconds: float,
) -> IntakeItemSummary:
    """Poll the Intake Item projection until this Intake Item commits its outcome."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    while loop.time() - started < deadline_seconds:
        listing = await control_plane.intake_items(client_id)
        for item in listing.intake_items:
            if item.id != intake_item_id:
                continue
            if item.state == _ROUTED or item.blocked_reason is not None:
                return item
        await asyncio.sleep(poll_seconds)
    raise RehearsalTimeoutError(intake_item_id=intake_item_id, seconds=deadline_seconds)


async def _signal_findings(
    control_plane: ControlPlane, *, client_id: UUID, known: set[UUID]
) -> list[Finding]:
    """Check that exactly one new Feature Request Product Signal exists."""
    listing = await control_plane.cases(client_id)
    created = [case for case in listing.cases if case.id not in known]
    if len(created) != 1:
        return [
            violated(
                "one_feature_request_signal",
                f"{len(created)} Product Signals were created "
                f"({[str(case.id) for case in created]}); the scenario creates exactly one",
            )
        ]
    case = created[0]
    if case.initial_flow_type is not FlowType.FEATURE_REQUEST_FLOW:
        return [
            violated(
                "one_feature_request_signal",
                f"Product Signal {case.id} was routed as {case.initial_flow_type.value}, not "
                f"{FlowType.FEATURE_REQUEST_FLOW.value}",
            )
        ]
    return [
        holds(
            "one_feature_request_signal",
            f"Product Signal {case.id} routed as {case.initial_flow_type.value} in "
            f"{case.state.value}, pinning release {case.pinned_configuration_release_id}",
        )
    ]


async def _offer_finding(control_plane: ControlPlane, *, client_id: UUID) -> Finding:
    """Check that the invitation reserved the fixed reward, and only once."""
    view = await control_plane.offers(client_id, scenario.REWARD_CURRENCY.upper())
    reserved = [offer for offer in view.offers if offer.state is OfferState.RESERVED]
    if len(reserved) != 1:
        return violated(
            "one_reserved_invitation",
            f"{len(reserved)} reserved offers exist; the scenario sends one private "
            f"invitation carrying one reservation",
        )
    offer = reserved[0]
    if offer.amount_minor_units != scenario.REWARD_MINOR_UNITS:
        return violated(
            "one_reserved_invitation",
            f"offer {offer.outreach_offer_id} reserved {offer.amount_minor_units} minor "
            f"units, not the fixed {scenario.REWARD_MINOR_UNITS}",
        )
    return holds(
        "one_reserved_invitation",
        f"offer {offer.outreach_offer_id} reserved {offer.amount_minor_units} "
        f"{offer.currency} minor units against a {view.authorized_minor_units} ceiling; "
        f"{view.remaining_minor_units} remain",
    )
