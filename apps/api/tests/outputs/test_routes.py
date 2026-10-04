"""The authenticated read models this workstream adds to the dashboard.

Requests go through the assembled application with real Supabase access tokens.
Authentication alone grants nothing: every route resolves an active membership
for the client in the path, and authorizing the issuance allowance is the owner's
alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest_asyncio
from fastapi import FastAPI
from utrace_contracts.domain.lifecycle import (
    CaseFileVariant,
    ClientMemberRole,
    CriterionObservability,
    MembershipStatus,
    ProductSignalState,
)

from app.db.pool import Database
from app.ids import uuid7
from app.main import create_app
from app.offers.budget import authorize_allowance, reserve_offer
from app.offers.entitlement import CaseStopReason, evaluate_entitlement
from app.outputs.handlers import assemble
from app.ports import PortRegistry
from app.settings import Settings
from app.validation.completion import CriterionConfirmation, complete_validation
from app.validation.controller import begin_validation, open_validation_attempt
from app.validation.evidence import DurableEvidenceQuery
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import (
    ValidationScenario,
    add_intake_source,
    add_transcript_fragment,
    binding_of,
    build_scenario,
)

BASE_URL = "http://control-plane.test"
CURRENCY = "USD"
REWARD_MINOR_UNITS = 25_000
ASSEMBLY_DEADLINE_SECONDS = 7 * 24 * 60 * 60

ONE_CRITERION: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view reopens with every filter", CriterionObservability.OBSERVED),
)


@pytest_asyncio.fixture
async def client(
    settings: Settings, database: Database
) -> AsyncIterator[httpx.AsyncClient]:
    """The assembled application with no provider adapters installed."""
    app: FastAPI = create_app(settings, PortRegistry(voice=None, receipts=None, observation=None, evidence=None))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            yield http


def _auth(user: SignedInUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.access_token}"}


async def _grant(
    database: Database,
    *,
    client_id: UUID,
    user: SignedInUser,
    role: ClientMemberRole,
) -> None:
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.client_members (id, client_id, auth_user_id, role, status)
            values (%s, %s, %s, %s, %s)
            """,
            (
                uuid7(),
                client_id,
                user.auth_user_id,
                role.value,
                MembershipStatus.ACTIVE.value,
            ),
        )


async def _published_case(
    database: Database, auth_users: dict[str, SignedInUser]
) -> tuple[ValidationScenario, UUID, UUID]:
    """A case validated as unmet, closed with a published blocker Case File."""
    scenario = await build_scenario(
        database,
        name="routes",
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    await add_intake_source(
        database,
        scenario,
        summary="A growth lead asks to save a conversion filter stack and reopen it.",
    )
    await add_transcript_fragment(
        database,
        scenario,
        field="expected_behavior",
        summary="the filter stack should survive a refresh",
    )
    attempt_id = await open_validation_attempt(database, binding=binding_of(scenario))
    await begin_validation(
        database, attempt_id=attempt_id, originating_user_id=scenario.client_user_id
    )
    window = await DurableEvidenceQuery(database).freeze_window(
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        interview_session_id=scenario.interview_session_id,
        purpose="validation",
        starts_at=datetime.now(tz=UTC) - timedelta(hours=2),
        ends_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    await complete_validation(
        database,
        DurableEvidenceQuery(database),
        attempt_id=attempt_id,
        evidence_window_id=window.evidence_window_id,
        confirmations=[
            CriterionConfirmation(
                criterion_id=scenario.criteria[0].id,
                user_confirmed=False,
                user_rejected=True,
                confirmation_evidence_record_id=None,
                workflow_observed=True,
            )
        ],
        assembly_deadline_seconds=ASSEMBLY_DEADLINE_SECONDS,
    )
    result = await assemble(
        database,
        DurableEvidenceQuery(database),
        product_signal_id=scenario.product_signal_id,
        variant=CaseFileVariant.BLOCKED,
        validation_attempt_id=attempt_id,
        blocker_reason="validation left acceptance criteria unmet",
        closure_trigger="validation_unsuccessful",
        closure_reason="validation_unsuccessful",
        narrative_source="evidence_derived",
        drafter=None,
        repository=None,
    )
    return scenario, attempt_id, result.case_file_id


async def test_only_the_owner_may_authorize_the_issuance_allowance(
    client: httpx.AsyncClient,
    database: Database,
    auth_users: dict[str, SignedInUser],
) -> None:
    """An admin may read the allowance but never raise the ceiling."""
    scenario = await build_scenario(
        database,
        name="budget-routes",
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    await _grant(
        database,
        client_id=scenario.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    await _grant(
        database,
        client_id=scenario.client_id,
        user=auth_users["owner_b"],
        role=ClientMemberRole.ADMIN,
    )
    path = f"/v1/clients/{scenario.client_id}/offers/budget"
    body = {"currency": CURRENCY, "authorized_minor_units": 50_000}

    refused = await client.post(path, json=body, headers=_auth(auth_users["owner_b"]))
    assert refused.status_code == 403
    assert refused.json()["error"] == "AuthorizationError"

    accepted = await client.post(path, json=body, headers=_auth(auth_users["owner_a"]))
    assert accepted.status_code == 200
    assert accepted.json()["remaining_minor_units"] == 50_000

    read = await client.get(path, headers=_auth(auth_users["owner_b"]))
    assert read.status_code == 200
    assert read.json()["authorized_minor_units"] == 50_000

    unauthenticated = await client.get(path)
    assert unauthenticated.status_code == 403


async def test_the_offer_disposition_hides_invoice_application_until_reconciled(
    client: httpx.AsyncClient,
    database: Database,
    auth_users: dict[str, SignedInUser],
) -> None:
    """An issued credit is not a consumed one."""
    scenario = await build_scenario(
        database,
        name="offer-routes",
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    await _grant(
        database,
        client_id=scenario.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    await authorize_allowance(
        database,
        client_id=scenario.client_id,
        currency=CURRENCY,
        authorized_minor_units=50_000,
        authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    offer_id = await reserve_offer(
        database,
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        customer_account_id=scenario.customer_account_id,
        currency=CURRENCY,
        amount_minor_units=REWARD_MINOR_UNITS,
        billing_account_ref="acct_utrace_sandbox",
        billing_customer_ref="cus_route_test",
        billing_mapping_revision=1,
        completion_terms={"amount_minor_units": REWARD_MINOR_UNITS},
    )
    await evaluate_entitlement(
        database,
        product_signal_id=scenario.product_signal_id,
        stop_reason=CaseStopReason.CLIENT_STOP,
    )

    listed = await client.get(
        f"/v1/clients/{scenario.client_id}/offers/dispositions",
        headers=_auth(auth_users["owner_a"]),
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["earned_minor_units"] == REWARD_MINOR_UNITS
    assert payload["remaining_minor_units"] == 25_000
    assert len(payload["offers"]) == 1
    assert payload["offers"][0]["issuance_state"] == "pending"
    # An absent optional is omitted rather than sent as null, so "not reconciled
    # from Stripe yet" is the field's absence.
    assert "invoice_application_minor_units" not in payload["offers"][0]

    one = await client.get(
        f"/v1/clients/{scenario.client_id}/offers/{offer_id}",
        headers=_auth(auth_users["owner_a"]),
    )
    assert one.status_code == 200
    assert one.json()["offer"]["state"] == "earned"

    missing = await client.get(
        f"/v1/clients/{scenario.client_id}/offers/{uuid7()}",
        headers=_auth(auth_users["owner_a"]),
    )
    assert missing.status_code == 404


async def test_the_validation_and_case_file_reads_expose_the_frozen_record(
    client: httpx.AsyncClient,
    database: Database,
    auth_users: dict[str, SignedInUser],
) -> None:
    """The dashboard reads the committed record, not a re-derived summary."""
    scenario, attempt_id, case_file_id = await _published_case(database, auth_users)
    await _grant(
        database,
        client_id=scenario.client_id,
        user=auth_users["owner_a"],
        role=ClientMemberRole.OWNER,
    )
    headers = _auth(auth_users["owner_a"])

    validation = await client.get(
        f"/v1/clients/{scenario.client_id}/cases/{scenario.product_signal_id}/validation",
        headers=headers,
    )
    assert validation.status_code == 200
    record = validation.json()
    assert record["validation_attempt_id"] == str(attempt_id)
    assert record["outcome"] == "not_validated"
    assert record["preview_version_id"] == str(scenario.preview_version_id)
    assert record["frozen_result"]["binding"]["dataset_manifest_digest"] == (
        scenario.dataset_manifest_digest
    )
    assert len(record["criterion_proofs"]) == 1
    assert record["criterion_proofs"][0]["result"] == "unmet"

    amendments = await client.get(
        f"/v1/clients/{scenario.client_id}/case-files/{case_file_id}/amendments",
        headers=headers,
    )
    assert amendments.status_code == 200
    assert amendments.json()["amendments"] == []

    delivery = await client.get(
        f"/v1/clients/{scenario.client_id}/case-files/{case_file_id}/delivery",
        headers=headers,
    )
    assert delivery.status_code == 404

    outsider = await client.get(
        f"/v1/clients/{scenario.client_id}/case-files/{case_file_id}/amendments",
        headers=_auth(auth_users["owner_b"]),
    )
    assert outsider.status_code == 403
