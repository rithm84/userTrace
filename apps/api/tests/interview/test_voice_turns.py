"""The voice agent's turn strategies, driven with the frames Deepgram Flux sends.

A predicted end of turn starts one speculative answer. When Flux revises the
prediction inside the same turn, the agent must still answer the turn once: the
same words keep the answer already running, and new words replace it.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

from pipecat.frames.frames import EagerTranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

from app.interview.voice.pipeline import OneSpeculation


async def test_a_revised_prediction_answers_the_turn_once() -> None:
    strategy = OneSpeculation()
    await strategy.setup(
        cast(
            FrameProcessorSetup,
            SimpleNamespace(task_manager=TaskManager(loop=asyncio.get_running_loop())),
        )
    )
    events: list[str] = []

    @strategy.event_handler("on_user_turn_inference_triggered")
    async def inferred(*_: object, **__: object) -> None:
        events.append("inference")

    @strategy.event_handler("on_user_turn_speculation_cancelled")
    async def cancelled(*_: object, **__: object) -> None:
        events.append("cancelled")

    # What Flux sent at 03:08:18.857 and 03:08:19.057 in the call that answered twice.
    await strategy.process_frame(EagerTranscriptionFrame("Alright. Let me try", "user", "t1"))
    await strategy.process_frame(EagerTranscriptionFrame("Alright. Let me try.", "user", "t2"))
    assert events == ["inference"]

    await strategy.process_frame(
        EagerTranscriptionFrame("Alright, let me try saving it now.", "user", "t3")
    )
    assert events == ["inference", "cancelled", "inference"]
    await strategy.cleanup()
