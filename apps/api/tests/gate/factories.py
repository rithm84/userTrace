"""Durable fixtures for the Implementation Gate tests.

Every helper inserts real rows through the same connector the control plane
uses, so a test exercises the production schema, its constraints, and its
immutability triggers. The pinned policy is a complete implementation policy:
the gate refuses an incomplete one, and a fixture that cheated around that would
prove nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from psycopg.types.json import Jsonb
from utrace_contracts.domain.lifecycle import FlowType, IntakeItemState, ProductSignalState

from app.db.pool import Database
from app.ids import uuid7
from tests.helpers import policy_digest

__all__ = [
    "SHOPALYTICS_BASE_REVISION",
    "SHOPALYTICS_CLONE_URL",
    "SHOPALYTICS_REPOSITORY",
    "SNAPSHOT",
    "GatedCase",
    "create_base_allocation",
    "create_code_finding",
    "create_evidence_record",
    "create_gated_case",
    "create_validation_criterion",
    "gate_policy",
    "implementation_policy",
    "prepare_gated_case",
    "shopalytics_checks",
]

SNAPSHOT = "utrace-implementation-node24-2026-09-20"

SHOPALYTICS_REPOSITORY = "ShopalyticsStore/shopalytics"
SHOPALYTICS_CLONE_URL = "https://github.com/ShopalyticsStore/shopalytics.git"
#: The fixture commit the preview template is built from, so the live checkout,
#: the candidate and the pinned template share one source.
SHOPALYTICS_BASE_REVISION: str = json.loads(
    (
        Path(__file__).resolve().parents[4] / "deploy/vercel/preview/shopalytics-recipe.json"
    ).read_text()
)["fixture_revision"]


@dataclass(frozen=True, slots=True)
class GatedCase:
    """One client with a Product Signal sitting at `gate_pending`."""

    client_id: UUID
    configuration_release_id: UUID
    client_user_id: UUID
    customer_account_id: UUID
    product_signal_id: UUID
    cancellation_scope_id: UUID


def implementation_policy(
    *,
    live_implementation_allowed: bool,
    permitted_paths: Sequence[str],
    blocked_paths: Sequence[str],
    required_checks: Sequence[Mapping[str, object]],
    dependency_policy: str,
    live_window_seconds: int,
    correction_cycles: int,
    changed_files: int,
    workspace_snapshot_id: str,
    elapsed_seconds: int,
    process_executions: int,
) -> dict[str, object]:
    """Build one complete implementation policy section for a configuration release."""
    return {
        "live_implementation_allowed": live_implementation_allowed,
        "repository_ref": SHOPALYTICS_REPOSITORY,
        "clone_url": SHOPALYTICS_CLONE_URL,
        "base_revision": SHOPALYTICS_BASE_REVISION,
        "workspace_snapshot_id": workspace_snapshot_id,
        "callback_hosts": ["callbacks.utrace.dev"],
        "build_recipe": {
            "node_version": "24",
            "package_manager": "npm",
            "install_command": "npm ci",
            "build_command": "npm run build",
            "start_command": "npm run start:preview",
            "application_port": 3300,
            "environment_profile": "utrace-preview",
        },
        "permitted_paths": list(permitted_paths),
        "blocked_paths": list(blocked_paths),
        "required_checks": [dict(check) for check in required_checks],
        "dependency_policy": dependency_policy,
        "permitted_dependencies": [],
        "dependency_manifest_paths": ["package.json", "package-lock.json"],
        "budgets": {
            "elapsed_seconds": elapsed_seconds,
            "agent_turns": 400,
            "agent_cost_cents": 5_000,
            "process_executions": process_executions,
            "artifact_bytes": 20_000_000,
            "correction_cycles": correction_cycles,
            "changed_files": changed_files,
        },
        "live_window_seconds": live_window_seconds,
        "implementation_limit": 3,
        "forbidden_path_patterns": [
            "**/auth/**",
            "**/*secret*",
            "**/*credential*",
            "**/billing/**",
            "**/.github/workflows/**",
            "**/utrace/signing.ts",
        ],
    }


async def create_gated_case(
    database: Database,
    *,
    name: str,
    authorizing_auth_user_id: UUID,
    policy: Mapping[str, object],
    state: ProductSignalState,
) -> GatedCase:
    """Insert a client, participant, account, Intake Item, and gated Product Signal."""
    client_id = uuid7()
    release_id = uuid7()
    utrace_user_id = uuid7()
    client_user_id = uuid7()
    account_id = uuid7()
    membership_id = uuid7()
    intake_id = uuid7()
    signal_id = uuid7()
    scope_id = uuid7()
    release_policy = {"implementation": dict(policy)}

    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "insert into public.clients (id, name, product_name) values (%s, %s, %s)",
            (client_id, name, "Shopalytics"),
        )
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest, authorized_by_auth_user_id
            )
            values (%s, %s, 1, %s, %s, %s)
            """,
            (
                release_id,
                client_id,
                Jsonb(release_policy),
                policy_digest(release_policy),
                authorizing_auth_user_id,
            ),
        )
        await cur.execute(
            "insert into public.users (id, auth_user_id, display_name) values (%s, null, %s)",
            (utrace_user_id, "gate integration participant"),
        )
        await cur.execute(
            "insert into public.client_users (id, client_id, utrace_user_id) values (%s, %s, %s)",
            (client_user_id, client_id, utrace_user_id),
        )
        await cur.execute(
            """
            insert into public.customer_accounts (id, client_id, external_account_key, name)
            values (%s, %s, %s, %s)
            """,
            (account_id, client_id, f"{name}-dudulemon", "Dudulemon"),
        )
        await cur.execute(
            """
            insert into public.customer_memberships (
                id, client_id, customer_account_id, client_user_id
            )
            values (%s, %s, %s, %s)
            """,
            (membership_id, client_id, account_id, client_user_id),
        )
        await cur.execute(
            """
            insert into public.intake_items (
                id, client_id, state, source_provider, source_channel_ref,
                source_batch_digest, author_provider_identity, client_user_id,
                customer_account_id, payload
            )
            values (%s, %s, %s, 'discord', 'channel', %s, 'discord-user', %s, %s, %s)
            """,
            (
                intake_id,
                client_id,
                IntakeItemState.ROUTED.value,
                f"digest-{intake_id}",
                client_user_id,
                account_id,
                Jsonb({"messages": ["I keep rebuilding the same conversion filters"]}),
            ),
        )
        await cur.execute(
            """
            insert into public.product_signals (
                id, client_id, state, intake_item_id, pinned_configuration_release_id,
                originating_client_user_id, originating_customer_account_id,
                initial_flow_type, established_flow_type, cancellation_scope_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                signal_id,
                client_id,
                state.value,
                intake_id,
                release_id,
                client_user_id,
                account_id,
                FlowType.FEATURE_REQUEST_FLOW.value,
                FlowType.FEATURE_REQUEST_FLOW.value,
                scope_id,
            ),
        )
        await cur.execute(
            "update public.intake_items set product_signal_id = %s where id = %s",
            (signal_id, intake_id),
        )
    return GatedCase(
        client_id=client_id,
        configuration_release_id=release_id,
        client_user_id=client_user_id,
        customer_account_id=account_id,
        product_signal_id=signal_id,
        cancellation_scope_id=scope_id,
    )


async def create_validation_criterion(
    database: Database, *, case: GatedCase, ordinal: int, statement: str
) -> UUID:
    """Freeze one acceptance criterion at the Product Signal's criteria revision."""
    criterion_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.validation_criteria (
                id, client_id, product_signal_id, criteria_revision, ordinal, statement,
                observability
            )
            values (%s, %s, %s, 0, %s, %s, 'observed')
            """,
            (criterion_id, case.client_id, case.product_signal_id, ordinal, statement),
        )
    return criterion_id


async def create_evidence_record(
    database: Database,
    *,
    case: GatedCase,
    evidence_class: str,
    claim_state: str,
    summary: str,
) -> UUID:
    """Append one evidence record the gate may read."""
    evidence_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.evidence_records (
                id, client_id, product_signal_id, evidence_class, claim_state,
                source_ref, context_revision, body
            )
            values (%s, %s, %s, %s, %s, %s, 0, %s)
            """,
            (
                evidence_id,
                case.client_id,
                case.product_signal_id,
                evidence_class,
                claim_state,
                f"interview:{evidence_id}",
                Jsonb({"summary": summary}),
            ),
        )
    return evidence_id


async def create_code_finding(
    database: Database,
    *,
    case: GatedCase,
    path: str,
    first_line: int,
    last_line: int,
    base_revision: str = SHOPALYTICS_BASE_REVISION,
    repository_ref: str = SHOPALYTICS_REPOSITORY,
) -> UUID:
    """Record one exact-revision finding as a read-only investigation would."""
    finding_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.code_investigation_findings (
                id, client_id, product_signal_id, investigation_id, repository_ref,
                base_revision, path, first_line, last_line, excerpt_digest, note
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                finding_id,
                case.client_id,
                case.product_signal_id,
                uuid7(),
                repository_ref,
                base_revision,
                path,
                first_line,
                last_line,
                f"sha256:{'0' * 64}",
                f"read {path}:{first_line}-{last_line}",
            ),
        )
    return finding_id


async def create_base_allocation(
    database: Database, *, case: GatedCase, state: str
) -> UUID:
    """Insert the base Preview Runtime Allocation the gate reads readiness from."""
    allocation_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.preview_runtime_allocations (
                id, client_id, product_signal_id, allocation_kind, pool_role, state,
                seed_manifest_digest, origin_host, fencing_token
            )
            values (%s, %s, %s, 'base', 'session_base', %s, %s, %s, utrace.next_fencing_token())
            """,
            (
                allocation_id,
                case.client_id,
                case.product_signal_id,
                state,
                "sha256:eee893377d423c2cade8778ff03e0060c75e59308f6350851bec21c08a87d51f",
                f"{allocation_id.hex[:16]}.preview.utrace.dev",
            ),
        )
    return allocation_id


def shopalytics_checks() -> tuple[dict[str, object], ...]:
    """The Shopalytics required checks, as the pinned policy declares them."""
    return (
        {
            "name": "lint",
            "command": "npm run lint",
            "working_directory": ".",
            "timeout_seconds": 900,
            "after": [],
        },
        {
            "name": "typecheck",
            "command": "npm run typecheck",
            "working_directory": ".",
            "timeout_seconds": 900,
            "after": [],
        },
        {
            "name": "test",
            "command": "npm test",
            "working_directory": ".",
            "timeout_seconds": 900,
            "after": [],
        },
    )


def far_future() -> datetime:
    """A deadline no test run reaches."""
    return datetime.now(tz=UTC) + timedelta(days=7)


def gate_policy(
    *, live_implementation_allowed: bool = True, live_window_seconds: int = 1800
) -> dict[str, object]:
    """The pinned implementation policy the gate tests decide against."""
    return implementation_policy(
        live_implementation_allowed=live_implementation_allowed,
        permitted_paths=["src/lib/dashboard", "src/components/dashboard", "tests/unit"],
        blocked_paths=["src/lib/utrace", "src/app/api", "db"],
        required_checks=shopalytics_checks(),
        dependency_policy="none",
        live_window_seconds=live_window_seconds,
        correction_cycles=2,
        changed_files=12,
        workspace_snapshot_id=SNAPSHOT,
        elapsed_seconds=3600,
        process_executions=200,
    )


async def prepare_gated_case(
    database: Database,
    owner_auth_user_id: UUID,
    name: str,
    *,
    policy: Mapping[str, object] | None = None,
    allocation_state: str = "ready",
) -> tuple[GatedCase, str, str]:
    """A gated case with criteria, confirmed evidence, a finding, and a base preview.

    Returns the case with the one evidence identifier and the one code-finding
    identifier a proposal is allowed to cite.
    """
    case = await create_gated_case(
        database,
        name=name,
        authorizing_auth_user_id=owner_auth_user_id,
        policy=policy if policy is not None else gate_policy(),
        state=ProductSignalState.GATE_PENDING,
    )
    await create_validation_criterion(
        database,
        case=case,
        ordinal=0,
        statement="the complete filter stack can be saved under a user-supplied name",
    )
    evidence_id = await create_evidence_record(
        database,
        case=case,
        evidence_class="transcript_fragment",
        claim_state="confirmed",
        summary="rebuilding the same conversion filters every Monday",
    )
    finding_id = await create_code_finding(
        database,
        case=case,
        path="src/components/dashboard/ConversionDashboard.tsx",
        first_line=60,
        last_line=82,
    )
    await create_base_allocation(database, case=case, state=allocation_state)
    return (case, str(evidence_id), str(finding_id))
