"""Durable fixtures for the telemetry edges.

Helpers insert real rows through the same connector the control plane uses and
drive the real HTTP and WebSocket surfaces, so a test exercises the production
schema, constraints, routes, and protocol rather than a convenient shortcut.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from uuid import UUID

import rfc8785
from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import (
    AgentCharacterState,
    ImplementationProgress,
    InterviewPhase,
    InterviewSessionState,
    ProductSignalState,
)

from app.auth.lane import Lane, mint_lane_credential
from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.preview_environments import resolve_preview_environment_id
from app.ingestion.server_activation import (
    ServerActivationRequest,
    create_server_activation,
)
from app.settings import Settings
from tests.factories import CaseFixture, create_case
from tests.helpers import policy_digest, record_session_readiness
from tests.ingestion.keys import SigningKeys

__all__ = [
    "PreviewFixture",
    "annotation_manifest",
    "create_preview",
    "front_door_credential",
    "server_instrumentation_manifest",
    "signed_server_manifest",
    "start_server_activation",
]

POLICY_VERSION = "1.4.0"
REDACTION_VERSION = "1.4.0"
SEMANTIC_CONVENTION_VERSION = "1.38.0"
UTRACE_SCHEMA_VERSION = "1.0.0"
MANIFEST_VERSION = 7


@dataclass(frozen=True, slots=True)
class PreviewFixture:
    """A routed case with a claimed base preview and an open Interview Session."""

    case: CaseFixture
    configuration_release_id: UUID
    preview_runtime_template_id: UUID
    preview_environment_id: UUID
    interview_session_id: UUID
    preview_runtime_allocation_id: UUID
    origin_host: str

    @property
    def document_origin(self) -> str:
        """The origin the browser document reports."""
        return f"https://{self.origin_host}"


def annotation_manifest() -> dict[str, object]:
    """Return the signed annotation manifest onboarding would pin to a release."""
    return {
        "manifest_version": "2026.09.20-1",
        "client_release_id": "shopalytics-2026-09-20",
        "surfaces": [{"surface_id": "conversion_dashboard", "sensitive": False}],
        "entities": [
            {
                "entity_definition_id": "saved_view",
                "instance_ref_rule_id": "saved_view_ref",
            }
        ],
        "targets": [
            {
                "definition_id": "save_view_button",
                "effects": ["observe", "visual_guidance"],
                "detail_fields": ["label"],
                "route_templates": ["/dashboard/conversion"],
            }
        ],
        "visual_targets": [],
        "milestones": [{"milestone_id": "view_saved", "attribute_rule_ids": []}],
        "severities": ["low", "medium", "high"],
        "priorities": ["p0", "p1", "p2"],
        "route_templates": ["/dashboard/conversion"],
        "approved_trace_origins": ["https://shopalytics.example"],
        "redaction": {
            "redaction_version": REDACTION_VERSION,
            "policy_version": POLICY_VERSION,
            "safe_value_rules": [
                {
                    "rule_id": "saved_view_ref",
                    "allowed_kinds": ["annotation_attribute"],
                    "output_class": "bounded_safe_value",
                    "max_length": 60,
                    "allowed_values": None,
                    "allowed_pattern": None,
                }
            ],
            "sensitive_surfaces": ["billing"],
            "sensitive_fields": ["card_number"],
        },
    }


async def create_preview(
    database: Database, *, name: str, authorizing_auth_user_id: UUID
) -> PreviewFixture:
    """Insert a client, case, Interview Session, and claimed base preview."""
    case = await create_case(
        database,
        name=name,
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=authorizing_auth_user_id,
        participant_auth_user_id=None,
    )
    session_id = uuid7()
    allocation_id = uuid7()
    release_id = uuid7()
    template_id = uuid7()
    origin_host = f"{uuid7().hex[:20]}.preview.utrace.dev"
    policy = {
        "live_implementation_allowed": True,
        "annotation_manifest": annotation_manifest(),
    }
    async with database.transaction() as conn, conn.cursor() as cur:
        # Configuration releases are append-only, so the manifest arrives on a new
        # release that the Product Signal then pins for its whole lifecycle.
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, 2, %s, %s, %s)
            """,
            (
                release_id,
                case.client_id,
                Jsonb(policy),
                policy_digest(policy),
                authorizing_auth_user_id,
            ),
        )
        await cur.execute(
            "update public.product_signals set pinned_configuration_release_id = %s "
            "where id = %s",
            (release_id, case.product_signal_id),
        )
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
                InterviewPhase.DISCOVERY.value,
                AgentCharacterState.LISTENING.value,
                ImplementationProgress.NOT_STARTED.value,
            ),
        )
        # The activation binds a preview environment, which the runtime template
        # and its pinned release define, so a claimed allocation has to carry both.
        await cur.execute(
            """
            insert into public.preview_runtime_templates (
                id, client_id, pinned_configuration_release_id, template_id,
                template_revision, snapshot_id, image_digest, recipe_digest,
                seed_manifest_digest, seed_manifest, fixture_clock, application_port,
                authenticated_identity, sdk_versions, warm_pool_target
            )
            values (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, now(), 3000, %s, %s, 2)
            """,
            (
                template_id,
                case.client_id,
                release_id,
                f"shopalytics-{name}",
                f"snapshot-{name}",
                f"sha256:image-{name}",
                f"sha256:recipe-{name}",
                f"sha256:seed-{name}",
                Jsonb({"seed": {"manifest": name}}),
                "maya@dudulemon.test",
                Jsonb({"browser": "1.0.0", "server": "0.1.0"}),
            ),
        )
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                interview_session_id, origin_host, fencing_token,
                preview_runtime_template_id, pinned_configuration_release_id
            )
            values (
                %s, %s, %s, 'base', 'session_base', 'ready', %s, %s,
                utrace.next_fencing_token(), %s, %s
            )
            """,
            (
                allocation_id,
                case.client_id,
                case.product_signal_id,
                session_id,
                origin_host,
                template_id,
                release_id,
            ),
        )
        await cur.execute(
            "update public.interview_sessions set preview_runtime_allocation_id = %s "
            "where id = %s",
            (allocation_id, session_id),
        )
        # Resolving it here both creates the durable record an activation binds
        # and gives a test the identity its wire scope has to claim.
        environment_id = await resolve_preview_environment_id(
            conn, preview_runtime_allocation_id=allocation_id
        )
    await record_session_readiness(
        database,
        interview_session_id=session_id,
        conversational=True,
        observation=False,
    )
    return PreviewFixture(
        case=case,
        configuration_release_id=release_id,
        preview_runtime_template_id=template_id,
        preview_environment_id=environment_id,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        origin_host=origin_host,
    )


def front_door_credential(settings: Settings, preview: PreviewFixture) -> str:
    """Mint the session credential the preview front door forwards."""
    return mint_lane_credential(
        signing_key=settings.lane_signing_key,
        lane=Lane.FRONT_DOOR,
        subject_id=preview.preview_runtime_allocation_id,
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
        binding=preview.origin_host,
        lifetime_seconds=900,
    ).token


def server_instrumentation_manifest() -> dict[str, object]:
    """Return the manifest a client release publishes for its server services."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "client_release_id": "shopalytics-2026-09-20",
        "policy_version": POLICY_VERSION,
        "utrace_schema_version": UTRACE_SCHEMA_VERSION,
        "semantic_convention_version": SEMANTIC_CONVENTION_VERSION,
        "services": [
            {
                "service_key": "shopalytics.api",
                "service_name": "shopalytics-api",
                "service_namespace": "shopalytics",
                "service_version": "2026.09.20",
                "required": True,
                "required_adapters": ["node_http", "pg"],
                "registered_routes": [
                    {
                        "route_template": "/api/saved-views",
                        "methods": ["POST"],
                        "operation_name": "saved_views.create",
                        "required": True,
                    }
                ],
                "registered_operations": ["saved_views.create"],
                "operational_noise": [],
            }
        ],
    }


def signed_server_manifest(keys: SigningKeys) -> dict[str, object]:
    """Sign the manifest over its RFC 8785 canonical form, as the SDK verifies it."""
    manifest = server_instrumentation_manifest()
    signature = keys.manifest_private.sign(rfc8785.dumps(manifest))
    return {
        "manifest": manifest,
        "signature": {
            "algorithm": "ed25519",
            "key_id": keys.manifest_key_id,
            "value": b64encode(signature).decode("ascii"),
        },
    }


async def start_server_activation(
    database: Database, *, preview: PreviewFixture, keys: SigningKeys
) -> tuple[UUID, UUID, str, str]:
    """Activate one server service and return its identities, token, and entry token."""
    server_stream_id = uuid7()
    activation, credential, preview_entry_token = await create_server_activation(
        database,
        request=ServerActivationRequest(
            preview_runtime_allocation_id=preview.preview_runtime_allocation_id,
            public_preview_host=preview.origin_host,
            server_stream_id=server_stream_id,
            service_instance_id=uuid7(),
            service_key="shopalytics.api",
            signed_manifest=signed_server_manifest(keys),
            required_hops=("server", "database"),
        ),
        trust_anchor=keys.manifest_private.public_key().public_bytes_raw(),
        trust_anchor_key_id=keys.manifest_key_id,
    )
    return (
        activation.activation_id,
        activation.server_stream_id,
        credential.token,
        preview_entry_token,
    )


