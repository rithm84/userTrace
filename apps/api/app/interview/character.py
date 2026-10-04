"""The semantic agent character state.

The control plane publishes a monotonically revisioned character state that the
session tab and the extension both render. Neither surface infers durable
session state from a local animation, and both ignore an older revision.

The state is a projection of durable interview state, never a provider-owned
lifecycle: `speaking` means the control plane knows generated speech is in
flight, not that the person heard it.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from utrace_contracts.domain.lifecycle import AgentCharacterState

from app.db.rows import as_int, as_str
from app.errors import AggregateNotFoundError

__all__ = [
    "CharacterProjection",
    "CharacterState",
    "change_character",
    "load_character",
    "set_character",
]


CharacterState = AgentCharacterState
"""The nine character states are the domain contract's; this module only renders them."""


@dataclass(frozen=True, slots=True)
class CharacterProjection:
    """One character state with the motion values both surfaces render.

    Gaze, speech energy, and motion scale are presentation values derived from
    durable state. They carry no evidence and authorize nothing.
    """

    interview_session_id: UUID
    character_revision: int
    state: CharacterState
    gaze_x: float
    gaze_y: float
    speech_energy: float
    motion_scale: float


_NEUTRAL_GAZE = 0.0
_STILL = 0.0

_MOTION_BY_STATE: dict[CharacterState, tuple[float, float]] = {
    # (speech_energy, motion_scale)
    CharacterState.IDLE: (_STILL, 0.2),
    CharacterState.LISTENING: (_STILL, 0.35),
    CharacterState.THINKING: (_STILL, 0.5),
    CharacterState.SPEAKING: (0.7, 0.6),
    CharacterState.ACKNOWLEDGEMENT: (0.3, 0.45),
    CharacterState.CONCERN: (_STILL, 0.4),
    CharacterState.PAUSED: (_STILL, _STILL),
    CharacterState.RECONNECTING: (_STILL, 0.25),
    CharacterState.COMPLETION: (0.2, 0.5),
}


async def load_character(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID
) -> CharacterProjection:
    """Return the current character projection for one session."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select character_state, character_revision
            from public.interview_sessions
            where id = %s
            """,
            (interview_session_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(
            table="interview_sessions", row_id=interview_session_id
        )
    return _project(
        interview_session_id=interview_session_id,
        state=CharacterState(as_str(row, "character_state")),
        character_revision=as_int(row, "character_revision"),
    )


async def set_character(
    conn: AsyncConnection[DictRow],
    *,
    interview_session_id: UUID,
    state: CharacterState,
) -> CharacterProjection:
    """Advance the character state, bumping its revision only on a real change.

    A repeated state returns the current projection unchanged so a surface that
    ignores older revisions is never asked to re-render the same frame.
    """
    revision = await _advance(conn, interview_session_id=interview_session_id, state=state)
    if revision is not None:
        return _project(
            interview_session_id=interview_session_id,
            state=state,
            character_revision=revision,
        )
    return await load_character(conn, interview_session_id=interview_session_id)


async def change_character(
    conn: AsyncConnection[DictRow],
    *,
    interview_session_id: UUID,
    state: CharacterState,
) -> bool:
    """Advance the character state and return whether it changed.

    For a caller that republishes the session projection only when something the
    participant sees moved.
    """
    revision = await _advance(conn, interview_session_id=interview_session_id, state=state)
    return revision is not None


async def _advance(
    conn: AsyncConnection[DictRow], *, interview_session_id: UUID, state: CharacterState
) -> int | None:
    """Write a changed state and return its revision, or ``None`` when it is unchanged.

    The character is presentation with its own revision, so it leaves the session's
    aggregate revision alone: a context bundle pinned to that revision stays
    current while the conversation turns.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            update public.interview_sessions
            set character_state = %s,
                character_revision = character_revision + 1,
                updated_at = now()
            where id = %s and character_state <> %s
            returning character_revision
            """,
            (state.value, interview_session_id, state.value),
        )
        updated = await cur.fetchone()
    return as_int(updated, "character_revision") if updated is not None else None


def _project(
    *, interview_session_id: UUID, state: CharacterState, character_revision: int
) -> CharacterProjection:
    """Attach the motion values one state renders with."""
    speech_energy, motion_scale = _MOTION_BY_STATE[state]
    return CharacterProjection(
        interview_session_id=interview_session_id,
        character_revision=character_revision,
        state=state,
        gaze_x=_NEUTRAL_GAZE,
        gaze_y=_NEUTRAL_GAZE,
        speech_energy=speech_energy,
        motion_scale=motion_scale,
    )
