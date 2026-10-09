"""The Pipecat Cloud entry point of the uTrace voice agent.

The platform imports this module, checks readiness with `readyz`, and calls `bot`
with each session's start body; `app.interview.voice.cloud_session` runs the
session.
"""

from uuid import UUID

from pipecatcloud.agent import PipecatSessionArguments

from app.interview.voice.cloud_session import prepare_instance, run_voice_session


async def bot(args: PipecatSessionArguments) -> None:
    """Run the voice agent session for the Job its start request names."""
    await run_voice_session(
        voice_agent_job_id=UUID(str(args.body["voice_agent_job_id"])),
        session_id=str(args.session_id),
    )


async def readyz() -> bool:
    """Ready once this instance's database pool is open."""
    await prepare_instance()
    return True
