"""Allowance, reservations, and entitlement against the real database."""

from __future__ import annotations

from uuid import UUID

import pytest
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    OfferState,
    ProductSignalState,
)

from app.db.pool import Database
from app.errors import BudgetExhaustedError, GuardRejectedError
from app.offers.budget import authorize_allowance, read_allowance, reserve_offer
from app.offers.entitlement import (
    CaseStopReason,
    EntitlementDecision,
    evaluate_entitlement,
)
from app.offers.read_model import load_offers_view
from tests.helpers import count_rows, read_state
from tests.supabase_auth import SignedInUser
from tests.validation.scenario import ValidationScenario, build_scenario

REWARD_MINOR_UNITS = 25_000
CURRENCY = "USD"

ONE_CRITERION: tuple[tuple[str, CriterionObservability], ...] = (
    ("the saved view reopens with every filter", CriterionObservability.OBSERVED),
)


async def _scenario(
    database: Database, auth_users: dict[str, SignedInUser], *, name: str
) -> ValidationScenario:
    return await build_scenario(
        database,
        name=name,
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=auth_users["participant"].auth_user_id,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )


async def _reserve(
    database: Database, scenario: ValidationScenario, *, amount: int
) -> UUID:
    return await reserve_offer(
        database,
        client_id=scenario.client_id,
        product_signal_id=scenario.product_signal_id,
        customer_account_id=scenario.customer_account_id,
        currency=CURRENCY,
        amount_minor_units=amount,
        billing_account_ref="acct_shopalytics_sandbox",
        billing_customer_ref="cus_dudulemon",
        billing_mapping_revision=1,
        completion_terms={"amount_minor_units": amount, "currency": CURRENCY},
    )


async def _stop_participation(
    database: Database, scenario: ValidationScenario
) -> None:
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.session_readiness as readiness
            set conversational_ready = false, observation_ready = false,
                participant_authenticated = false,
                readiness_revision = readiness_revision + 1
            from public.interview_sessions as sessions
            where sessions.id = readiness.interview_session_id
              and sessions.product_signal_id = %s
            """,
            (scenario.product_signal_id,),
        )


async def test_the_owner_authorizes_an_allowance_reservations_draw_down(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    scenario = await _scenario(database, auth_users, name="allowance")
    allowance = await authorize_allowance(
        database,
        client_id=scenario.client_id,
        currency=CURRENCY,
        authorized_minor_units=40_000,
        authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    assert allowance.remaining_minor_units == 40_000

    await _reserve(database, scenario, amount=REWARD_MINOR_UNITS)
    after = await read_allowance(
        database, client_id=scenario.client_id, currency=CURRENCY
    )
    assert after.reserved_minor_units == REWARD_MINOR_UNITS
    assert after.remaining_minor_units == 15_000


async def test_a_reservation_cannot_exceed_the_remaining_allowance(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    scenario = await _scenario(database, auth_users, name="allowance-exhausted")
    await authorize_allowance(
        database,
        client_id=scenario.client_id,
        currency=CURRENCY,
        authorized_minor_units=REWARD_MINOR_UNITS,
        authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    await _reserve(database, scenario, amount=REWARD_MINOR_UNITS)

    second = await build_scenario(
        database,
        name="allowance-exhausted-second",
        contact_budget=2,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
        criteria=ONE_CRITERION,
        signal_state=ProductSignalState.AWAITING_VALIDATION,
        candidate_ready=True,
    )
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.product_signals set client_id = client_id where id = %s",
            (second.product_signal_id,),
        )
    with pytest.raises(BudgetExhaustedError):
        await reserve_offer(
            database,
            client_id=scenario.client_id,
            product_signal_id=second.product_signal_id,
            customer_account_id=scenario.customer_account_id,
            currency=CURRENCY,
            amount_minor_units=1,
            billing_account_ref="acct_shopalytics_sandbox",
            billing_customer_ref="cus_dudulemon",
            billing_mapping_revision=1,
            completion_terms={},
        )


async def test_a_ceiling_below_consumed_allowance_is_refused(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """A budget edit cannot cancel outstanding promises."""
    scenario = await _scenario(database, auth_users, name="allowance-lowered")
    await authorize_allowance(
        database,
        client_id=scenario.client_id,
        currency=CURRENCY,
        authorized_minor_units=40_000,
        authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    await _reserve(database, scenario, amount=REWARD_MINOR_UNITS)
    with pytest.raises(GuardRejectedError) as refusal:
        await authorize_allowance(
            database,
            client_id=scenario.client_id,
            currency=CURRENCY,
            authorized_minor_units=10_000,
            authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
        )
    assert "already reserved or earned" in refusal.value.reason


async def test_a_system_stop_after_participation_earns_exactly_one_entitlement(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    """Re-running the evaluation never writes a second entitlement for an offer."""
    scenario = await _scenario(database, auth_users, name="earned")
    await authorize_allowance(
        database,
        client_id=scenario.client_id,
        currency=CURRENCY,
        authorized_minor_units=40_000,
        authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    offer_id = await _reserve(database, scenario, amount=REWARD_MINOR_UNITS)

    first = await evaluate_entitlement(
        database,
        product_signal_id=scenario.product_signal_id,
        stop_reason=CaseStopReason.CLIENT_STOP,
    )
    assert first is not None
    assert first.decision is EntitlementDecision.EARNED
    assert first.issuance_command_id is not None

    again = await evaluate_entitlement(
        database,
        product_signal_id=scenario.product_signal_id,
        stop_reason=CaseStopReason.CLIENT_STOP,
    )
    assert again is not None
    assert again.offer_entitlement_id == first.offer_entitlement_id

    assert (
        await count_rows(
            database,
            table="offer_entitlements",
            where="outreach_offer_id = %s",
            params=(offer_id,),
        )
        == 1
    )
    state, _ = await read_state(
        database, table="outreach_offers", row_id=offer_id, column="state"
    )
    assert state == OfferState.EARNED.value

    view = await load_offers_view(
        database, client_id=scenario.client_id, currency=CURRENCY
    )
    assert view.earned_minor_units == REWARD_MINOR_UNITS
    assert view.reserved_minor_units == 0
    assert view.remaining_minor_units == 15_000
    assert len(view.offers) == 1
    assert view.offers[0].invoice_application_minor_units is None


async def test_a_stop_before_participation_releases_the_reservation(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    scenario = await _scenario(database, auth_users, name="released")
    await authorize_allowance(
        database,
        client_id=scenario.client_id,
        currency=CURRENCY,
        authorized_minor_units=40_000,
        authorized_by_auth_user_id=auth_users["owner_a"].auth_user_id,
    )
    offer_id = await _reserve(database, scenario, amount=REWARD_MINOR_UNITS)
    await _stop_participation(database, scenario)

    outcome = await evaluate_entitlement(
        database,
        product_signal_id=scenario.product_signal_id,
        stop_reason=CaseStopReason.CLIENT_STOP,
    )
    assert outcome is not None
    assert outcome.decision is EntitlementDecision.NOT_EARNED
    assert outcome.offer_entitlement_id is None

    state, _ = await read_state(
        database, table="outreach_offers", row_id=offer_id, column="state"
    )
    assert state == OfferState.RELEASED.value

    view = await load_offers_view(
        database, client_id=scenario.client_id, currency=CURRENCY
    )
    assert view.reserved_minor_units == 0
    assert view.earned_minor_units == 0
    assert view.remaining_minor_units == 40_000
