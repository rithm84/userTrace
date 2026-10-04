"""Abstract provider boundaries.

The control plane owns authority and durable state; providers execute. These
protocols are the seams the provider-adapters workstream implements. A route that
needs an unregistered port fails with an explicit "not yet wired" error naming
the owning workstream, never with a plausible-looking stub response.

Two of the seams are filled by another area of this application rather than by a
provider: observation readiness and telemetry evidence are implemented over the
ingestion stream's own durable state. They are database-bound, so ``create_app``
installs them once the connector is open rather than at construction.
"""

from dataclasses import dataclass

from app.errors import NotYetWiredError
from app.ports.receipts import ReceiptVerifierPort
from app.ports.voice import VoicePort
from app.preview.ports import ObservationReadinessPort
from app.validation.evidence import EvidenceQueryPort

__all__ = [
    "EvidenceQueryPort",
    "ObservationReadinessPort",
    "PortRegistry",
    "ReceiptVerifierPort",
    "VoicePort",
]


@dataclass(frozen=True, slots=True)
class PortRegistry:
    """The provider adapters installed in this process."""

    voice: VoicePort | None
    receipts: ReceiptVerifierPort | None
    observation: ObservationReadinessPort | None
    evidence: EvidenceQueryPort | None

    def require_voice(self) -> VoicePort:
        """Return the voice adapter or refuse with the owning workstream named."""
        if self.voice is None:
            raise NotYetWiredError(
                capability="gpt_live_voice_session", owning_workstream="provider-adapters"
            )
        return self.voice

    def require_receipts(self) -> ReceiptVerifierPort:
        """Return the callback receipt verifier or refuse."""
        if self.receipts is None:
            raise NotYetWiredError(
                capability="provider_callback_receipt_verification",
                owning_workstream="provider-adapters",
            )
        return self.receipts

    def require_observation(self) -> ObservationReadinessPort:
        """Return the observation readiness adapter or refuse."""
        if self.observation is None:
            raise NotYetWiredError(
                capability="server_sdk_activation_and_telemetry_readiness",
                owning_workstream="telemetry-ingestion",
            )
        return self.observation

    def require_evidence(self) -> EvidenceQueryPort:
        """Return the promoted-evidence query adapter or refuse."""
        if self.evidence is None:
            raise NotYetWiredError(
                capability="promoted_telemetry_evidence_query",
                owning_workstream="telemetry-ingestion",
            )
        return self.evidence
