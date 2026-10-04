"""``uv run demo <command>``: the Shopalytics demo harness entry point.

Commands
--------
``prepare``          converge and check the scenario's starting state
``rehearse-intake``  post the canonical message and check what routing did
``watch``            follow the live journey and print milestones as they commit
``settle-invoice``   finalize the service invoice and cross-check the credit
``verify``           assert the scenario's acceptance boundary
``reset-target``     report, or with the guard flag remove, the demo database

The target control plane is ``UTRACE_DEMO_API_BASE_URL``; the harness signs in
as ``UTRACE_DEMO_STAFF_EMAIL`` through Supabase Auth. Every command exits
non-zero with a named reason when a check does not hold.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import Final

import structlog

from demo_harness import scenario
from demo_harness.billing import (
    finalize_service_invoice,
    read_balance_transaction,
    verify_invoice_application,
    verify_reward_identity,
)
from demo_harness.checks import Finding, holds, print_findings, violated
from demo_harness.control_plane import ControlPlane, sign_in
from demo_harness.models import OfferDisposition
from demo_harness.prepare import prepare, resolve_client
from demo_harness.rehearse import rehearse_intake
from demo_harness.reset import inventory, print_inventory, reset_target
from demo_harness.settings import (
    ControlPlaneSettings,
    DatabaseSettings,
    DiscordSettings,
    StripeSettings,
)
from demo_harness.verify import verify
from demo_harness.watch import watch

__all__ = ["main"]

_GUARD: Final[str] = "--i-understand-this-deletes-demo-state"
_DEFAULT_POLL_SECONDS: Final[float] = 5.0
_DEFAULT_WATCH_DEADLINE_SECONDS: Final[float] = 3600.0
_DEFAULT_REHEARSAL_DEADLINE_SECONDS: Final[float] = 300.0

_log = structlog.get_logger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Build the command parser."""
    parser = argparse.ArgumentParser(prog="demo", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("prepare", help="converge and check the scenario's starting state")

    rehearsal = commands.add_parser(
        "rehearse-intake", help="REHEARSAL ONLY: post the canonical message and check routing"
    )
    rehearsal.add_argument("--poll-seconds", type=float, default=_DEFAULT_POLL_SECONDS)
    rehearsal.add_argument(
        "--deadline-seconds", type=float, default=_DEFAULT_REHEARSAL_DEADLINE_SECONDS
    )

    following = commands.add_parser("watch", help="print each scenario milestone as it commits")
    following.add_argument("--poll-seconds", type=float, default=_DEFAULT_POLL_SECONDS)
    following.add_argument(
        "--deadline-seconds", type=float, default=_DEFAULT_WATCH_DEADLINE_SECONDS
    )

    commands.add_parser(
        "settle-invoice", help="finalize the service invoice and cross-check the credit"
    )
    commands.add_parser("verify", help="assert the scenario's acceptance boundary")

    resetting = commands.add_parser(
        "reset-target", help="report, or with the guard flag remove, the demo database"
    )
    resetting.add_argument(_GUARD, dest="confirmed", action="store_true")
    return parser


async def _connected() -> tuple[ControlPlane, ControlPlaneSettings]:
    """Sign in and bind a client to the configured target."""
    settings = ControlPlaneSettings.from_environment()
    session = await sign_in(settings)
    return ControlPlane(base_url=settings.api_base_url, session=session), settings


async def _prepare() -> int:
    """Run ``prepare`` against the configured target."""
    control_plane, settings = await _connected()
    async with control_plane:
        findings = await prepare(
            control_plane,
            staff_email=settings.staff_email,
            stripe_settings=StripeSettings.from_environment(),
        )
    return print_findings(f"prepare -> {settings.api_base_url}", findings)


async def _rehearse(poll_seconds: float, deadline_seconds: float) -> int:
    """Run ``rehearse-intake`` against the configured target."""
    control_plane, settings = await _connected()
    async with control_plane:
        client_id = await resolve_client(control_plane, staff_email=settings.staff_email)
        findings = await rehearse_intake(
            control_plane,
            client_id=client_id,
            discord_settings=DiscordSettings.from_environment(),
            poll_seconds=poll_seconds,
            deadline_seconds=deadline_seconds,
        )
    return print_findings(f"rehearse-intake -> {settings.api_base_url}", findings)


async def _watch(poll_seconds: float, deadline_seconds: float) -> int:
    """Run ``watch`` against the configured target."""
    control_plane, settings = await _connected()
    async with control_plane:
        client_id = await resolve_client(control_plane, staff_email=settings.staff_email)
        return await watch(
            control_plane,
            client_id=client_id,
            poll_seconds=poll_seconds,
            deadline_seconds=deadline_seconds,
        )


async def _settle_invoice() -> int:
    """Run ``settle-invoice`` against the configured target and Stripe sandbox."""
    control_plane, settings = await _connected()
    stripe_settings = StripeSettings.from_environment()
    async with control_plane:
        client_id = await resolve_client(control_plane, staff_email=settings.staff_email)
        view = await control_plane.offers(client_id, scenario.REWARD_CURRENCY.upper())
    findings = await _settlement_findings(
        issued=[offer for offer in view.offers if offer.provider_transaction_ref is not None],
        stripe_settings=stripe_settings,
    )
    return print_findings(f"settle-invoice -> {stripe_settings.dudulemon_customer_id}", findings)


async def _settlement_findings(
    *, issued: Sequence[OfferDisposition], stripe_settings: StripeSettings
) -> list[Finding]:
    """Cross-check the recorded credit, then finalize and check the invoice."""
    if len(issued) != 1:
        return [
            violated(
                "credit_recorded_by_utrace",
                f"{len(issued)} offers carry a Stripe transaction reference; settle-invoice "
                f"needs exactly one issued credit to cross-check",
            )
        ]
    offer = issued[0]
    reference = offer.provider_transaction_ref
    if reference is None:
        raise RuntimeError("the filtered offer lost its Stripe transaction reference")
    transaction = await read_balance_transaction(stripe_settings, transaction_id=reference)
    verify_reward_identity(transaction, customer_id=stripe_settings.dudulemon_customer_id)
    findings = [
        holds(
            "credit_recorded_by_utrace",
            f"uTrace recorded {reference}; Stripe reports {transaction.amount_minor_units} "
            f"{transaction.currency} minor units for customer {transaction.customer_id}, "
            f"ending balance {transaction.ending_balance_minor_units}",
        )
    ]
    invoice = await finalize_service_invoice(stripe_settings)
    verify_invoice_application(invoice)
    findings.append(
        holds(
            "invoice_shows_the_credit",
            f"invoice {invoice.invoice_id} ({invoice.status}) totals "
            f"{invoice.total_minor_units}, applied {invoice.applied_credit_minor_units}, "
            f"leaving {invoice.amount_due_minor_units} due",
        )
    )
    return findings


async def _verify() -> int:
    """Run ``verify`` against the configured target."""
    control_plane, settings = await _connected()
    async with control_plane:
        client_id = await resolve_client(control_plane, staff_email=settings.staff_email)
        findings = await verify(control_plane, client_id=client_id)
    return print_findings(f"verify -> {settings.api_base_url}", findings)


async def _reset(confirmed: bool) -> int:
    """Report the target's contents, or remove them when confirmed."""
    settings = DatabaseSettings.from_environment()
    if not confirmed:
        print_inventory("reset-target (report only)", await inventory(settings))
        sys.stdout.write(
            f"nothing was removed. Re-run with {_GUARD} to truncate every table listed above.\n"
        )
        return 0
    print_inventory("reset-target (removing)", await reset_target(settings))
    sys.stdout.write("the target is in a clean pre-prepare state\n")
    return 0


async def _dispatch(arguments: argparse.Namespace) -> int:
    """Run the selected command."""
    match arguments.command:
        case "prepare":
            return await _prepare()
        case "rehearse-intake":
            return await _rehearse(arguments.poll_seconds, arguments.deadline_seconds)
        case "watch":
            return await _watch(arguments.poll_seconds, arguments.deadline_seconds)
        case "settle-invoice":
            return await _settle_invoice()
        case "verify":
            return await _verify()
        case "reset-target":
            return await _reset(arguments.confirmed)
        case unknown:
            raise SystemExit(f"unknown command {unknown!r}")


def main() -> int:
    """Parse the command line, run the command, and return its exit status."""
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(20))
    arguments = _parser().parse_args()
    try:
        status = asyncio.run(_dispatch(arguments))
    except Exception as failure:
        _log.error("demo_command_failed", command=arguments.command, error=str(failure))
        sys.stderr.write(f"{type(failure).__name__}: {failure}\n")
        return 2
    return status


if __name__ == "__main__":
    raise SystemExit(main())
