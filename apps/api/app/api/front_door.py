"""Front-door service routes.

Every front-door endpoint lives with the area that owns its durable state:
claim exchange and route resolution in ``app.api.preview_handoffs`` and
``app.api.preview_routes`` (preview runtime), document activation and the
telemetry-credential exchange in ``app.api.preview_activation`` (telemetry
ingestion). This router is kept so the front-door prefix stays registered in
one place.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/v1/preview", tags=["front-door"])
