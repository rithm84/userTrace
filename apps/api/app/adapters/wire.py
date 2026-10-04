"""Installing the ingestion-backed adapters into the port registry.

Both adapters read durable state through the process's one connector, which only
exists once the application's lifespan has opened it. ``create_app`` therefore
calls this after the pool is open and replaces the registry it was assembled
with, so a route resolving a port sees the installed adapter rather than the
placeholder the factory was handed.
"""

from dataclasses import replace

from app.adapters.observation_readiness import IngestionObservationReadiness
from app.adapters.telemetry_evidence import TelemetryEvidenceQuery
from app.db.pool import Database
from app.logging import get_logger
from app.ports import PortRegistry

__all__ = ["install_ingestion_adapters"]

_log = get_logger(__name__)


def install_ingestion_adapters(
    ports: PortRegistry, *, database: Database
) -> PortRegistry:
    """Return the registry with observation readiness and evidence installed."""
    installed = replace(
        ports,
        observation=IngestionObservationReadiness(database),
        evidence=TelemetryEvidenceQuery(database),
    )
    _log.info("ingestion_adapters_installed")
    return installed
