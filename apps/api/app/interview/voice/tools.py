"""The tools the application executes on GPT-6.1 Sol's behalf during a delegation.

GPT-Live delegates to uTrace's backend, and the backend runs GPT-6.1 Sol with these
tools as Responses API function tools. Every one of them is an application-executed function: the model names a
tool and its typed arguments, code performs the work under the control plane's
own authority, and the result is returned as data.

Two rules shape every tool here:

- Nothing returns internal implementation detail. Retrieved documents, observed
  activity, and implementation state come back as customer-appropriate findings,
  because what a tool returns can be spoken aloud.
- No tool grants authority. A visual gesture still passes policy, permission,
  readiness, and target-freshness checks; recorded claims and criteria are
  written as typed, source-linked records.
"""

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from openai.types.responses import FunctionToolParam
from pydantic import BaseModel, ConfigDict, ValidationError
from utrace_contracts.domain.lifecycle import (
    CriterionObservability,
    EvidenceClaimState,
    EvidenceClass,
)

from app.adapters.telemetry_evidence import TelemetryEvidenceQuery
from app.connectors.typesafe_jev import JevClient
from app.context.bundles import AssembledBundle
from app.db.pool import Database
from app.db.rows import as_optional_str, as_str
from app.envelope import JsonValue
from app.errors import ControlPlaneError
from app.gate.policy import load_implementation_policy
from app.ingestion.errors import QueryAuthorizationError
from app.interview.agreements import (
    CriterionDraft,
    record_acceptance_criteria_agreement,
    record_change_request,
)
from app.interview.evidence import record_claim
from app.interview.gesture_attention import select_visual_target
from app.interview.judgements import AttentionResult
from app.interview.observations import describe_activity, recent_activity
from app.interview.ports import TargetRegistryPort
from app.interview.voice.pointing import (
    POINT_DWELL_MS,
    bound_document_attachment,
    circle_step,
    gesture_at,
    point_step,
    target_option,
)
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
            "observability": {"type": "string", "enum": ["observed", "subjective"]},
        },
        "required": ["statement", "observability"],
        "additionalProperties": False,
    },
}

__all__ = ["TOOL_DEFINITIONS", "ToolContext", "execute_tool"]

_log = get_logger(__name__)

_NO_TARGET = "no_target"
_TARGET_LIMIT = 24
_OBSERVATION_LIMIT = 20
_GESTURES = ("point", "circle")
_WORKFLOW_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}")


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything one delegation's tools may touch, supplied explicitly."""

    database: Database
    client_id: UUID
    product_signal_id: UUID
    interview_session_id: UUID
    bundle: AssembledBundle
    targets: TargetRegistryPort | None
    jev: JevClient | None
    visual_command_deadline_seconds: int
    observation_window_seconds: int


TOOL_DEFINITIONS: tuple[FunctionToolParam, ...] = (
    {
        "type": "function",
        "name": "review_observed_activity",
        "description": (
            "Return what uTrace observed in the person's product session recently, "
            "as plain summaries. Use it to ground a question about what they just "
            "did, never to describe internals."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "How many recent observations to return.",
                }
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "point_at_product_element",
        "description": (
            "Point the agent cursor at, or circle, one element the person can see, "
            "described in their own words. Use it whenever the person asks you to "
            "show, point at, circle, or highlight something. The application chooses "
            "the registered target and refuses if none matches."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "The element as the person would describe it.",
                },
                "gesture": {
                    "type": "string",
                    "enum": list(_GESTURES),
                    "description": (
                        "point moves the cursor onto the element; circle draws a mark "
                        "around it that stays until the next gesture."
                    ),
                },
            },
            "required": ["description", "gesture"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_evidence_claim",
        "description": (
            "Record what the person established, as a claim citing this "
            "conversation. Use `confirmed` only for a deliberate, criterion-bound "
            "confirmation."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "statement": {"type": "string"},
                "claim_state": {
                    "type": "string",
                    "enum": ["proposed", "confirmed", "contradicted"],
                },
            },
            "required": ["statement", "claim_state"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_acceptance_criteria",
        "description": (
            "Freeze the agreed scope and its observable acceptance criteria once the "
            "person has agreed to them out loud."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
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
            },
            "required": ["agreed_outcome", "workflow", "criteria"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_requested_change",
        "description": (
            "While the person tries a new version, record a change they asked for in "
            "it once they have agreed it out loud: the whole change as it now stands "
            "and the checks that now apply, earlier checks included unless the change "
            "replaces them. The application builds the next version while the "
            "voice agent waits in silence. Refused when the case has used all its versions."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
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
            },
            "required": ["agreed_outcome", "workflow", "criteria"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "begin_validation",
        "description": (
            "Begin or resume validating the person's ready preview. Validation "
            "normally begins by itself once the new version is ready, so this is "
            "needed only when it has not. Returns the agreed criteria, in order."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "cite_validation_evidence",
        "description": (
            "Cite one observation from review_observed_activity as showing what the "
            "product did for one criterion during validation. Cite only what was "
            "observed; the application refuses anything outside this validation."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "criterion_ordinal": {"type": "integer", "minimum": 0},
                "observation_id": {"type": "string"},
                "stance": {"type": "string", "enum": ["supports", "contradicts"]},
                "summary": {
                    "type": "string",
                    "description": "What the product did, in one plain sentence.",
                },
            },
            "required": ["criterion_ordinal", "observation_id", "stance", "summary"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_validation_outcome",
        "description": (
            "Record the person's answer for every criterion once they have answered "
            "each one out loud. Never record an answer they did not give; use "
            "`unsure` when they did not settle it."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "answers": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion_ordinal": {"type": "integer", "minimum": 0},
                            "answer": {
                                "type": "string",
                                "enum": ["confirmed", "rejected", "unsure"],
                            },
                            "statement": {
                                "type": "string",
                                "description": "What the person said, close to their words.",
                            },
                        },
                        "required": ["criterion_ordinal", "answer", "statement"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["answers"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "implementation_status",
        "description": (
            "Return the current user-facing progress of any work uTrace is doing for "
            "this person, in plain language."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
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
    if name == "point_at_product_element":
        return await _point_at_product_element(context, parsed)
    if name == "record_evidence_claim":
        return await _record_evidence_claim(context, parsed)
    if name == "record_acceptance_criteria":
        return await _record_acceptance_criteria(context, parsed)
    if name == "record_requested_change":
        return await _record_requested_change(context, parsed)
    if name == "implementation_status":
        return await _implementation_status(context)
    if name == "begin_validation":
        return await _begin_validation(context)
    if name == "cite_validation_evidence":
        return await _cite_validation_evidence(context, parsed)
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
                    "occurred_at": observation.occurred_at,
                    "did": describe_activity(observation),
                }
                for observation in activity
            ]
        }
    )


async def _point_at_product_element(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Select the registered target a description means and gesture at it."""
    description = str(arguments.get("description", "")).strip()
    if not description:
        return _result({"error": "description is required"})
    gesture = str(arguments.get("gesture", ""))
    if gesture not in _GESTURES:
        return _result({"error": f"gesture must be one of {list(_GESTURES)}"})
    return _result(
        await _gesture(
            context,
            gesture=gesture,
            select=lambda targets: select_visual_target(
                context.database,
                _require_jev(context),
                bundle=context.bundle,
                description=description,
                targets=targets,
            ),
        )
    )


async def _gesture(
    context: ToolContext,
    *,
    gesture: str,
    select: Callable[[Mapping[str, str]], Awaitable[AttentionResult]],
) -> Mapping[str, JsonValue]:
    """Choose one registered target with ``select`` and dispatch one gesture scene."""
    if context.targets is None:
        return {"refused": "target_registry_not_wired"}
    if context.jev is None:
        return {"refused": "target_selection_not_configured"}
    document_attachment_id = await bound_document_attachment(
        context.database, interview_session_id=context.interview_session_id
    )
    if document_attachment_id is None:
        return {"refused": "no_current_binding"}
    candidates = await context.targets.registered_targets(
        client_id=context.client_id,
        interview_session_id=context.interview_session_id,
        document_attachment_id=document_attachment_id,
        limit=_TARGET_LIMIT,
    )
    if not candidates:
        return {"refused": "no_registered_targets"}

    selection = await select(
        {candidate.target_id: target_option(candidate) for candidate in candidates}
    )
    chosen = selection.selections.get("visual_target::selection")
    if not selection.accepted or chosen is None or chosen == _NO_TARGET:
        return {"refused": "no_matching_target", "detail": selection.detail}
    target = next(candidate for candidate in candidates if candidate.target_id == chosen)
    outcome = await gesture_at(
        context.database,
        client_id=context.client_id,
        interview_session_id=context.interview_session_id,
        steps=[
            point_step(target, dwell_ms=POINT_DWELL_MS)
            if gesture == "point"
            else circle_step(target)
        ],
        deadline_seconds=context.visual_command_deadline_seconds,
    )
    if "refused" in outcome:
        return outcome
    return {
        "pointed_at" if gesture == "point" else "circled": target.description,
        **outcome,
    }


def _require_jev(context: ToolContext) -> JevClient:
    """The Jev client `_gesture` already checked is configured."""
    if context.jev is None:
        raise RuntimeError("target selection ran without a configured Jev client")
    return context.jev


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


async def _record_acceptance_criteria(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Freeze the agreed scope and criteria at a new criteria revision."""
    outcome = str(arguments.get("agreed_outcome", "")).strip()
    workflow = str(arguments.get("workflow", "")).strip()
    raw = arguments.get("criteria")
    if not outcome or not isinstance(raw, list) or not raw:
        return _result({"error": "agreed_outcome and at least one criterion are required"})
    if _WORKFLOW_PATTERN.fullmatch(workflow) is None:
        return _result({"error": "workflow must be a short snake_case name"})
    try:
        supplied = [_CriterionArgument.model_validate(item) for item in raw]
        drafts = [
            CriterionDraft(
                ordinal=index,
                statement=criterion.statement,
                observability=criterion.observability,
            )
            for index, criterion in enumerate(supplied)
        ]
    except ValidationError as error:
        return _result({"error": f"a criterion failed validation: {error.error_count()}"})
    try:
        agreement = await record_acceptance_criteria_agreement(
            context.database,
            bundle=context.bundle,
            agreed_scope={"intended_outcome": outcome, "workflow_ref": workflow},
            criteria=drafts,
            supporting_evidence_ids=context.bundle.cited_evidence_ids(),
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
    outcome = str(arguments.get("agreed_outcome", "")).strip()
    workflow = str(arguments.get("workflow", "")).strip()
    raw = arguments.get("criteria")
    if not outcome or not isinstance(raw, list) or not raw:
        return _result({"error": "agreed_outcome and at least one criterion are required"})
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
    policy = await load_implementation_policy(
        context.database,
        configuration_release_id=context.bundle.dependencies.pinned_configuration_release_id,
    )
    try:
        request = await record_change_request(
            context.database,
            bundle=context.bundle,
            agreed_scope={"intended_outcome": outcome, "workflow_ref": workflow},
            criteria=drafts,
            supporting_evidence_ids=context.bundle.cited_evidence_ids(),
            implementation_limit=policy.implementation_limit,
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
                    "criterion_ordinal": criterion.ordinal,
                    "statement": criterion.statement,
                    "observability": criterion.observability.value,
                }
                for criterion in started.criteria
            ]
        }
    )


class _Citation(BaseModel):
    """One observation the model proposes as validation evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_ordinal: int
    observation_id: UUID
    stance: EvidenceStance
    summary: str


async def _cite_validation_evidence(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Promote one proposed observation after the controller checks it."""
    try:
        citation = _Citation.model_validate(arguments)
    except ValidationError as error:
        return _result({"error": f"the citation failed validation: {error.error_count()}"})
    try:
        evidence_id = await cite_session_observation(
            context.database,
            TelemetryEvidenceQuery(context.database),
            interview_session_id=context.interview_session_id,
            ordinal=citation.criterion_ordinal,
            observation_id=citation.observation_id,
            stance=citation.stance,
            summary=citation.summary,
        )
    except ControlPlaneError as refusal:
        return _result({"refused": "citation_rejected", "detail": str(refusal)})
    return _result({"recorded_evidence_id": str(evidence_id)})


class _Answer(BaseModel):
    """One criterion answer as the model supplied it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_ordinal: int
    answer: CriterionAnswer
    statement: str


async def _record_validation_outcome(
    context: ToolContext, arguments: Mapping[str, object]
) -> str:
    """Complete validation from the person's criterion-level answers."""
    raw = arguments.get("answers")
    if not isinstance(raw, list) or not raw:
        return _result({"error": "at least one answer is required"})
    try:
        answers = [_Answer.model_validate(item) for item in raw]
    except ValidationError as error:
        return _result({"error": f"an answer failed validation: {error.error_count()}"})
    try:
        completion = await finish_session_validation(
            context.database,
            TelemetryEvidenceQuery(context.database),
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
    """Serialize one tool result for the provider's function-call output."""
    return json.dumps(payload, separators=(",", ":"))
