"""Live smoke: GitHub App authentication and read-only repository preflight.

Exercises the first half of the delivery protocol: App JWT, installation access token, and
the preflight reads that confirm the installation can see the one authorized repository.

No branch, commit, or pull request is created. Delivery writes are gated on same-user
validation of an exact candidate, which no smoke test has, and a stray uTrace branch or
draft PR in a client repository is not something a test should leave behind.
"""

from __future__ import annotations

from app.connectors.github_delivery import (
    GitHubAppDelivery,
    github_app_from_environment,
    idempotency_marker,
)
from tests.connectors.conftest import require_env


def _delivery() -> GitHubAppDelivery:
    values = require_env(
        "UTRACE_GITHUB_APP_ID",
        "UTRACE_GITHUB_INSTALLATION_ID",
        "UTRACE_GITHUB_PRIVATE_KEY",
        "UTRACE_GITHUB_OWNER",
        "UTRACE_GITHUB_REPOSITORY",
    )
    return github_app_from_environment(
        repository_ref=f"{values['UTRACE_GITHUB_OWNER']}/{values['UTRACE_GITHUB_REPOSITORY']}",
        request_timeout_seconds=30.0,
    )


async def test_installation_token_and_repository_preflight() -> None:
    async with _delivery() as delivery:
        token = await delivery.installation_token()
        assert token.startswith("ghs_")

        # A second call reuses the cached token rather than minting another.
        assert await delivery.installation_token() == token

        default_branch = await delivery.read_default_branch()
        assert default_branch

        head = await delivery.resolve_branch_head(branch=default_branch)
        assert head is not None
        assert len(head) == 40

        assert await delivery.verify_head(
            branch=default_branch, expected_head_sha=head
        )
        assert not await delivery.verify_head(
            branch=default_branch, expected_head_sha="0" * 40
        )


async def test_an_absent_branch_resolves_to_none() -> None:
    async with _delivery() as delivery:
        absent = await delivery.resolve_branch_head(
            branch="utrace/connector-smoke-never-created"
        )
        assert absent is None


async def test_no_delivery_exists_for_an_unused_marker() -> None:
    async with _delivery() as delivery:
        marker = idempotency_marker(command_id="cmd-connector-smoke-unused")
        assert await delivery.find_existing_delivery(marker=marker) is None
