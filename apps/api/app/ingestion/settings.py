"""Ingestion configuration.

These values are read from the process environment like the rest of the control
plane's configuration, but they live in this module rather than in
``app.settings`` so the ingestion workstream does not add required variables to
every other workstream's start-up while the branches are still separate. Merging
them into ``Settings`` is a one-line move per field.

Every value is required. An unset variable fails with the variable's name; the
value itself is never echoed, because two of them are private keys.
"""

from base64 import urlsafe_b64decode
from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.settings import ConfigurationError, _offending_variables

__all__ = ["IngestionSettings", "get_ingestion_settings"]


class IngestionSettings(BaseSettings):
    """Configuration the telemetry ingestion edges and evidence stores need."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # Raw 32-byte Ed25519 seed, base64url without padding. The browser SDK ships
    # only the matching public half.
    attestation_signing_key: str = Field(
        validation_alias="UTRACE_ATTESTATION_SIGNING_KEY"
    )
    attestation_signing_key_id: str = Field(
        validation_alias="UTRACE_ATTESTATION_SIGNING_KEY_ID"
    )
    # Raw 32-byte Ed25519 public key, base64url without padding. The server SDK
    # verifies signed instrumentation manifests against it.
    server_manifest_trust_anchor: str = Field(
        validation_alias="UTRACE_SERVER_MANIFEST_TRUST_ANCHOR"
    )
    server_manifest_trust_anchor_key_id: str = Field(
        validation_alias="UTRACE_SERVER_MANIFEST_TRUST_ANCHOR_KEY_ID"
    )

    # Public base URL of the telemetry ingestion edge; it may include a path,
    # for example ``wss://api.utrace.dev/compute/v1/api``. The browser
    # credential response carries this base URL plus the one fixed ingestion
    # path.
    ingest_websocket_origin: str = Field(
        validation_alias="UTRACE_INGEST_WEBSOCKET_ORIGIN"
    )

    # Private Supabase Storage bucket holding raw batches, large baselines, and
    # promoted evidence artifacts.
    telemetry_bucket: str = Field(validation_alias="UTRACE_TELEMETRY_BUCKET")

    def attestation_signing_seed(self) -> bytes:
        """Return the raw Ed25519 seed, or refuse with the variable named."""
        return _decode_key(
            self.attestation_signing_key, "UTRACE_ATTESTATION_SIGNING_KEY"
        )

    def manifest_trust_anchor_key(self) -> bytes:
        """Return the raw Ed25519 public key the server manifest is checked against."""
        return _decode_key(
            self.server_manifest_trust_anchor, "UTRACE_SERVER_MANIFEST_TRUST_ANCHOR"
        )


def _decode_key(value: str, variable: str) -> bytes:
    """Decode one unpadded base64url 32-byte key without echoing its value."""
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = urlsafe_b64decode(padded)
    except ValueError:
        raise ConfigurationError((variable,)) from None
    if len(raw) != 32:
        raise ConfigurationError((variable,))
    return raw


@lru_cache(maxsize=1)
def get_ingestion_settings() -> IngestionSettings:
    """Return the process-wide ingestion configuration, read once."""
    try:
        return IngestionSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigurationError(_offending_variables(error)) from None
