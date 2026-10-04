"""Write ``apps/api/openapi.json`` from the assembled application.

Run from the repository root:

    uv run --project apps/api python apps/api/scripts/export_openapi.py

The document is generated: edit the routers and response models, never the file.
Settings are not read, so the schema can be regenerated without credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import create_app
from app.ports import PortRegistry
from app.settings import Settings

OUTPUT = Path(__file__).resolve().parents[1] / "openapi.json"

_SCHEMA_ONLY_SETTINGS = Settings.model_construct(
    database_url="postgresql://schema-export/none",
    database_pool_min_size=1,
    database_pool_max_size=1,
    supabase_url="https://schema-export.invalid",
    supabase_publishable_key="schema-export",
    supabase_secret_key="schema-export",
    supabase_jwt_audience="authenticated",
    lane_signing_key="schema-export",
    front_door_service_token="schema-export",
    preview_origin_suffix="preview.invalid",
)


def main() -> int:
    """Generate the OpenAPI document and report where it was written."""
    app = create_app(_SCHEMA_ONLY_SETTINGS, PortRegistry(voice=None, receipts=None, observation=None, evidence=None))
    document = app.openapi()
    OUTPUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {len(document['paths'])} paths to {OUTPUT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
