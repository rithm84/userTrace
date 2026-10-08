"""Test configuration.

Every database test runs against the real Supabase Postgres named by
``UTRACE_DATABASE_URL``. There are no mocks: the kernel's guarantees are
transactional, so only a real transaction proves them. When a required variable
is missing the tests skip with that variable named.

Credentials come from the repository ``.env.local``. It is never committed and
its values are never logged.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from app.db.pool import Database
from app.ids import uuid7
from app.settings import Settings, get_settings
from tests.supabase_auth import (
    SignedInUser,
    create_user,
    delete_user,
    sign_in,
)

ENV_FILE = Path(__file__).resolve().parents[3] / ".env.local"

# Every public table this workstream owns. Tests truncate exactly these.
# The telemetry tables are listed first because they reference the lifecycle
# tables below; one TRUNCATE naming every referencing table needs no CASCADE.
OWNED_TABLES: tuple[str, ...] = (
    "session_projections",
    "session_readiness",
    "participant_preflight_records",
    "voice_delegations",
    "voice_playback_segments",
    "voice_transcript_fragments",
    "visual_command_dispatches",
    "extension_control_frames",
    "extension_control_streams",
    "extension_binding_offers",
    "extension_bindings",
    "extension_possession_proofs",
    "extension_link_codes",
    "extension_preferences",
    "extension_installations",
    "acceptance_criteria_agreements",
    "flow_classification_records",
    "dependency_holds",
    "semantic_judgements",
    "context_bundle_invalidations",
    "context_bundle_items",
    "context_bundles",
    "server_control_commands",
    "server_health_reports",
    "telemetry_evidence_items",
    "telemetry_evidence_windows",
    "telemetry_readiness_projections",
    "correlation_projections",
    "telemetry_identity_conflicts",
    "browser_stream_gaps",
    "browser_observations",
    "browser_streams",
    "server_spans",
    "server_streams",
    "ingestion_failures",
    "ingestion_receipt_stages",
    "ingestion_receipts",
    "telemetry_artifacts",
    "telemetry_credential_revocations",
    "telemetry_credentials",
    "telemetry_activations",
    "preview_environments",
    "audit_records",
    "artifacts",
    "implementation_check_runs",
    "implementation_scope_checks",
    "code_investigation_findings",
    "inbox",
    "outbox",
    "results",
    "commands",
    "job_runs",
    "jobs",
    "events",
    "offer_ledger_entries",
    "offer_invoice_applications",
    "offer_issuance_operations",
    "offer_entitlements",
    "offer_reservations",
    "outreach_offers",
    "offer_budgets",
    "deliveries",
    "case_file_amendments",
    "case_files",
    "validation_outcomes",
    "validation_attempts",
    "validation_criteria",
    "preview_readiness_results",
    "preview_handoff_codes",
    "preview_routes",
    "preview_versions",
    "preview_runtime_allocations",
    "preview_runtime_templates",
    "environment_leases",
    "prepared_workspaces",
    "implementation_attempts",
    "gate_decisions",
    "evidence_windows",
    "evidence_records",
    "voice_connections",
    "interview_sessions",
    "invitations",
    "outreach_attempts",
    "outreach_deliveries",
    "inbound_direct_messages",
    "routing_decisions",
    "source_reports",
    "product_signal_concerns",
    "product_signals",
    "intake_messages",
    "intake_batches",
    "intake_author_attributions",
    "intake_items",
    "product_overview_versions",
    "configuration_releases",
    "provider_refs",
    "customer_memberships",
    "customer_accounts",
    "client_users",
    "users",
    "client_members",
    "clients",
)

_TEST_DEFAULTS = {
    "UTRACE_LANE_SIGNING_KEY": "control-plane-integration-test-signing-key",
    "UTRACE_FRONT_DOOR_SERVICE_TOKEN": "control-plane-integration-test-front-door",
    "UTRACE_PREVIEW_ORIGIN_SUFFIX": "preview.utrace.dev",
}


def _load_env_file() -> None:
    """Load the repository .env.local into this process without printing values."""
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip('"').strip("'")


_load_env_file()
for _name, _value in _TEST_DEFAULTS.items():
    os.environ.setdefault(_name, _value)


def require_env(name: str) -> str:
    """Return an environment variable or skip the test naming the variable."""
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set in the environment or {ENV_FILE.name}")
    return value


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Settings for the integration run."""
    require_env("UTRACE_DATABASE_URL")
    require_env("SUPABASE_URL")
    return get_settings()


@pytest_asyncio.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    """An open pool against the real project database, with owned tables clean.

    Only tests that request this fixture touch the database, and it truncates
    exactly the public tables this workstream owns, so reruns are safe and the
    schemas other workstreams use in the same project are never touched.
    """
    connector = Database(dsn=settings.database_url, min_size=1, max_size=4)
    await connector.open()
    try:
        await _truncate(connector)
        yield connector
        await _truncate(connector)
    finally:
        await connector.close()


async def _truncate(database: Database) -> None:
    statement = "truncate table " + ", ".join(
        f"public.{name}" for name in OWNED_TABLES
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(statement)
        await cur.execute("select pgmq.purge_queue('utrace_outbox')")


@pytest.fixture(scope="session")
def auth_users(settings: Settings) -> Iterator[dict[str, SignedInUser]]:
    """Three real Supabase Auth users, created once for the whole run.

    Creating them once keeps the Auth admin API calls bounded while still proving
    JWT verification, membership resolution, and RLS against genuine tokens.
    """
    require_env("UTRACE_SUPABASE_SECRET_KEY")
    require_env("UTRACE_SUPABASE_PUBLISHABLE_KEY")
    created: dict[str, SignedInUser] = {}
    run = uuid7().hex[:12]
    try:
        for role in ("owner_a", "owner_b", "participant"):
            email = f"utrace-cp-{role.replace('_', '-')}-{run}@example.com"
            password = f"Ut-{uuid7().hex}"
            auth_user_id = create_user(
                supabase_url=settings.supabase_url,
                secret_key=settings.supabase_secret_key,
                email=email,
                password=password,
            )
            token, provider, provider_id = sign_in(
                supabase_url=settings.supabase_url,
                publishable_key=settings.supabase_publishable_key,
                email=email,
                password=password,
            )
            created[role] = SignedInUser(
                auth_user_id=auth_user_id,
                email=email,
                password=password,
                access_token=token,
                identity_provider=provider,
                identity_provider_id=provider_id,
            )
        yield created
    finally:
        for user in created.values():
            delete_user(
                supabase_url=settings.supabase_url,
                secret_key=settings.supabase_secret_key,
                auth_user_id=user.auth_user_id,
            )
