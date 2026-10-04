"""Driving the real ``@utrace/server-sdk`` against the control plane.

There is no test double here. The suite builds the published SDK, starts a real
Express service under ``node --import @utrace/server-sdk/preload``, and lets that
process activate over ``WS /v1/server-sdk/control``, export OTLP/HTTP to the real
ingestion edge, and read real rows from the project's Postgres. What the control
plane has to prove is only observable that way: a stub client would prove the
test's idea of the protocol rather than the SDK's.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from base64 import b64encode, urlsafe_b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from psycopg.types.json import Jsonb

from app.db.pool import Database
from app.envelope import JsonValue
from app.ids import uuid7
from app.ingestion.bootstrap import mint_server_bootstrap_credential
from app.ingestion.server_activation import sign_server_manifest as sign_with_release_key
from app.settings import Settings
from tests.helpers import policy_digest
from tests.ingestion.factories import PreviewFixture, annotation_manifest
from tests.ingestion.keys import SigningKeys

__all__ = [
    "APP_PORT",
    "DATABASE_CLASS",
    "PROBE_ROUTE_TEMPLATE",
    "SERVICE_KEY",
    "TEST_SCHEMA",
    "SdkProcess",
    "bootstrap_credential",
    "full_server_manifest",
    "prepare_product_schema",
    "publish_server_manifest",
    "sign_server_manifest",
    "start_service",
    "trust_anchor_env_value",
]

#: Inside the workstream's 8110-8129 range.
APP_PORT = 8118

#: The schema this workstream owns in the shared project database.
TEST_SCHEMA = "utrace_ingestion_control_test"
_TEST_TABLE = "saved_views"
DATABASE_CLASS = f"{TEST_SCHEMA}.{_TEST_TABLE}"

SERVICE_KEY = "shopalytics_api"
PROBE_ROUTE_TEMPLATE = "/api/saved-views/:view_id"

_PACKAGE_ROOT = (
    Path(__file__).resolve().parents[4] / "packages" / "utrace-server-sdk"
)
_APP_ROOT = Path(__file__).resolve().parent / "server_sdk"

_POLICY_VERSION = "1.4.0"
_UTRACE_SCHEMA_VERSION = "1.0.0"
_SEMANTIC_CONVENTION_VERSION = "1.38.0"
_MANIFEST_VERSION = 9


def full_server_manifest(*, client_id: UUID) -> dict[str, object]:
    """Build the manifest a client release publishes for its server services.

    Every member the shared JSON Schema requires is present, because the SDK
    validates the manifest it is handed before it installs anything.
    """
    return {
        "manifest_id": str(uuid4()),
        "client_id": str(client_id),
        "client_release_id": "shopalytics-2026-09-20",
        "manifest_version": _MANIFEST_VERSION,
        "policy_version": _POLICY_VERSION,
        "utrace_schema_version": _UTRACE_SCHEMA_VERSION,
        "semantic_convention_version": _SEMANTIC_CONVENTION_VERSION,
        "server_sdk_contract_major": 1,
        "services": [
            {
                "service_key": SERVICE_KEY,
                "service_name": "shopalytics-api",
                "service_namespace": "shopalytics",
                "service_version": "2026.09.20",
                "required": True,
                "required_adapters": ["node_http", "express", "pg"],
                "registered_routes": [
                    {
                        "route_template": PROBE_ROUTE_TEMPLATE,
                        "methods": ["GET"],
                        "operation_name": "saved_view.read",
                        "required": True,
                    }
                ],
                "registered_operations": [],
                "operational_noise": [
                    {"path_pattern": "/healthz", "methods": ["GET"]}
                ],
            }
        ],
        "propagation_targets": [],
        "redaction_rules": {
            "allowed_http_destinations": [],
            "allowed_database_classes": [DATABASE_CLASS, _TEST_TABLE],
            "allowed_cache_classes": [],
            "allowed_queue_destinations": [],
            "allowed_error_types": ["DatabaseError"],
            "sanitized_error_messages": [],
            "repository_root": str(_APP_ROOT),
            "max_stack_frames": 8,
            "max_attribute_value_length": 256,
            "max_span_attribute_count": 32,
        },
        "annotation_definitions": [],
    }


def sign_server_manifest(
    manifest: dict[str, object], keys: SigningKeys
) -> dict[str, object]:
    """Sign the manifest with this run's release key, exactly as onboarding does."""
    signed = sign_with_release_key(
        cast("dict[str, JsonValue]", manifest),
        private_key=keys.manifest_private,
        key_id=keys.manifest_key_id,
    )
    return cast("dict[str, object]", signed)


async def publish_server_manifest(
    database: Database,
    *,
    preview: PreviewFixture,
    signed: dict[str, object],
    authorizing_auth_user_id: UUID,
) -> UUID:
    """Publish the signed server manifest and pin the release that carries it.

    Configuration releases are append-only, so onboarding publishes a new release
    rather than editing one. The Product Signal then pins it for its whole
    lifecycle, which is what the control socket reads on activation.
    """
    release_id = uuid7()
    policy = {
        "live_implementation_allowed": True,
        "annotation_manifest": annotation_manifest(),
        "server_instrumentation_manifest": signed,
    }
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.configuration_releases (
                id, client_id, release_number, policy, digest,
                authorized_by_auth_user_id
            )
            values (%s, %s, 3, %s, %s, %s)
            """,
            (
                release_id,
                preview.case.client_id,
                Jsonb(policy),
                policy_digest(policy),
                authorizing_auth_user_id,
            ),
        )
        await cur.execute(
            """
            update public.product_signals set pinned_configuration_release_id = %s
            where id = %s
            """,
            (release_id, preview.case.product_signal_id),
        )
    return release_id


def trust_anchor_env_value(keys: SigningKeys) -> str:
    """Encode the manifest trust anchor the way the SDK's environment expects it."""
    spki = keys.manifest_private.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    anchor = json.dumps(
        {
            "key_id": keys.manifest_key_id,
            "algorithm": "ed25519",
            "public_key_spki_base64": b64encode(spki).decode("ascii"),
        }
    )
    return urlsafe_b64encode(anchor.encode("utf-8")).decode("ascii").rstrip("=")


def bootstrap_credential(settings: Settings, preview: PreviewFixture) -> str:
    """Mint the credential the prepared-environment runtime injects.

    This is the helper the preview stream calls at sandbox creation, so the
    journey authenticates against exactly what that stream would produce.
    """
    return mint_server_bootstrap_credential(
        signing_key=settings.lane_signing_key,
        client_id=preview.case.client_id,
        interview_session_id=preview.interview_session_id,
        preview_runtime_allocation_id=preview.preview_runtime_allocation_id,
        public_preview_host=preview.origin_host,
    ).token


async def prepare_product_schema(database: Database) -> None:
    """Create this workstream's own schema and the row the product path reads."""
    async with database.transaction() as conn, conn.cursor() as cur:
        await cur.execute(f"create schema if not exists {TEST_SCHEMA}")
        await cur.execute(
            f"""
            create table if not exists {TEST_SCHEMA}.{_TEST_TABLE} (
                view_id integer primary key,
                label text not null
            )
            """
        )
        await cur.execute(
            f"""
            insert into {TEST_SCHEMA}.{_TEST_TABLE} (view_id, label)
            values (1, 'tiktok-sizing-drop-off')
            on conflict (view_id) do nothing
            """
        )


def scoped_database_url(database_url: str) -> str:
    """Return the project URL with the session search path pinned to this schema."""
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-c%20search_path%3D{TEST_SCHEMA}"


def _run(command: list[str], *, cwd: Path) -> None:
    """Run one build or install step, failing with its output when it fails."""
    completed = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed in {cwd}:\n{completed.stdout}\n{completed.stderr}"
        )


def build_sdk() -> Path:
    """Build the published SDK and install the test app, and return the preload."""
    preload = _PACKAGE_ROOT / "dist" / "preload.js"
    if not (_PACKAGE_ROOT / "node_modules").is_dir():
        _run(["npm", "install", "--no-audit", "--no-fund"], cwd=_PACKAGE_ROOT)
    if not preload.exists():
        _run(["npm", "run", "build"], cwd=_PACKAGE_ROOT)
    if not (_APP_ROOT / "node_modules").is_dir():
        _run(["npm", "install", "--no-audit", "--no-fund"], cwd=_APP_ROOT)
    return preload


@dataclass(slots=True)
class SdkProcess:
    """One running product service with the SDK installed."""

    process: subprocess.Popen[str]
    stdout_path: Path
    stderr_path: Path

    def output(self) -> str:
        """Return everything the process has written, for a failure message."""
        return (
            self.stdout_path.read_text(encoding="utf-8")
            + self.stderr_path.read_text(encoding="utf-8")
        )

    def stop(self) -> None:
        """Terminate the process and wait for it to exit."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)


def start_service(
    *,
    preload: Path,
    control_origin: str,
    ingest_origin: str,
    bootstrap_token: str,
    trust_anchor: str,
    database_url: str,
    scratch: Path,
) -> SdkProcess:
    """Start the Express service under the SDK preload and wait for it to listen."""
    stdout_path = scratch / "app-stdout.log"
    stderr_path = scratch / "app-stderr.log"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "APP_PORT": str(APP_PORT),
        "APP_DATABASE_URL": scoped_database_url(database_url),
        "UTRACE_SERVER_BOOTSTRAP_CREDENTIAL": bootstrap_token,
        "UTRACE_SERVER_CONTROL_URL": control_origin,
        "UTRACE_SERVER_INGEST_URL": ingest_origin,
        "UTRACE_SERVER_SERVICE_NAME": SERVICE_KEY,
        "UTRACE_SERVER_MANIFEST_TRUST_ANCHOR": trust_anchor,
    }
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open(
        "w", encoding="utf-8"
    ) as err:
        process = subprocess.Popen(
            [
                "node",
                "--import",
                str(preload),
                str(_APP_ROOT / "app.cjs"),
            ],
            cwd=_APP_ROOT,
            env=environment,
            stdout=out,
            stderr=err,
            text=True,
        )
    running = SdkProcess(
        process=process, stdout_path=stdout_path, stderr_path=stderr_path
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if "APP_LISTENING" in stdout_path.read_text(encoding="utf-8"):
            return running
        if process.poll() is not None:
            raise RuntimeError(
                f"the product service exited before listening:\n{running.output()}"
            )
        time.sleep(0.1)
    running.stop()
    raise RuntimeError(f"the product service never listened:\n{running.output()}")
