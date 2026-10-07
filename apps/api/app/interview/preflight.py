"""The participant compatibility preflight.

A claimed invitation runs this before the Interview Session enters interview
activity. It verifies a supported Chrome profile and version, an account-linked
compatible extension installation, the required host permission for the expected
preview origin, microphone access, control-plane connectivity, and compatible
protocol support.

The facts are recorded whether they pass or fail, so setup guidance is grounded
in what was actually observed. Reward-eligible participation does not begin
until every check passes, and passing establishes neither preview, SDK, nor
observation readiness: the session still completes the full runtime handshake.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.auth.principals import ParticipantPrincipal
from app.db.pool import Database
from app.errors import AggregateNotFoundError
from app.extension.installations import verify_session_preflight_proof
from app.extension.proofs import PossessionError
from app.ids import uuid7
from app.interview.character import CharacterState, set_character
from app.interview.projection import SessionProjection, publish_projection_on_connection
from app.interview.readiness import record_conversational_facts
from app.lifecycle.session_controls import PreflightReport, run_preflight
from app.logging import get_logger

__all__ = [
    "MINIMUM_CHROME_VERSION",
    "ParticipantEnvironment",
    "PreflightCheck",
    "PreflightOutcome",
    "run_participant_preflight",
]

_log = get_logger(__name__)

MINIMUM_CHROME_VERSION = 137
# `Google Chrome` is the Client Hints brand the session tab reports.
_SUPPORTED_BROWSERS = frozenset({"google chrome", "chrome", "chromium"})


class ParticipantEnvironment(BaseModel):
    """What the session tab observed about this participant's environment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    browser_name: str
    browser_major_version: int
    extension_installed: bool
    extension_installation_proof: str | None
    reported_protocol_version: str
    host_permission_granted: bool
    microphone_permission_granted: bool


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """One named check and why it passed or failed."""

    check_id: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightOutcome:
    """The recorded preflight, and the projection when it admitted the session."""

    record_id: UUID
    passed: bool
    checks: tuple[PreflightCheck, ...]
    projection: SessionProjection | None

    @property
    def failed_checks(self) -> tuple[str, ...]:
        """The identifiers of every check that did not pass."""
        return tuple(check.check_id for check in self.checks if not check.passed)


async def run_participant_preflight(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
    environment: ParticipantEnvironment,
    supported_protocol_versions: Sequence[str],
    possession_audience: str,
    possession_max_age_seconds: int,
) -> PreflightOutcome:
    """Verify the environment, record the facts, and admit on a full pass."""
    installation = await _resolve_installation(
        database,
        interview_session_id=interview_session_id,
        utrace_user_id=participant.utrace_user_id,
        installation_proof=environment.extension_installation_proof,
        audience=possession_audience,
        max_age_seconds=possession_max_age_seconds,
    )
    checks = _evaluate(
        environment=environment,
        installation_linked=installation is not None,
        supported_protocol_versions=tuple(supported_protocol_versions),
    )
    passed = all(check.passed for check in checks)
    record_id = await _record(
        database,
        participant=participant,
        interview_session_id=interview_session_id,
        environment=environment,
        installation_id=installation,
        checks=checks,
        passed=passed,
    )
    _log.info(
        "participant_preflight_recorded",
        client_id=str(participant.client_id),
        interview_session_id=str(interview_session_id),
        passed=passed,
        failed_checks=[check.check_id for check in checks if not check.passed],
        browser=environment.browser_name,
        browser_major_version=environment.browser_major_version,
    )
    if not passed:
        return PreflightOutcome(
            record_id=record_id, passed=False, checks=checks, projection=None
        )

    await run_preflight(
        database,
        participant=participant,
        session_id=interview_session_id,
        report=PreflightReport(
            browser_supported=True,
            extension_installed=True,
            permissions_granted=True,
            contract_version_supported=True,
        ),
    )
    await record_conversational_facts(
        database,
        interview_session_id=interview_session_id,
        participant_authenticated=True,
        voice_transport_ready=False,
        sideband_attached=False,
        backend_context_current=True,
    )
    async with database.transaction() as conn:
        await set_character(
            conn, interview_session_id=interview_session_id, state=CharacterState.IDLE
        )
        projection = await publish_projection_on_connection(
            conn, interview_session_id=interview_session_id
        )
    return PreflightOutcome(
        record_id=record_id, passed=True, checks=checks, projection=projection
    )


def _evaluate(
    *,
    environment: ParticipantEnvironment,
    installation_linked: bool,
    supported_protocol_versions: tuple[str, ...],
) -> tuple[PreflightCheck, ...]:
    """Run every named check against the observed environment."""
    browser_supported = (
        environment.browser_name.strip().lower() in _SUPPORTED_BROWSERS
        and environment.browser_major_version >= MINIMUM_CHROME_VERSION
    )
    protocol_supported = (
        environment.reported_protocol_version in supported_protocol_versions
    )
    return (
        PreflightCheck(
            check_id="supported_browser",
            passed=browser_supported,
            detail=(
                f"{environment.browser_name} {environment.browser_major_version}"
                if browser_supported
                else (
                    "uTrace sessions need Chrome "
                    f"{MINIMUM_CHROME_VERSION} or newer in a normal profile"
                )
            ),
        ),
        PreflightCheck(
            check_id="extension_installed",
            passed=environment.extension_installed and protocol_supported,
            detail=(
                f"extension protocol {environment.reported_protocol_version}"
                if environment.extension_installed and protocol_supported
                else "install or update the uTrace extension to continue"
            ),
        ),
        PreflightCheck(
            check_id="extension_account_linked",
            passed=installation_linked,
            detail=(
                "this browser is linked to your uTrace account"
                if installation_linked
                else "link the uTrace extension to your account to continue"
            ),
        ),
        PreflightCheck(
            check_id="extension_host_permission",
            passed=environment.host_permission_granted,
            detail=(
                "site access granted"
                if environment.host_permission_granted
                else "allow the extension to run on the pages you will be shown"
            ),
        ),
        PreflightCheck(
            check_id="microphone",
            passed=environment.microphone_permission_granted,
            detail=(
                "microphone available"
                if environment.microphone_permission_granted
                else "allow microphone access so we can talk"
            ),
        ),
    )


async def _resolve_installation(
    database: Database,
    *,
    interview_session_id: UUID,
    utrace_user_id: UUID,
    installation_proof: str | None,
    audience: str,
    max_age_seconds: int,
) -> UUID | None:
    """Return the linked installation this participant proved, if the proof holds.

    A refused proof fails the account-link check rather than the request, so the
    participant sees what to fix; the refusal reason is logged for diagnosis.
    """
    if installation_proof is None:
        return None
    try:
        async with database.transaction() as conn:
            return await verify_session_preflight_proof(
                conn,
                encoded_proof=installation_proof,
                utrace_user_id=utrace_user_id,
                audience=audience,
                max_age_seconds=max_age_seconds,
            )
    except (PossessionError, AggregateNotFoundError) as refusal:
        _log.warning(
            "preflight_installation_proof_refused",
            interview_session_id=str(interview_session_id),
            refusal_class=type(refusal).__name__,
            reason=str(refusal),
        )
        return None


async def _record(
    database: Database,
    *,
    participant: ParticipantPrincipal,
    interview_session_id: UUID,
    environment: ParticipantEnvironment,
    installation_id: UUID | None,
    checks: tuple[PreflightCheck, ...],
    passed: bool,
) -> UUID:
    """Write the immutable preflight record, passing or failing."""
    record_id = uuid7()
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.participant_preflight_records (
                id, client_id, interview_session_id, client_user_id, browser_name,
                browser_major_version, browser_supported, extension_installed,
                extension_installation_id, account_linked, host_permission_granted,
                microphone_permission_granted, control_plane_reachable,
                reported_protocol_version, protocol_supported, passed, failed_checks
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s)
            """,
            (
                record_id,
                participant.client_id,
                interview_session_id,
                participant.client_user_id,
                environment.browser_name,
                environment.browser_major_version,
                _passed(checks, "supported_browser"),
                environment.extension_installed,
                installation_id,
                _passed(checks, "extension_account_linked"),
                environment.host_permission_granted,
                environment.microphone_permission_granted,
                environment.reported_protocol_version,
                _passed(checks, "extension_installed"),
                passed,
                [check.check_id for check in checks if not check.passed],
            ),
        )
    return record_id


def _passed(checks: tuple[PreflightCheck, ...], check_id: str) -> bool:
    """Return whether one named check passed."""
    for check in checks:
        if check.check_id == check_id:
            return check.passed
    raise KeyError(f"no preflight check named {check_id!r}")
