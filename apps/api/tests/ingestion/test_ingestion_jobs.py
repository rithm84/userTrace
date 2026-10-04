"""The `ingestion.*` Jobs: registered, claimable, and idempotent."""

from __future__ import annotations

import secrets
from uuid import UUID

from app.db.pool import Database
from app.execution.handlers import build_handlers
from app.execution.jobs import claim_job, complete_job_run
from app.ids import uuid7
from app.ingestion.jobs import CORRELATE_TRACE, EVALUATE_READINESS
from app.ingestion.readiness import current_readiness
from app.settings import Settings
from tests.ingestion.activation_client import activate_document
from tests.ingestion.factories import create_preview, front_door_credential
from tests.ingestion.keys import SigningKeys
from tests.supabase_auth import SignedInUser


def test_the_ingestion_job_types_are_registered() -> None:
    """``build_handlers()`` exposes both ingestion job types to the worker."""
    handlers = build_handlers()
    assert CORRELATE_TRACE in handlers
    assert EVALUATE_READINESS in handlers


async def test_the_readiness_job_runs_through_the_worker_contract(
    telemetry_database: Database,
    settings: Settings,
    keys: SigningKeys,
    ingest_server: str,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The Job an activation scheduled is claimed, executed, and completed.

    Nothing inserts the Job by hand: activating a document is what schedules it,
    so this proves the whole path from the ingress commit to a durable readiness
    projection through the same claim, handler, and completion a worker uses.
    """
    preview = await create_preview(
        telemetry_database,
        name=f"job-{uuid7().hex[:8]}",
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    document = await activate_document(
        ingest_server,
        service_token=settings.front_door_service_token,
        session_credential=front_door_credential(settings, preview),
        public_preview_host=preview.origin_host,
        document_origin=preview.document_origin,
        document_nonce=secrets.token_hex(24),
        sdk_installation_id="shopalytics-browser-sdk",
        public_key=keys.attestation_public,
    )
    activation_id = UUID(str(document.attestation["attestation_id"]))

    claimed = await claim_job(
        telemetry_database,
        job_types=[EVALUATE_READINESS],
        worker_id="ingestion-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    assert claimed.job_type == EVALUATE_READINESS
    assert UUID(str(claimed.input["activation_id"])) == activation_id

    handler = build_handlers()[EVALUATE_READINESS]
    await handler(telemetry_database, claimed)
    await complete_job_run(
        telemetry_database,
        job_id=claimed.job_id,
        job_run_id=claimed.job_run_id,
        fencing_token=claimed.fencing_token,
    )

    projection = await current_readiness(
        telemetry_database, activation_id=activation_id, kind="browser"
    )
    assert projection is not None
    assert projection.revision == 1

    async with telemetry_database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select state from public.jobs where id = %s", (claimed.job_id,)
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["state"] == "succeeded"
