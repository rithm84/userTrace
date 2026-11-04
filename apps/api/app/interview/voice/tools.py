"""The tools the application executes on the voice agent's behalf.

The voice agent's text model calls these during the conversation. Every one of them
is an application-executed function: the model names a tool and its typed
arguments, code performs the work under the control plane's own authority, and the
result is returned as data. Each call runs against a context bundle assembled for
it, so a recorded agreement cites the conversation as it stood.

Two rules shape every tool here:

- Nothing returns internal implementation detail. Observed activity and
  implementation state come back as customer-appropriate findings, because what a
  tool returns can be spoken aloud.
- No tool grants authority. Recorded claims and criteria are written as typed,
  source-linked records, and the control plane's own guards decide what they start.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    EvidenceClaimState,
    EvidenceClass,
)

from app.adapters.telemetry_evidence import TelemetryEvidenceQuery
from app.context.bundles import AssembledBundle
from app.db.pool import Database
from app.db.rows import as_optional_str, as_str
from app.envelope import JsonValue
from app.errors import ControlPlaneError
from app.gate.policy import load_implementation_policy
from app.implementation.draft_records import ProposedChange, ProposedCriterion
from app.implementation.drafts import record_change_proposal
from app.ingestion.errors import QueryAuthorizationError
from app.interview.agreements import (
    CriterionDraft,
    record_acceptance_criteria_agreement,
    record_change_request,
)
from app.interview.evidence import record_claim
from app.interview.observations import describe_activity, recent_activity
from app.logging import get_logger
from app.validation.evidence import EvidenceStance
from app.validation.session import (
    CriterionAnswer,
    SpokenAnswer,
    cite_session_observation,
    finish_session_validation,
    start_session_validation,
)

#: The acceptance criteria the agreement and change-request tools freeze.
_CRITERIA_SCHEMA: Final[dict[str, object]] = {
    "type": "array",
    "minItems": 1,
    "maxItems": 8,
    "items": {
        "type": "object",
        "properties": {
            "statement": {
                "type": "string",
                "description": (
                    "One check on the new version being built, as what the person will "
                    "see it do, such as \"Reopening a saved view restores every saved "
                    "filter.\""
                ),
            },
            "observability": {
                "type": "string",
                "enum": ["observed", "subjective"],
                "description": (
                    "observed only for something the person does or the data the product "
                    "saves or shows, of the kind the application reports to you from the "
                    "preview, such as a view being saved or reopened, its filters being "
                    "restored, or what a chart displays; subjective for the page itself, "
                    "which only the person can judge, such as where a control sits, "
                    "whether it is shown, enabled, or disabled, or how it looks. Validation "
                    "needs a reported observation for every observed check."
                ),
            },
        },
        "required": ["statement", "observability"],
        "additionalProperties": False,
    },
}

#: Whether an agreement is the change exactly as the agent last proposed it.
_AS_PROPOSED_SCHEMA: Final[dict[str, object]] = {
    "type": "boolean",
    "description": (
        "true when they agreed to the change and checks exactly as you last proposed "
        "them in your proposal mark."
    ),
}

__all__ = [
    "TOOL_DEFINITIONS",
    "ToolContext",
    "announcement",
    "answer_follows",
    "execute_tool",
    "progress_in_words",
    "refused",
]

#: Tools the agent answers from: once one returns, it speaks or takes its next step.
_CONTINUING_TOOLS: Final[frozenset[str]] = frozenset(
    {"review_observed_activity", "implementation_status", "begin_validation"}
)
#: Tools whose recorded outcome the application follows at once with a cue of its own:
#: a recorded validation outcome is followed by the closing.
_CUED_TOOLS: Final[frozenset[str]] = frozenset({"record_validation_outcome"})
#: What the application says once a tool has recorded an agreement, instead of the
#: agent: the model's own words could promise the change without recording it.
_COMMITMENT: Final[str] = "I'll get that started and tell you when it's ready to try."
#: How the application opens that line when the answer that recorded it said nothing.
_ANNOUNCEMENT_OPENINGS: Final[Mapping[str, str]] = {
    "record_acceptance_criteria": "Great, that's agreed; ",
    "record_requested_change": "Great, ",
}
#: The keys of a result that says the call did not do what was asked.
_REFUSALS: Final[tuple[str, ...]] = ("error", "refused", "unavailable")

_log = get_logger(__name__)

_OBSERVATION_LIMIT = 20
_WORKFLOW_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}")


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything one tool call may touch, supplied explicitly."""

    database: Database
    client_id: UUID
    product_signal_id: UUID
    interview_session_id: UUID
    bundle: AssembledBundle
    observation_window_seconds: int


TOOL_DEFINITIONS: tuple[FunctionSchema, ...] = (
    FunctionSchema(
        name="review_observed_activity",
        description=(
            "Return what uTrace observed in the person's product session recently, "
            "as plain summaries. Use it to ground a question about what they just "
            "did, never to describe internals."
        ),
        properties={
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "How many recent observations to return.",
            }
        },
        required=["limit"],
    ),
    FunctionSchema(
        name="record_evidence_claim",
        description=(
            "Record what the person established, as a claim citing this "
            "conversation. Use `confirmed` only for a deliberate, criterion-bound "
            "confirmation."
        ),
        properties={
            "statement": {"type": "string"},
            "claim_state": {
                "type": "string",
                "enum": ["proposed", "confirmed", "contradicted"],
            },
        },
        required=["statement", "claim_state"],
    ),
    FunctionSchema(
        name="record_acceptance_criteria",
        description=(
            "Freeze the agreed scope and its observable acceptance criteria once the "
            "person has agreed to them out loud. Say that it is agreed first, in the "
            "same reply, before calling this."
        ),
        properties={
            "agreed_outcome": {"type": "string"},
            "workflow": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]{2,63}$",
                "description": (
                    "A short snake_case name for the workflow validation will "
                    "observe, such as saved_conversion_views."
                ),
            },
            "criteria": _CRITERIA_SCHEMA,
            "as_proposed": _AS_PROPOSED_SCHEMA,
        },
        required=["agreed_outcome", "workflow", "criteria"],
    ),
    FunctionSchema(
        name="record_requested_change",
        description=(
            "While the person tries a new version, record a change they asked for in "
            "it once they have agreed it out loud: the whole change as it now stands "
            "and the checks that now apply, earlier checks included unless the change "
            "replaces them. The application builds the next version while the "
            "voice agent waits in silence. Refused when the case has used all its versions."
        ),
        properties={
            "agreed_outcome": {
                "type": "string",
                "description": "The whole change as it now stands, the asked-for change included.",
            },
            "workflow": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]{2,63}$",
                "description": "The snake_case workflow name recorded with the first agreement.",
            },
            "criteria": _CRITERIA_SCHEMA,
            "as_proposed": _AS_PROPOSED_SCHEMA,
        },
        required=["agreed_outcome", "workflow", "criteria"],
    ),
    FunctionSchema(
        name="begin_validation",
        description=(
            "Begin or resume validating the person's ready preview. Validation "
            "normally begins by itself once the new version is ready, so this is "
            "needed only when it has not. Returns the agreed criteria, in order."
        ),
        properties={},
        required=[],
    ),
    FunctionSchema(
        name="record_validation_outcome",
        description=(
            "Record the person's answer for every criterion once they have answered "
            "each one out loud, each with the observations from "
            "review_observed_activity that show what the product did for it. Never "
            "record an answer they did not give; use `unsure` when they did not "
            "settle it. Cite an observation as contradicting a criterion only when "
            "the product did something other than it says for what they actually did. "
            "When their answers do not confirm every criterion, record them only once "
            "they have said they want no change, or no further version can be made."
        ),
        properties={
            "answers": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "check_number": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "The agreed check's number, as the application lists it.",
                        },
                        "answer": {
                            "type": "string",
                            "enum": ["confirmed", "rejected", "unsure"],
                        },
                        "statement": {
                            "type": "string",
                            "description": "What the person said, close to their words.",
                        },
                        "evidence": {
                            "type": "array",
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "observation_id": {"type": "string"},
                                    "stance": {
                                        "type": "string",
                                        "enum": ["supports", "contradicts"],
                                    },
                                    "summary": {
                                        "type": "string",
                                        "description": "What the product did, in one plain sentence.",
                                    },
                                },
                                "required": ["observation_id", "stance", "summary"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["check_number", "answer", "statement", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        required=["answers"],
    ),
    FunctionSchema(
        name="implementation_status",
        description=(
            "Return the current user-facing progress of any work uTrace is doing for "
            "this person, in plain language."
        ),
        properties={},
        required=[],
    ),
)


async def execute_tool(
    context: ToolContext, *, name: str, arguments: Mapping[str, object]
) -> str:
    """Run one application-executed tool call and return its JSON result.

    A refusal is a result, not an exception: the model needs to hear that a
    gesture was not permitted so it can say something true instead. Each tool
    validates its own arguments, because the model's input is untrusted.
    """
    parsed = arguments
    if name == "review_observed_activity":
        return await _review_observed_activity(context, parsed)
    if name == "record_evidence_claim":
        return await _record_evidence_claim(context, parsed)
    if name == "propose_change":
        # Not offered to the model: the agent's proposal mark carries these arguments.
        return await _propose_change(context, parsed)
    if name == "record_acceptance_criteria":
        return await _record_acceptance_criteria(context, parsed)
    if name == "record_requested_change":
        return await _record_requested_change(context, parsed)
    if name == "implementation_status":
        return await _implementation_status(context)
    if name == "begin_validation":
        return await _begin_validation(context)
    if name == "record_validation_outcome":
        return await _record_validation_outcome(context, parsed)
    return _result({"error": f"no tool named {name!r}"})


async def _review_observed_activity(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Return the bounded session timeline as customer-safe summaries."""
    limit = _bounded_int(arguments.get("limit"), default=_OBSERVATION_LIMIT)
    since = datetime.now(tz=UTC) - timedelta(seconds=context.observation_window_seconds)
    try:
        activity = await recent_activity(
            context.database,
            client_id=context.client_id,
            interview_session_id=context.interview_session_id,
            since=since,
            limit=limit,
        )
    except QueryAuthorizationError as refusal:
        return _result({"unavailable": "query_refused", "detail": str(refusal)})
    return _result(
        {
            "observations": [
                {
                    "observation_id": observation.observation_id,
                    "observed_at": observation.observed_at,
                    "did": describe_activity(observation),
                }
                for observation in activity
            ]
        }
    )


async def _record_evidence_claim(context: ToolContext, arguments: Mapping[str, object]) -> str:
    """Append one typed, source-linked claim to the evidence ledger."""
    statement = str(arguments.get("statement", "")).strip()
    state = str(arguments.get("claim_state", "")).strip()
    if not statement:
        return _result({"error": "statement is required"})
    try:
        claim_state = EvidenceClaimState(state)
    except ValueError:
        return _result({"error": f"unknown claim state {state!r}"})
    async with context.database.transaction() as conn:
        evidence_id = await record_claim(
            conn,
            client_id=context.client_id,
            product_signal_id=context.product_signal_id,
            interview_session_id=context.interview_session_id,
            evidence_class=EvidenceClass.TRANSCRIPT_FRAGMENT,
            claim_state=claim_state,
            source_ref=f"context_bundle:{context.bundle.bundle_id}",
            context_revision=context.bundle.context_revision,
            body={"kind": "interview_claim", "statement": statement},
            supersedes_id=None,
        )
    return _result({"recorded_evidence_id": str(evidence_id)})


class _CriterionArgument(BaseModel):
    """One acceptance criterion as the model supplied it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str
    observability: CriterionObservability


def _agreement_arguments(
    arguments: Mapping[str, object], *, outcome_key: str = "agreed_outcome"
) -> tuple[Mapping[str, JsonValue], list[CriterionDraft]] | str:
    """The agreed scope and criteria the model supplied, or the error result to return."""
    outcome = str(arguments.get(outcome_key, "")).strip()
    workflow = str(arguments.get("workflow", "")).strip()
    raw = arguments.get("criteria")
    if not outcome or not isinstance(raw, list) or not raw:
        return _result({"error": f"{outcome_key} and at least one criterion are required"})
    if _WORKFLOW_PATTERN.fullmatch(workflow) is None:
        return _result({"error": "workflow must be a short snake_case name"})
    try:
        drafts = [
            CriterionDraft(
                ordinal=index,
                statement=criterion.statement,
                observability=criterion.observability,
            )
            for index, criterion in enumerate(
                _CriterionArgument.model_validate(item) for item in raw
            )
        ]
    except ValidationError as error:
        return _result({"error": f"a criterion failed validation: {error.error_count()}"})
    return {"intended_outcome": outcome, "workflow_ref": workflow}, drafts


async def _propose_change(context: ToolContext, arguments: Mapping[str, object]) -> str:
    """Record the change the agent is proposing, so its draft starts before the answer.

    Nothing the person hears depends on it, so a proposal that cannot be recorded is
    reported as not noted rather than refused: the agent has nothing to say about it.
    """
    supplied = _agreement_arguments(arguments, outcome_key="proposed_outcome")
    if isinstance(supplied, str):
        return supplied
    scope, drafts = supplied
    try:
        await record_change_proposal(
            context.database, bundle=context.bundle, change=_proposed_change(scope, drafts)
        )
    except ControlPlaneError as error:
        _log.info("voice_tool_proposal_not_noted", detail=str(error))
        return _result({"noted": False})
    return _result({"noted": True})


def _proposed_change(
    scope: Mapping[str, JsonValue], drafts: Sequence[CriterionDraft]
) -> ProposedChange:
    return ProposedChange(
        intended_outcome=str(scope["intended_outcome"]),
        workflow_ref=str(scope["workflow_ref"]),
        criteria=tuple(
            ProposedCriterion(statement=draft.statement, observability=draft.observability)
            for draft in drafts
        ),
    )


async def _record_acceptance_criteria(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Freeze the agreed scope and criteria at a new criteria revision."""
    supplied = _agreement_arguments(arguments)
    if isinstance(supplied, str):
        return supplied
    agreed_scope, drafts = supplied
    try:
        agreement = await record_acceptance_criteria_agreement(
            context.database,
            bundle=context.bundle,
            agreed_scope=agreed_scope,
            criteria=drafts,
            supporting_evidence_ids=context.bundle.cited_evidence_ids(),
            proposal=_proposed_change(agreed_scope, drafts),
            as_proposed=arguments.get("as_proposed") is True,
        )
    except ControlPlaneError as error:
        return _result({"refused": "stale_context", "detail": str(error)})
    return _result(
        {
            "acceptance_criteria_revision": agreement.acceptance_criteria_revision,
            "criterion_count": len(agreement.criterion_ids),
        }
    )


async def _record_requested_change(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Freeze a change asked for in the new version and send it to the gate."""
    supplied = _agreement_arguments(arguments)
    if isinstance(supplied, str):
        return supplied
    agreed_scope, drafts = supplied
    policy = await load_implementation_policy(
        context.database,
        configuration_release_id=context.bundle.dependencies.pinned_configuration_release_id,
    )
    try:
        request = await record_change_request(
            context.database,
            bundle=context.bundle,
            agreed_scope=agreed_scope,
            criteria=drafts,
            supporting_evidence_ids=context.bundle.cited_evidence_ids(),
            implementation_limit=policy.implementation_limit,
            proposal=_proposed_change(agreed_scope, drafts),
            as_proposed=arguments.get("as_proposed") is True,
        )
    except ControlPlaneError as error:
        return _result({"refused": str(error)})
    return _result(
        {
            "acceptance_criteria_revision": request.agreement.acceptance_criteria_revision,
            "version_being_built": request.implementation_number,
            "versions_allowed": request.implementation_limit,
        }
    )


async def _implementation_status(context: ToolContext) -> str:
    """Return the user-facing progress projection in plain language."""
    async with context.database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select implementation_progress, phase
            from public.interview_sessions
            where id = %s
            """,
            (context.interview_session_id,),
        )
        row = await cur.fetchone()
        await cur.execute(
            """
            select state from public.implementation_attempts
            where product_signal_id = %s
            order by created_at desc
            limit 1
            """,
            (context.product_signal_id,),
        )
        attempt = await cur.fetchone()
    if row is None:
        return _result({"error": "no such Interview Session"})
    return _result(
        {
            "progress": _PROGRESS_LANGUAGE[as_str(row, "implementation_progress")],
            "phase": as_str(row, "phase"),
            "work_in_flight": attempt is not None
            and as_optional_str(attempt, "state")
            in {"queued", "acquiring_environment", "executing", "checking"},
        }
    )


async def _begin_validation(context: ToolContext) -> str:
    """Begin or resume validation of the case's ready candidate."""
    try:
        started = await start_session_validation(
            context.database, interview_session_id=context.interview_session_id
        )
    except ControlPlaneError as refusal:
        return _result({"refused": "validation_not_ready", "detail": str(refusal)})
    return _result(
        {
            "criteria": [
                {
                    "check_number": criterion.ordinal + 1,
                    "statement": criterion.statement,
                    "observability": criterion.observability.value,
                }
                for criterion in started.criteria
            ]
        }
    )


class _Citation(BaseModel):
    """One observation the model proposes as validation evidence for an answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: UUID
    stance: EvidenceStance
    summary: str


class _Answer(BaseModel):
    """One criterion answer as the model supplied it, with its evidence.

    The model numbers the agreed checks from 1, as people count; the controller's
    criterion ordinals start at 0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_number: int = Field(ge=1)
    answer: CriterionAnswer
    statement: str
    evidence: tuple[_Citation, ...]

    @property
    def criterion_ordinal(self) -> int:
        return self.check_number - 1


async def _record_validation_outcome(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Cite each answer's evidence, then complete validation from the answers.

    One call does both, so recording the person's answers is one step for the agent
    rather than a citation per check followed by the outcome.
    """
    raw = arguments.get("answers")
    if not isinstance(raw, list) or not raw:
        return _result({"error": "at least one answer is required"})
    try:
        answers = [_Answer.model_validate(item) for item in raw]
    except ValidationError as error:
        return _result({"error": f"an answer failed validation: {error.error_count()}"})
    evidence = TelemetryEvidenceQuery(context.database)
    for answer in answers:
        for citation in answer.evidence:
            try:
                await cite_session_observation(
                    context.database,
                    evidence,
                    interview_session_id=context.interview_session_id,
                    ordinal=answer.criterion_ordinal,
                    observation_id=citation.observation_id,
                    stance=citation.stance,
                    summary=citation.summary,
                )
            except ControlPlaneError as refusal:
                return _result(
                    {
                        "refused": "citation_rejected",
                        "check_number": answer.check_number,
                        "detail": str(refusal),
                    }
                )
    try:
        completion = await finish_session_validation(
            context.database,
            evidence,
            interview_session_id=context.interview_session_id,
            answers=[
                SpokenAnswer(
                    ordinal=answer.criterion_ordinal,
                    answer=answer.answer,
                    statement=answer.statement,
                )
                for answer in answers
            ],
            source_ref=f"context_bundle:{context.bundle.bundle_id}",
            context_revision=context.bundle.context_revision,
        )
    except ControlPlaneError as refusal:
        return _result({"refused": "validation_not_recorded", "detail": str(refusal)})
    return _result({"outcome": completion.outcome.value})


def refused(result: object) -> bool:
    """Whether a tool call's result says it did not do what was asked.

    A result that is not this module's JSON object is Pipecat's stand-in for a call
    whose handler raised.
    """
    return not isinstance(result, Mapping) or any(key in result for key in _REFUSALS)


def answer_follows(name: str, *, refused: bool, answer_spoke: bool) -> bool:
    """Whether the agent speaks again once a tool's result is in.

    It does after a refusal, to fix the call or tell the person what they need to
    do, and after a tool it answers from. A recorded validation outcome is followed
    at once by the application's closing cue, and a recorded agreement or requested
    change by its `announcement`. A recorded claim it speaks after only if the
    answer that recorded it said nothing: the person is never left in silence, and
    never hears a second turn after one that already answered them. What an
    agreement or a requested change leads to, the application announces once the
    Implementation Gate decides.
    """
    if refused or name in _CONTINUING_TOOLS:
        return True
    if name in _CUED_TOOLS or name in _ANNOUNCEMENT_OPENINGS:
        return False
    return not answer_spoke


def announcement(name: str, *, refused: bool, answer_spoke: bool) -> str | None:
    """What the application says once a tool's result is in, if anything.

    Once an agreement or a requested change is recorded, the application says the
    agent will get it started and tell them when it is ready to try, so the person
    hears that promise only once it is recorded. When the answer that recorded it
    said nothing, the line opens by saying it is agreed; when it said something,
    such as "Agreed.", the promise follows it.
    """
    opening = _ANNOUNCEMENT_OPENINGS.get(name)
    if refused or opening is None:
        return None
    return _COMMITMENT if answer_spoke else opening + _COMMITMENT


def progress_in_words(progress: str) -> str:
    """The change's recorded implementation progress, as the person is told it."""
    return _PROGRESS_LANGUAGE[progress]


_PROGRESS_LANGUAGE: Mapping[str, str] = {
    "not_started": "nothing is being changed yet",
    "reproduction_captured": "we have captured what you showed us",
    "making_changes": "we are making the change now",
    "preparing_preview": "we are preparing your preview",
    "ready_for_validation": "your preview is ready to try",
}


def _bounded_int(value: object, *, default: int) -> int:
    """Read an integer argument, clamping it to the documented bound."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(1, min(value, _OBSERVATION_LIMIT))


def _result(payload: Mapping[str, JsonValue]) -> str:
    return json.dumps(payload, separators=(",", ":"))
