"""Vercel Sandbox operations the preview area needs.

Every preview Job after the creating one — seeding, probing, advancing a route,
tearing down — runs in a different process and starts from the durable
``runtime_ref``, which is the sandbox's name. This adapter adds the two things the
preview area needs on top of ``app.connectors.vercel_sandboxes``:

- **The supervisor.** Vercel Sandbox does not run an image's entrypoint, so a
  runtime's product is started by running the template's supervisor as a detached
  command, and restarted by stopping that supervisor and starting it again.
- **The upstream URL.** A runtime's exposed application port is a public URL with
  no provider-level authorization; the runtime's own middleware and entry token
  are the ingress check. The URL is a server-side route target only.

Fencing is the caller's: ``app.preview.pool.require_current_fencing_token``
re-reads the allocation's token immediately before any mutating sequence, so a
worker holding a superseded handle cannot write, publish, or route traffic.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Self

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    redacted_parameters,
)
from app.connectors.ports import WorkspaceExecution
from app.connectors.vercel_sandboxes import (
    EgressPolicy,
    SandboxGrant,
    SandboxHandle,
    VercelSandboxes,
)
from app.logging import get_logger

__all__ = [
    "PreviewRuntimeHandle",
    "PreviewRuntimeProvider",
]

_log = get_logger(__name__)
_PROVIDER: Final[str] = "vercel"
_SUPERVISOR_STOP_SECONDS: Final[int] = 60
"""How long a stopping supervisor may take to stop the product and Postgres."""
_DESCENDANT_GRACE_SECONDS: Final[int] = 15
"""How long the product may take to finish its own shutdown after the supervisor exits."""

_STOP_SUPERVISOR: Final[str] = """
supervisor={supervisor}
descendants() {{
  echo "$1 $2"
  for entry in /proc/[0-9]*; do
    if [ "$(awk '/^PPid:/ {{print $2}}' "$entry/status" 2>/dev/null)" = "$1" ]; then
      descendants "${{entry#/proc/}}" "$(($2 + 1))"
    fi
  done
}}
alive() {{
  for pid in "$@"; do
    state="$(awk '/^State:/ {{print $2}}' "/proc/$pid/status" 2>/dev/null)"
    [ -n "$state" ] && [ "$state" != "Z" ] && return 0
  done
  return 1
}}
supervisors=""
for entry in /proc/[0-9]*; do
  if [ "$(tr '\\0' '\\n' < "$entry/cmdline" 2>/dev/null | sed -n 2p)" = "$supervisor" ]; then
    supervisors="$supervisors ${{entry#/proc/}}"
  fi
done
[ -z "$supervisors" ] && exit 0
tree=""
for pid in $supervisors; do tree="$tree
$(descendants "$pid" 0)"; done
pids="$(echo "$tree" | awk 'NF {{print $1}}')"
unreached="$(echo "$tree" | awk '$2 >= 2 {{print $1}}')"
kill -TERM $supervisors 2>/dev/null
for attempt in $(seq 1 $(({attempts} * 5))); do
  alive $supervisors || break
  sleep 0.2
done
alive $supervisors && exit 1
[ -n "$unreached" ] && kill -TERM $unreached 2>/dev/null
for attempt in $(seq 1 $(({grace} * 5))); do
  alive $pids || exit 0
  sleep 0.2
done
kill -TERM $pids 2>/dev/null
for attempt in $(seq 1 $(({attempts} * 5))); do
  alive $pids || exit 0
  sleep 0.2
done
exit 1
"""
"""Stop the supervisor, then every process it left behind, found through `/proc`.

Every process in a sandbox shares the init process's group, so a group signal is
not an option, and the Postgres base image ships without `ps` or `pkill`. The
supervisor is the process whose first argument is its command. It is signalled
alone first, because only its trap stops Postgres: `pg_ctl` daemonizes the
postmaster under init, outside the supervisor's tree, and the trap needs the log
pipe the supervisor's descendants hold open. Once the supervisor has exited, the
descendants it started are signalled too, so the product process cannot outlive
it. The trap signals only the processes it started itself, so anything deeper,
such as the server a package manager's `run` wrapper spawned, never hears it and
is signalled the moment the supervisor is gone; waiting for it to finish on its
own only ever ran out the grace period. The supervisor's direct children get that
grace period: the trap may already have signalled the product, whose Server SDK
is flushing its terminal stream report, and a second signal would kill it
mid-report. An exited process counts as gone even while it waits as a zombie,
because the sandbox's init does not reap it promptly.
"""


@dataclass(frozen=True, slots=True)
class PreviewRuntimeHandle:
    """A live preview sandbox and the application port its route targets."""

    runtime_ref: str
    application_port: int
    sandbox: SandboxHandle


class PreviewRuntimeProvider:
    """Sandbox operations for preview runtimes."""

    def __init__(self, *, grant: SandboxGrant) -> None:
        self._sandboxes = VercelSandboxes(grant=grant)

    async def __aenter__(self) -> Self:
        """Enter the connector's scope."""
        await self._sandboxes.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connector's session."""
        await self._sandboxes.__aexit__(exc_type, exc, traceback)

    async def snapshot_exists(self, *, snapshot_id: str) -> bool:
        """Whether the pinned template snapshot can still create runtimes."""
        return await self._sandboxes.snapshot_exists(snapshot_id=snapshot_id)

    async def create_runtime(
        self,
        *,
        runtime_ref: str,
        snapshot_id: str,
        application_port: int,
        env_vars: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
        supervisor_command: str,
        application_directory: str,
    ) -> PreviewRuntimeHandle:
        """Create one runtime from the template snapshot and start its supervisor.

        Creation is never retried: the name is the reconciliation key, so a lost
        response is resolved by looking the name up rather than by creating again.
        """
        handle = await self.create_idle_runtime(
            runtime_ref=runtime_ref,
            snapshot_id=snapshot_id,
            application_port=application_port,
            env_vars=env_vars,
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )
        await self.start_runtime(
            handle,
            supervisor_command=supervisor_command,
            application_directory=application_directory,
        )
        return handle

    async def create_idle_runtime(
        self,
        *,
        runtime_ref: str,
        snapshot_id: str,
        application_port: int,
        env_vars: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> PreviewRuntimeHandle:
        """Create one runtime from the template snapshot without starting it.

        A candidate installs and builds its files first, so its supervisor starts
        once, on the candidate build. Creation is never retried, for the same reason
        as `create_runtime`.
        """
        sandbox = await self._sandboxes.create_from_snapshot(
            name=runtime_ref,
            snapshot_id=snapshot_id,
            ports=(application_port,),
            env=env_vars,
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )
        _log.info(
            "preview_runtime_created",
            runtime_ref=runtime_ref,
            snapshot_id=snapshot_id,
            application_port=application_port,
        )
        return PreviewRuntimeHandle(
            runtime_ref=runtime_ref, application_port=application_port, sandbox=sandbox
        )

    async def snapshot_image(
        self,
        *,
        runtime_ref: str,
        image_reference: str,
        application_port: int,
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> str:
        """Snapshot a fresh sandbox of one registry image and delete the sandbox.

        Nothing runs in the sandbox first: the snapshot captures the image exactly.
        """
        sandbox = await self._sandboxes.create_from_image(
            name=runtime_ref,
            image=image_reference,
            ports=(application_port,),
            env={},
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )
        try:
            return await self._sandboxes.snapshot(sandbox)
        finally:
            await self._sandboxes.delete(sandbox)

    async def attach(self, *, runtime_ref: str, application_port: int) -> PreviewRuntimeHandle:
        """Reach an existing runtime again by its durable name."""
        sandbox = await self._sandboxes.attach(name=runtime_ref)
        return PreviewRuntimeHandle(
            runtime_ref=runtime_ref, application_port=application_port, sandbox=sandbox
        )

    async def execute(
        self,
        *,
        handle: PreviewRuntimeHandle,
        script: str,
        working_directory: str,
        timeout_seconds: int,
    ) -> WorkspaceExecution:
        """Run one script inside the runtime as its image's default user.

        A non-zero exit code is a result, not a failure: a seed verification that
        reports a mismatch is exactly the outcome the caller has to read.
        """
        return await self._sandboxes.run(
            handle.sandbox,
            script=script,
            cwd=working_directory,
            timeout_seconds=timeout_seconds,
            sudo=False,
        )

    async def write_file(
        self, *, handle: PreviewRuntimeHandle, path: str, content: bytes
    ) -> None:
        """Replace one file inside the runtime."""
        await self._sandboxes.write_file(handle.sandbox, path=path, content=content)

    async def read_file(self, *, handle: PreviewRuntimeHandle, path: str) -> bytes:
        """Read one file out of the runtime."""
        return await self._sandboxes.read_file(handle.sandbox, path=path)

    def upstream_url(self, *, handle: PreviewRuntimeHandle) -> str:
        """Return the public URL of the runtime's application port.

        It stays server-side: only the front door follows it, and only with the
        runtime's entry token and a signed route claim.
        """
        return self._sandboxes.port_url(handle.sandbox, port=handle.application_port)

    async def restart_runtime(
        self,
        *,
        handle: PreviewRuntimeHandle,
        supervisor_command: str,
        application_directory: str,
    ) -> None:
        """Stop the runtime's supervisor and start it again.

        This is how a rebuilt candidate begins being served and how a runtime picks
        up an activation installed after it was created. The supervisor and every
        process under it are signalled, the supervisor's trap stops Postgres cleanly,
        and the new supervisor starts only once all of them are gone, so two never
        race for the same data directory and application port.
        """
        stopped = await self.execute(
            handle=handle,
            script=_STOP_SUPERVISOR.format(
                supervisor=shlex.quote(supervisor_command),
                attempts=_SUPERVISOR_STOP_SECONDS,
                grace=_DESCENDANT_GRACE_SECONDS,
            ),
            working_directory=application_directory,
            timeout_seconds=2 * _SUPERVISOR_STOP_SECONDS + _DESCENDANT_GRACE_SECONDS + 10,
        )
        if stopped.exit_code != 0:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="restart_runtime",
                    category=TechnicalCategory.TIMEOUT,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.INDETERMINATE,
                    message=(
                        f"the supervisor in {handle.runtime_ref} did not stop within "
                        f"{_SUPERVISOR_STOP_SECONDS} seconds: {stopped.stdout[-500:]}"
                    ),
                    request_parameters=redacted_parameters({"runtime_ref": handle.runtime_ref}),
                )
            )
        await self.start_runtime(
            handle,
            supervisor_command=supervisor_command,
            application_directory=application_directory,
        )
        _log.info("preview_runtime_restarted", runtime_ref=handle.runtime_ref)

    async def extend_runtime(self, *, handle: PreviewRuntimeHandle, seconds: int) -> None:
        """Lengthen the runtime's remaining execution time by `seconds`."""
        await self._sandboxes.extend_execution_time(handle.sandbox, seconds=seconds)

    async def delete_runtime(self, *, handle: PreviewRuntimeHandle) -> None:
        """Destroy the runtime outright. Never reset it back into the warm pool."""
        await self._sandboxes.delete(handle.sandbox)

    async def start_runtime(
        self,
        handle: PreviewRuntimeHandle,
        *,
        supervisor_command: str,
        application_directory: str,
    ) -> None:
        """Start the template's supervisor as a detached command."""
        await self._sandboxes.start_detached(
            handle.sandbox,
            script=supervisor_command,
            cwd=application_directory,
            sudo=False,
        )
