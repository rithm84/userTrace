"""Which controls a new version changed, which checks it showed, and which of the
participant's moments the agent acknowledges.

The controls test reads the targets ingestion recorded for each bound document. The
other judgements run against the real Jev model: the checks test over the activity
lines a validation pass produces, the acknowledgement test over a recorded
conversation.
"""

import json
from pathlib import Path
from uuid import UUID

from utrace_contracts.domain.lifecycle import InterviewPhase, ProductSignalState

from app.connectors.typesafe_jev import JevClient
from app.db.pool import Database
from app.ids import uuid7
from app.ingestion.target_registry import IngestionTargetRegistry
from app.interview.evidence import TranscriptFragment, record_transcript_fragment
from app.interview.ports import RegisteredTarget
from app.interview.voice.channel import VoiceSubject
from app.interview.voice.cues import _new_controls
from app.interview.voice.judge import VoiceJudge
from tests.connectors.conftest import optional_env, require_env
from tests.factories import CaseFixture, create_case
from tests.helpers import record_session_readiness
from tests.interview.fixtures import (
    create_binding,
    create_live_session,
    create_preview_allocation,
    link_installation,
    observe_document,
    observe_next_document,
)
from tests.supabase_auth import SignedInUser

_DEVICE_KEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEtoolstestkeyfortheinstallationrecord"

_FILTERS = (
    ("filter-date-range", 'conversion_filter_date_range showing "Last 30 days"'),
    ("filter-channel", 'conversion_filter_channel showing "TikTok"'),
    ("filter-segment", 'conversion_filter_segment showing "Women 25-34"'),
    ("filter-sentiment", 'conversion_filter_sentiment showing "Negative"'),
    ("filter-reset", 'conversion_filter_reset showing "Reset"'),
)

#: The demo's real validation pass up to the reload, then the reopen after it.
_PASS = json.loads((Path(__file__).parent / "saved_view_validation.json").read_text())
_SAVED_THEN_RELOADED: tuple[str, ...] = tuple(_PASS["before_reopen"])
_REOPENED: tuple[str, ...] = (*_SAVED_THEN_RELOADED, *_PASS["reopened"])
_CHECKS = {
    "save": "The person can save the complete current filter set under a name.",
    "reopen": (
        "After refreshing, the person can reopen the named saved view and restore all "
        "saved filter selections."
    ),
}


async def _case(database: Database, auth_users: dict[str, SignedInUser]) -> CaseFixture:
    return await create_case(
        database,
        name="live-attention",
        state=ProductSignalState.INTERVIEWING,
        authorizing_auth_user_id=auth_users["owner_a"].auth_user_id,
        participant_auth_user_id=None,
    )


async def _bound_session(database: Database, *, case: CaseFixture) -> UUID:
    """A live session whose preview document registers the conversion filters."""
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=_DEVICE_KEY,
        protocol_version="1.0",
    )
    _, attachment_id = await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )
    await record_session_readiness(
        database, interview_session_id=session_id, conversational=True, observation=True
    )
    await observe_document(
        database,
        case=case,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=attachment_id,
        targets=tuple(
            RegisteredTarget(
                target_id=target_id,
                target_revision=1,
                description=description,
                placement="top center of the screen",
                role="button",
                visible=True,
            )
            for target_id, description in _FILTERS
        ),
    )
    return session_id


def _judge(database: Database, jev: JevClient) -> VoiceJudge:
    return VoiceJudge(database=database, jev=jev, targets=IngestionTargetRegistry(database))


def _jev() -> JevClient:
    values = require_env("VERCEL_AI_GATEWAY_API_KEY")
    return JevClient(
        api_key=values["VERCEL_AI_GATEWAY_API_KEY"],
        model_id=optional_env("UTRACE_JEV_MODEL_ID") or "typesafe-ai/jev",
        timeout_seconds=30.0,
    )


def _controls(names: tuple[str, ...]) -> tuple[RegisteredTarget, ...]:
    """One page's controls, in reading order."""
    return tuple(
        RegisteredTarget(
            target_id=name,
            target_revision=1,
            description=f'{name} showing "All"',
            placement="top left of the screen",
            role="button",
            visible=True,
        )
        for name in names
    )


async def test_a_new_version_names_a_control_moved_only_when_its_order_changed(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users)
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.VALIDATION)
    allocation_id = await create_preview_allocation(
        database, case=case, interview_session_id=session_id, state="ready"
    )
    installation_id = await link_installation(
        database,
        utrace_user_id=case.utrace_user_id,
        device_public_key_spki_b64u=_DEVICE_KEY,
        protocol_version="1.0",
    )
    _, attachment_id = await create_binding(
        database,
        case=case,
        interview_session_id=session_id,
        extension_installation_id=installation_id,
        preview_runtime_allocation_id=allocation_id,
    )
    filters = ("filter_channel", "filter_segment", "filter_review_topic")
    page = await observe_document(
        database,
        case=case,
        interview_session_id=session_id,
        preview_runtime_allocation_id=allocation_id,
        document_attachment_id=attachment_id,
        targets=_controls(filters),
    )
    subject = VoiceSubject(
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        voice_connection_id=uuid7(),
    )

    async def load(judge: VoiceJudge, names: tuple[str, ...]) -> list[str]:
        nonlocal page
        attachment = uuid7()
        async with database.transaction() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.extension_bindings set document_attachment_id = %s "
                "where interview_session_id = %s and status = 'bound'",
                (attachment, session_id),
            )
        page = await observe_next_document(
            database,
            case=case,
            earlier=page,
            preview_runtime_allocation_id=allocation_id,
            document_attachment_id=attachment,
            targets=_controls(names),
        )
        return [
            control.line for control in await _new_controls(database, judge=judge, subject=subject)
        ]

    async with _jev() as jev:
        judge = _judge(database, jev)
        # Filters around a new control keep their order, so only the new control is named.
        assert await load(judge, (*filters, "saved_view_save_control")) == [
            "new: saved view save control (top left of the screen)"
        ]
        # Placing the topic filter first moves it, and only it.
        assert await load(
            judge,
            ("filter_review_topic", "filter_channel", "filter_segment", "saved_view_save_control"),
        ) == ["moved: filter review topic, now top left of the screen"]


async def test_agreed_checks_count_as_shown_only_once_the_new_version_showed_them(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    case = await _case(database, auth_users)
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.VALIDATION)
    subject = VoiceSubject(
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        voice_connection_id=uuid7(),
    )
    async with _jev() as jev:
        judge = _judge(database, jev)
        before_reopen = await judge.checks_shown(
            subject=subject, checks=_CHECKS, activity=_SAVED_THEN_RELOADED
        )
        after_reopen = await judge.checks_shown(subject=subject, checks=_CHECKS, activity=_REOPENED)

    assert before_reopen is not None
    assert before_reopen.shown == frozenset({"save"})
    assert after_reopen is not None
    assert after_reopen.shown == frozenset({"save", "reopen"})


#: The conversation's dashboard coming up, and one step of a walkthrough, as the
#: activity feed describes them.
_DASHBOARD_LOADED = (
    "the page loaded",
    (
        "conversion filters applied (date range last_30_days, channels all, segments all, "
        "review topics all, conversion states Purchased, products all, sentiments all)"
    ),
    "conversion render completed (surface conversion_kpis, status rendered, row count 1)",
)
_CHANNEL_SET = (
    'clicked conversion filter channel showing "All"',
    "conversion filters applied (channels: all → TikTok)",
    "conversion render completed (surface review_list, status rendered, row count 92)",
)
_GREETING = (
    "Hi, I'm Wren, a product agent helping the Shopalytics team. What would you like help with?"
)


async def test_the_agent_acknowledges_what_the_conversation_awaited_not_each_step(
    database: Database, auth_users: dict[str, SignedInUser]
) -> None:
    awaited = await _conversation(
        database,
        case=await _case(database, auth_users),
        turns=(
            ("agent", _GREETING),
            ("participant", "I rebuild the same filters every Monday. Let me open the dashboard and show you."),
            ("agent", "Sure, take your time."),
        ),
    )
    walking = await _conversation(
        database,
        case=await _case(database, auth_users),
        turns=(
            ("agent", _GREETING),
            ("participant", "So every Monday I rebuild the same conversion filters, let me show you."),
            ("agent", "Sure, go ahead."),
            ("participant", "For example, TikTok for the channel,"),
        ),
    )
    async with _jev() as jev:
        judge = _judge(database, jev)
        dashboard = await judge.acknowledgement(subject=awaited, activity=_DASHBOARD_LOADED)
        step = await judge.acknowledgement(subject=walking, activity=_CHANNEL_SET)

    assert dashboard
    assert not step


async def _conversation(
    database: Database, *, case: CaseFixture, turns: tuple[tuple[str, str], ...]
) -> VoiceSubject:
    """A live session whose transcript is ``turns``, one fragment each."""
    session_id = await create_live_session(database, case=case, phase=InterviewPhase.DISCOVERY)
    connection_id = uuid7()
    async with database.transaction() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into public.voice_connections (
                    id, client_id, interview_session_id, state, voice_run_id,
                    participant_connection_id, provider_session_ref
                )
                values (%s, %s, %s, 'closed', %s, %s, 'sess_test')
                """,
                (connection_id, case.client_id, session_id, uuid7(), connection_id),
            )
        for index, (speaker, text) in enumerate(turns):
            await record_transcript_fragment(
                conn,
                client_id=case.client_id,
                fragment=TranscriptFragment(
                    interview_session_id=session_id,
                    voice_connection_id=connection_id,
                    voice_run_id=uuid7(),
                    provider_session_ref="sess_test",
                    speaker=speaker,
                    provider_event_type=(
                        "agent.word_played" if speaker == "agent" else "flux.end_of_turn"
                    ),
                    start_ms=index * 4000,
                    end_ms=index * 4000 + 3000,
                    text=text,
                ),
                provenance={"provider": "utrace_voice_agent"},
            )
    return VoiceSubject(
        client_id=case.client_id,
        product_signal_id=case.product_signal_id,
        interview_session_id=session_id,
        voice_connection_id=connection_id,
    )
