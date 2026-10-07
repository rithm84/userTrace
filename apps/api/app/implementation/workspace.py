"""Workspace creation, the pinned checkout, and read-only filesystem access.

A workspace is a Vercel sandbox named for the attempt and lease it serves, so a
superseded lease's sandbox is never mistaken for the current one. The repository
is cloned inside it while the checkout egress policy adds the repository token at
the firewall; the token never exists inside the sandbox, so the checkout keeps no
credential in its remote or its history.

Reads mutate nothing, which is what lets the read-only pre-gate investigation
inspect an authorized checkout with no Environment Lease at all.
"""

from __future__ import annotations

import shlex
from typing import Final

import structlog

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    redacted_parameters,
)
from app.connectors.vercel_sandboxes import EgressPolicy, SandboxHandle, VercelSandboxes

__all__ = [
    "WorkspaceFilesystem",
    "checkout_repository",
    "create_workspace",
]

_logger = structlog.get_logger(__name__)
_PROVIDER: Final[str] = "vercel"
_GIT_TIMEOUT_SECONDS: Final[int] = 600
_SEARCH_TIMEOUT_SECONDS: Final[int] = 120
_WORKSPACE_PORTS: Final[tuple[int, ...]] = ()
"""A workspace serves nothing: the candidate runs in its own preview runtime."""


async def create_workspace(
    sandboxes: VercelSandboxes,
    *,
    name: str,
    snapshot_id: str,
    egress: EgressPolicy,
    env: dict[str, str],
    execution_time_limit_seconds: int,
    vcpus: int,
) -> SandboxHandle:
    """Create one workspace from the pinned template snapshot behind its wall."""
    return await sandboxes.create_from_snapshot(
        name=name,
        snapshot_id=snapshot_id,
        ports=_WORKSPACE_PORTS,
        env=env,
        egress=egress,
        execution_time_limit_seconds=execution_time_limit_seconds,
        vcpus=vcpus,
    )


async def checkout_repository(
    sandboxes: VercelSandboxes,
    handle: SandboxHandle,
    *,
    clone_url: str,
    base_revision: str,
    workspace_root: str,
) -> str:
    """Clone the authorized repository at the pinned commit and verify the head.

    Returns the commit the checkout resolved to. A mismatch is a refusal: an
    attempt must never edit a revision the gate did not approve.
    """
    script = (
        f"git clone --quiet {shlex.quote(clone_url)} {shlex.quote(workspace_root)} && "
        f"git -C {shlex.quote(workspace_root)} checkout --quiet --detach "
        f"{shlex.quote(base_revision)} && "
        f"git -C {shlex.quote(workspace_root)} rev-parse HEAD"
    )
    result = await sandboxes.run(
        handle, script=script, cwd="/", timeout_seconds=_GIT_TIMEOUT_SECONDS, sudo=False
    )
    head = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.exit_code != 0 or head != base_revision:
        raise ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="checkout_repository",
                category=TechnicalCategory.VALIDATION,
                retry_advice=RetryAdvice.NEVER,
                effect_state=EffectState.COMPLETED,
                message=(
                    f"the checkout exited {result.exit_code} and resolved to '{head}', not "
                    f"the pinned base revision '{base_revision}': {result.stdout[-1000:]}"
                ),
                request_parameters=redacted_parameters(
                    {"name": handle.name, "workspace_root": workspace_root}
                ),
            )
        )
    _logger.info(
        "implementation.workspace.checked_out", name=handle.name, base_revision=base_revision
    )
    return head


class WorkspaceFilesystem:
    """Read-only, typed filesystem access to one workspace's checkout."""

    def __init__(self, sandboxes: VercelSandboxes, handle: SandboxHandle, *, root: str) -> None:
        self._sandboxes = sandboxes
        self._handle = handle
        self._root = root


    async def tracked_paths(self) -> tuple[str, ...]:
        """Return the checkout-relative paths git tracks."""
        output = await self._search("git ls-files")
        return tuple(line for line in output.splitlines() if line)


    async def read_text(self, *, path: str) -> str:
        """Read one checkout-relative file as UTF-8 text."""
        body = await self._sandboxes.read_file(self._handle, path=f"{self._root}/{path}")
        return body.decode("utf-8", errors="replace")

    async def _search(self, script: str) -> str:
        result = await self._sandboxes.run(
            self._handle,
            script=script,
            cwd=self._root,
            timeout_seconds=_SEARCH_TIMEOUT_SECONDS,
            sudo=False,
        )
        if result.exit_code != 0:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="search",
                    category=TechnicalCategory.PROVIDER_REJECTED,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.ABSENT,
                    message=f"workspace search exited {result.exit_code}: {result.stdout[-500:]}",
                    request_parameters=redacted_parameters({"name": self._handle.name}),
                )
            )
        return result.stdout
