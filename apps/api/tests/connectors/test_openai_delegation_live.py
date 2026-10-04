"""Live smoke: a voice delegation run on GPT-6.1 Sol through the Responses API.

It sends the real delegation tool definitions in strict mode, and its second turn
only succeeds if the first turn's encrypted reasoning went back with it.
"""

from __future__ import annotations

from app.connectors.openai_roles import OpenAIRole, openai_gateway_for
from app.interview.voice.tools import TOOL_DEFINITIONS
from tests.connectors.conftest import require_env


async def test_the_voice_delegation_role_completes_a_tool_run() -> None:
    """A stateless run carries its turns and answers from the tool's result."""
    require_env("OPENAI_API_KEY")
    async with openai_gateway_for(OpenAIRole.VOICE_DELEGATION) as gateway:
        run = gateway.begin_tool_run(
            instructions="Answer from the tool's result only, in one short sentence.",
            tools=TOOL_DEFINITIONS,
            max_output_tokens=2000,
            cache_key="voice_delegation:live-smoke",
        )
        first = await run.start(prompt="How is the change I asked for coming along?")
        assert [call.name for call in first.pending_calls] == ["implementation_status"]
        second = await run.submit_tool_results(
            results={
                first.pending_calls[0].call_id: (
                    '{"progress":"preparing_preview","summary":"The new version is '
                    'being prepared in your preview."}'
                )
            }
        )
    assert second.is_complete()
    assert "preview" in second.output_text.lower()
