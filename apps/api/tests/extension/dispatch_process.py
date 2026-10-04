"""Dispatch one pointing gesture from a process that holds no extension socket.

The control-socket tests run this as its own operating-system process, the way
the worker compute's voice sideband dispatches a gesture: it opens its own pool,
authorizes and records the command, and exits. Nothing links it to the API
process that holds the extension's socket except the committed rows.

Usage: ``python -m tests.extension.dispatch_process <client_id> <session_id>
<target_id> <target_revision>``. The last line on stdout is the outcome as JSON.
"""

import asyncio
import json
import os
import sys
from uuid import UUID

from app.db.pool import Database
from app.extension.commands import PointStep, Scene, SceneCommand, dispatch_visual_command
from app.ids import uuid7

_DEADLINE_SECONDS = 20


async def _dispatch(
    *, client_id: UUID, session_id: UUID, target_id: str, target_revision: int
) -> dict[str, str | bool | None]:
    """Authorize and record one pointing scene, and report the outcome."""
    database = Database(dsn=os.environ["UTRACE_DATABASE_URL"], min_size=1, max_size=1)
    await database.open()
    try:
        outcome = await dispatch_visual_command(
            database,
            client_id=client_id,
            interview_session_id=session_id,
            command=SceneCommand(
                type="scene",
                scene=Scene(
                    scene_id=uuid7(),
                    steps=[
                        PointStep(
                            step="point",
                            target_id=target_id,
                            target_revision=target_revision,
                            dwell_ms=900,
                        )
                    ],
                ),
            ),
            deadline_seconds=_DEADLINE_SECONDS,
        )
    finally:
        await database.close()
    return {
        "dispatched": outcome.dispatched,
        "command_id": str(outcome.command_id) if outcome.command_id else None,
        "reason": outcome.reason,
    }


if __name__ == "__main__":
    client, session, target, revision = sys.argv[1:5]
    result = asyncio.run(
        _dispatch(
            client_id=UUID(client),
            session_id=UUID(session),
            target_id=target,
            target_revision=int(revision),
        )
    )
    print(json.dumps(result))
