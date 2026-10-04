"""Readiness probes and their typed results.

A preview runtime is ready only after the probes its pinned template gates on
have passed. Every probe answers with a typed record, so "ready" is always
reconstructible from what was actually observed rather than from a boolean
somebody set.

Probes reach the runtime the way the front door does: through the runtime's
application port URL with a signed ``x-utrace-route-context``, whose
``public_preview_host`` claim carries the session origin. When the public front
door is deployed the same probes run through the public path instead, which is
the stronger proof; until then the record says which path answered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.ids import uuid7
from app.logging import get_logger
from app.preview.handoff import mint_front_door_claim, mint_product_handoff
from app.preview.ports import ACTIVATION_PORT_OWNER, ObservationReadinessPort
from app.preview.recipe import TemplateRecipe
from app.preview.records import (
    AllocationPins,
    PreviewRouteRecord,
    ReadinessOutcome,
    ReadinessProbe,
    ReadinessReport,
    ReadinessResult,
    SeedTarget,
)
from app.preview.routing import BASE_PREVIEW_VERSION_ID

__all__ = [
    "OBSERVATION_PROBES",
    "PROVISIONING_PROBES",
    "SESSION_PROBES",
    "DoorClaim",
    "DoorEntryError",
    "ProbeTarget",
    "front_door_claim",
    "record_readiness",
    "route_context_token",
    "run_provisioning_readiness",
    "run_session_observation",
    "run_session_readiness",
]

_log = get_logger(__name__)

ROUTE_CONTEXT_HEADER = "x-utrace-route-context"
ROUTE_CONTEXT_SCHEMA_VERSION = "utrace_preview_route_v1"
#: The refusal reason the runtime's own preview gate names on every 401 it makes.
#: Its absence on a refusal says the request never reached the runtime, so a
#: provider hop is distinguishable from the product's middleware without guessing.
PREVIEW_GATE_HEADER = "x-utrace-preview-gate"
_TOKEN_VERSION = "v1"
_PRODUCT_HANDOFF_LIFETIME_SECONDS = 60
_ROUTE_CONTEXT_LIFETIME_SECONDS = 60
_DOOR_CLAIM_LIFETIME_SECONDS = 60
_DOOR_ENTERED_STATUS = 303

type DoorClaim = Callable[[], Awaitable[str]]
"""Mints one single-use front-door claim URL for the route under test."""


class DoorEntryError(RuntimeError):
    """The public front door refused a readiness probe's session claim."""


def front_door_claim(database: Database, *, record: PreviewRouteRecord) -> DoorClaim:
    """Return the claim source a public-path probe enters the door with.

    Each call mints a fresh one-time code for the route's session and origin,
    exactly as the participant's handoff does, because every simulated browser
    needs its own origin-bound door session.
    """

    async def claim() -> str:
        minted = await mint_front_door_claim(
            database,
            client_id=record.client_id,
            interview_session_id=record.interview_session_id,
            preview_route_id=record.preview_route_id,
            public_preview_host=record.public_preview_host,
            lifetime_seconds=_DOOR_CLAIM_LIFETIME_SECONDS,
        )
        return minted.url

    return claim


async def _enter_door(client: httpx.AsyncClient, door_claim: DoorClaim) -> None:
    """Claim a door session into this client's cookie jar, as a participant's browser does.

    The claim URL carries a one-time code, so it is never logged.
    """
    response = await client.get(await door_claim(), follow_redirects=False)
    if response.status_code != _DOOR_ENTERED_STATUS or not response.cookies:
        raise DoorEntryError(
            f"the front door answered the readiness claim with HTTP {response.status_code} "
            f"and {len(response.cookies)} cookies instead of a {_DOOR_ENTERED_STATUS} "
            "that sets the session cookie"
        )

PROVISIONING_PROBES: tuple[ReadinessProbe, ...] = (
    ReadinessProbe.SERVICE_HEALTH,
    ReadinessProbe.DATA_IDENTITY,
    ReadinessProbe.REDACTION,
    ReadinessProbe.SIDE_EFFECT_NEUTERING,
)

#: The probes only the participant's own traffic can satisfy. A browser stream
#: and a joined trace exist once the participant has opened the routed origin,
#: which is necessarily after the route that carries them was committed, so
#: these are answered again by ``run_session_observation`` rather than gating
#: the commit that makes them reachable.
OBSERVATION_PROBES: tuple[ReadinessProbe, ...] = (
    ReadinessProbe.BROWSER_OBSERVATION,
    ReadinessProbe.TRACE_CORRELATION,
)

SESSION_PROBES: tuple[ReadinessProbe, ...] = (
    ReadinessProbe.SERVICE_HEALTH,
    ReadinessProbe.DATA_IDENTITY,
    ReadinessProbe.AUTHENTICATED_HANDOFF,
    ReadinessProbe.ROUTE_RESOLUTION,
    ReadinessProbe.STALE_ROUTE_REJECTION,
    ReadinessProbe.SERVER_SDK_ACTIVATION,
    ReadinessProbe.BROWSER_OBSERVATION,
    ReadinessProbe.TRACE_CORRELATION,
    ReadinessProbe.REDACTION,
    ReadinessProbe.SIDE_EFFECT_NEUTERING,
)

type ProbeDetail = Mapping[str, str | int | bool | None]


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def route_context_token(
    *, record: PreviewRouteRecord, signing_secret: str, lifetime_seconds: int
) -> str:
    """Sign the route context the front door attaches to every forwarded request.

    The envelope and claim set are the runtime verifier's, not this module's:
    ``v1.<base64url payload>.<base64url HMAC-SHA256 signature>`` over the encoded
    payload bytes exactly as transmitted.
    """
    issued_at = datetime.now(tz=UTC)
    expires_at = min(
        record.expires_at, issued_at + timedelta(seconds=lifetime_seconds)
    )
    claims = {
        "schema_version": ROUTE_CONTEXT_SCHEMA_VERSION,
        "preview_route_id": str(record.preview_route_id),
        "client_id": str(record.client_id),
        "product_signal_id": str(record.product_signal_id),
        "preview_version_id": str(record.preview_version_id),
        "preview_runtime_allocation_id": str(record.preview_runtime_allocation_id),
        "public_preview_host": record.public_preview_host,
        "route_revision": record.route_revision,
        "issued_at": issued_at.isoformat(timespec="milliseconds"),
        "expires_at": expires_at.isoformat(timespec="milliseconds"),
    }
    payload = _base64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = _base64url(
        hmac.new(
            signing_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).digest()
    )
    return f"{_TOKEN_VERSION}.{payload}.{signature}"


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """Where probes send requests and how they authenticate the hop.

    ``public_path`` records whether the public front door answered. Both paths
    reach the same runtime; only the public one also proves DNS, TLS, the door's
    own guards, and the session cookie. A public-path probe therefore needs the
    door's origin-bound cookie in the caller's client, obtained by claiming the
    session's one-time code first.
    """

    base_url: str
    public_preview_host: str
    route_context: str | None
    public_path: bool

    def headers(self, extra: Mapping[str, str]) -> dict[str, str]:
        """Build the request headers for one probe."""
        headers = dict(extra)
        if self.route_context is not None and not self.public_path:
            # The door sets its own route context and strips any caller-supplied copy.
            headers[ROUTE_CONTEXT_HEADER] = self.route_context
        return headers

    def url(self, path: str) -> str:
        """Point the target's base URL at one path."""
        return f"{self.base_url.rstrip('/')}{path}"


def upstream_target(
    *, record: PreviewRouteRecord, signing_secret: str, public_path_enabled: bool
) -> ProbeTarget:
    """Build the probe target for one committed route revision."""
    if public_path_enabled:
        return ProbeTarget(
            base_url=f"https://{record.public_preview_host}",
            public_preview_host=record.public_preview_host,
            route_context=None,
            public_path=True,
        )
    return ProbeTarget(
        base_url=record.upstream.port_url,
        public_preview_host=record.public_preview_host,
        route_context=route_context_token(
            record=record,
            signing_secret=signing_secret,
            lifetime_seconds=_ROUTE_CONTEXT_LIFETIME_SECONDS,
        ),
        public_path=public_path_enabled,
    )


def provisioning_target(*, upstream_url: str, public_preview_host: str) -> ProbeTarget:
    """Build the probe target for a runtime that has no route yet.

    A warm-pool runtime is verified before any Interview Session exists, so there
    is no committed route and no route context. Only the routeless surfaces —
    health and the dataset identity it reports — can be probed here.
    """
    return ProbeTarget(
        base_url=upstream_url,
        public_preview_host=public_preview_host,
        route_context=None,
        public_path=False,
    )


def _refusal(response: httpx.Response) -> dict[str, str | None]:
    """Describe where a refusal was made, with nothing the runtime holds in it.

    A readiness record that says only ``401`` cannot distinguish the provider's
    proxy from the runtime's own gate, which is the first question any preview
    refusal raises. The gate's reason is a closed set of configuration and
    envelope faults, so it is recorded verbatim; response bodies are not, because
    a product route's body is customer-shaped.
    """
    return {
        "gate_refusal": response.headers.get(PREVIEW_GATE_HEADER),
        "content_type": response.headers.get("content-type"),
    }


def _result(
    probe: ReadinessProbe, outcome: ReadinessOutcome, detail: ProbeDetail
) -> ReadinessResult:
    return ReadinessResult(
        probe=probe,
        outcome=outcome,
        detail=dict(detail),
        observed_at=datetime.now(tz=UTC),
    )


async def _fetch_health(
    client: httpx.AsyncClient, target: ProbeTarget, recipe: TemplateRecipe
) -> tuple[int, Mapping[str, object]]:
    response = await client.get(
        target.url(recipe.health_path), headers=target.headers({})
    )
    try:
        body = response.json()
    except ValueError:
        return response.status_code, {}
    return response.status_code, body if isinstance(body, dict) else {}


async def _probe_service_health(
    client: httpx.AsyncClient, target: ProbeTarget, recipe: TemplateRecipe
) -> tuple[ReadinessResult, Mapping[str, object]]:
    status, body = await _fetch_health(client, target, recipe)
    database = body.get("database")
    reachable = bool(database.get("reachable")) if isinstance(database, dict) else False
    healthy = status == 200 and body.get("status") == "ok" and reachable
    return (
        _result(
            ReadinessProbe.SERVICE_HEALTH,
            ReadinessOutcome.PASSED if healthy else ReadinessOutcome.FAILED,
            {
                "status_code": status,
                "profile": str(body.get("profile")),
                "database_reachable": reachable,
                "path": "public" if target.public_path else "port_upstream",
            },
        ),
        body,
    )


def _probe_data_identity(
    health: Mapping[str, object], pins: AllocationPins, seed_target: SeedTarget
) -> ReadinessResult:
    manifest = health.get("manifest")
    if not isinstance(manifest, dict):
        return _result(
            ReadinessProbe.DATA_IDENTITY,
            ReadinessOutcome.FAILED,
            {"reason": "the runtime reported no installed manifest"},
        )
    content_hash = str(manifest.get("content_hash"))
    matches_build = manifest.get("matches_build") is True
    target_matches = manifest.get("seed_target") == seed_target.value
    identity_matches = content_hash == pins.seed_manifest_digest
    passed = matches_build and target_matches and identity_matches
    return _result(
        ReadinessProbe.DATA_IDENTITY,
        ReadinessOutcome.PASSED if passed else ReadinessOutcome.FAILED,
        {
            "installed_content_hash": content_hash,
            "expected_content_hash": pins.seed_manifest_digest,
            "matches_build": matches_build,
            "seed_target": str(manifest.get("seed_target")),
            "expected_seed_target": seed_target.value,
        },
    )


async def _probe_route_resolution(
    client: httpx.AsyncClient, target: ProbeTarget
) -> ReadinessResult:
    response = await client.get(target.url("/"), headers=target.headers({}))
    accepted = response.status_code not in (401, 403)
    return _result(
        ReadinessProbe.ROUTE_RESOLUTION,
        ReadinessOutcome.PASSED if accepted else ReadinessOutcome.FAILED,
        {
            "status_code": response.status_code,
            "path": "public" if target.public_path else "port_upstream",
            **_refusal(response),
        },
    )


async def _probe_stale_route_rejection(
    client: httpx.AsyncClient, target: ProbeTarget, record: PreviewRouteRecord
) -> ReadinessResult:
    """Prove the runtime refuses route authority it did not issue.

    Two refusals are required: a context signed with a secret this runtime was
    never given, and no context at all. The first is what a superseded revision
    looks like after an advance, because each revision's secret belongs to the
    runtime it points at.
    """
    foreign = route_context_token(
        record=record,
        signing_secret=f"not-this-runtimes-secret-{uuid7().hex}",
        lifetime_seconds=_ROUTE_CONTEXT_LIFETIME_SECONDS,
    )
    headers = target.headers({})
    headers[ROUTE_CONTEXT_HEADER] = foreign
    forged = await client.get(target.url("/"), headers=headers)

    bare = dict(target.headers({}))
    bare.pop(ROUTE_CONTEXT_HEADER, None)
    missing = await client.get(target.url("/"), headers=bare)

    refused = forged.status_code == 401 and missing.status_code == 401
    return _result(
        ReadinessProbe.STALE_ROUTE_REJECTION,
        ReadinessOutcome.PASSED if refused else ReadinessOutcome.FAILED,
        {
            "foreign_secret_status": forged.status_code,
            "foreign_secret_refusal": forged.headers.get(PREVIEW_GATE_HEADER),
            "absent_context_status": missing.status_code,
            "absent_context_refusal": missing.headers.get(PREVIEW_GATE_HEADER),
        },
    )


async def _probe_authenticated_handoff(
    client: httpx.AsyncClient,
    target: ProbeTarget,
    recipe: TemplateRecipe,
    pins: AllocationPins,
    handoff_secret: str,
    door_claim: DoorClaim | None,
) -> ReadinessResult:
    """Sign the originating user in and prove the data API still needs that session.

    The probe is one simulated browser with its own cookie jar, so a session cookie
    an earlier readiness run set on the same host cannot authenticate its anonymous
    request. The anonymous request runs first, on purpose: the sign-in response sets
    the product's own session cookie, so a request after it would prove nothing.
    """
    async with httpx.AsyncClient(
        timeout=client.timeout, follow_redirects=client.follow_redirects
    ) as browser:
        if door_claim is not None:
            await _enter_door(browser, door_claim)
        return await _sign_in_once(browser, target, recipe, pins, handoff_secret)


async def _sign_in_once(
    browser: httpx.AsyncClient,
    target: ProbeTarget,
    recipe: TemplateRecipe,
    pins: AllocationPins,
    handoff_secret: str,
) -> ReadinessResult:
    """Run the anonymous request, the sign-in, and its replay in one browser."""
    anonymous = await browser.post(
        target.url(recipe.data_api_path),
        headers=target.headers({"content-type": "application/json"}),
        json=dict(recipe.data_api_probe_body),
    )
    token = mint_product_handoff(
        handoff_secret=handoff_secret,
        audience_host=target.public_preview_host,
        subject_email=pins.authenticated_identity,
        lifetime_seconds=_PRODUCT_HANDOFF_LIFETIME_SECONDS,
    )
    signed_in = await browser.post(
        target.url(recipe.handoff_path),
        headers=target.headers({"content-type": "application/json"}),
        json={"token": token.token},
    )
    body = signed_in.json() if signed_in.status_code == 200 else {}
    user = body.get("user") if isinstance(body, dict) else None
    identity = user.get("email") if isinstance(user, dict) else None

    replayed = await browser.post(
        target.url(recipe.handoff_path),
        headers=target.headers({"content-type": "application/json"}),
        json={"token": token.token},
    )
    passed = (
        signed_in.status_code == 200
        and identity == pins.authenticated_identity
        and replayed.status_code == 401
        and anonymous.status_code == 401
    )
    return _result(
        ReadinessProbe.AUTHENTICATED_HANDOFF,
        ReadinessOutcome.PASSED if passed else ReadinessOutcome.FAILED,
        {
            "sign_in_status": signed_in.status_code,
            "signed_in_identity": identity if isinstance(identity, str) else None,
            "expected_identity": pins.authenticated_identity,
            "replay_status": replayed.status_code,
            "anonymous_data_api_status": anonymous.status_code,
            **_refusal(signed_in),
        },
    )


async def _probe_observation(
    port: ObservationReadinessPort | None,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
) -> tuple[tuple[ReadinessResult, ...], str | None]:
    """Answer the three observation probes, and return the preview entry token.

    The activation answer is what the runtime's own Server SDK committed. It is
    the one of the three that can be true before any participant arrives, and it
    has to be: the front door needs the activation's preview entry token before
    it will let a browser-issued ``traceparent`` reach the runtime at all.

    The browser answer is the ingestion stream's ``browser`` projection. The
    correlation answer requires its ``server`` projection too, because a joined
    trace is by definition a server span that accepted the browser's parent; a
    correlation claim without a ready server stream would assert a join nobody
    observed.
    """
    if port is None:
        blocked = {
            "reason": "no observation readiness adapter is installed",
            "owning_workstream": ACTIVATION_PORT_OWNER,
        }
        return (
            (
                _result(
                    ReadinessProbe.SERVER_SDK_ACTIVATION, ReadinessOutcome.BLOCKED, blocked
                ),
                _result(
                    ReadinessProbe.BROWSER_OBSERVATION, ReadinessOutcome.BLOCKED, blocked
                ),
                _result(
                    ReadinessProbe.TRACE_CORRELATION, ReadinessOutcome.BLOCKED, blocked
                ),
            ),
            None,
        )
    activation = await port.activation(
        client_id=client_id,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
    )
    readiness = await port.readiness(
        client_id=client_id, interview_session_id=interview_session_id
    )
    results = (
        _result(
            ReadinessProbe.SERVER_SDK_ACTIVATION,
            ReadinessOutcome.PASSED if activation.activated else ReadinessOutcome.FAILED,
            {
                "service_name": activation.service_name,
                "sdk_version": activation.sdk_version,
                "trust_anchor_key_id": activation.manifest_trust_anchor_key_id,
                "server_projection_ready": readiness.server_ready,
                "entry_token_issued": activation.preview_entry_token is not None,
                "detail": activation.detail,
            },
        ),
        _result(
            ReadinessProbe.BROWSER_OBSERVATION,
            ReadinessOutcome.PASSED
            if readiness.browser_ready
            else ReadinessOutcome.FAILED,
            {
                "observed_at": readiness.observed_at.isoformat(),
                "detail": readiness.detail,
            },
        ),
        _result(
            ReadinessProbe.TRACE_CORRELATION,
            ReadinessOutcome.PASSED
            if readiness.server_ready and readiness.correlation_ready
            else ReadinessOutcome.FAILED,
            {
                "server_projection_ready": readiness.server_ready,
                "observed_at": readiness.observed_at.isoformat(),
                "detail": readiness.detail,
            },
        ),
    )
    return results, activation.preview_entry_token


def _declared_absent(recipe: TemplateRecipe, probe: ReadinessProbe) -> ReadinessResult:
    """Record a probe the pinned template declares this runtime has no subject for."""
    reason = recipe.not_applicable_probes.get(probe)
    if reason is None:
        return _result(
            probe,
            ReadinessOutcome.BLOCKED,
            {
                "reason": (
                    "the pinned template neither exercises this probe nor records "
                    "why its subject is absent"
                )
            },
        )
    return _result(probe, ReadinessOutcome.NOT_APPLICABLE, {"reason": reason})


async def run_provisioning_readiness(
    *,
    client: httpx.AsyncClient,
    target: ProbeTarget,
    recipe: TemplateRecipe,
    pins: AllocationPins,
    seed_target: SeedTarget,
    gating_probes: Sequence[ReadinessProbe],
) -> ReadinessReport:
    """Probe a runtime that has no route yet, before it enters the warm pool."""
    health_result, health = await _probe_service_health(client, target, recipe)
    results = [
        health_result,
        _probe_data_identity(health, pins, seed_target),
        _declared_absent(recipe, ReadinessProbe.REDACTION),
        _declared_absent(recipe, ReadinessProbe.SIDE_EFFECT_NEUTERING),
    ]
    return ReadinessReport(
        preview_runtime_allocation_id=pins.preview_runtime_allocation_id,
        preview_version_id=None,
        route_revision=None,
        results=tuple(results),
        gating_probes=tuple(
            probe for probe in gating_probes if probe in PROVISIONING_PROBES
        ),
        preview_entry_token=None,
    )


async def run_session_readiness(
    *,
    client: httpx.AsyncClient,
    record: PreviewRouteRecord,
    recipe: TemplateRecipe,
    pins: AllocationPins,
    seed_target: SeedTarget,
    handoff_secret: str,
    gating_probes: Sequence[ReadinessProbe],
    observation_port: ObservationReadinessPort | None,
    door_claim: DoorClaim | None,
) -> ReadinessReport:
    """Probe a committed route revision end to end before it may carry traffic.

    With a `door_claim` the traffic probes go through the public front door and
    first claim a door session, as the participant's browser does; without one
    they call the runtime directly.
    """
    # The runtime target always carries a signed route context: it is what proves
    # the runtime's own middleware, independently of the provider hop. The traffic
    # target is the public path once the door is deployed, because that also
    # proves DNS, TLS, the door's guards, and the origin-bound session cookie.
    runtime_target = upstream_target(
        record=record,
        signing_secret=record.route_context_signing_secret,
        public_path_enabled=False,
    )
    traffic_target = upstream_target(
        record=record,
        signing_secret=record.route_context_signing_secret,
        public_path_enabled=door_claim is not None,
    )
    if door_claim is not None:
        await _enter_door(client, door_claim)
    health_result, health = await _probe_service_health(client, traffic_target, recipe)
    results = [
        health_result,
        _probe_data_identity(health, pins, seed_target),
        await _probe_route_resolution(client, traffic_target),
        await _probe_stale_route_rejection(client, runtime_target, record),
        await _probe_authenticated_handoff(
            client, traffic_target, recipe, pins, handoff_secret, door_claim
        ),
    ]
    observation, entry_token = await _probe_observation(
        observation_port,
        client_id=record.client_id,
        interview_session_id=record.interview_session_id,
        preview_runtime_allocation_id=record.preview_runtime_allocation_id,
    )
    results.extend(observation)
    results.append(_declared_absent(recipe, ReadinessProbe.REDACTION))
    results.append(_declared_absent(recipe, ReadinessProbe.SIDE_EFFECT_NEUTERING))
    report = ReadinessReport(
        preview_runtime_allocation_id=record.preview_runtime_allocation_id,
        preview_version_id=record.preview_version_id,
        route_revision=record.route_revision,
        results=tuple(results),
        gating_probes=tuple(probe for probe in gating_probes if probe in SESSION_PROBES),
        preview_entry_token=entry_token,
    )
    _log.info(
        "preview_session_readiness",
        preview_runtime_allocation_id=str(record.preview_runtime_allocation_id),
        route_revision=record.route_revision,
        ready=report.ready,
        withheld=[result.probe.value for result in report.withheld()],
    )
    return report


async def run_session_observation(
    *,
    record: PreviewRouteRecord,
    observation_port: ObservationReadinessPort,
) -> ReadinessReport:
    """Answer the observation probes again for a route that already carries traffic.

    The Server SDK activates when the runtime is prepared, but the browser and
    correlation answers only exist once the participant's browser has reached
    the routed origin and its telemetry has joined the server's. This re-runs
    all three against the current route and gates on every one of them, so the
    Interview Session's observation readiness is a recorded observation rather
    than an assumption carried over from commit time.
    """
    results, entry_token = await _probe_observation(
        observation_port,
        client_id=record.client_id,
        interview_session_id=record.interview_session_id,
        preview_runtime_allocation_id=record.preview_runtime_allocation_id,
    )
    report = ReadinessReport(
        preview_runtime_allocation_id=record.preview_runtime_allocation_id,
        preview_version_id=record.preview_version_id,
        route_revision=record.route_revision,
        results=results,
        gating_probes=(ReadinessProbe.SERVER_SDK_ACTIVATION, *OBSERVATION_PROBES),
        preview_entry_token=entry_token,
    )
    _log.info(
        "preview_session_observation",
        preview_runtime_allocation_id=str(record.preview_runtime_allocation_id),
        route_revision=record.route_revision,
        ready=report.ready,
        withheld=[result.probe.value for result in report.withheld()],
    )
    return report


def _durable_version_id(preview_version_id: UUID | None) -> UUID | None:
    """Map the base route's nil Preview Version onto a null column.

    The wire contract requires a Preview Version on every route record, and the
    base runtime uses the nil UUID for "the pinned template, not a candidate".
    No such row exists, so the durable record stores null instead.
    """
    if preview_version_id is None or preview_version_id == BASE_PREVIEW_VERSION_ID:
        return None
    return preview_version_id


async def record_readiness(
    database: Database,
    *,
    client_id: UUID,
    report: ReadinessReport,
    preview_route_id: UUID | None,
) -> None:
    """Append every probe result, and record the entry token the probes observed.

    The entry token is written onto the allocation rather than the route, because
    the front door resolves it through the allocation and an activation that
    lands after a revision was committed must reach the next request without a
    new revision.
    """
    async with database.transaction() as conn, conn.cursor() as cur:
        if report.preview_entry_token is not None:
            await cur.execute(
                """
                update public.preview_runtime_allocations
                set preview_entry_token = %s, revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (report.preview_entry_token, report.preview_runtime_allocation_id),
            )
        for result in report.results:
            await cur.execute(
                """
                insert into public.preview_readiness_results (
                    id, client_id, preview_runtime_allocation_id, preview_route_id,
                    preview_version_id, route_revision, probe, outcome, detail,
                    observed_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid7(),
                    client_id,
                    report.preview_runtime_allocation_id,
                    preview_route_id,
                    _durable_version_id(report.preview_version_id),
                    report.route_revision,
                    result.probe.value,
                    result.outcome.value,
                    Jsonb(dict(result.detail)),
                    result.observed_at,
                ),
            )
