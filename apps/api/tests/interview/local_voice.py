"""The production voice adapter pointed at the local test GPT-Live server.

Only the API root and the key change: the connector, the adapter, and the worker run
unmodified, and the local server checks that every request carries a bearer key.
"""

from app.connectors.openai_live import GptLiveSessions
from app.interview.voice.adapter import GptLiveVoiceAdapter

__all__ = ["local_voice_adapter"]

_TEST_API_KEY = "test-openai-api-key"
_REQUEST_TIMEOUT_SECONDS = 10.0


def local_voice_adapter(*, base_url: str) -> GptLiveVoiceAdapter:
    """Build the production adapter against a local test GPT-Live server."""
    sessions = GptLiveSessions(
        api_key=_TEST_API_KEY,
        base_url=base_url,
        model="gpt-live-1",
        request_timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
    )
    return GptLiveVoiceAdapter(sessions=sessions, voice="marin")
