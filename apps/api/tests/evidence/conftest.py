"""The evidence tests reuse the ingestion fixtures.

They run against the same real server, database, and Storage bucket, because an
evidence window that is not backed by committed telemetry proves nothing about
freezing, promotion, or authorization.
"""

from tests.ingestion.conftest import (  # noqa: F401 - re-exported pytest fixtures
    artifacts,
    ingest_base_url,
    ingest_server,
    ingest_websocket_url,
    keys,
    telemetry_database,
)
