"""The adapter tests reuse the ingestion fixtures.

An adapter that reads ingestion state proves nothing against invented rows, so
these tests run against the same real server, database, and Storage bucket the
ingestion suite uses.
"""

from tests.ingestion.conftest import (  # noqa: F401 - re-exported pytest fixtures
    artifacts,
    ingest_base_url,
    ingest_server,
    ingest_websocket_url,
    keys,
    telemetry_database,
)
