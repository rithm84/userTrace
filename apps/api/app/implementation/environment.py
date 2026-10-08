"""Where an implementation workspace keeps its checkout, and what it may reach.

The workspace is a Vercel sandbox created from a uTrace-owned template snapshot of
Vercel's default image plus the pinned Codex CLI and its model catalog
(``scripts/build_workspace_snapshot.py``). It contains the authorized checkout and
scratch space, and no customer dataset, production credential, or workflow
authority. Commands run as the image's non-root default user.

Credentials never enter the sandbox. The sandbox firewall adds the OpenAI API key
to requests bound for the OpenAI API, and the repository token to requests bound
for GitHub while the checkout is cloned; once the checkout exists, the policy is
replaced by one that no longer reaches GitHub at all, so nothing the agent runs can
push. See the [implementation contract](../../../../docs/arch-components/implementation.md#agent-harness).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Final

from app.connectors.vercel_sandboxes import EgressPolicy, HeaderInjection

__all__ = [
    "BASELINE_ROOT",
    "CODEX_BINARY",
    "CODEX_MODEL_CATALOG",
    "CODEX_ROOT",
    "CODEX_VERSION",
    "DEPENDENCY_INSTALL_STATUS",
    "OPENAI_API_DOMAIN",
    "PLACEHOLDER_API_KEY",
    "REGISTRY_DOMAINS",
    "WORKSPACE_ROOT",
    "WORKSPACE_VCPUS",
    "checkout_egress",
    "preparation_egress",
    "working_egress",
]

WORKSPACE_ROOT: Final[str] = "/vercel/sandbox/repository"
"""Where the authorized repository is checked out inside the workspace."""

WORKSPACE_VCPUS: Final[int] = 8
"""Enough for the dependency install, and the baseline and candidate checks at once."""

BASELINE_ROOT: Final[str] = "/vercel/sandbox/baseline"
"""Where a copy of the clean base runs the baseline checks, outside the checkout."""

DEPENDENCY_INSTALL_STATUS: Final[str] = "/tmp/utrace-dependency-install.status"
"""Where the workspace writes the dependency install's exit status once it ends.

The install runs while the first cycle is authored, so the agent waits for this file
before it runs anything that needs the dependencies."""

CODEX_VERSION: Final[str] = "0.159.3"
"""The `@openai/codex` CLI release the workspace template installs."""

CODEX_ROOT: Final[str] = "/opt/utrace/codex"
"""Where the template installs the pinned Codex CLI and its model catalog."""

CODEX_BINARY: Final[str] = f"{CODEX_ROOT}/node_modules/.bin/codex"

CODEX_MODEL_CATALOG: Final[str] = f"{CODEX_ROOT}/models.json"
"""Codex's own catalog for the pinned release, with the `ultrafast` tier added to GPT-6
Astra and GPT-6.1 Sol.

Codex sends a service tier only when the model's catalog entry advertises it, and the
release bundled with the CLI lists only `priority` for both."""

PLACEHOLDER_API_KEY: Final[str] = "utrace-injected-at-egress"
"""What the sandbox's `CODEX_API_KEY` holds: never a credential.

Codex refuses to start without a key, and the firewall replaces the `Authorization`
header on every request to the OpenAI API with the real one.
"""

OPENAI_API_DOMAIN: Final[str] = "api.openai.com"

REGISTRY_DOMAINS: Final[tuple[str, ...]] = ("registry.npmjs.org", "*.npmjs.org")
"""The package registry the authorized repository's build recipe installs from."""

_GITHUB_DOMAIN: Final[str] = "github.com"


def working_egress(*, callback_hosts: Sequence[str], openai_api_key: str) -> EgressPolicy:
    """Return the wall a workspace runs behind once its checkout exists."""
    return EgressPolicy(
        allowed_domains=(*REGISTRY_DOMAINS, OPENAI_API_DOMAIN, *callback_hosts),
        injections=(_openai_injection(openai_api_key),),
    )


def checkout_egress(
    *, callback_hosts: Sequence[str], openai_api_key: str, repository_token: str
) -> EgressPolicy:
    """Return the wall a workspace is created behind, which also reaches GitHub.

    The repository token travels as HTTP basic authorization added at egress, the
    form GitHub accepts for an installation token over HTTPS.
    """
    return EgressPolicy(
        allowed_domains=(*REGISTRY_DOMAINS, OPENAI_API_DOMAIN, _GITHUB_DOMAIN, *callback_hosts),
        injections=(_openai_injection(openai_api_key), _github_injection(repository_token)),
    )


def preparation_egress(*, repository_token: str | None) -> EgressPolicy:
    """Return the wall a workspace is prepared behind, before any model runs in it.

    It reaches the package registry, and GitHub only while a repository token is
    given for the checkout; it never reaches the OpenAI API.
    """
    if repository_token is None:
        return EgressPolicy(allowed_domains=REGISTRY_DOMAINS, injections=())
    return EgressPolicy(
        allowed_domains=(*REGISTRY_DOMAINS, _GITHUB_DOMAIN),
        injections=(_github_injection(repository_token),),
    )


def _github_injection(repository_token: str) -> HeaderInjection:
    basic = base64.b64encode(f"x-access-token:{repository_token}".encode()).decode()
    return HeaderInjection(domain=_GITHUB_DOMAIN, headers={"Authorization": f"Basic {basic}"})


def _openai_injection(openai_api_key: str) -> HeaderInjection:
    return HeaderInjection(
        domain=OPENAI_API_DOMAIN, headers={"Authorization": f"Bearer {openai_api_key}"}
    )
