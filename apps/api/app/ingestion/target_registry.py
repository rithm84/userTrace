"""The structured targets the shared browser runtime currently registers.

The runtime reports its target registry in two shapes on the observation
stream: a baseline snapshot carries every mounted target, and each later
`target.registry_change` mounts, updates, re-lays-out, or unmounts one. The
current registry of a document is therefore the newest baseline of its stream
with every later change applied in export order, which is the order ingestion
treats as authoritative.

This is the one reading of that state. The interview runtime lists it to choose
a gesture's target, and the visual-command controller rereads it immediately
before dispatch, so a target the runtime re-registered or unmounted is refused
as stale rather than retargeted.

Only the safe fields the redaction policy already released leave this module:
the declared label value, the definition identifier, the role, and whether the
target is visible and unobscured. Raw DOM and unreleased values never cross it.

Contract: docs/arch-components/browser-sdk.md#structured-target-registry
"""

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.db.pool import Database
from app.db.rows import as_str
from app.interview.ports import RegisteredTarget
from app.logging import get_logger

__all__ = [
    "IngestionTargetRegistry",
    "control_identity",
    "load_registered_targets",
    "screen_region",
]

_log = get_logger(__name__)

_BASELINE = "baseline.snapshot"
_REGISTRY_CHANGE = "target.registry_change"
_UNLABELED = "unlabeled"


class _SafeValue(BaseModel):
    """A redacted value; ``value`` is present only when policy released it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    value: str | None = None


class _Geometry(BaseModel):
    """Where the target is, and the visibility facts a gesture depends on."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    x: int
    y: int
    width: int
    height: int
    viewport_width: int
    viewport_height: int
    visibility: Literal["visible", "offscreen", "hidden"]
    occluded: bool


class _TargetSummary(BaseModel):
    """The `target_summary` shape the SDK reports, reduced to what is read here."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    target_id: str
    target_revision: int
    role: str
    definition_id: str | None = None
    label: _SafeValue | None = None
    geometry: _Geometry


class _Baseline(BaseModel):
    """A baseline snapshot's mounted targets."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    targets: Sequence[_TargetSummary]


class _RegistryChange(BaseModel):
    """One registry change. A rejected annotation carries no target."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    change: Literal["mounted", "updated", "unmounted", "layout", "rejected"]
    target: _TargetSummary | None = None


class IngestionTargetRegistry:
    """`TargetRegistryPort` over the ingestion stream's durable observations."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def registered_targets(
        self,
        *,
        client_id: UUID,
        interview_session_id: UUID,
        document_attachment_id: UUID,
        limit: int,
    ) -> Sequence[RegisteredTarget]:
        """Return up to ``limit`` describable registered targets, visible ones first.

        A target with neither a released label nor a declared definition cannot be
        matched to anything a person or agent says, so it is left out rather than
        crowding describable targets past the limit.
        """
        targets = await load_registered_targets(
            self._database,
            client_id=client_id,
            interview_session_id=interview_session_id,
            document_attachment_id=document_attachment_id,
        )
        describable = [target for target in targets if target.description != _UNLABELED]
        ordered = sorted(describable, key=lambda target: not target.visible)
        return tuple(ordered[:limit])


async def load_registered_targets(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    document_attachment_id: UUID,
) -> tuple[RegisteredTarget, ...]:
    """Return every target the document's runtime currently registers.

    The document's newest browser stream is the runtime serving it: reload or
    full navigation starts a new document, and a resume continues the same
    stream. An empty tuple means nothing is registered, which refuses a gesture
    rather than letting it guess.
    """
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            with stream as (
              select streams.id
              from public.browser_streams as streams
              join public.telemetry_activations as activations
                on activations.id = streams.activation_id
              where streams.client_id = %(client)s
                and activations.interview_session_id = %(session)s
                and streams.document_attachment_id = %(attachment)s
              order by streams.opened_at desc
              limit 1
            ),
            baseline as (
              select coalesce(max(observations.export_sequence), 0) as export_sequence
              from public.browser_observations as observations
              where observations.browser_stream_id = (select id from stream)
                and observations.observation_type = %(baseline)s
            ),
            -- Every change carries the whole target, so only each target's newest
            -- change since the baseline decides what it is now.
            newest as (
              select distinct on (observations.target_id)
                     observations.observation_type, observations.payload,
                     observations.export_sequence
              from public.browser_observations as observations
              where observations.browser_stream_id = (select id from stream)
                and observations.observation_type = %(change)s
                and observations.export_sequence > (select export_sequence from baseline)
                and observations.payload->>'change' <> 'rejected'
              order by observations.target_id, observations.export_sequence desc
            )
            select observation_type, payload, export_sequence
            from public.browser_observations
            where browser_stream_id = (select id from stream)
              and observation_type = %(baseline)s
              and export_sequence = (select export_sequence from baseline)
            union all
            select observation_type, payload, export_sequence from newest
            order by export_sequence
            """,
            {
                "client": client_id,
                "session": interview_session_id,
                "attachment": document_attachment_id,
                "baseline": _BASELINE,
                "change": _REGISTRY_CHANGE,
            },
        )
        rows = await cur.fetchall()
    registry: dict[str, _TargetSummary] = {}
    for row in rows:
        payload = row["payload"]
        if as_str(row, "observation_type") == _BASELINE:
            registry = {
                target.target_id: target
                for target in _Baseline.model_validate(payload).targets
            }
            continue
        _apply(registry, _RegistryChange.model_validate(payload))
    targets = _registered_all(tuple(registry.values()))
    _log.info(
        "registered_targets_read",
        client_id=str(client_id),
        interview_session_id=str(interview_session_id),
        document_attachment_id=str(document_attachment_id),
        registry_records=len(rows),
        targets=len(targets),
    )
    return targets


def _apply(registry: dict[str, _TargetSummary], change: _RegistryChange) -> None:
    """Fold one registry change into the registry it follows."""
    if change.target is None:
        if change.change != "rejected":
            raise ValueError(f"a {change.change!r} registry change carries no target")
        return
    if change.change == "unmounted":
        registry.pop(change.target.target_id, None)
        return
    registry[change.target.target_id] = change.target


def _registered_all(summaries: Sequence[_TargetSummary]) -> tuple[RegisteredTarget, ...]:
    """Project every summary, placing each among the targets described like it."""
    alike: dict[str, list[_TargetSummary]] = {}
    for summary in summaries:
        alike.setdefault(_description(summary), []).append(summary)
    for group in alike.values():
        group.sort(key=lambda summary: (summary.geometry.y, summary.geometry.x))
    return tuple(
        _registered(summary, alike=alike[_description(summary)]) for summary in summaries
    )


def _placement(summary: _TargetSummary, *, alike: Sequence[_TargetSummary]) -> str:
    """Where the target sits on screen, and which of the targets described alike it is.

    Four summary cards declared under one definition read the same; their places
    are what tell "the card on the far right" from the others.
    """
    geometry = summary.geometry
    if geometry.visibility != "visible":
        where = "scrolled out of view"
    else:
        middle_x = (geometry.x + geometry.width / 2) / max(geometry.viewport_width, 1)
        middle_y = (geometry.y + geometry.height / 2) / max(geometry.viewport_height, 1)
        vertical = "top" if middle_y < 1 / 3 else "middle" if middle_y < 2 / 3 else "bottom"
        horizontal = "left" if middle_x < 1 / 3 else "center" if middle_x < 2 / 3 else "right"
        where = f"{vertical} {horizontal} of the screen"
    if len(alike) < 2:
        return where
    return f"{where}, number {alike.index(summary) + 1} of {len(alike)} alike from top left"


def _registered(summary: _TargetSummary, *, alike: Sequence[_TargetSummary]) -> RegisteredTarget:
    """Project one summary onto the description a model may read."""
    return RegisteredTarget(
        target_id=summary.target_id,
        target_revision=summary.target_revision,
        description=_description(summary),
        placement=_placement(summary, alike=alike),
        role=summary.role,
        visible=summary.geometry.visibility == "visible" and not summary.geometry.occluded,
    )


def control_identity(description: str) -> str:
    """What a described target is, without the value it currently shows.

    The inverse of the composition in `_description`: a filter described as
    `conversion_filter_channel showing "TikTok"` is the control
    `conversion_filter_channel`, whichever value it shows.
    """
    definition, separator, _label = description.partition(' showing "')
    return definition if separator else description


def screen_region(placement: str) -> str:
    """Where a placed target sits on screen, without its position among targets alike.

    The inverse of the composition in `_placement`: `middle left of the screen,
    number 1 of 2 alike from top left` sits in `middle left of the screen`.
    """
    region, _separator, _position = placement.partition(", number ")
    return region


def _description(summary: _TargetSummary) -> str:
    """The client's declared definition identifier and the released label, as present.

    The definition says what the target is and the label what it currently shows,
    so a filter control reads `conversion_filter_channel showing "All"` rather than
    a bare value several controls share.
    """
    label = summary.label.value if summary.label is not None else None
    if summary.definition_id is not None and label is not None:
        return f'{summary.definition_id} showing "{label}"'
    if summary.definition_id is not None:
        return summary.definition_id
    if label is not None:
        return label
    return _UNLABELED
