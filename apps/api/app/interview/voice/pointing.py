"""Showing the participant a registered control: the gesture and its dispatch.

A gesture is one scene step at an exact registered target revision, played in the
bound preview document by the extension; one scene plays several in order. Which control to show, and whether to
show one at all, is decided elsewhere: by Jev from the conversation when the
agent delegates (`delegation`), or by the delegation model's own tool.
"""

from collections.abc import Mapping, Sequence
from typing import Final
from uuid import UUID

from app.db.pool import Database
from app.envelope import JsonValue
from app.extension.commands import (
    CircleStep,
    PointStep,
    Scene,
    SceneCommand,
    dispatch_visual_command,
)
from app.ids import uuid7
from app.interview.ports import RegisteredTarget

__all__ = [
    "POINT_DWELL_MS",
    "bound_document_attachment",
    "circle_step",
    "gesture_at",
    "point_step",
    "target_option",
]

#: How long the agent cursor rests on a control it points at.
POINT_DWELL_MS: Final[int] = 1200


def target_option(target: RegisteredTarget) -> str:
    """How a target is offered to a model choosing which control is meant."""
    return f"{target.role}: {target.description} ({target.placement})"


def point_step(target: RegisteredTarget, *, dwell_ms: int) -> PointStep:
    """Move the agent cursor onto a target and rest there."""
    return PointStep(
        step="point",
        target_id=target.target_id,
        target_revision=target.target_revision,
        dwell_ms=dwell_ms,
    )


def circle_step(target: RegisteredTarget) -> CircleStep:
    """Circle a target with a mark that fades after a moment."""
    return CircleStep(
        step="circle",
        target_id=target.target_id,
        target_revision=target.target_revision,
        retain=False,
    )


async def bound_document_attachment(
    database: Database, *, interview_session_id: UUID
) -> UUID | None:
    """The document the session's extension is bound to, if it is bound."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select document_attachment_id from public.extension_bindings
            where interview_session_id = %s and status = 'bound'
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    value = row["document_attachment_id"]
    return value if isinstance(value, UUID) else None


async def gesture_at(
    database: Database,
    *,
    client_id: UUID,
    interview_session_id: UUID,
    steps: Sequence[PointStep | CircleStep],
    deadline_seconds: int,
) -> Mapping[str, JsonValue]:
    """Dispatch one scene of gestures, played in order, and say what happened to it."""
    outcome = await dispatch_visual_command(
        database,
        client_id=client_id,
        interview_session_id=interview_session_id,
        command=SceneCommand(type="scene", scene=Scene(scene_id=uuid7(), steps=list(steps))),
        deadline_seconds=deadline_seconds,
    )
    if not outcome.dispatched:
        return {"refused": outcome.reason}
    return {"command_id": str(outcome.command_id)}
