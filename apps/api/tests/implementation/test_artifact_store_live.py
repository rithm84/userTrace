"""Content-addressed artifact storage over the real Supabase project.

The store writes bodies to a private, tenant-scoped bucket and their metadata to
Postgres. These tests use the real Storage API and the real database: the
idempotency guarantee is a unique constraint plus an object path derived from the
body's own digest, and only real writes prove it.
"""

from __future__ import annotations

import pytest
from utrace_contracts.domain.lifecycle import ProductSignalState
from utrace_contracts.envelope.artifacts import ArtifactReference

from app.db.pool import Database
from app.db.rows import as_int, as_str
from app.implementation.artifact_store import ArtifactStore, digest_of
from tests.gate.factories import (
    GatedCase,
    create_gated_case,
    implementation_policy,
    shopalytics_checks,
)
from tests.supabase_auth import SignedInUser


async def _client(database: Database, owner: SignedInUser, name: str) -> GatedCase:
    return await create_gated_case(
        database,
        name=name,
        authorizing_auth_user_id=owner.auth_user_id,
        policy=implementation_policy(
            live_implementation_allowed=True,
            permitted_paths=["src"],
            blocked_paths=["db"],
            required_checks=shopalytics_checks(),
            dependency_policy="none",
            live_window_seconds=1800,
            correction_cycles=2,
            changed_files=10,
            workspace_snapshot_id="unused",
            elapsed_seconds=3600,
            process_executions=100,
        ),
        state=ProductSignalState.GATE_PENDING,
    )


@pytest.mark.parametrize("body", [b"final diff\n", b'{"check": "lint", "exit_code": 0}'])
async def test_one_body_is_stored_and_addressed_by_its_own_digest(
    database: Database,
    artifact_store: ArtifactStore,
    auth_users: dict[str, SignedInUser],
    body: bytes,
) -> None:
    """The reference carries identity, type, digest, size, scope, and retention."""
    case = await _client(database, auth_users["owner_a"], f"artifact-{digest_of(body)[:8]}")
    reference = await artifact_store.put(
        client_id=case.client_id,
        artifact_type="implementation_final_diff",
        media_type="text/x-patch; charset=utf-8",
        retention_class="client_evidence",
        producer="implementation_controller",
        body=body,
        source_revision="46ce0544d2e46b48fe5dcd9807185b87de43d6cb",
        implementation_attempt_id=None,
        environment_lease_id=None,
        product_signal_id=case.product_signal_id,
    )
    assert reference.digest.algorithm == "sha256"
    assert reference.digest.value == digest_of(body)
    assert reference.byte_size == len(body)
    assert reference.retention_class == "client_evidence"
    assert reference.client_scope.scope == "client"
    # The reference carries no bucket, path, URL, or credential.
    assert "storage" not in reference.model_dump(mode="json")

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select storage_bucket, storage_path, byte_size, producer
            from public.artifacts where id = %s
            """,
            (reference.artifact_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert as_str(row, "storage_bucket") == "utrace-artifacts"
    assert as_str(row, "storage_path").endswith(digest_of(body))
    assert as_int(row, "byte_size") == len(body)
    assert as_str(row, "producer") == "implementation_controller"


async def test_the_same_bytes_are_stored_once(
    database: Database, artifact_store: ArtifactStore, auth_users: dict[str, SignedInUser]
) -> None:
    """A retried extraction adopts the existing artifact instead of duplicating it."""
    case = await _client(database, auth_users["owner_a"], "artifact-idempotent")
    body = b"the same evidence, extracted twice\n"

    async def store() -> ArtifactReference:
        return await artifact_store.put(
            client_id=case.client_id,
            artifact_type="implementation_tool_journal",
            media_type="application/json",
            retention_class="client_evidence",
            producer="implementation_controller",
            body=body,
            source_revision=None,
            implementation_attempt_id=None,
            environment_lease_id=None,
            product_signal_id=case.product_signal_id,
        )

    first = await store()
    second = await store()
    assert first.artifact_id == second.artifact_id

    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select count(*) as total from public.artifacts where client_id = %s and digest = %s",
            (case.client_id, digest_of(body)),
        )
        row = await cur.fetchone()
    assert row is not None
    assert as_int(row, "total") == 1
