"""A case's implementation workspace, prepared while its interview runs.

Contract: docs/arch-components/implementation.md#prepared-workspace

Once the participant claims the invitation, a Job prepares the workspace the case's
Implementation Attempt would use, from the release the case pins: a sandbox from
the pinned template, the authorized repository checked out at the pinned base
revision, its dependencies installed from the client's recipe, and the required
checks run on the clean base. Nothing in it belongs to the case or grants
authority: it holds no lease and never reaches the OpenAI API.

An approved attempt claims it under its lease, binding the sandbox to the attempt
and its fencing token in the same row update, and starts authoring at once. It
claims only a workspace prepared from the pins its own specification carries.
While preparation is still running the attempt waits for it, since it is further
along than a fresh start; with none to claim, the attempt prepares its own. A
prepared workspace no attempt claims is released once its case ends or its window
passes (`utrace.scan_prepared_workspace_releases`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Final
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict
from utrace_contracts.canonical import payload_digest
from utrace_contracts.domain.lifecycle import ProductSignalState

from app.connectors.github_delivery import github_app_from_environment
from app.connectors.vercel_sandboxes import (
    SandboxHandle,
    VercelSandboxes,
    sandbox_grant_from_environment,
)
from app.db.pool import Database
from app.db.rows import as_str, as_uuid
from app.errors import AggregateNotFoundError
from app.execution.jobs import ClaimedJob
from app.gate.policy import ImplementationPolicy, load_implementation_policy
from app.gate.scope import RequiredCheck
from app.ids import uuid7
from app.implementation.checks import CheckRun
from app.implementation.environment import (
    PLACEHOLDER_API_KEY,
    WORKSPACE_ROOT,
    WORKSPACE_VCPUS,
    preparation_egress,
)
from app.implementation.ports import BuildRecipe
from app.implementation.specification import AttemptSpecification
from app.implementation.workspace import checkout_repository, create_workspace
from app.implementation.workspace_setup import (
    SandboxShell,
    exclude_check_logs,
    export_clean_base,
    install_dependencies,
    run_baseline,
)

__all__ = [
    "ClaimedWorkspace",
    "WorkspacePins",
    "claim_prepared_workspace",
    "pins_for_attempt",
    "prepare_case_workspace",
    "prepare_workspace",
    "release_prepared_workspace",
]

_logger = structlog.get_logger(__name__)

_WINDOW_SECONDS: Final[int] = 3600
"""How long a prepared workspace waits for its attempt before it is released."""
_CLAIM_WAIT_SECONDS: Final[float] = 300.0
"""How long an attempt waits for a preparation still running before preparing its own."""
_CLAIM_POLL_SECONDS: Final[float] = 2.0
_CLAIM_MARGIN_SECONDS: Final[int] = 60
"""A workspace this close to its release is not claimed."""
_GITHUB_TIMEOUT_SECONDS: Final[float] = 30.0
_ENDED_STATES: Final[frozenset[str]] = frozenset(
    {
        ProductSignalState.CLOSED_VALIDATED.value,
        ProductSignalState.CLOSED_NO_IMPLEMENTATION.value,
        ProductSignalState.CLOSED_BLOCKED.value,
        ProductSignalState.CANCELED.value,
    }
)


class WorkspacePins(BaseModel):
    """Everything a prepared workspace's contents follow from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_snapshot_id: str
    clone_url: str
    base_revision: str
    install_command: str
    required_checks: tuple[RequiredCheck, ...]
    vcpus: int

    def digest(self) -> str:
        """The digest a claim compares, over the canonical JSON of every pin."""
        return f"sha256:{payload_digest(self.model_dump(mode='json'))}"


def _pins_for_policy(policy: ImplementationPolicy) -> WorkspacePins:
    return WorkspacePins(
        workspace_snapshot_id=policy.workspace_snapshot_id,
        clone_url=policy.clone_url,
        base_revision=policy.base_revision,
        install_command=policy.build_recipe.install_command,
        required_checks=policy.required_checks,
        vcpus=WORKSPACE_VCPUS,
    )


def pins_for_attempt(
    specification: AttemptSpecification, *, build_recipe: BuildRecipe
) -> WorkspacePins:
    """The pins an attempt's own specification and recipe call for."""
    return WorkspacePins(
        workspace_snapshot_id=specification.workspace_snapshot_id,
        clone_url=specification.checkout.clone_url,
        base_revision=specification.checkout.base_revision,
        install_command=build_recipe.install_command,
        required_checks=specification.approved_scope.required_checks,
        vcpus=WORKSPACE_VCPUS,
    )


@dataclass(frozen=True, slots=True)
class ClaimedWorkspace:
    """A prepared workspace an attempt now holds, with its clean-base check runs."""

    prepared_workspace_id: UUID
    handle: SandboxHandle
    baseline: tuple[CheckRun, ...]


async def prepare_workspace(database: Database, claimed: ClaimedJob) -> None:
    """Prepare the workspace one case's attempt would claim, unless it needs none."""
    product_signal_id = UUID(str(claimed.input["product_signal_id"]))
    state, release_id = await _case(database, product_signal_id=product_signal_id)
    if state in _ENDED_STATES:
        _logger.info(
            "implementation.prepared_workspace.skipped",
            product_signal_id=str(product_signal_id),
            reason=f"the case is {state}",
        )
        return
    policy = await load_implementation_policy(database, configuration_release_id=release_id)
    if not policy.live_implementation_allowed:
        _logger.info(
            "implementation.prepared_workspace.skipped",
            product_signal_id=str(product_signal_id),
            reason="the pinned policy allows no live implementation",
        )
        return
    async with (
        VercelSandboxes(grant=sandbox_grant_from_environment()) as sandboxes,
        github_app_from_environment(
            repository_ref=policy.repository_ref, request_timeout_seconds=_GITHUB_TIMEOUT_SECONDS
        ) as github,
    ):
        await prepare_case_workspace(
            database,
            sandboxes,
            client_id=claimed.client_id,
            product_signal_id=product_signal_id,
            policy=policy,
            repository_token=await github.installation_token(),
        )


async def prepare_case_workspace(
    database: Database,
    sandboxes: VercelSandboxes,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    policy: ImplementationPolicy,
    repository_token: str,
) -> None:
    """Prepare one case's workspace from its policy, unless one already finished."""
    pins = _pins_for_policy(policy)
    sandbox_name = await _begin(
        database, client_id=client_id, product_signal_id=product_signal_id, pins=pins
    )
    if sandbox_name is None:
        return
    started = time.monotonic()
    stale = await sandboxes.find(name=sandbox_name)
    if stale is not None:
        # A run that died mid-preparation left this sandbox behind; start clean.
        await sandboxes.delete(stale)
    try:
        handle = await create_workspace(
            sandboxes,
            name=sandbox_name,
            snapshot_id=pins.workspace_snapshot_id,
            egress=preparation_egress(repository_token=repository_token),
            env={"CODEX_API_KEY": PLACEHOLDER_API_KEY, "CI": "1"},
            execution_time_limit_seconds=_WINDOW_SECONDS,
            vcpus=pins.vcpus,
        )
    except Exception as error:
        await _fail(database, product_signal_id=product_signal_id, detail=repr(error))
        raise
    try:
        await checkout_repository(
            sandboxes,
            handle,
            clone_url=pins.clone_url,
            base_revision=pins.base_revision,
            workspace_root=WORKSPACE_ROOT,
        )
        await sandboxes.update_egress(handle, egress=preparation_egress(repository_token=None))
        shell = SandboxShell(sandboxes, handle, root=WORKSPACE_ROOT)
        await exclude_check_logs(shell)
        await export_clean_base(shell)
        await install_dependencies(shell, install_command=pins.install_command)
        baseline = await run_baseline(shell, checks=pins.required_checks)
    except Exception as error:
        await _fail(database, product_signal_id=product_signal_id, detail=repr(error))
        await sandboxes.delete(handle)
        raise
    if not await _mark_ready(database, product_signal_id=product_signal_id, baseline=baseline):
        # The case ended while the workspace was prepared, and the release
        # already took the row: nothing will claim this sandbox.
        await sandboxes.delete(handle)
        return
    _logger.info(
        "implementation.prepared_workspace.ready",
        product_signal_id=str(product_signal_id),
        sandbox=sandbox_name,
        seconds=round(time.monotonic() - started, 1),
        baseline=[f"{run.name}={run.exit_code}" for run in baseline],
    )


async def claim_prepared_workspace(
    database: Database,
    sandboxes: VercelSandboxes,
    *,
    specification: AttemptSpecification,
    pins: WorkspacePins,
) -> ClaimedWorkspace | None:
    """Bind the case's prepared workspace to this attempt's lease, or return `None`.

    A preparation still running is waited for. A workspace prepared from other pins,
    failed, released, or about to be released is left alone.
    """
    product_signal_id = specification.product_signal_id
    deadline = time.monotonic() + _CLAIM_WAIT_SECONDS
    while True:
        refusal: str | None = None
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select id, state, sandbox_name, pins_digest, baseline_checks,
                       expires_at > now() + make_interval(secs => %s) as fresh
                from public.prepared_workspaces
                where product_signal_id = %s
                for update
                """,
                (_CLAIM_MARGIN_SECONDS, product_signal_id),
            )
            row = await cur.fetchone()
            state = None if row is None else as_str(row, "state")
            waiting = row is not None and state == "preparing" and bool(row["fresh"])
            if row is None:
                refusal = "none was prepared"
            elif waiting:
                pass
            elif state != "ready":
                refusal = f"it is {state}"
            elif as_str(row, "pins_digest") != pins.digest():
                refusal = "it was prepared from other pins"
            elif not row["fresh"]:
                refusal = "its window is ending"
            else:
                await cur.execute(
                    """
                    update public.prepared_workspaces
                    set state = 'claimed', implementation_attempt_id = %s,
                        lease_fencing_token = %s, updated_at = now()
                    where id = %s
                    """,
                    (
                        specification.implementation_attempt_id,
                        specification.lease.fencing_token,
                        as_uuid(row, "id"),
                    ),
                )
        if waiting and time.monotonic() < deadline:
            await asyncio.sleep(_CLAIM_POLL_SECONDS)
            continue
        if waiting:
            refusal = "its preparation outlasted the wait"
        if refusal is not None or row is None:
            _logger.info(
                "implementation.prepared_workspace.unclaimed",
                product_signal_id=str(product_signal_id),
                reason=refusal,
            )
            return None
        recorded = row["baseline_checks"]
        if not isinstance(recorded, list):
            raise TypeError("prepared_workspaces.baseline_checks is not a JSON array")
        handle = await sandboxes.attach(name=as_str(row, "sandbox_name"))
        _logger.info(
            "implementation.prepared_workspace.claimed",
            product_signal_id=str(product_signal_id),
            implementation_attempt_id=str(specification.implementation_attempt_id),
            fencing_token=specification.lease.fencing_token,
            sandbox=handle.name,
        )
        return ClaimedWorkspace(
            prepared_workspace_id=as_uuid(row, "id"),
            handle=handle,
            baseline=tuple(CheckRun.model_validate(item) for item in recorded),
        )


async def release_prepared_workspace(database: Database, claimed: ClaimedJob) -> None:
    """Destroy one prepared workspace no attempt claimed.

    The row is released first, so a workspace an attempt claimed in the meantime is
    never destroyed under it.
    """
    workspace_id = UUID(str(claimed.input["prepared_workspace_id"]))
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_workspaces
            set state = 'released', updated_at = now()
            where id = %s and state in ('preparing', 'ready', 'failed')
            returning sandbox_name
            """,
            (workspace_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return
    async with VercelSandboxes(grant=sandbox_grant_from_environment()) as sandboxes:
        handle = await sandboxes.find(name=as_str(row, "sandbox_name"))
        if handle is not None:
            await sandboxes.delete(handle)
    _logger.info(
        "implementation.prepared_workspace.released",
        prepared_workspace_id=str(workspace_id),
        sandbox=as_str(row, "sandbox_name"),
    )


async def _case(database: Database, *, product_signal_id: UUID) -> tuple[str, UUID]:
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state::text as state, pinned_configuration_release_id "
            "from public.product_signals where id = %s",
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="product_signals", row_id=product_signal_id)
    return as_str(row, "state"), as_uuid(row, "pinned_configuration_release_id")


async def _begin(
    database: Database, *, client_id: UUID, product_signal_id: UUID, pins: WorkspacePins
) -> str | None:
    """Record the preparation, or return `None` when one already finished for the case."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.prepared_workspaces (
                id, client_id, product_signal_id, state, sandbox_name, pins_digest, expires_at
            )
            values (%s, %s, %s, 'preparing', %s, %s, now() + make_interval(secs => %s))
            on conflict (product_signal_id) do update
            set state = 'preparing', pins_digest = excluded.pins_digest,
                expires_at = excluded.expires_at, baseline_checks = null,
                failure_detail = null, updated_at = now()
            where public.prepared_workspaces.state in ('preparing', 'failed')
            returning sandbox_name
            """,
            (
                uuid7(),
                client_id,
                product_signal_id,
                f"utrace-workspace-{product_signal_id.hex}",
                pins.digest(),
                _WINDOW_SECONDS,
            ),
        )
        row = await cur.fetchone()
    return None if row is None else as_str(row, "sandbox_name")


async def _mark_ready(
    database: Database, *, product_signal_id: UUID, baseline: tuple[CheckRun, ...]
) -> bool:
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_workspaces
            set state = 'ready', baseline_checks = %s, updated_at = now()
            where product_signal_id = %s and state = 'preparing'
            """,
            (Jsonb([run.model_dump(mode="json") for run in baseline]), product_signal_id),
        )
        return cur.rowcount == 1


async def _fail(database: Database, *, product_signal_id: UUID, detail: str) -> None:
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.prepared_workspaces
            set state = 'failed', failure_detail = %s, updated_at = now()
            where product_signal_id = %s and state = 'preparing'
            """,
            (detail[:2000], product_signal_id),
        )
