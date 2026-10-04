"""The target-registry seam between the interview runtime and ingestion.

Observation readiness and the telemetry query surface answer whether the
correlated path is ready and what a session observed. A visual gesture asks one
more question first: *which* structured targets the shared browser runtime
currently registers, so the agent chooses among supplied identifiers instead of
inventing a selector. `app.ingestion.target_registry.IngestionTargetRegistry`
answers it from the ingestion stream's durable observations.

A port never decides authority. It reports registered facts; the command
controller rechecks the chosen target's revision immediately before dispatch.
"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

__all__ = ["RegisteredTarget", "TargetRegistryPort"]


class RegisteredTarget(BaseModel):
    """One registered structured target with the safe description a model reads.

    ``description`` is the SDK's declared semantic label, already classified as
    safe to discuss. Raw DOM and unredacted field values never cross this seam.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    target_revision: int
    description: str
    placement: str
    """Where it sits on the participant's screen, such as ``top right of the screen``,
    with its position among targets described alike, so a model can tell them apart."""
    role: str
    visible: bool


class TargetRegistryPort(Protocol):
    """The structured targets a gesture may address, for one bound document."""

    async def registered_targets(
        self,
        *,
        client_id: UUID,
        interview_session_id: UUID,
        document_attachment_id: UUID,
        limit: int,
    ) -> Sequence[RegisteredTarget]:
        """Return the targets currently registered in the bound document."""
        ...
