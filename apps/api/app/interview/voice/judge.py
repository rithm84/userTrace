"""Jev's judgements during the call, and the controls on the participant's screen.

Jev judges two things the conversation cannot settle by itself: which agreed checks
the participant's activity in a new version already showed, and whether what they
just did is worth acknowledging out loud. Each judgement reads the conversation as
recorded right now. The screen's controls come from the target registry of the
document the session's extension is bound to.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from app.connectors.failures import ConnectorFailure
from app.connectors.typesafe_jev import JevClient
from app.context.bundles import assemble_conversation_bundle
from app.context.purposes import BundlePurpose
from app.db.pool import Database
from app.interview.attention import ChecksShown, judge_acknowledgement, judge_checks_shown
from app.interview.ports import RegisteredTarget, TargetRegistryPort
from app.interview.voice.channel import VoiceSubject
from app.interview.voice.pointing import bound_document_attachment
from app.logging import get_logger

__all__ = ["VoiceJudge"]

_log = get_logger(__name__)

_ATTENTION_PURPOSE: Final = BundlePurpose.JEV_ATTENTION
#: The participant's latest words, the agent's reply, and the exchange before them.
_CONVERSATION_FRAGMENTS: Final[int] = 120
_TARGET_LIMIT: Final[int] = 48


class VoiceJudge:
    """Jev's judgements and the screen's registered controls for one call."""

    def __init__(self, *, database: Database, jev: JevClient, targets: TargetRegistryPort) -> None:
        self._database = database
        self._jev = jev
        self._targets = targets

    async def current_targets(self, *, subject: VoiceSubject) -> tuple[RegisteredTarget, ...]:
        """The targets the bound preview document registers now; none while unbound."""
        attachment = await bound_document_attachment(
            self._database, interview_session_id=subject.interview_session_id
        )
        if attachment is None:
            return ()
        return tuple(
            await self._targets.registered_targets(
                client_id=subject.client_id,
                interview_session_id=subject.interview_session_id,
                document_attachment_id=attachment,
                limit=_TARGET_LIMIT,
            )
        )

    async def checks_shown(
        self, *, subject: VoiceSubject, checks: Mapping[str, str], activity: Sequence[str]
    ) -> ChecksShown | None:
        """Which agreed checks the person's activity in the new version showed.

        `None` while Jev is unavailable, which is logged.
        """
        bundle = await assemble_conversation_bundle(
            self._database,
            client_id=subject.client_id,
            product_signal_id=subject.product_signal_id,
            interview_session_id=subject.interview_session_id,
            purpose=_ATTENTION_PURPOSE,
            recent_fragment_limit=_CONVERSATION_FRAGMENTS,
        )
        try:
            return await judge_checks_shown(
                self._database, self._jev, bundle=bundle, checks=checks, activity=activity
            )
        except ConnectorFailure as failure:
            _log.warning(
                "voice_checks_shown_unavailable",
                interview_session_id=str(subject.interview_session_id),
                category=failure.category.value,
                detail=str(failure),
            )
            return None

    async def acknowledgement(self, *, subject: VoiceSubject, activity: Sequence[str]) -> bool:
        """Whether Jev judges what the person just did worth acknowledging out loud now.

        False when Jev is unsure or unavailable, which is logged.
        """
        bundle = await assemble_conversation_bundle(
            self._database,
            client_id=subject.client_id,
            product_signal_id=subject.product_signal_id,
            interview_session_id=subject.interview_session_id,
            purpose=_ATTENTION_PURPOSE,
            recent_fragment_limit=_CONVERSATION_FRAGMENTS,
        )
        try:
            judged = await judge_acknowledgement(
                self._database, self._jev, bundle=bundle, activity=activity
            )
        except ConnectorFailure as failure:
            _log.warning(
                "voice_acknowledgement_unavailable",
                interview_session_id=str(subject.interview_session_id),
                category=failure.category.value,
                detail=str(failure),
            )
            return False
        return judged.accepted
