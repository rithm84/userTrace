"""The signed server instrumentation manifest one client release publishes.

Onboarding owns the manifest's content and its signature. It is pinned to a
configuration release alongside the browser annotation manifest, and a Product
Signal pins that release for its whole lifecycle, so every service process in one
case activates against the same declaration.

The SDK never sends the manifest: it presents only the service key it claims, and
the control plane returns the manifest the release pinned. Application code
therefore cannot broaden the manifest at runtime, and a process claiming a
service the release does not declare fails activation.

Contract: docs/arch-components/server-sdk.md#configuration-identity-and-credentials
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.envelope import JsonValue
from app.errors import AggregateNotFoundError
from app.ingestion.errors import SourceValidationError
from app.logging import get_logger

__all__ = ["ManifestService", "PinnedServerManifest", "load_signed_server_manifest"]

_log = get_logger(__name__)

_POLICY_KEY = "server_instrumentation_manifest"


@dataclass(frozen=True, slots=True)
class ManifestService:
    """The one manifest service an activating process claims."""

    service_key: str
    service_name: str
    service_namespace: str
    service_version: str
    required: bool
    required_adapters: tuple[str, ...]
    probe_route_template: str
    probe_method: str


@dataclass(frozen=True, slots=True)
class PinnedServerManifest:
    """The signed manifest envelope and the release that pinned it."""

    configuration_release_id: UUID
    signed: Mapping[str, JsonValue]

    def manifest(self) -> Mapping[str, JsonValue]:
        """Return the manifest the signature covers."""
        manifest = self.signed.get("manifest")
        if not isinstance(manifest, dict):
            raise SourceValidationError(
                field=f"configuration_releases.policy.{_POLICY_KEY}.manifest",
                reason="is not an object",
            )
        return manifest

    def service(self, service_key: str) -> ManifestService:
        """Return the declared service, or refuse with the claimed key named."""
        services = self.manifest().get("services")
        if not isinstance(services, list):
            raise SourceValidationError(
                field=f"configuration_releases.policy.{_POLICY_KEY}.manifest.services",
                reason="is not a list",
            )
        for candidate in services:
            if isinstance(candidate, dict) and candidate.get("service_key") == service_key:
                return _service_from(candidate, service_key=service_key)
        raise SourceValidationError(
            field="service_name",
            reason=(
                f"'{service_key}' is not a service the pinned release's server "
                "instrumentation manifest declares"
            ),
        )


def _required_text(source: Mapping[str, JsonValue], key: str, *, field: str) -> str:
    """Read one required string from a manifest object."""
    value = source.get(key)
    if not isinstance(value, str) or len(value) == 0:
        raise SourceValidationError(field=f"{field}.{key}", reason="is not a string")
    return value


def _service_from(
    source: Mapping[str, JsonValue], *, service_key: str
) -> ManifestService:
    """Build the typed service view one activation needs from the manifest."""
    field = f"{_POLICY_KEY}.manifest.services[{service_key}]"
    adapters = source.get("required_adapters")
    if not isinstance(adapters, list) or len(adapters) == 0:
        raise SourceValidationError(
            field=f"{field}.required_adapters", reason="is empty or not a list"
        )
    routes = source.get("registered_routes")
    if not isinstance(routes, list):
        raise SourceValidationError(
            field=f"{field}.registered_routes", reason="is not a list"
        )
    required_routes = [
        route
        for route in routes
        if isinstance(route, dict) and route.get("required") is True
    ]
    if len(required_routes) == 0:
        raise SourceValidationError(
            field=f"{field}.registered_routes",
            reason=(
                "declares no required route, so there is no product path the "
                "propagation probe can enter through"
            ),
        )
    probe_route = required_routes[0]
    methods = probe_route.get("methods")
    if not isinstance(methods, list) or len(methods) == 0:
        raise SourceValidationError(
            field=f"{field}.registered_routes[0].methods", reason="is empty"
        )
    required = source.get("required")
    if not isinstance(required, bool):
        raise SourceValidationError(field=f"{field}.required", reason="is not a boolean")
    return ManifestService(
        service_key=service_key,
        service_name=_required_text(source, "service_name", field=field),
        service_namespace=_required_text(source, "service_namespace", field=field),
        service_version=_required_text(source, "service_version", field=field),
        required=required,
        required_adapters=tuple(str(adapter) for adapter in adapters),
        probe_route_template=_required_text(
            probe_route, "route_template", field=f"{field}.registered_routes[0]"
        ),
        probe_method=str(methods[0]),
    )


async def load_signed_server_manifest(
    conn: AsyncConnection[DictRow], *, configuration_release_id: UUID
) -> PinnedServerManifest:
    """Read the signed server manifest the pinned release carries.

    A release with no server manifest cannot prepare an observed preview, so this
    refuses rather than activating a service against an empty declaration that
    would authorize nothing and still look healthy.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "select policy from public.configuration_releases where id = %s",
            (configuration_release_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="configuration_releases", row_id=configuration_release_id
        )
    policy = row["policy"]
    if not isinstance(policy, dict):
        raise SourceValidationError(
            field="configuration_releases.policy", reason="is not an object"
        )
    signed = policy.get(_POLICY_KEY)
    if not isinstance(signed, dict):
        raise SourceValidationError(
            field=f"configuration_releases.policy.{_POLICY_KEY}",
            reason=(
                "the pinned configuration release carries no signed server "
                "instrumentation manifest; onboarding must publish one before a "
                "service can activate"
            ),
        )
    return PinnedServerManifest(
        configuration_release_id=configuration_release_id, signed=signed
    )
