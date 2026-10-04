"""FastAPI application assembly.

The application owns one Postgres transaction authority, one JWT verifier, and
one provider port registry. Routers hold no state: they resolve authority through
dependencies and commit through the kernel.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging import INFO

from fastapi import FastAPI

from app.adapters.wire import install_ingestion_adapters
from app.api.callbacks import router as callbacks_router
from app.api.clients import router as clients_router
from app.api.exception_handlers import install_exception_handlers
from app.api.extension import router as extension_router
from app.api.front_door import router as front_door_router
from app.api.implementation import router as implementation_router
from app.api.ingest import router as ingest_router
from app.api.ingestion import router as ingestion_router
from app.api.ingestion_queries import router as ingestion_queries_router
from app.api.intake import router as intake_router
from app.api.invitations import router as invitations_router
from app.api.offers import router as offers_router
from app.api.outputs import router as outputs_router
from app.api.participant import router as participant_router
from app.api.preview_activation import router as preview_activation_router
from app.api.preview_handoffs import router as preview_handoffs_router
from app.api.preview_routes import router as preview_routes_router
from app.api.sessions import router as sessions_router
from app.api.staff import router as staff_router
from app.api.validation import router as validation_router
from app.auth.jwt import SupabaseJwtVerifier
from app.db.pool import Database
from app.execution.reconcile import reconcile_startup
from app.ingestion.wiring import close_ingestion
from app.interview.settings import get_openai_voice_settings
from app.interview.voice.adapter import build_voice_adapter
from app.logging import configure_logging, get_logger
from app.ports import PortRegistry
from app.settings import Settings, get_settings

__all__ = ["create_app", "create_default_app"]

_log = get_logger(__name__)

JWKS_LIFESPAN_SECONDS = 300
STALE_OUTBOX_SECONDS = 300
STALE_DISPATCH_SECONDS = 900


def create_app(settings: Settings, ports: PortRegistry) -> FastAPI:
    """Assemble the control-plane API with its connectors and provider ports."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(
            dsn=settings.database_url,
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
        )
        await database.open()
        application.state.database = database
        application.state.settings = settings
        # The ingestion-backed adapters read through this connector, so they are
        # installed once it is open rather than when the factory ran.
        application.state.ports = install_ingestion_adapters(
            ports, database=database
        )
        application.state.jwt_verifier = SupabaseJwtVerifier(
            settings, jwks_lifespan_seconds=JWKS_LIFESPAN_SECONDS
        )
        await reconcile_startup(
            database,
            stale_outbox_seconds=STALE_OUTBOX_SECONDS,
            stale_dispatch_seconds=STALE_DISPATCH_SECONDS,
        )
        _log.info("control_plane_ready")
        try:
            yield
        finally:
            await close_ingestion(application)
            await database.close()

    application = FastAPI(
        title="uTrace control plane",
        version="0.1.0",
        summary=(
            "Durable authority for Product Signals, interviews, implementation, "
            "validation, and client outputs."
        ),
        lifespan=lifespan,
    )
    install_exception_handlers(application)
    application.include_router(staff_router)
    application.include_router(clients_router)
    application.include_router(implementation_router)
    application.include_router(intake_router)
    application.include_router(invitations_router)
    application.include_router(validation_router)
    application.include_router(outputs_router)
    application.include_router(offers_router)
    application.include_router(participant_router)
    application.include_router(sessions_router)
    application.include_router(extension_router)
    application.include_router(front_door_router)
    application.include_router(preview_handoffs_router)
    application.include_router(preview_routes_router)
    application.include_router(ingest_router)
    application.include_router(ingestion_router)
    application.include_router(ingestion_queries_router)
    application.include_router(preview_activation_router)
    application.include_router(callbacks_router)

    @application.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        """Liveness only. Readiness lives with the durable-state checks."""
        return {"status": "ok"}

    return application


def create_default_app() -> FastAPI:
    """Application factory for ASGI servers.

    Settings are read when the factory runs, not at import, so tools that only
    need the route shapes can import this module without credentials. Run it with
    ``uvicorn --factory app.main:create_default_app``. The deployed API holds the
    GPT-Live voice adapter; the ingestion-backed ports are installed once the
    database is open.
    """
    configure_logging(INFO)
    return create_app(
        get_settings(),
        PortRegistry(
            voice=build_voice_adapter(settings=get_openai_voice_settings()),
            receipts=None,
            observation=None,
            evidence=None,
        ),
    )
