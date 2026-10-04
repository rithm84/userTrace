"""The GitHub App half of delivery, gated on the App's credentials being present.

Delivery authenticates as the narrowly scoped uTrace GitHub App, whose private
key the environment carries as ``UTRACE_GITHUB_PRIVATE_KEY``. Without it the test
skips with that reason instead of passing vacuously. The protocol itself is
proved against a real repository in ``test_delivery_live``; what is gated here is
only whether this machine may authenticate as the App.
"""

from __future__ import annotations

import os

import pytest

from app.connectors.github_delivery import GitHubAppDelivery, github_app_from_environment
from app.outputs.delivery import DeliveryRepositoryPort

REQUEST_TIMEOUT_SECONDS = 30.0


_REQUIRED = (
    "UTRACE_GITHUB_APP_ID",
    "UTRACE_GITHUB_INSTALLATION_ID",
    "UTRACE_GITHUB_PRIVATE_KEY",
    "UTRACE_GITHUB_OWNER",
    "UTRACE_GITHUB_REPOSITORY",
)


def _app_delivery() -> GitHubAppDelivery:
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.skip(f"the GitHub App credentials are not configured: {', '.join(missing)} unset")
    return github_app_from_environment(
        repository_ref=f"{os.environ['UTRACE_GITHUB_OWNER']}/{os.environ['UTRACE_GITHUB_REPOSITORY']}",
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )


async def test_the_app_satisfies_the_delivery_port_and_preflights() -> None:
    """The App connector is what the delivery controller depends on."""
    async with _app_delivery() as delivery:
        assert isinstance(delivery, DeliveryRepositoryPort)
        token = await delivery.installation_token()
        assert token.startswith("ghs_")
        default_branch = await delivery.read_default_branch()
        head = await delivery.resolve_branch_head(branch=default_branch)
        assert head is not None
        assert await delivery.verify_head(
            branch=default_branch, expected_head_sha=head
        )


async def test_the_production_connector_satisfies_the_delivery_port() -> None:
    """The App connector fits the delivery seam, with no credentials involved."""
    connector = GitHubAppDelivery(
        app_id="structural-check",
        installation_id="structural-check",
        private_key_pem="",
        owner="ShopalyticsStore",
        repository="shopalytics",
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )
    async with connector as delivery:
        assert isinstance(delivery, DeliveryRepositoryPort)
        assert delivery.repository_full_name == "ShopalyticsStore/shopalytics"
