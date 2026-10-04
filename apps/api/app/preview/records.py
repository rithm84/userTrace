"""Typed records the preview area commits and returns.

Nothing in this area passes an untyped mapping across a boundary. A readiness
result, a route record, an allocation's pins, and a candidate artifact are each a
closed model, so a probe outcome cannot be a string someone forgot to check and
an allocation cannot claim a template it did not run.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AllocationPins",
    "CandidateArtifact",
    "CandidateFile",
    "PoolRole",
    "PreviewRouteRecord",
    "ReadinessOutcome",
    "ReadinessProbe",
    "ReadinessReport",
    "ReadinessResult",
    "RouteReadinessState",
    "RouteUpstream",
    "RuntimeTemplate",
    "SeedIdentity",
    "SeedTarget",
]

ROUTE_RECORD_SCHEMA_VERSION = "utrace_preview_route_record_v1"
HANDOFF_EXCHANGE_SCHEMA_VERSION = "utrace_preview_handoff_exchange_v1"
DENIAL_SCHEMA_VERSION = "utrace_preview_denial_v1"


class _Record(BaseModel):
    """Immutable, closed base for every preview record."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class PoolRole(StrEnum):
    """What an allocation is for. Matches ``public.preview_pool_role``."""

    WARM_POOL = "warm_pool"
    SESSION_BASE = "session_base"
    CANDIDATE = "candidate"


class SeedTarget(StrEnum):
    """Which dataset installation the runtime performs at start-up."""

    BASE = "base"
    CANDIDATE = "candidate"


class RouteReadinessState(StrEnum):
    """Durable readiness of a committed route revision."""

    READY = "ready"
    RECONNECTING = "reconnecting"


class ReadinessOutcome(StrEnum):
    """What one readiness probe found.

    ``blocked`` is not a pass: it records that the probe could not run because a
    dependency it needs is absent, and it withholds readiness exactly as a
    failure does. ``not_applicable`` records that the pinned template declares
    the probe's subject absent from this runtime, with the reason.
    """

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class ReadinessProbe(StrEnum):
    """The probes a preview runtime answers before it may carry traffic."""

    SERVICE_HEALTH = "service_health"
    DATA_IDENTITY = "data_identity"
    AUTHENTICATED_HANDOFF = "authenticated_handoff"
    ROUTE_RESOLUTION = "route_resolution"
    STALE_ROUTE_REJECTION = "stale_route_rejection"
    SERVER_SDK_ACTIVATION = "server_sdk_activation"
    BROWSER_OBSERVATION = "browser_observation"
    TRACE_CORRELATION = "trace_correlation"
    REDACTION = "redaction"
    SIDE_EFFECT_NEUTERING = "side_effect_neutering"


class SeedIdentity(_Record):
    """The approved dataset a runtime must hold.

    ``manifest_digest`` is the identity a candidate installs independently and
    that validation evidence cites. Frozen table hashes travel with it so a
    partially installed dataset cannot be presented as the approved one.
    """

    fixture_id: str
    schema_version: str
    manifest_digest: str
    fixture_clock: datetime
    table_digests: Mapping[str, str]
    authenticated_identity: str
    """The seeded account identity a preview signs in, such as the growth lead's email."""


class RuntimeTemplate(_Record):
    """One client configuration's pinned preview runtime template."""

    id: UUID
    client_id: UUID
    pinned_configuration_release_id: UUID
    template_id: str
    template_revision: int
    snapshot_id: str
    image_digest: str
    recipe_digest: str
    seed: SeedIdentity
    application_port: int = Field(ge=1, le=65535)
    sdk_versions: Mapping[str, str]
    warm_pool_target: int = Field(ge=0)
    gating_probes: tuple[ReadinessProbe, ...]
    """Probes that must pass or be not applicable before a route may be committed."""


class AllocationPins(_Record):
    """Everything one allocation records about what it is running.

    Validation evidence is admissible only when it cites the exact candidate and
    a matching dataset identity, so these pins are copied onto the allocation
    instead of being read back through the template at query time.
    """

    preview_runtime_allocation_id: UUID
    client_id: UUID
    preview_runtime_template_id: UUID
    pinned_configuration_release_id: UUID
    pool_role: PoolRole
    image_digest: str
    recipe_digest: str
    seed_manifest_digest: str
    fixture_clock: datetime
    authenticated_identity: str
    sdk_versions: Mapping[str, str]
    application_port: int
    runtime_ref: str
    fencing_token: int
    route_revision: int | None


class RouteUpstream(_Record):
    """The server-held public URL of the runtime's application port."""

    port_url: str
    application_port: int = Field(ge=1, le=65535)


class PreviewRouteRecord(_Record):
    """One committed route revision, shaped by ``schemas/preview/route-record.v1``.

    The front door never caches it, so every field it needs travels on every
    resolution: the upstream it may call, the secret it signs route context with,
    the revision that must still be current, and when the mapping stops being
    valid.
    """

    schema_version: str = ROUTE_RECORD_SCHEMA_VERSION
    preview_route_id: UUID
    client_id: UUID
    interview_session_id: UUID
    product_signal_id: UUID
    preview_version_id: UUID
    preview_runtime_allocation_id: UUID
    public_preview_host: str = Field(max_length=253)
    route_revision: int = Field(ge=1)
    readiness_state: RouteReadinessState
    upstream: RouteUpstream
    route_context_signing_secret: str
    preview_entry_token: str | None
    """Attached by the front door as ``x-utrace-preview-entry``.

    The telemetry-ingestion stream issues it when the runtime's Server SDK
    activates, so it is ``None`` on a route whose runtime has not activated one.
    """
    expires_at: datetime


class ReadinessResult(_Record):
    """One probe's typed outcome. Never a log line and never a bare boolean."""

    probe: ReadinessProbe
    outcome: ReadinessOutcome
    detail: Mapping[str, str | int | bool | None]
    observed_at: datetime


class ReadinessReport(_Record):
    """Every probe a runtime answered, and whether that admits traffic."""

    preview_runtime_allocation_id: UUID
    preview_version_id: UUID | None
    route_revision: int | None
    results: tuple[ReadinessResult, ...]
    gating_probes: tuple[ReadinessProbe, ...]
    preview_entry_token: str | None
    """The entry token the activation probe observed, recorded on the allocation."""

    @property
    def ready(self) -> bool:
        """True when every gating probe passed or was declared not applicable."""
        admitted = {ReadinessOutcome.PASSED, ReadinessOutcome.NOT_APPLICABLE}
        answered = {result.probe: result.outcome for result in self.results}
        return all(answered.get(probe) in admitted for probe in self.gating_probes)

    def withheld(self) -> tuple[ReadinessResult, ...]:
        """The gating results that are not admitting traffic, for the blocker record."""
        gating = set(self.gating_probes)
        admitted = {ReadinessOutcome.PASSED, ReadinessOutcome.NOT_APPLICABLE}
        return tuple(
            result
            for result in self.results
            if result.probe in gating and result.outcome not in admitted
        )


class CandidateFile(_Record):
    """One file the approved patch writes at the pinned source revision.

    The patch is carried as the exact bytes to install rather than as a diff:
    the preview runtime image is a source export with no version-control history
    to apply a diff against, and a content digest is what makes the installed
    result verifiable.
    """

    path: str
    """Repository-relative path inside the application directory."""
    content: str
    digest: str
    """``sha256:<hex>`` of the UTF-8 content, rechecked after the file is written."""


class CandidateArtifact(_Record):
    """The immutable Preview Version artifact a candidate runtime is built from.

    The implementation stream publishes this; candidate deployment reads exactly
    these fields and nothing else, so an idle candidate can be rebuilt from the
    artifact and the seed manifest alone. ``files`` are installed at the pinned
    source revision, ``build_recipe`` is the checked command sequence that
    produces the candidate build, and ``migrations`` run only against the
    candidate's own database.
    """

    preview_version_id: UUID
    client_id: UUID
    product_signal_id: UUID
    implementation_attempt_id: UUID
    source_commit: str
    source_digest: str
    build_digest: str
    base_revision: str
    """The template's fixture commit this patch was produced against."""
    patch_digest: str
    """``sha256:<hex>`` over the sorted ``path\n digest`` lines of ``files``."""
    files: tuple[CandidateFile, ...]
    source_manifest: Mapping[str, str]
    build_recipe: Sequence[str]
    """Shell commands run in the candidate's application directory, in order."""
    migrations: Sequence[str]
    """SQL files, relative to the application directory, applied to the candidate database."""
    seed_manifest_digest: str
