"""What the voice sideband does on its own as the session's committed state changes.

Each of these waits on `SessionChanges` rather than polling: it re-reads the
session's state whenever a trigger says that state may have changed.

- Mute: the participant's Mute and Unmute reach the worker as queued Commands,
  which it applies to the provider connection, so a worker restart still applies
  them and a lost socket cannot swallow them.
- Open preview: once the preview has stayed ready but unopened for a grace
  period, the agent is asked to have the participant open it, and told if they
  open it before it spoke.
- Ready: once a new version serves in the participant's preview, the agent says
  it is loading; once it has loaded, the agent is told what changed (the controls
  this version shows that no earlier page of the session did) and shows them
  before asking the participant to try it and walking the agreed checks. Every
  new version of the change is announced this way.
- Validation: each new version's Validation Attempt begins as soon as its guards
  allow, the exact candidate ready and observation current on it, so the evidence
  window opens before the participant starts trying it rather than when the agent
  next delegates.
- Gate failure: once the Implementation Gate has failed for good, the agent is
  told the change cannot start, so it never promises a version that is not coming.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from utrace_contracts.domain.lifecycle import (
    CommandDisposition,
    CommandState,
    EffectState,
    GateDecisionKind,
    ImplementationProgress,
)

from app.connectors.openai_live import SidebandConnection
from app.db.pool import Database
from app.db.rows import as_int, as_str, as_uuid
from app.errors import AggregateNotFoundError, ControlPlaneError
from app.execution.commands import TerminalResult, record_terminal_result
from app.gate.job_types import (
    GATE_FAILED_EVENT,
    GATE_NO_IMPLEMENTATION_EVENT,
    GateFailureOutcome,
)
from app.ids import uuid7
from app.ingestion.target_registry import (
    control_identity,
    load_registered_targets,
    screen_region,
)
from app.interview.voice.delegation import DelegationRunner, DelegationSubject
from app.interview.voice.pointing import bound_document_attachment
from app.interview.voice.session_changes import SessionChanges
from app.interview.voice.speech_gestures import GestureMoments
from app.logging import get_logger
from app.validation.records import load_criteria_on_connection
from app.validation.session import start_session_validation

__all__ = [
    "announce_gate_failure",
    "announce_ready_preview",
    "announce_validation_outcome",
    "apply_controls",
    "begin_validation_when_ready",
    "prompt_open_preview",
]

_log = get_logger(__name__)

_SET_INPUT_MUTED: Final[str] = "voice.set_input_muted"
_READY: Final[str] = ImplementationProgress.READY_FOR_VALIDATION.value
_APPROVED: Final[str] = GateDecisionKind.APPROVED_LIVE.value

#: How long a ready preview may stay unopened before the agent is asked to prompt
#: for it. Participants usually open it unprompted, and GPT-Live speaks an
#: instruction at its next pause, which may be well after it was sent.
_OPEN_PREVIEW_GRACE_SECONDS: Final[float] = 20.0

#: Sent once the participant's preview is ready while they have not opened it, so
#: the agent asks them to open it rather than leaving them to find the button.
_OPEN_PREVIEW_CUE: Final[str] = (
    "The person's preview of the product is ready, but they have not opened it yet. "
    "At the next natural pause, ask them to click Open preview on this page and "
    "show you the workflow there."
)

#: Sent when the preview opens after the agent was asked to prompt for it.
_OPEN_PREVIEW_DONE: Final[str] = (
    "The person has opened the preview now. Do not ask them to open it again."
)

#: Speakable context sent once the candidate is ready in the participant's preview.
#: The application announces it rather than waiting for the agent to ask, so the
#: participant hears it as soon as the preview serves the new version; their
#: preview tab reloads onto it by itself.
_READY_CUE: Final[str] = (
    "The new version is ready and is loading in the person's preview tab, which "
    "refreshes by itself, so they do not need to reload it. Tell them in one short "
    "sentence that it is loading. Do not ask them to try it yet: the application "
    "tells you what changed once it has loaded."
)

#: Sent once the new version has loaded, so the agent walks the participant through
#: what changed, control by control, before they try it. The agreed change and its
#: recorded checks travel with it, so the walkthrough serves exactly what was agreed.
_DEBRIEF_CUE: Final[str] = (
    "The new version has loaded in their preview. Before they try it, walk them "
    "through what changed: a separate short sentence for each new control that "
    "delivers what you agreed, in the order they will use it, naming it plainly "
    "and saying where it is and what it does, such as \"The new export button "
    "at the top right downloads this report.\"; the cursor circles each one as you name "
    "it. Mention a moved control only if it matters to what "
    "you agreed. {changes} What you agreed: {outcome} Then ask them to try it and go "
    "through the agreed checks one at a time, waiting for their answer to each "
    "before the next. If they try several at once, the application tells you which "
    "checks the new version showed; ask them to confirm those rather than repeat "
    "them. The agreed checks, in order: {checks}"
)

#: Sent when the gate refused a change the participant asked for, so the agent says
#: so and validation of the version they have carries on.
_DECLINED_CUE: Final[str] = (
    "The change they asked for cannot be made in this session ({reason}). Tell them "
    "so in one short sentence, without technical detail, then carry on checking the "
    "version they have."
)

#: Sent when the Implementation Gate failed before any work on the agreed change
#: began, so the agent stops promising a new version; the case then closes with its
#: blocker Case File, which ends the session.
_GATE_FAILED_CUE: Final[str] = (
    "The change you agreed cannot be started: a technical problem on our side stopped "
    "it before any work began, and it was nothing they did. Tell them so now in one or "
    "two short sentences, without technical detail, and do not say a new version is "
    "coming. Thank them: what they showed and agreed is recorded for the product team, "
    "and this session will close shortly."
)

#: Sent when the Implementation Gate chose no implementation for the agreed change,
#: so the agent stops promising a new version; the case then closes with its
#: explanatory Case File, which ends the session.
_NO_IMPLEMENTATION_CUE: Final[str] = (
    "The change you agreed will not be built in this session: the review of what it "
    "would take decided against starting it here, and it was nothing they did. Tell "
    "them so now in one or two short sentences, without technical detail, and do not "
    "say a new version is coming. Thank them: what they showed and agreed is recorded "
    "for the product team, and this session will close shortly."
)

#: Sent once the person's validation of the new version is recorded as validated,
#: so the agent closes the conversation instead of waiting to be asked.
_VALIDATED_CUE: Final[str] = (
    "Validation is complete: every agreed check passed and they confirmed it. Close "
    "the conversation now in one or two short sentences: thank them, tell them the "
    "product team gets the change they just validated, and that this session will "
    "close now. Ask nothing more."
)

#: Sent once validation is recorded without passing every agreed check, so the agent
#: closes the conversation honestly.
_NOT_VALIDATED_CUE: Final[str] = (
    "Validation is finished, and the new version did not pass every agreed check. "
    "Close the conversation now in one or two short sentences: thank them, tell them "
    "the product team gets what they found and their time still counts, and that "
    "this session will close now. Ask nothing more."
)

#: The facts that send a case to the gate or settle it; the newest says whether a
#: stopped change is still the case's current outcome.
_GATE_EVENTS: Final[tuple[str, ...]] = (
    "product_signal_evidence_sufficient",
    "product_signal_revision_requested",
    "implementation_gate_decided",
    GATE_FAILED_EVENT,
    GATE_NO_IMPLEMENTATION_EVENT,
)

#: How many earlier pages of the session the new controls are compared against.
_EARLIER_DOCUMENTS: Final[int] = 4
#: How long the debrief waits for the new version's controls to be observed.
_NEW_CONTROLS_WAIT_SECONDS: Final[float] = 6.0
_TARGET_LIMIT: Final[int] = 48


async def apply_controls(
    database: Database,
    changes: SessionChanges,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    sideband: SidebandConnection,
) -> None:
    """Apply each queued session-control Command to the provider connection."""
    woken = changes.watch()
    while True:
        await woken.wait()
        woken.clear()
        for command_id, payload in await _claim_control_commands(
            database, interview_session_id=interview_session_id
        ):
            muted = bool(payload.get("muted"))
            await sideband.set_input_muted(muted=muted)
            await record_terminal_result(
                database,
                message_id=uuid7(),
                client_id=client_id,
                source="openai_gpt_live",
                result=TerminalResult(
                    result_id=uuid7(),
                    command_id=command_id,
                    disposition=CommandDisposition.SUCCEEDED,
                    effect_state=EffectState.COMPLETED,
                    technical_category=None,
                    retry_advice=None,
                    payload={"muted": muted},
                ),
            )


async def announce_ready_preview(
    database: Database,
    changes: SessionChanges,
    *,
    subject: DelegationSubject,
    delegations: DelegationRunner,
    sideband: SidebandConnection,
    agent_quiet: asyncio.Event,
    moments: GestureMoments,
) -> None:
    """Announce each new version: that it is loading, then what changed once it loads.

    The announcement starts as the version is found ready, so nothing else
    acknowledges its loading, and the debrief moment opens just before its cue, so
    the controls the agent names in it are circled as it names them.
    """
    woken = changes.watch()
    announced: UUID | None = None
    declined: UUID | None = None
    while True:
        await woken.wait()
        woken.clear()
        if await _implementation_progress(
            database, interview_session_id=subject.interview_session_id
        ) != _READY:
            continue
        version = await _ready_preview_version(
            database, product_signal_id=subject.product_signal_id
        )
        if version is None:
            continue
        if version == announced:
            refusal = await _declined_change(
                database, product_signal_id=subject.product_signal_id, after=declined
            )
            if refusal is not None:
                declined, reason = refusal
                await agent_quiet.wait()
                await sideband.append_instructions(
                    content=_DECLINED_CUE.format(reason=reason), delegation_id=None
                )
            continue
        announced = version
        moments.announce()
        await agent_quiet.wait()
        await sideband.append_commentary(content=_READY_CUE, delegation_id=None)
        _log.info(
            "voice_ready_preview_announced",
            interview_session_id=str(subject.interview_session_id),
            preview_version_id=str(version),
        )
        while not await _validating(
            database, interview_session_id=subject.interview_session_id, preview_version_id=version
        ):
            await woken.wait()
            woken.clear()
        new_controls = await _controls_once_observed(
            database, woken, delegations=delegations, subject=subject
        )
        checks = await _agreed_checks(database, product_signal_id=subject.product_signal_id)
        await agent_quiet.wait()
        outcome = await _agreed_outcome(database, subject=subject)
        moments.open_debrief(frozenset(control.identity for control in new_controls))
        await sideband.append_commentary(
            content=_DEBRIEF_CUE.format(
                changes=(
                    "What this version shows differently: "
                    f"{'; '.join(control.line for control in new_controls)}."
                    if new_controls
                    else "It shows no new or moved controls, so say in a sentence what "
                    "now behaves differently."
                ),
                outcome=f"{outcome.rstrip('.')}.",
                checks=" ".join(
                    f"{number}. {check}" for number, check in enumerate(checks, start=1)
                ),
            ),
            delegation_id=None,
        )
        _log.info(
            "voice_change_debrief_cued",
            interview_session_id=str(subject.interview_session_id),
            preview_version_id=str(version),
            new_controls=len(new_controls),
            checks=len(checks),
        )


async def announce_gate_failure(
    database: Database,
    changes: SessionChanges,
    *,
    product_signal_id: UUID,
    sideband: SidebandConnection,
    agent_quiet: asyncio.Event,
) -> None:
    """Tell the agent the change will not start, once per connection, when the gate stopped it.

    The gate stops an agreed change by failing or by choosing no implementation. The
    outcome is announced while it is the case's latest gate outcome, so a new
    connection hears it too and a later change request is never answered with an
    old one. A declined change keeps the version the person has, so they go on
    checking it.
    """
    woken = changes.watch()
    announced: UUID | None = None
    while True:
        await woken.wait()
        woken.clear()
        stopped = await _stopped_change(database, product_signal_id=product_signal_id)
        if stopped is None or stopped[0] == announced:
            continue
        announced, cue, outcome = stopped
        await agent_quiet.wait()
        await sideband.append_commentary(content=cue, delegation_id=None)
        _log.info(
            "voice_stopped_change_announced",
            product_signal_id=str(product_signal_id),
            event_id=str(announced),
            outcome=outcome,
        )


async def announce_validation_outcome(
    database: Database,
    changes: SessionChanges,
    *,
    product_signal_id: UUID,
    sideband: SidebandConnection,
    agent_quiet: asyncio.Event,
) -> None:
    """Have the agent close the conversation once, when the case's validation is recorded.

    The Case File that follows ends the session a little later; without this the
    agent waits silently for the person to ask whether they are done.
    """
    woken = changes.watch()
    announced: UUID | None = None
    while True:
        await woken.wait()
        woken.clear()
        completed = await _completed_validation(database, product_signal_id=product_signal_id)
        if completed is None or completed[0] == announced:
            continue
        announced, outcome = completed
        await agent_quiet.wait()
        await sideband.append_commentary(
            content=_VALIDATED_CUE if outcome == "validated" else _NOT_VALIDATED_CUE,
            delegation_id=None,
        )
        _log.info(
            "voice_validation_outcome_announced",
            product_signal_id=str(product_signal_id),
            validation_attempt_id=str(announced),
            outcome=outcome,
        )


async def _completed_validation(
    database: Database, *, product_signal_id: UUID
) -> tuple[UUID, str] | None:
    """The case's newest completed Validation Attempt and its outcome, if one completed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, outcome::text as outcome from public.validation_attempts
            where product_signal_id = %s and completed_at is not null
            order by completed_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return None if row is None else (as_uuid(row, "id"), as_str(row, "outcome"))


async def _stopped_change(
    database: Database, *, product_signal_id: UUID
) -> tuple[UUID, str, str] | None:
    """The fact, cue, and outcome of the case's latest gate outcome when it stopped the change."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, event_type, payload ->> 'outcome' as outcome
            from public.events
            where stream_kind = 'product_signal' and stream_id = %s
              and event_type = any(%s)
            order by sequence desc
            limit 1
            """,
            (product_signal_id, list(_GATE_EVENTS)),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    event_type = as_str(row, "event_type")
    if event_type == GATE_NO_IMPLEMENTATION_EVENT:
        return as_uuid(row, "id"), _NO_IMPLEMENTATION_CUE, "no_implementation"
    if event_type != GATE_FAILED_EVENT:
        return None
    outcome = GateFailureOutcome(as_str(row, "outcome"))
    cue = (
        _GATE_FAILED_CUE
        if outcome is GateFailureOutcome.CASE_BLOCKED
        else _DECLINED_CUE.format(reason="a technical problem on our side stopped it")
    )
    return as_uuid(row, "id"), cue, outcome.value


async def begin_validation_when_ready(
    database: Database, changes: SessionChanges, *, interview_session_id: UUID
) -> None:
    """Begin, or resume, each new version's Validation Attempt as soon as its guards allow.

    The controller refuses until the new version is ready and observation is
    current on it; each refusal waits for the next committed change and tries
    again. Beginning is idempotent, so a reconnected sideband resumes the same
    attempt rather than opening another, and a later version of the change begins
    its own attempt once it is ready.
    """
    woken = changes.watch()
    refused: str | None = None
    begun: UUID | None = None
    while True:
        await woken.wait()
        woken.clear()
        if await _implementation_progress(
            database, interview_session_id=interview_session_id
        ) != _READY:
            continue
        try:
            started = await start_session_validation(
                database, interview_session_id=interview_session_id
            )
        except ControlPlaneError as refusal:
            if str(refusal) != refused:
                refused = str(refusal)
                _log.info(
                    "voice_validation_waiting",
                    interview_session_id=str(interview_session_id),
                    reason=refused,
                )
            continue
        if started.validation_attempt_id == begun:
            continue
        begun = started.validation_attempt_id
        refused = None
        # The debrief waits for this attempt, and no trigger reports it.
        changes.committed()
        _log.info(
            "voice_validation_begun",
            interview_session_id=str(interview_session_id),
            validation_attempt_id=str(started.validation_attempt_id),
            criteria=len(started.criteria),
        )


async def prompt_open_preview(
    database: Database,
    changes: SessionChanges,
    *,
    interview_session_id: UUID,
    sideband: SidebandConnection,
    agent_quiet: asyncio.Event,
) -> None:
    """Ask the agent, once per connection, to have the participant open a ready preview.

    Nothing is sent while the preview is open, its browser SDK reporting being what
    shows the participant has it in front of them, nor before it has stayed unopened
    for the grace period. If it opens after the ask, the agent is told, so a prompt
    it has not spoken yet is never voiced stale.
    """
    woken = changes.watch()
    while True:
        await woken.wait()
        woken.clear()
        ready, open_ = await _preview_state(database, interview_session_id=interview_session_id)
        if open_:
            return
        if ready:
            break
    try:
        async with asyncio.timeout(_OPEN_PREVIEW_GRACE_SECONDS):
            while True:
                await woken.wait()
                woken.clear()
                if (await _preview_state(database, interview_session_id=interview_session_id))[1]:
                    return
    except TimeoutError:
        pass
    await agent_quiet.wait()
    if (await _preview_state(database, interview_session_id=interview_session_id))[1]:
        return
    await sideband.append_instructions(content=_OPEN_PREVIEW_CUE, delegation_id=None)
    _log.info("voice_open_preview_prompted", interview_session_id=str(interview_session_id))
    while True:
        await woken.wait()
        woken.clear()
        if (await _preview_state(database, interview_session_id=interview_session_id))[1]:
            break
    await agent_quiet.wait()
    await sideband.append_instructions(content=_OPEN_PREVIEW_DONE, delegation_id=None)
    _log.info("voice_open_preview_done", interview_session_id=str(interview_session_id))


async def _preview_state(database: Database, *, interview_session_id: UUID) -> tuple[bool, bool]:
    """Whether the session's preview is ready, and whether the participant has it open."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
                     select 1 from public.preview_routes routes
                     where routes.interview_session_id = sessions.id
                       and routes.state = 'active' and routes.readiness_state = 'ready'
                   ) as preview_routed,
                   coalesce(readiness.browser_sdk_ready, false) as preview_open
            from public.interview_sessions as sessions
            left join public.session_readiness as readiness
              on readiness.interview_session_id = sessions.id
            where sessions.id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("interview_sessions", interview_session_id)
    return bool(row["preview_routed"]), bool(row["preview_open"])


async def _implementation_progress(database: Database, *, interview_session_id: UUID) -> str:
    """The session's current implementation progress, as its projection shows it."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select implementation_progress from public.interview_sessions where id = %s",
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError("interview_sessions", interview_session_id)
    return as_str(row, "implementation_progress")


async def _agreed_checks(database: Database, *, product_signal_id: UUID) -> Sequence[str]:
    """The statements of the case's current acceptance criteria, in order."""
    async with database.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select acceptance_criteria_revision from public.product_signals where id = %s",
                (product_signal_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise AggregateNotFoundError("product_signals", product_signal_id)
        criteria = await load_criteria_on_connection(
            conn,
            product_signal_id=product_signal_id,
            criteria_revision=as_int(row, "acceptance_criteria_revision"),
        )
    return tuple(criterion.statement for criterion in criteria)


async def _claim_control_commands(
    database: Database, *, interview_session_id: UUID
) -> tuple[tuple[UUID, Mapping[str, object]], ...]:
    """Claim the queued control Commands for this session, oldest first."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.commands
            set state = %s, dispatching_at = now(), revision = revision + 1,
                updated_at = now()
            where id in (
                select id from public.commands
                where command_type = %s and target_id = %s and state = %s
                order by created_at
                for update skip locked
            )
            returning id, payload
            """,
            (
                CommandState.DISPATCHING.value,
                _SET_INPUT_MUTED,
                interview_session_id,
                CommandState.QUEUED.value,
            ),
        )
        rows = list(await cur.fetchall())
    claimed: list[tuple[UUID, Mapping[str, object]]] = []
    for row in rows:
        payload = row["payload"]
        if not isinstance(payload, Mapping):
            raise TypeError("commands.payload is not an object")
        claimed.append((as_uuid(row, "id"), payload))
    return tuple(claimed)


async def _ready_preview_version(database: Database, *, product_signal_id: UUID) -> UUID | None:
    """The case's newest ready Preview Version that no later one superseded."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id from public.preview_versions
            where product_signal_id = %s and state = 'ready'
              and superseded_by_preview_version_id is null
            order by ready_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    return None if row is None else as_uuid(row, "id")


async def _validating(
    database: Database, *, interview_session_id: UUID, preview_version_id: UUID
) -> bool:
    """Whether validation of this version is under way, so its page has loaded and is observed."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select exists (
              select 1 from public.validation_attempts
              where interview_session_id = %s and preview_version_id = %s
                and state = 'in_progress'
            ) as validating
            """,
            (interview_session_id, preview_version_id),
        )
        row = await cur.fetchone()
    return row is not None and bool(row["validating"])


@dataclass(frozen=True, slots=True)
class _ChangedControl:
    """A control a new version added or moved: its declared identity and how to say it."""

    identity: str
    line: str


async def _controls_once_observed(
    database: Database,
    woken: asyncio.Event,
    *,
    delegations: DelegationRunner,
    subject: DelegationSubject,
) -> tuple[_ChangedControl, ...]:
    """The loaded version's changed controls, once its page has reported them.

    Validation can begin before the new document's registered controls arrive, so
    the debrief re-reads on each committed change for a few seconds rather than
    walking through a version it has not seen yet.
    """
    deadline = asyncio.get_running_loop().time() + _NEW_CONTROLS_WAIT_SECONDS
    while True:
        controls = await _new_controls(database, delegations=delegations, subject=subject)
        remaining = deadline - asyncio.get_running_loop().time()
        if controls or remaining <= 0:
            return controls
        woken.clear()
        try:
            await asyncio.wait_for(woken.wait(), timeout=remaining)
        except TimeoutError:
            return await _new_controls(database, delegations=delegations, subject=subject)


async def _new_controls(
    database: Database, *, delegations: DelegationRunner, subject: DelegationSubject
) -> tuple[_ChangedControl, ...]:
    """What the loaded version shows differently: new controls first, then moved ones.

    A control is known by what the client declared it to be, not by the value it
    currently shows, so a filter showing a different value is not new, and each
    control is named once however many of its parts the page registers. One has
    moved when the page before this one showed it in none of the screen regions
    it occupies now.
    """
    current = await delegations.current_targets(subject=subject)
    bound = await bound_document_attachment(
        database, interview_session_id=subject.interview_session_id
    )
    earlier: set[str] = set()
    last_regions: dict[str, set[str]] = {}
    documents = await _earlier_documents(
        database, interview_session_id=subject.interview_session_id, excluding=bound
    )
    for position, document in enumerate(documents):
        targets = await load_registered_targets(
            database,
            client_id=subject.client_id,
            interview_session_id=subject.interview_session_id,
            document_attachment_id=document,
        )
        earlier.update(control_identity(target.description) for target in targets)
        if position == 0:
            for target in targets:
                last_regions.setdefault(control_identity(target.description), set()).add(
                    screen_region(target.placement)
                )
    visible = [target for target in current[:_TARGET_LIMIT] if target.visible]
    regions: dict[str, set[str]] = {}
    for target in visible:
        regions.setdefault(control_identity(target.description), set()).add(
            screen_region(target.placement)
        )
    new: dict[str, _ChangedControl] = {}
    moved: dict[str, _ChangedControl] = {}
    for target in visible:
        identity = control_identity(target.description)
        name = identity.replace("_", " ")
        if identity not in earlier:
            new.setdefault(
                identity,
                _ChangedControl(
                    identity=identity, line=f"new: {name} ({screen_region(target.placement)})"
                ),
            )
        elif identity in last_regions and not regions[identity] & last_regions[identity]:
            moved.setdefault(
                identity,
                _ChangedControl(
                    identity=identity,
                    line=(
                        f"moved: {name}, from the "
                        f"{' and '.join(sorted(last_regions[identity]))} "
                        f"to the {screen_region(target.placement)}"
                    ),
                ),
            )
    return (*new.values(), *moved.values())


async def _earlier_documents(
    database: Database, *, interview_session_id: UUID, excluding: UUID | None
) -> tuple[UUID, ...]:
    """The session's most recent earlier preview pages, newest first."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select streams.document_attachment_id, max(streams.opened_at) as opened_at
            from public.browser_streams as streams
            join public.telemetry_activations as activations
              on activations.id = streams.activation_id
            where activations.interview_session_id = %s
              and streams.document_attachment_id is distinct from %s
            group by streams.document_attachment_id
            order by opened_at desc
            limit %s
            """,
            (interview_session_id, excluding, _EARLIER_DOCUMENTS),
        )
        rows = await cur.fetchall()
    return tuple(as_uuid(row, "document_attachment_id") for row in rows)


async def _agreed_outcome(database: Database, *, subject: DelegationSubject) -> str:
    """The agreed change, as recorded with the case's current acceptance criteria."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select agreed_scope ->> 'intended_outcome' as intended_outcome
            from public.acceptance_criteria_agreements
            where interview_session_id = %s
            order by recorded_at desc
            limit 1
            """,
            (subject.interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None or row["intended_outcome"] is None:
        raise AggregateNotFoundError("acceptance_criteria_agreements", subject.interview_session_id)
    return as_str(row, "intended_outcome")


async def _declined_change(
    database: Database, *, product_signal_id: UUID, after: UUID | None
) -> tuple[UUID, str] | None:
    """The gate's newest refusal of a change, when it is newer than ``after`` and latest."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, decision, blocker_reason from public.gate_decisions
            where product_signal_id = %s
            order by decided_at desc
            limit 1
            """,
            (product_signal_id,),
        )
        row = await cur.fetchone()
    if row is None or as_str(row, "decision") == _APPROVED or as_uuid(row, "id") == after:
        return None
    return as_uuid(row, "id"), str(row["blocker_reason"] or "the change was not approved")
