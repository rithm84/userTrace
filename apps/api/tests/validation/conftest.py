"""The validation tests reuse the ingestion fixtures.

Validation from a live session cites observations the ingestion socket actually
committed, so these tests run against the same real server and database the
ingestion suite uses.
"""

from tests.ingestion.conftest import (  # noqa: F401 - re-exported pytest fixtures
    ingest_base_url,
    ingest_server,
    ingest_websocket_url,
    keys,
    telemetry_database,
)
