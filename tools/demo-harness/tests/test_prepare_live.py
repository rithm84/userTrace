"""``prepare`` against the configured target, with a real Supabase Auth session.

This is the harness's own smoke test: it signs in, resolves the Shopalytics
client, and runs every starting-state check against the real control plane and
the real Stripe sandbox. It skips, naming the variable, when the target or its
credentials are not configured.
"""

from __future__ import annotations

import os

import pytest

from demo_harness.checks import failed
from demo_harness.control_plane import ControlPlane, sign_in
from demo_harness.prepare import prepare, resolve_client
from demo_harness.settings import ControlPlaneSettings, StripeSettings

_REQUIRED = (
    "UTRACE_DEMO_API_BASE_URL",
    "SUPABASE_URL",
    "UTRACE_SUPABASE_PUBLISHABLE_KEY",
    "UTRACE_DEMO_STAFF_EMAIL",
    "UTRACE_DEMO_STAFF_PASSWORD",
    "STRIPE_SANDBOX_SECRET_KEY",
    "UTRACE_STRIPE_DUDULEMON_CUSTOMER_ID",
)


def _configured() -> None:
    for name in _REQUIRED:
        if not os.environ.get(name):
            pytest.skip(f"{name} is unset; the live prepare smoke needs it")


async def test_prepare_reports_the_scenario_starting_state() -> None:
    _configured()
    settings = ControlPlaneSettings.from_environment()
    session = await sign_in(settings)
    assert session.access_token

    async with ControlPlane(base_url=settings.api_base_url, session=session) as control_plane:
        assert await control_plane.health() == "ok"
        client_id = await resolve_client(control_plane, staff_email=settings.staff_email)
        findings = await prepare(
            control_plane,
            staff_email=settings.staff_email,
            stripe_settings=StripeSettings.from_environment(),
        )

    assert findings
    assert failed(findings) == [], [finding.model_dump() for finding in failed(findings)]
    assert str(client_id)


async def test_prepare_is_idempotent() -> None:
    _configured()
    settings = ControlPlaneSettings.from_environment()
    session = await sign_in(settings)
    stripe_settings = StripeSettings.from_environment()
    async with ControlPlane(base_url=settings.api_base_url, session=session) as control_plane:
        first = await prepare(
            control_plane, staff_email=settings.staff_email, stripe_settings=stripe_settings
        )
        second = await prepare(
            control_plane, staff_email=settings.staff_email, stripe_settings=stripe_settings
        )
    assert [finding.name for finding in first] == [finding.name for finding in second]
    assert failed(second) == []
