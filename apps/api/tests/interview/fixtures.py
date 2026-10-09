"""Durable fixtures and stand-ins for the interview-orchestration tests.

Two kinds of thing live here, and they are different:

- **Durable fixtures** insert real rows through the same connector the control
  plane uses, so every test exercises the production schema and its constraints.
  Registered targets are made real by writing ingestion's own
  `browser_observations`: the baseline and registry changes a document's runtime
  exports.
- **A stand-in for observation readiness.** `RecordingObservationReadiness`
  answers the readiness protocol with facts the test states, for tests that do
  not stand up the whole ingestion edge. Nothing in `app/` is replaced.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
)

from app.db.pool import Database
from app.db.rows import as_uuid
from app.ids import uuid7
from app.ingestion.preview_environments import resolve_preview_environment_id
from app.ingestion.readiness import ReadinessFact, publish_readiness
from app.interview.ports import RegisteredTarget
from app.preview.ports import ServerSdkActivation, TelemetryReadiness
from tests.factories import CaseFixture
from tests.helpers import record_session_readiness

__all__ = [
    "ObservedDocument",
    "RecordingObservationReadiness",
    "complete_readiness",
    "create_binding",
    "create_live_session",
    "create_preview_allocation",
    "incomplete_readiness",
    "link_installation",
    "observe_document",
    "observe_ready_page",
    "open_page",
    "record_ready_page",
    "record_registry_change",
    "session_client_user",
]


class RecordingObservationReadiness:
    """`ObservationReadinessPort` over readiness the test states explicitly.

    The real adapter reads ingestion's projections; this one answers the same
    protocol so a test can state a complete or incomplete correlated path
    without standing up the whole ingestion edge.
    """

    def __init__(self, *, readiness: TelemetryReadiness) -> None:
        self._readiness = readiness
        self.seen_queries: list[str] = []

    async def activation(
        self,
        *,
        client_id: UUID,
        interview_session_id: UUID,
        preview_runtime_allocation_id: UUID,
    ) -> ServerSdkActivation:
        """Report an activation the preview stream would have observed."""
        self.seen_queries.append("activation")
        return ServerSdkActivation(
            activated=self._readiness.server_ready,
            service_name="shopalytics",
            manifest_trust_anchor_key_id="test-anchor",
            sdk_version="1.0.0",
            activated_at=self._readiness.observed_at,
            preview_entry_token="test-entry-token"
            if self._readiness.server_ready
            else None,
            detail="" if self._readiness.server_ready else "no server activation",
        )

    async def readiness(
        self, *, client_id: UUID, interview_session_id: UUID
    ) -> TelemetryReadiness:
        """Return the readiness the test stated."""
        self.seen_queries.append("readiness")
        return self._readiness


def complete_readiness() -> TelemetryReadiness:
    """A complete correlated evidence path across all three kinds."""
    return TelemetryReadiness(
        browser_ready=True,
        server_ready=True,
        correlation_ready=True,
        observed_at=datetime.now(tz=UTC),
        detail="",
    )


def incomplete_readiness(detail: str) -> TelemetryReadiness:
    """An incomplete correlated evidence path, with the reason it is incomplete."""
    return TelemetryReadiness(
        browser_ready=False,
        server_ready=False,
        correlation_ready=False,
        observed_at=datetime.now(tz=UTC),
        detail=detail,
    )


async def create_preview_allocation(
    database: Database, *, case: CaseFixture, interview_session_id: UUID, state: str
) -> UUID:
    """Insert one base Preview Runtime Allocation and bind it to a session."""
    allocation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                interview_session_id, origin_host, runtime_ref, fencing_token
            )
            values (%s, %s, %s, 'base', 'session_base', %s, %s, %s, %s,
                    utrace.next_fencing_token())
            """,
            (
                allocation_id,
                case.client_id,
                case.product_signal_id,
                state,
                interview_session_id,
                f"{allocation_id.hex[:12]}.preview.utrace.dev",
                f"daytona:{allocation_id.hex[:8]}",
            ),
        )
        await cur.execute(
            """
            update public.interview_sessions
            set preview_runtime_allocation_id = %s, revision = revision + 1
            where id = %s
            """,
            (allocation_id, interview_session_id),
        )
    return allocation_id


async def create_live_session(
    database: Database, *, case: CaseFixture, phase: InterviewPhase
) -> UUID:
    """Insert one live, conversationally ready Interview Session."""
    session_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.interview_sessions (
                id, client_id, product_signal_id, client_user_id, state, phase,
                character_state, implementation_progress
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                case.client_id,
                case.product_signal_id,
                case.client_user_id,
                InterviewSessionState.LIVE.value,
                phase.value,
                AgentCharacterState.LISTENING.value,
                ImplementationProgress.NOT_STARTED.value,
            ),
        )
    await record_session_readiness(
        database,
        interview_session_id=session_id,
        conversational=True,
        observation=False,
    )
    return session_id


async def link_installation(
    database: Database,
    *,
    utrace_user_id: UUID,
    device_public_key_spki_b64u: str,
    protocol_version: str,
) -> UUID:
    """Insert one linked extension installation for a uTrace User."""
    installation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.extension_installations (
                id, utrace_user_id, device_public_key_spki_b64u, account_link_state,
                extension_version, supported_protocol_versions,
                negotiated_protocol_version
            )
            values (%s, %s, %s, 'linked', '1.0.0', %s, %s)
            """,
            (
                installation_id,
                utrace_user_id,
                device_public_key_spki_b64u,
                [protocol_version],
                protocol_version,
            ),
        )
        await cur.execute(
            """
            insert into public.extension_preferences (utrace_user_id)
            values (%s)
            on conflict (utrace_user_id) do nothing
            """,
            (utrace_user_id,),
        )
    return installation_id


async def create_binding(
    database: Database,
    *,
    case: CaseFixture,
    interview_session_id: UUID,
    extension_installation_id: UUID,
    preview_runtime_allocation_id: UUID,
) -> tuple[UUID, UUID]:
    """Insert one bound document for a session, returning binding and attachment."""
    binding_id = uuid7()
    attachment_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.extension_bindings (
                id, client_id, interview_session_id, extension_installation_id,
                preview_runtime_allocation_id, document_attachment_id, status,
                binding_revision, document_origin, tab_id, document_nonce,
                sdk_protocol_version, page_proof_digest
            )
            values (%s, %s, %s, %s, %s, %s, 'bound', 1, %s, 7, 'nonce-1',
                    '1.0', 'sha256:test')
            """,
            (
                binding_id,
                case.client_id,
                interview_session_id,
                extension_installation_id,
                preview_runtime_allocation_id,
                attachment_id,
                "https://preview.utrace.dev",
            ),
        )
    return binding_id, attachment_id


async def session_client_user(database: Database, *, session_id: UUID) -> UUID:
    """Return the client-scoped identity bound to one Interview Session."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select client_user_id from public.interview_sessions where id = %s",
            (session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"interview session {session_id} does not exist")
    value = row["client_user_id"]
    if not isinstance(value, UUID):
        raise TypeError("interview_sessions.client_user_id is not a UUID")
    return value


@dataclass(frozen=True, slots=True)
class ObservedDocument:
    """One document's browser stream, as ingestion records it."""

    client_id: UUID
    activation_id: UUID
    browser_stream_id: UUID
    ingestion_receipt_id: UUID
    runtime_id: UUID
    document_attachment_id: UUID


async def observe_document(
    database: Database,
    *,
    case: CaseFixture,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
    document_attachment_id: UUID,
    targets: Sequence[RegisteredTarget],
) -> ObservedDocument:
    """Commit the activation baseline one document's runtime exports.

    The rows are the ones telemetry ingestion writes: the activation, credential,
    stream, and receipt the observation's foreign keys require, and one
    `baseline.snapshot` whose mounted targets are ``targets``, each described by
    its released label. The target registry reads exactly these.
    """
    template_id = uuid7()
    environment_id = uuid7()
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_templates (
                id, client_id, pinned_configuration_release_id, template_id,
                template_revision, snapshot_id, image_digest, recipe_digest,
                seed_manifest_digest, seed_manifest, fixture_clock,
                application_port, authenticated_identity, sdk_versions,
                warm_pool_target
            )
            values (%s, %s, %s, %s, 1, 'snapshot', 'sha256:image', 'sha256:recipe',
                    'sha256:seed', '{}'::jsonb, %s, 3000, 'maya@dudulemon.test',
                    '{}'::jsonb, 1)
            """,
            (
                template_id,
                case.client_id,
                case.configuration_release_id,
                f"template-{template_id.hex[:12]}",
                now,
            ),
        )
        await cur.execute(
            """
            insert into public.preview_environments (
                id, client_id, preview_runtime_template_id,
                pinned_configuration_release_id
            )
            values (%s, %s, %s, %s)
            """,
            (
                environment_id,
                case.client_id,
                template_id,
                case.configuration_release_id,
            ),
        )
    return await _attach_document(
        database,
        case=case,
        interview_session_id=interview_session_id,
        preview_environment_id=environment_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
        document_attachment_id=document_attachment_id,
        targets=targets,
    )


async def open_page(
    database: Database,
    *,
    case: CaseFixture,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
) -> ObservedDocument:
    """Open a page on one preview, before any readiness evaluation has read it.

    The page joins the preview environment of the allocation's own template, as an
    activation on that preview does.
    """
    async with database.connection() as conn:
        environment_id = await resolve_preview_environment_id(
            conn, preview_runtime_allocation_id=preview_runtime_allocation_id
        )
    return await _attach_document(
        database,
        case=case,
        interview_session_id=interview_session_id,
        preview_environment_id=environment_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
        document_attachment_id=uuid7(),
        targets=(),
    )


async def observe_ready_page(
    database: Database,
    *,
    case: CaseFixture,
    interview_session_id: UUID,
    preview_runtime_allocation_id: UUID,
) -> ObservedDocument:
    """Open a page on one preview and leave its observation path ready, as ingestion does."""
    document = await open_page(
        database,
        case=case,
        interview_session_id=interview_session_id,
        preview_runtime_allocation_id=preview_runtime_allocation_id,
    )
    await record_ready_page(database, case=case, document=document)
    return document


async def record_ready_page(
    database: Database, *, case: CaseFixture, document: ObservedDocument
) -> None:
    """Make one observed page's path ready: its preview's server stream and a trace under it.

    The three readiness projections name what they were evaluated from, as
    ingestion's evaluation does, so a reader can tell they describe this page.
    """
    server_activation_id = uuid7()
    server_stream_id = uuid7()
    service_instance_id = uuid7()
    correlation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select interview_session_id, preview_environment_id, preview_runtime_allocation_id
            from public.telemetry_activations where id = %s
            """,
            (document.activation_id,),
        )
        page = await cur.fetchone()
        if page is None:
            raise LookupError(f"activation {document.activation_id} does not exist")
        session_id = as_uuid(page, "interview_session_id")
        await cur.execute(
            """
            insert into public.telemetry_activations (
                id, client_id, kind, state, client_user_id, customer_account_id,
                interview_session_id, preview_environment_id,
                preview_runtime_allocation_id, protocol_version,
                source_schema_version, policy_version, redaction_version, limits,
                server_stream_id, service_instance_id, service_key,
                preview_entry_token_digest, semantic_convention_version, probe_id
            )
            values (%s, %s, 'server', 'active', %s, %s, %s, %s, %s, '1.0', '1',
                    'policy-1', 'redaction-1', '{}'::jsonb, %s, %s, 'shopalytics.api',
                    %s, '1.37.0', %s)
            """,
            (
                server_activation_id,
                case.client_id,
                case.client_user_id,
                case.customer_account_id,
                session_id,
                as_uuid(page, "preview_environment_id"),
                as_uuid(page, "preview_runtime_allocation_id"),
                server_stream_id,
                service_instance_id,
                f"digest-{server_activation_id.hex}",
                uuid7(),
            ),
        )
        await cur.execute(
            """
            insert into public.server_streams (
                id, client_id, activation_id, service_instance_id, service_key
            )
            values (%s, %s, %s, %s, 'shopalytics.api')
            """,
            (server_stream_id, case.client_id, server_activation_id, service_instance_id),
        )
        await cur.execute(
            """
            insert into public.correlation_projections (
                id, client_id, activation_id, interview_session_id, trace_id,
                required_components, observed_components, gaps, conflicts, status
            )
            values (%s, %s, %s, %s, %s, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                    '[]'::jsonb, 'complete')
            """,
            (correlation_id, case.client_id, document.activation_id, session_id, uuid7().hex),
        )
    for kind, resource in (
        ("browser", document.browser_stream_id),
        ("server", server_stream_id),
        ("correlation", correlation_id),
    ):
        await publish_readiness(
            database,
            client_id=case.client_id,
            activation_id=document.activation_id,
            interview_session_id=session_id,
            fact=ReadinessFact(
                kind=kind,
                ready=True,
                contributing_resource_ids=[str(resource)],
                accepted_watermarks={},
                probe_id=None,
                failures=[],
            ),
        )


async def observe_next_document(
    database: Database,
    *,
    case: CaseFixture,
    earlier: ObservedDocument,
    preview_runtime_allocation_id: UUID,
    document_attachment_id: UUID,
    targets: Sequence[RegisteredTarget],
) -> ObservedDocument:
    """Commit the activation baseline of a page loaded after ``earlier`` in its session.

    The new document joins the earlier one's session and preview environment, as a
    reload or a navigation does, with its own activation, stream, and baseline.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select interview_session_id, preview_environment_id
            from public.telemetry_activations where id = %s
            """,
            (earlier.activation_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"activation {earlier.activation_id} does not exist")
    return await _attach_document(
        database,
        case=case,
        interview_session_id=as_uuid(row, "interview_session_id"),
        preview_environment_id=as_uuid(row, "preview_environment_id"),
        preview_runtime_allocation_id=preview_runtime_allocation_id,
        document_attachment_id=document_attachment_id,
        targets=targets,
    )


async def _attach_document(
    database: Database,
    *,
    case: CaseFixture,
    interview_session_id: UUID,
    preview_environment_id: UUID,
    preview_runtime_allocation_id: UUID,
    document_attachment_id: UUID,
    targets: Sequence[RegisteredTarget],
) -> ObservedDocument:
    """The activation, credential, stream, receipt, and baseline of one document."""
    activation_id = uuid7()
    credential_id = uuid7()
    stream_id = uuid7()
    receipt_id = uuid7()
    runtime_id = uuid7()
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.telemetry_activations (
                id, client_id, kind, state, client_user_id, customer_account_id,
                interview_session_id, preview_environment_id,
                preview_runtime_allocation_id, protocol_version,
                source_schema_version, policy_version, redaction_version,
                annotation_manifest_version, annotation_manifest_digest, limits,
                document_attachment_id, document_nonce, document_origin,
                sdk_installation_id, attestation_expires_at
            )
            values (%s, %s, 'browser', 'active', %s, %s, %s, %s, %s, '1.0', '1',
                    'policy-1', 'redaction-1', 'manifest-1',
                    'sha256:0000000000000000000000000000000000000000000000000000000000000000',
                    '{}'::jsonb, %s, %s, %s, 'shopalytics-browser-sdk', %s)
            """,
            (
                activation_id,
                case.client_id,
                case.client_user_id,
                case.customer_account_id,
                interview_session_id,
                preview_environment_id,
                preview_runtime_allocation_id,
                document_attachment_id,
                f"nonce-{activation_id.hex[:16]}",
                "https://preview.utrace.test",
                now + timedelta(hours=1),
            ),
        )
        await cur.execute(
            """
            insert into public.telemetry_credentials (
                id, client_id, activation_id, kind, token_digest, expires_at
            )
            values (%s, %s, %s, 'browser', %s, %s)
            """,
            (
                credential_id,
                case.client_id,
                activation_id,
                f"digest-{credential_id.hex}",
                now + timedelta(hours=1),
            ),
        )
        await cur.execute(
            """
            insert into public.browser_streams (
                id, client_id, activation_id, runtime_id, document_attachment_id,
                accepted_through_export_sequence, highest_received_export_sequence
            )
            values (%s, %s, %s, %s, %s, 1, 1)
            """,
            (stream_id, case.client_id, activation_id, runtime_id, document_attachment_id),
        )
        await cur.execute(
            """
            insert into public.ingestion_receipts (
                id, client_id, activation_id, producer_kind, telemetry_credential_id,
                source_stream_id, batch_id, batch_sequence, first_export_sequence,
                last_export_sequence, byte_count, record_count, content_sha256,
                source_schema_version, canonical_schema_version, policy_version,
                outcome, stage
            )
            values (%s, %s, %s, 'browser', %s, %s, %s, 1, 1, 1, 1, 1,
                    'sha256:1111111111111111111111111111111111111111111111111111111111111111',
                    '1', '1', 'policy-1', 'accepted', 'available')
            """,
            (
                receipt_id,
                case.client_id,
                activation_id,
                credential_id,
                stream_id,
                uuid7(),
            ),
        )
    document = ObservedDocument(
        client_id=case.client_id,
        activation_id=activation_id,
        browser_stream_id=stream_id,
        ingestion_receipt_id=receipt_id,
        runtime_id=runtime_id,
        document_attachment_id=document_attachment_id,
    )
    await _observe(
        database,
        document=document,
        export_sequence=1,
        observation_type="baseline.snapshot",
        target=None,
        payload={
            "baseline_id": str(uuid7()),
            "reason": "activation",
            "route": {
                "route_template": "/dashboard/conversions",
                "unknown_route": False,
                "segment_count": 2,
                "has_query": False,
                "has_fragment": False,
                "params": [],
            },
            "landmarks": [],
            "targets": [_target_summary(target) for target in targets],
            "surfaces": [],
            "entities": [],
            "node_budget_exhausted": False,
        },
    )
    return document


async def record_registry_change(
    database: Database,
    *,
    document: ObservedDocument,
    export_sequence: int,
    change: str,
    target: RegisteredTarget,
) -> None:
    """Commit one `target.registry_change` the document's runtime exported."""
    await _observe(
        database,
        document=document,
        export_sequence=export_sequence,
        observation_type="target.registry_change",
        target=target,
        payload={
            "change": change,
            "registry_revision": export_sequence,
            "target": _target_summary(target),
        },
    )


async def record_observation(
    database: Database,
    *,
    document: ObservedDocument,
    export_sequence: int,
    observation_type: str,
    target: RegisteredTarget | None,
    payload: Mapping[str, object],
) -> None:
    """Commit one browser observation of any type the document's runtime exported."""
    await _observe(
        database,
        document=document,
        export_sequence=export_sequence,
        observation_type=observation_type,
        target=target,
        payload=payload,
    )


async def _observe(
    database: Database,
    *,
    document: ObservedDocument,
    export_sequence: int,
    observation_type: str,
    target: RegisteredTarget | None,
    payload: Mapping[str, object],
) -> None:
    """Insert one canonical browser observation on the document's stream."""
    now = datetime.now(tz=UTC)
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.browser_observations (
                id, client_id, activation_id, browser_stream_id, ingestion_receipt_id,
                observation_id, observation_type, runtime_id, document_attachment_id,
                frame_id, source_sequence, export_sequence, occurred_at,
                monotonic_offset, route_revision, visibility_state, target_id,
                target_revision, payload, policy_version, redaction_version,
                source_schema_version, canonical_schema_version, normalization_run_id,
                content_sha256, received_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'top', %s, %s, %s, 10.0, 1,
                    'visible', %s, %s, %s, 'policy-1', 'redaction-1', '1', '1', %s,
                    'sha256:2222222222222222222222222222222222222222222222222222222222222222',
                    %s)
            """,
            (
                uuid7(),
                document.client_id,
                document.activation_id,
                document.browser_stream_id,
                document.ingestion_receipt_id,
                uuid7(),
                observation_type,
                document.runtime_id,
                document.document_attachment_id,
                export_sequence,
                export_sequence,
                now,
                target.target_id if target is not None else None,
                target.target_revision if target is not None else None,
                Jsonb(dict(payload)),
                uuid7(),
                now,
            ),
        )


def _target_summary(target: RegisteredTarget) -> dict[str, object]:
    """The SDK's `target_summary` for one target, labelled with its description."""
    return {
        "target_id": target.target_id,
        "target_revision": target.target_revision,
        "layout_revision": 1,
        "visual_only": False,
        "role": target.role,
        "label": {
            "output_class": "bounded_safe_value",
            "value": target.description,
            "length_bucket": "len_11_30",
            "rule_id": "label.safe",
        },
        "semantic_state": {"disabled": False, "busy": False, "invalid": False},
        "geometry": {
            "x": 40,
            "y": 120,
            "width": 160,
            "height": 36,
            "clipped": False,
            "visibility": "visible" if target.visible else "offscreen",
            "occluded": False,
            "visible_fraction": 1.0 if target.visible else 0.0,
            "viewport_width": 1280,
            "viewport_height": 800,
        },
    }
