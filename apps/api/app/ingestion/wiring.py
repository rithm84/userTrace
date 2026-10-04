"""Process-wide ingestion resources.

The artifact store, the signing key, and the server edge's rate budget are built
once per process and cached on the application as one typed record.

They are built on first use rather than during start-up. Ingestion needs its own
configuration — a signing key, a manifest trust anchor, an ingest origin, and a
bucket — and a control plane that serves no telemetry should not have to carry
them to boot. A process that does serve telemetry fails on its first ingestion
request with the exact variables named, and the failure never yields a
half-configured store or a second signing key.
"""

from dataclasses import dataclass

from fastapi import FastAPI

from app.ingestion.artifacts import ArtifactStore
from app.ingestion.limits import default_server_limits
from app.ingestion.rate_budget import RateBudget
from app.ingestion.settings import get_ingestion_settings
from app.ingestion.signing import Signer
from app.logging import get_logger
from app.settings import Settings

__all__ = [
    "IngestionResources",
    "close_ingestion",
    "require_artifact_store",
    "require_ingestion",
    "require_server_rate_budget",
    "require_signer",
]

_log = get_logger(__name__)

_ATTRIBUTE = "ingestion_resources"


@dataclass(frozen=True, slots=True)
class IngestionResources:
    """The ingestion resources one process owns."""

    artifacts: ArtifactStore
    signer: Signer
    server_rate_budget: RateBudget


def _build(settings: Settings) -> IngestionResources:
    """Build the resources from the process environment, or name what is missing."""
    ingestion = get_ingestion_settings()
    limits = default_server_limits()
    resources = IngestionResources(
        artifacts=ArtifactStore(
            supabase_url=settings.supabase_url,
            service_key=settings.supabase_secret_key,
            bucket=ingestion.telemetry_bucket,
        ),
        signer=Signer(
            seed=ingestion.attestation_signing_seed(),
            key_id=ingestion.attestation_signing_key_id,
        ),
        server_rate_budget=RateBudget(
            max_bytes_per_second=limits.max_batch_bytes * limits.max_in_flight_batches,
            max_records_per_second=limits.max_batch_spans * limits.max_in_flight_batches,
        ),
    )
    _log.info(
        "ingestion_resources_installed",
        bucket=ingestion.telemetry_bucket,
        signing_key_id=ingestion.attestation_signing_key_id,
    )
    return resources


def require_ingestion(application: FastAPI) -> IngestionResources:
    """Return the process's ingestion resources, building them on first use."""
    cached = getattr(application.state, _ATTRIBUTE, None)
    if isinstance(cached, IngestionResources):
        return cached
    settings = application.state.settings
    if not isinstance(settings, Settings):
        raise TypeError("the application was assembled without settings")
    resources = _build(settings)
    setattr(application.state, _ATTRIBUTE, resources)
    return resources


async def close_ingestion(application: FastAPI) -> None:
    """Release the ingestion resources, if this process ever built them."""
    cached = getattr(application.state, _ATTRIBUTE, None)
    if not isinstance(cached, IngestionResources):
        return
    await cached.artifacts.aclose()
    setattr(application.state, _ATTRIBUTE, None)


def require_artifact_store(application: FastAPI) -> ArtifactStore:
    """Return the process-wide artifact store."""
    return require_ingestion(application).artifacts


def require_signer(application: FastAPI) -> Signer:
    """Return the process-wide attestation signer."""
    return require_ingestion(application).signer


def require_server_rate_budget(application: FastAPI) -> RateBudget:
    """Return the server edge's process-wide rate budget."""
    return require_ingestion(application).server_rate_budget
