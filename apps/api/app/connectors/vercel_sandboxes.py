"""Isolated Vercel Sandbox compute for preview runtimes and implementation workspaces.

Verified against the installed `vercel` Python SDK (0.11.4, `vercel.sandbox` 0.7.0).
Each sandbox is a Firecracker microVM. The rules this connector holds:

- **Named sandboxes.** Every sandbox is created under a name uTrace derives from the
  allocation or lease it serves, so a create whose response was lost is reconciled by
  looking the name up rather than by listing labels, and a repeat create cannot leave a
  second, orphaned copy holding seeded data.
- **Non-persistent.** Vercel sandboxes persist by default and snapshot their
  filesystem when they stop, which would retain seeded data after an allocation ends.
  Every sandbox here is created with persistence off.
- **No entrypoint.** Vercel Sandbox does not run an image's `ENTRYPOINT` or `CMD`, so
  the caller starts a runtime's supervisor with `start_detached`.
- **Public port URLs.** An exposed port is a public URL with no provider-level
  authorization. The runtime's own middleware is the ingress check; a port URL is a
  server-side route target and never reaches a browser.
- **Credentials at egress.** A header a network rule injects is added by the sandbox
  firewall outside the microVM, so the credential never exists inside it.

See the [preview runtime contract](../../../../docs/arch-components/preview-runtime-and-seeding.md#template-and-readiness)
and the [implementation contract](../../../../docs/arch-components/implementation.md#enforcement-model).
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Self

import httpx2
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from vercel.sandbox import (
    NetworkPolicy,
    NetworkPolicyRule,
    NetworkPolicyTransform,
    Process,
    Sandbox,
    SandboxApiError,
    SandboxClient,
    SandboxCredentials,
    SandboxError,
    SandboxPathNotFoundError,
    SandboxResources,
    SandboxServiceOptions,
    SandboxTimeoutError,
    SnapshotExpiration,
    SnapshotSource,
)

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    default_retry_advice,
    redacted_parameters,
    technical_category_for_status,
)
from app.connectors.ports import WorkspaceExecution
from app.connectors.retry import RetryPolicy, retry_safe_call

__all__ = [
    "EgressPolicy",
    "HeaderInjection",
    "SandboxConfigurationError",
    "SandboxGrant",
    "SandboxHandle",
    "VercelSandboxes",
    "sandbox_grant_from_environment",
]

_logger = structlog.get_logger(__name__)
_PROVIDER: Final[str] = "vercel"
_NEVER_EXPIRES: Final[SnapshotExpiration] = SnapshotExpiration(0)
"""Vercel otherwise expires a snapshot 30 days after its last use."""
_REATTACH_GRACE_SECONDS: Final[int] = 60
"""How long past a process's kill deadline a dropped wait keeps re-attaching."""
_REATTACH_DELAY_SECONDS: Final[float] = 2.0
_WAIT_WINDOW_SECONDS: Final[float] = 30.0
"""How long one wait for a process's exit may stay silent before it is re-attached.

A wait can also stall without dropping: the connection stays open and nothing
arrives, even after the process has exited. Re-attaching asks again."""
_READ_RETRY: Final[RetryPolicy] = RetryPolicy(
    max_attempts=4,
    initial_delay_seconds=1.0,
    backoff_multiplier=2.0,
    safety_justification="read-only",
)
_FILE_TRANSFER_CONCURRENCY: Final[int] = 4
"""File reads and writes in flight at once; a wider burst drops connections."""


class SandboxGrant(BaseModel):
    """The Vercel access token and the team and project sandboxes are created in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str
    team_id: str
    project_id: str


class SandboxConfigurationError(RuntimeError):
    """The Vercel Sandbox grant is not configured."""

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__(
            "the Vercel Sandbox grant is incomplete; set " + ", ".join(sorted(missing))
        )


class _GrantSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    token: str = Field(validation_alias="UTRACE_VERCEL_SANDBOX_TOKEN", min_length=1)
    team_id: str = Field(validation_alias="UTRACE_VERCEL_TEAM_ID", min_length=1)
    project_id: str = Field(validation_alias="UTRACE_VERCEL_PROJECT_ID", min_length=1)


def sandbox_grant_from_environment() -> SandboxGrant:
    """Read the grant from `UTRACE_VERCEL_SANDBOX_TOKEN`, `_TEAM_ID`, and `_PROJECT_ID`.

    On a deployed compute the token is a Supabase Compute project secret. The `UTRACE_` prefix keeps Vercel's
    own injected `VERCEL_*` variables from ever being read as this grant.
    """
    try:
        settings = _GrantSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        missing = [str(issue["loc"][0]) for issue in error.errors()]
        raise SandboxConfigurationError(missing) from None
    return SandboxGrant(
        token=settings.token, team_id=settings.team_id, project_id=settings.project_id
    )


class HeaderInjection(BaseModel):
    """Headers the sandbox firewall adds to requests bound for one domain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str
    headers: Mapping[str, str]


class EgressPolicy(BaseModel):
    """The only outbound destinations a sandbox may reach.

    `allowed_domains` may use a leading `*.` wildcard. Every injection's domain must
    also be allowed, because the firewall only transforms traffic it permits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_domains: tuple[str, ...]
    injections: tuple[HeaderInjection, ...]

    def to_network_policy(self) -> NetworkPolicy:
        """Render the policy the Sandbox API enforces."""
        injected = {injection.domain: injection for injection in self.injections}
        unknown = sorted(set(injected) - set(self.allowed_domains))
        if unknown:
            raise ValueError(f"header injections name domains the policy does not allow: {unknown}")
        return NetworkPolicy.custom(
            allow={
                domain: (
                    (NetworkPolicyRule(transform=[NetworkPolicyTransform(headers=injected[domain].headers)]),)
                    if domain in injected
                    else ()
                )
                for domain in self.allowed_domains
            }
        )


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """One live sandbox and the name uTrace created it under."""

    name: str
    sandbox: Sandbox


class VercelSandboxes:
    """Connector for creating, driving, and deleting Vercel sandboxes.

    Usage is `async with VercelSandboxes(grant=...) as sandboxes:`; the context manager
    owns the SDK session and its HTTP transport.
    """

    def __init__(self, *, grant: SandboxGrant) -> None:
        credentials = SandboxCredentials(
            token=grant.token, team_id=grant.team_id, project_id=grant.project_id
        )

        async def resolve() -> SandboxCredentials:
            return credentials

        self._client = SandboxClient.create(
            options=SandboxServiceOptions(credentials_factory=resolve)
        )
        self._file_transfers = asyncio.Semaphore(_FILE_TRANSFER_CONCURRENCY)

    async def __aenter__(self) -> Self:
        """Return the connector; the SDK authenticates on its first request."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the SDK session."""
        await self._client.aclose()

    async def create_from_image(
        self,
        *,
        name: str,
        image: str,
        ports: Sequence[int],
        env: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> SandboxHandle:
        """Create one sandbox from a Vercel Container Registry image pinned by digest."""
        return await self._create(
            name=name,
            image=image,
            source=None,
            ports=ports,
            env=env,
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )

    async def create_from_default_image(
        self,
        *,
        name: str,
        env: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> SandboxHandle:
        """Create one sandbox from Vercel's default image, to build a template snapshot on."""
        return await self._create(
            name=name,
            image=None,
            source=None,
            ports=(),
            env=env,
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )

    async def create_from_snapshot(
        self,
        *,
        name: str,
        snapshot_id: str,
        ports: Sequence[int],
        env: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> SandboxHandle:
        """Create one sandbox from a template snapshot."""
        return await self._create(
            name=name,
            image=None,
            source=SnapshotSource(snapshot_id=snapshot_id),
            ports=ports,
            env=env,
            egress=egress,
            execution_time_limit_seconds=execution_time_limit_seconds,
            vcpus=vcpus,
        )

    async def find(self, *, name: str) -> SandboxHandle | None:
        """Return the named sandbox, or `None` when no sandbox has that name."""
        try:
            sandbox = await self._client.get_sandbox(name=name)
        except SandboxApiError as error:
            if error.status_code == 404:
                return None
            raise _map_error(
                operation="find", error=error, effect=EffectState.ABSENT, parameters={"name": name}
            ) from error
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="find", error=error, effect=EffectState.ABSENT, parameters={"name": name}
            ) from error
        return SandboxHandle(name=name, sandbox=sandbox)

    async def attach(self, *, name: str) -> SandboxHandle:
        """Reach an existing sandbox by the name it was created under."""
        handle = await self.find(name=name)
        if handle is None:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="attach",
                    category=TechnicalCategory.NOT_FOUND,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.ABSENT,
                    message=f"no sandbox is named {name}",
                    request_parameters=redacted_parameters({"name": name}),
                )
            )
        return handle

    async def run(
        self,
        handle: SandboxHandle,
        *,
        script: str,
        cwd: str,
        timeout_seconds: int,
        sudo: bool,
    ) -> WorkspaceExecution:
        """Run one `bash -c` script to completion.

        The shell is not a login shell, so the image's own environment, `PATH`
        included, is what the script runs under. `sudo` runs it through the image's
        `sudo` binary; a custom image whose default user is already root has none.

        A non-zero exit code is a result, not a failure: a check or seed verification
        that reports a mismatch is exactly what the caller has to read. The service
        kills the process at `timeout_seconds`.

        The process does not depend on one connection: when the connection waiting
        on it drops, as a long, quiet run such as the coding harness can see, or
        stays silent past a short window, the wait re-attaches until the service's
        kill deadline has passed.
        """
        started = time.monotonic()
        parameters = {"name": handle.name, "cwd": cwd}
        try:
            process = await handle.sandbox.create_process(
                "bash",
                ["-c", script],
                cwd=cwd,
                sudo=sudo,
                kill_after=timeout_seconds,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="run",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters=parameters,
            ) from error
        exit_code = await _wait_for_exit(
            process,
            deadline=started + timeout_seconds + _REATTACH_GRACE_SECONDS,
            parameters=parameters,
        )
        output = process.stdout
        if output is None:
            raise AssertionError("a piped process always has a stdout reader")
        try:
            stdout = await output.read()
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="run_output",
                error=error,
                effect=EffectState.COMPLETED,
                parameters=parameters,
            ) from error
        _logger.info("vercel.sandbox.run", name=handle.name, exit_code=exit_code, cwd=cwd)
        return WorkspaceExecution(
            command=script,
            exit_code=exit_code,
            stdout=stdout,
            duration_seconds=time.monotonic() - started,
        )

    async def start_detached(
        self, handle: SandboxHandle, *, script: str, cwd: str, sudo: bool
    ) -> str:
        """Start one long-running `bash -c` script and return its process ID."""
        try:
            process = await handle.sandbox.create_process(
                "bash",
                ["-c", script],
                cwd=cwd,
                sudo=sudo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="start_detached",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters={"name": handle.name, "cwd": cwd},
            ) from error
        _logger.info("vercel.sandbox.detached", name=handle.name, process_id=process.id)
        return process.id

    async def extend_execution_time(self, handle: SandboxHandle, *, seconds: int) -> None:
        """Lengthen the sandbox's current execution time limit by `seconds`."""
        try:
            await handle.sandbox.extend_execution_time_limit(seconds)
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="extend_execution_time",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters={"name": handle.name, "seconds": seconds},
            ) from error
        _logger.info("vercel.sandbox.extended", name=handle.name, seconds=seconds)

    async def write_file(self, handle: SandboxHandle, *, path: str, content: bytes) -> None:
        """Replace one file inside the sandbox.

        The SDK extracts uploads inside `/vercel/sandbox`, so a custom image must
        contain that directory.
        """
        try:
            async with self._file_transfers:
                await handle.sandbox.fs.write_bytes(path, content)
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="write_file",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters={"name": handle.name, "path": path},
            ) from error

    async def read_file(self, handle: SandboxHandle, *, path: str) -> bytes:
        """Read one file out of the sandbox, retrying a read that never reached it."""
        return await retry_safe_call(
            operation="vercel.read_file",
            policy=_READ_RETRY,
            call=lambda: self._read_once(handle, path=path),
        )

    async def _read_once(self, handle: SandboxHandle, *, path: str) -> bytes:
        try:
            async with self._file_transfers:
                return await handle.sandbox.fs.read_bytes(path)
        except SandboxPathNotFoundError as error:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="read_file",
                    category=TechnicalCategory.NOT_FOUND,
                    retry_advice=RetryAdvice.AFTER_CONDITION_CHANGE,
                    effect_state=EffectState.ABSENT,
                    message=f"{path} does not exist in sandbox {handle.name}",
                    request_parameters=redacted_parameters({"name": handle.name, "path": path}),
                )
            ) from error
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="read_file",
                error=error,
                effect=EffectState.ABSENT,
                parameters={"name": handle.name, "path": path},
            ) from error

    async def update_egress(self, handle: SandboxHandle, *, egress: EgressPolicy) -> None:
        """Replace the sandbox's outbound policy, and with it every header injection."""
        try:
            await handle.sandbox.update_network_policy(egress.to_network_policy())
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="update_egress",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters={"name": handle.name, "allowed_domains": list(egress.allowed_domains)},
            ) from error
        _logger.info(
            "vercel.sandbox.egress_updated",
            name=handle.name,
            allowed_domains=list(egress.allowed_domains),
        )

    def port_url(self, handle: SandboxHandle, *, port: int) -> str:
        """Return the public URL of one exposed port. A server-side route target only."""
        for route in handle.sandbox.routes:
            if route.port == port and not route.system:
                return route.url
        raise ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="port_url",
                category=TechnicalCategory.CONFIGURATION,
                retry_advice=RetryAdvice.NEVER,
                effect_state=EffectState.ABSENT,
                message=f"sandbox {handle.name} exposes no port {port}",
                request_parameters=redacted_parameters({"name": handle.name, "port": port}),
            )
        )

    async def snapshot(self, handle: SandboxHandle) -> str:
        """Snapshot the sandbox with no expiry and return the snapshot ID.

        The provider stops the sandbox's session once the snapshot completes.
        """
        try:
            snapshot = await handle.sandbox.snapshot(expiration=_NEVER_EXPIRES)
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="snapshot",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters={"name": handle.name},
            ) from error
        _logger.info("vercel.sandbox.snapshot", name=handle.name, snapshot_id=snapshot.id)
        return snapshot.id

    async def snapshot_exists(self, *, snapshot_id: str) -> bool:
        """Whether a template snapshot still exists and can create sandboxes."""
        try:
            snapshot = await self._client.get_snapshot(snapshot_id=snapshot_id)
        except SandboxApiError as error:
            if error.status_code == 404:
                return False
            raise _map_error(
                operation="snapshot_exists",
                error=error,
                effect=EffectState.ABSENT,
                parameters={"snapshot_id": snapshot_id},
            ) from error
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="snapshot_exists",
                error=error,
                effect=EffectState.ABSENT,
                parameters={"snapshot_id": snapshot_id},
            ) from error
        return snapshot.status == "created"

    async def delete(self, handle: SandboxHandle) -> None:
        """Stop and destroy the sandbox. Template snapshots it was created from remain."""
        try:
            await handle.sandbox.stop()
            await handle.sandbox.destroy(delete_orphan_snapshots=False)
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="delete",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters={"name": handle.name},
            ) from error
        _logger.info("vercel.sandbox.deleted", name=handle.name)

    async def _create(
        self,
        *,
        name: str,
        image: str | None,
        source: SnapshotSource | None,
        ports: Sequence[int],
        env: Mapping[str, str],
        egress: EgressPolicy,
        execution_time_limit_seconds: int,
        vcpus: int,
    ) -> SandboxHandle:
        """Create one named, non-persistent sandbox.

        Creation is never retried here: a transport failure reports an indeterminate
        effect, and the caller reconciles by looking the name up.
        """
        parameters: Mapping[str, object] = {
            "name": name,
            "image": image,
            "snapshot_id": None if source is None else source.snapshot_id,
            "ports": list(ports),
            "vcpus": vcpus,
        }
        try:
            sandbox = await self._client.create_sandbox(
                name=name,
                image=image,
                source=source,
                ports=list(ports),
                execution_time_limit=execution_time_limit_seconds,
                resources=SandboxResources(vcpus=vcpus),
                persistent=False,
                network_policy=egress.to_network_policy(),
                env=dict(env),
            )
        except (SandboxError, httpx2.TransportError) as error:
            raise _map_error(
                operation="create",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters=parameters,
            ) from error
        _logger.info("vercel.sandbox.created", name=name, image=image, ports=list(ports))
        return SandboxHandle(name=name, sandbox=sandbox)


async def _wait_for_exit(
    process: Process, *, deadline: float, parameters: Mapping[str, object]
) -> int:
    """Return the process's exit code, re-attaching when the waiting connection drops.

    The process keeps running in the sandbox whatever happens to the connection, so
    a dropped wait, or one silent for `_WAIT_WINDOW_SECONDS`, is retried with a
    warning until `deadline`, then raised.
    """
    while True:
        remaining = deadline - time.monotonic()
        try:
            async with asyncio.timeout(min(max(remaining, 0.0), _WAIT_WINDOW_SECONDS)):
                return await process.wait()
        except TimeoutError as error:
            if time.monotonic() >= deadline:
                raise ConnectorFailure(
                    FailureDetail(
                        provider=_PROVIDER,
                        operation="run",
                        category=TechnicalCategory.TIMEOUT,
                        retry_advice=default_retry_advice(TechnicalCategory.TIMEOUT),
                        effect_state=EffectState.INDETERMINATE,
                        message=(
                            f"process {process.id} reported no exit before its deadline "
                            "passed, though each silent wait was re-attached"
                        ),
                        request_parameters=redacted_parameters(parameters),
                    )
                ) from error
            _logger.info(
                "vercel.sandbox.wait_renewed",
                process_id=process.id,
                silent_seconds=_WAIT_WINDOW_SECONDS,
                **parameters,
            )
        except SandboxError as error:
            raise _map_error(
                operation="run",
                error=error,
                effect=EffectState.INDETERMINATE,
                parameters=parameters,
            ) from error
        except httpx2.TransportError as error:
            if time.monotonic() >= deadline:
                raise _map_error(
                    operation="run",
                    error=error,
                    effect=EffectState.INDETERMINATE,
                    parameters=parameters,
                ) from error
            _logger.warning(
                "vercel.sandbox.wait_reattaching",
                process_id=process.id,
                error_class=type(error).__name__,
                error=str(error),
                **parameters,
            )
            await asyncio.sleep(_REATTACH_DELAY_SECONDS)


def _map_error(
    *,
    operation: str,
    error: SandboxError | httpx2.TransportError,
    effect: EffectState,
    parameters: Mapping[str, object],
) -> ConnectorFailure:
    """Map a Sandbox SDK or transport error onto the shared typed failure."""
    status_code: int | None = None
    body: str | None = None
    code: str | None = None
    if isinstance(error, SandboxApiError):
        status_code = error.status_code
        category = technical_category_for_status(status_code)
        body = error.response.text
        code = error.code
    elif isinstance(error, SandboxTimeoutError | httpx2.TimeoutException):
        category = TechnicalCategory.TIMEOUT
    elif isinstance(error, httpx2.TransportError):
        category = TechnicalCategory.PROVIDER_UNAVAILABLE
    else:
        category = TechnicalCategory.PROVIDER_REJECTED
    return ConnectorFailure(
        FailureDetail(
            provider=_PROVIDER,
            operation=operation,
            category=category,
            retry_advice=default_retry_advice(category),
            effect_state=effect,
            message=f"Vercel Sandbox call failed: {type(error).__name__}: {error}",
            request_parameters=redacted_parameters(parameters),
            status_code=status_code,
            provider_error_code=code,
            response_body=body,
        )
    )
