"""Reading what a session observed, through the telemetry query surface.

The interview runtime asks ingestion what the person just did during a live
encounter. That goes through `app.evidence.queries`, which is the versioned,
purpose-scoped read surface; there is no other path to telemetry from here. Which
structured targets a gesture may address is the target registry's question, in
`app.ingestion.target_registry`.

Every read names `live_interview` as its purpose, its own session, bounded time,
and an explicit field projection. What crosses into model context must be a safe
typed summary, and a raw payload is not one: the payload is read only to lift a
product milestone's declared identifier and the attribute values its redaction
rules already released, and nothing else from it leaves this module.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.db.pool import Database
from app.evidence.queries import (
    QueryPurpose,
    QueryScope,
    SourceClass,
    session_activity,
)
from app.ingestion.target_registry import load_registered_targets
from app.logging import get_logger

__all__ = [
    "LIVE_INTERVIEW_PURPOSE",
    "ObservedActivity",
    "describe_activity",
    "recent_activity",
]

_log = get_logger(__name__)

LIVE_INTERVIEW_PURPOSE: QueryPurpose = "live_interview"

_BROWSER_SOURCE: tuple[SourceClass, ...] = ("browser_observation",)
_ACTIVITY_PROJECTION: tuple[str, ...] = (
    "id",
    "observation_type",
    "observed_at",
    "target_id",
    "document_attachment_id",
    "payload",
)
_MILESTONE_OBSERVATION = "product.milestone"
_ACTIVATION_OBSERVATION = "interaction.activation"
_PAGE_LOAD_OBSERVATION = "baseline.snapshot"


@dataclass(frozen=True, slots=True)
class ObservedActivity:
    """One bounded, customer-safe summary of something the session observed."""

    observation_id: str
    observation_type: str
    #: When it happened on the server's clock (`app.evidence.observed_time`).
    observed_at: str
    target_id: str | None
    control: str | None
    """For an activation, the control as the client declared and labelled it."""
    milestone: str | None
    milestone_attributes: Mapping[str, str]


async def recent_activity(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    since: datetime,
    limit: int,
) -> tuple[ObservedActivity, ...]:
    """Return the person's latest activity after ``since``, oldest first.

    The read keeps the ``limit`` newest milestones, control activations, and page
    loads, then returns them in the order they happened.
    """
    result = await session_activity(
        database,
        QueryScope(
            client_id=client_id,
            purpose=LIVE_INTERVIEW_PURPOSE,
            interview_session_id=interview_session_id,
            allowed_source_classes=_BROWSER_SOURCE,
            not_before=since,
            projection=_ACTIVITY_PROJECTION,
            limit=limit,
        ),
    )
    controls = await _control_descriptions(
        database,
        client_id=client_id,
        interview_session_id=interview_session_id,
        rows=result.rows,
    )
    return tuple(_activity(row, controls=controls) for row in reversed(result.rows))


def describe_activity(activity: ObservedActivity) -> str:
    """One plain line the agent may use: what the person did, in product terms."""
    if activity.milestone is not None:
        details = ", ".join(
            f"{_words(name)} {value}" for name, value in activity.milestone_attributes.items()
        )
        return f"{_words(activity.milestone)}" + (f" ({details})" if details else "")
    if activity.observation_type == _ACTIVATION_OBSERVATION:
        return f"clicked {activity.control}" if activity.control else "clicked a control"
    if activity.observation_type == _PAGE_LOAD_OBSERVATION:
        return "the page loaded"
    return _words(activity.observation_type)


def _words(identifier: str) -> str:
    return identifier.replace(".", " ").replace("_", " ")


async def _control_descriptions(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    rows: tuple[Mapping[str, object], ...],
) -> Mapping[str, str]:
    """Describe each activated target from its document's registry, by target id."""
    documents = {
        str(row["document_attachment_id"])
        for row in rows
        if row["observation_type"] == _ACTIVATION_OBSERVATION
        and row["document_attachment_id"] is not None
    }
    described: dict[str, str] = {}
    for document in documents:
        for target in await load_registered_targets(
            database,
            client_id=client_id,
            interview_session_id=interview_session_id,
            document_attachment_id=UUID(document),
        ):
            described[target.target_id] = _words(target.description)
    return described


def _activity(row: Mapping[str, object], *, controls: Mapping[str, str]) -> ObservedActivity:
    """Project one activity row onto the summary the agent may hear."""
    target = row.get("target_id")
    observation_type = str(row["observation_type"])
    milestone, attributes = (
        _milestone(row["payload"]) if observation_type == _MILESTONE_OBSERVATION else (None, {})
    )
    return ObservedActivity(
        observation_id=str(row["id"]),
        observation_type=observation_type,
        observed_at=str(row["observed_at"]),
        target_id=str(target) if target is not None else None,
        control=controls.get(str(target)) if target is not None else None,
        milestone=milestone,
        milestone_attributes=attributes,
    )


def _milestone(payload: object) -> tuple[str, dict[str, str]]:
    """Lift a milestone's identifier and its released safe attribute values.

    An attribute whose redaction rule released no value carries none here either.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("milestone_id"), str):
        raise TypeError("a product.milestone observation carries no milestone_id")
    released: dict[str, str] = {}
    for attribute in payload.get("attributes") or ():
        if not isinstance(attribute, Mapping) or not isinstance(attribute.get("value"), Mapping):
            raise TypeError("a product.milestone attribute is not a named safe value")
        text = attribute["value"].get("value")
        if isinstance(text, str):
            released[str(attribute["name"])] = text
    return str(payload["milestone_id"]), released
