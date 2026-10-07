"""A throwaway private GitHub repository for proving the delivery protocol.

Delivery authenticates as a GitHub App; the App path is proven separately, gated
on the App's private key. The
protocol itself — preflight, deterministic branch, hidden marker, reconciliation,
exact-head verification, and the refusal to force-push — is provider behaviour,
not authentication behaviour, so it is proved here against a real repository
using the ``gh`` CLI's token.

``TokenAuthorizedDelivery`` subclasses the production connector and replaces only
how it obtains a token. Every request, payload, and response path under test is
the connector's own.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from app.connectors.github_delivery import GitHubAppDelivery

__all__ = [
    "SMOKE_REPOSITORY",
    "SmokeRepository",
    "TokenAuthorizedDelivery",
    "delete_repository",
    "ensure_repository",
    "gh_token",
]

SMOKE_REPOSITORY = "rithm84/utrace-delivery-smoke"


@dataclass(frozen=True, slots=True)
class SmokeRepository:
    """The throwaway repository the delivery protocol runs against."""

    owner: str
    name: str
    default_branch: str
    head_sha: str
    """The default branch's head, a real commit a delivery can be based on."""
    token: str

    @property
    def full_name(self) -> str:
        """``owner/name`` for this repository."""
        return f"{self.owner}/{self.name}"


class TokenAuthorizedDelivery(GitHubAppDelivery):
    """The production delivery connector, authenticated with a personal token.

    Only ``installation_token`` changes. The App JWT is never built, so the
    placeholder key is never used.
    """

    def __init__(self, *, repository: SmokeRepository, request_timeout_seconds: float) -> None:
        super().__init__(
            app_id="token-authorized",
            installation_id="token-authorized",
            private_key_pem="",
            owner=repository.owner,
            repository=repository.name,
            request_timeout_seconds=request_timeout_seconds,
        )
        self._personal_token = repository.token

    async def installation_token(self) -> str:
        """Return the personal access token this smoke run is authorized with."""
        return self._personal_token


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments, capture_output=True, text=True, check=False, timeout=120
    )


def gh_token() -> str | None:
    """Return the ``gh`` CLI's token, or ``None`` when it has none."""
    result = _run(["gh", "auth", "token"])
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def ensure_repository(*, token: str) -> SmokeRepository:
    """Create the throwaway private repository if it does not already exist."""
    owner, _, name = SMOKE_REPOSITORY.partition("/")
    view = _run(["gh", "repo", "view", SMOKE_REPOSITORY, "--json", "defaultBranchRef"])
    if view.returncode != 0:
        created = _run(
            ["gh", "repo", "create", SMOKE_REPOSITORY, "--private", "--add-readme"]
        )
        if created.returncode != 0:
            raise RuntimeError(
                f"could not create {SMOKE_REPOSITORY}: {created.stderr.strip()}"
            )
        view = _run(
            ["gh", "repo", "view", SMOKE_REPOSITORY, "--json", "defaultBranchRef"]
        )
        if view.returncode != 0:
            raise RuntimeError(
                f"could not read {SMOKE_REPOSITORY}: {view.stderr.strip()}"
            )
    payload = json.loads(view.stdout)
    branch = payload["defaultBranchRef"]["name"]
    head = _run(["gh", "api", f"repos/{SMOKE_REPOSITORY}/commits/{branch}", "--jq", ".sha"])
    if head.returncode != 0:
        raise RuntimeError(f"could not read the {branch} head of {SMOKE_REPOSITORY}: {head.stderr.strip()}")
    return SmokeRepository(
        owner=owner, name=name, default_branch=branch, head_sha=head.stdout.strip(), token=token
    )


def delete_repository() -> str:
    """Delete the throwaway repository, returning what GitHub said.

    Returns an empty string when the deletion succeeded, and the provider's
    refusal otherwise; the caller reports it rather than swallowing it.
    """
    result = _run(["gh", "repo", "delete", SMOKE_REPOSITORY, "--yes"])
    if result.returncode == 0 and not result.stderr.strip():
        return ""
    return (result.stderr or result.stdout).strip()
