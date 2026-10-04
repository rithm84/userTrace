"""``demo prepare``: bring a target control plane to the scenario's starting state.

Everything the control plane exposes a route for is converged here; everything
else is asserted and reported. The harness never writes demo state through raw
SQL against a target, so client onboarding — the client, its configuration
release, the customer accounts, Maya's identity, and the provider references —
stays with the server-side onboarding tool, and a missing piece is reported as a
precise, actionable gap rather than silently created.

Onboarding, when a check below reports it missing::

    set -a; source .env.local; set +a
    uv run --project apps/api python apps/api/scripts/seed_shopalytics_client.py
"""

from __future__ import annotations

from uuid import UUID

from utrace_contracts.domain.lifecycle import ClientMemberRole

from demo_harness import scenario
from demo_harness.billing import credit_balance_minor_units
from demo_harness.checks import Finding, holds, violated
from demo_harness.control_plane import ControlPlane
from demo_harness.models import (
    ConfigurationRelease,
    CustomerAccount,
    OutreachOfferPolicy,
)
from demo_harness.settings import StripeSettings

__all__ = ["ClientNotOnboardedError", "prepare", "resolve_client"]

_SEED_COMMAND = (
    "uv run --project apps/api python apps/api/scripts/seed_shopalytics_client.py"
)


class ClientNotOnboardedError(RuntimeError):
    """The target holds no Shopalytics client for the demo staff identity."""

    def __init__(self, *, base_url: str, staff_email: str, seen: list[str]) -> None:
        """Name the target, the identity, and the command that onboards it."""
        super().__init__(
            f"{base_url} has no '{scenario.CLIENT_NAME}' client that {staff_email} owns "
            f"(saw: {seen or ['no client memberships at all']}). "
            f"There is no staff route that creates one, so run the onboarding tool "
            f"against that target's database first: {_SEED_COMMAND}"
        )


async def resolve_client(control_plane: ControlPlane, *, staff_email: str) -> UUID:
    """Return the Shopalytics client the demo staff identity owns."""
    listing = await control_plane.staff_clients()
    for entry in listing.clients:
        if entry.client.name == scenario.CLIENT_NAME:
            return entry.client.id
    raise ClientNotOnboardedError(
        base_url=control_plane.base_url,
        staff_email=staff_email,
        seen=[entry.client.name for entry in listing.clients],
    )


def _configuration_findings(release: ConfigurationRelease) -> list[Finding]:
    """Check the pinned release against every configuration fact the scenario names."""
    policy = release.policy
    surface = policy.customer_surface
    implementation = policy.implementation
    seeding = policy.data_seeding
    offers = policy.outreach_offers
    findings = [
        holds(
            "configuration_release_pinned",
            f"release {release.release_number} ({release.id}), "
            f"digest {release.digest[:23]}",
        ),
        (
            holds(
                "discord_customer_surface",
                f"{surface.provider} installation {surface.installation_ref} with "
                f"{len(surface.watched_channels)} watched channels: "
                f"{', '.join(channel.name for channel in surface.watched_channels)}",
            )
            if surface.provider == "discord" and surface.watched_channels
            else violated(
                "discord_customer_surface",
                f"the release pins provider {surface.provider!r} with "
                f"{len(surface.watched_channels)} watched channels",
            )
        ),
        (
            holds(
                "repository_scope",
                f"{implementation.repository_ref} at {implementation.base_revision[:12]}; "
                f"permitted {list(implementation.permitted_paths)}, "
                f"blocked {list(implementation.blocked_paths)}, "
                f"workspace {implementation.workspace_snapshot_id}",
            )
            if implementation.permitted_paths
            else violated(
                "repository_scope",
                f"the release pins {implementation.repository_ref} with no permitted paths",
            )
        ),
        (
            holds(
                "required_checks_pinned",
                "implementation requires "
                f"{[check.command for check in implementation.required_checks]}",
            )
            if implementation.required_checks
            else violated("required_checks_pinned", "the release pins no required checks")
        ),
        (
            holds("live_implementation_permitted", "the release allows live implementation")
            if implementation.live_implementation_allowed
            else violated(
                "live_implementation_permitted",
                "the scenario's canonical gate result is approved_live, but the release "
                "allows no live implementation",
            )
        ),
        holds(
            "preview_template_and_seeding_contract",
            f"template {seeding.environment_template_id} revision "
            f"{seeding.environment_template_revision}, manifest {seeding.manifest_hash[:23]}…, "
            f"fixture clock {seeding.fixture_clock}",
        ),
    ]
    findings.append(_offer_policy_finding(offers))
    return findings


def _offer_policy_finding(policy: OutreachOfferPolicy) -> Finding:
    """Check the release's Outreach Offers policy against the fixed reward."""
    if not policy.enabled:
        return violated(
            "outreach_offers_enabled",
            "the release disables Outreach Offers; the invitation cannot carry the reward",
        )
    if policy.currency.lower() != scenario.REWARD_CURRENCY:
        return violated(
            "outreach_offers_enabled",
            f"the release pins {policy.currency}, not {scenario.REWARD_CURRENCY.upper()}",
        )
    if policy.fixed_amount_minor_units != scenario.REWARD_MINOR_UNITS:
        return violated(
            "outreach_offers_enabled",
            f"the release pins a {policy.fixed_amount_minor_units} minor unit reward, not "
            f"the scenario's {scenario.REWARD_MINOR_UNITS}",
        )
    return holds(
        "outreach_offers_enabled",
        f"{policy.fixed_amount_minor_units} minor units in {policy.currency}, inside a "
        f"{policy.authorized_ceiling_minor_units} ceiling, billed as {policy.billing_use!r}",
    )


def _account_findings(accounts: list[CustomerAccount], *, stripe_customer_id: str) -> list[Finding]:
    """Check that Dudulemon exists and maps to the sandbox Stripe Customer."""
    by_key = {account.external_account_key: account for account in accounts}
    findings: list[Finding] = []

    dudulemon = by_key.get(scenario.CUSTOMER_ACCOUNT_KEY)
    if dudulemon is None:
        findings.append(
            violated(
                "dudulemon_eligible",
                f"no customer account keyed {scenario.CUSTOMER_ACCOUNT_KEY!r} exists; "
                f"run {_SEED_COMMAND}",
            )
        )
    else:
        findings.append(holds("dudulemon_eligible", f"{dudulemon.name} ({dudulemon.id}) exists"))

    if dudulemon is None:
        findings.append(violated("dudulemon_stripe_mapping", "the account does not exist"))
    elif dudulemon.stripe_customer_ref is None:
        findings.append(
            violated(
                "dudulemon_stripe_mapping",
                "no Stripe Customer is mapped to Dudulemon; the offer reservation reads that "
                "mapping and refuses without it",
            )
        )
    elif dudulemon.stripe_customer_ref != stripe_customer_id:
        findings.append(
            violated(
                "dudulemon_stripe_mapping",
                f"the control plane maps {dudulemon.stripe_customer_ref}, but "
                f"UTRACE_STRIPE_DUDULEMON_CUSTOMER_ID names {stripe_customer_id}",
            )
        )
    else:
        findings.append(
            holds("dudulemon_stripe_mapping", f"mapped to {dudulemon.stripe_customer_ref}")
        )

    return findings


async def prepare(
    control_plane: ControlPlane, *, staff_email: str, stripe_settings: StripeSettings
) -> list[Finding]:
    """Converge and check the scenario's starting state on one target."""
    status = await control_plane.health()
    findings = [holds("target_reachable", f"{control_plane.base_url} reports {status}")]
    client_id = await resolve_client(control_plane, staff_email=staff_email)
    listing = await control_plane.staff_clients()
    membership = next(entry for entry in listing.clients if entry.client.id == client_id)
    findings.append(
        holds(
            "shopalytics_client",
            f"{membership.client.name} ({client_id}); {staff_email} is "
            f"{membership.membership.role.value}",
        )
    )

    release = await control_plane.configuration(client_id)
    findings.extend(_configuration_findings(release))

    accounts = await control_plane.customer_accounts(client_id)
    findings.extend(
        _account_findings(
            accounts.customer_accounts,
            stripe_customer_id=stripe_settings.dudulemon_customer_id,
        )
    )

    findings.append(
        await _converge_allowance(
            control_plane,
            client_id=client_id,
            is_owner=membership.membership.role is ClientMemberRole.OWNER,
            ceiling=release.policy.outreach_offers.authorized_ceiling_minor_units,
        )
    )
    findings.append(await _starting_credit_balance(stripe_settings))
    return findings


async def _converge_allowance(
    control_plane: ControlPlane, *, client_id: UUID, is_owner: bool, ceiling: int
) -> Finding:
    """Raise the issuance allowance to the release's ceiling when it is short."""
    currency = scenario.REWARD_CURRENCY.upper()
    allowance = await control_plane.allowance(client_id, currency)
    if allowance.remaining_minor_units >= scenario.REWARD_MINOR_UNITS:
        return holds(
            "outreach_offers_allowance",
            f"{allowance.remaining_minor_units} of {allowance.authorized_minor_units} minor "
            f"units remain in {currency}, covering the {scenario.REWARD_MINOR_UNITS} reward",
        )
    if not is_owner:
        return violated(
            "outreach_offers_allowance",
            f"only {allowance.remaining_minor_units} minor units remain, and only the client "
            f"owner may raise the ceiling",
        )
    raised = await control_plane.authorize_allowance(
        client_id, currency=currency, authorized_minor_units=ceiling
    )
    if raised.remaining_minor_units < scenario.REWARD_MINOR_UNITS:
        return violated(
            "outreach_offers_allowance",
            f"raised the ceiling to {raised.authorized_minor_units}, but only "
            f"{raised.remaining_minor_units} minor units remain, which cannot cover the "
            f"{scenario.REWARD_MINOR_UNITS} reward",
        )
    return holds(
        "outreach_offers_allowance",
        f"authorized {raised.authorized_minor_units} minor units in {currency}; "
        f"{raised.remaining_minor_units} remain, covering the "
        f"{scenario.REWARD_MINOR_UNITS} reward",
    )


async def _starting_credit_balance(stripe_settings: StripeSettings) -> Finding:
    """Verify the fixture's required zero starting invoice-credit balance."""
    balance = await credit_balance_minor_units(stripe_settings)
    if balance == 0:
        return holds(
            "zero_starting_credit_balance",
            f"customer {stripe_settings.dudulemon_customer_id} holds no credit",
        )
    return violated(
        "zero_starting_credit_balance",
        f"customer {stripe_settings.dudulemon_customer_id} already holds {balance} minor units "
        f"of credit; the fixture requires a zero starting balance and no other balance "
        f"adjustments",
    )
