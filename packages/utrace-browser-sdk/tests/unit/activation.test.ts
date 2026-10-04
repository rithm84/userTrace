/**
 * Attestation binding checks and the write-only telemetry credential.
 */

import { describe, expect, it } from "vitest";
import { assertAttestationBinding, assertManifestPinned, decodeAttestation } from "../../src/activation/attestation.js";
import { decodeCredential, INGEST_PATH, TelemetryCredential } from "../../src/activation/credential.js";
import { ACTIVATION_REQUEST_TIMEOUT_MS } from "../../src/activation/gate.js";
import { PACKAGED_SIGNING_KEYS, findPackagedKey } from "../../src/activation/signing-keys.js";
import type { JsonObject } from "../../src/core/json.js";
import { rfc3339 } from "../../src/core/time.js";

const NOW = Date.parse("2026-10-03T09:00:00.000Z");

const config = {
  sdkInstallationId: "shopalytics-preview",
  environmentName: "preview",
  clientReleaseId: "main-8f21",
  schemaVersion: "1",
  protocolVersion: "1",
};

const payload = (overrides: JsonObject): JsonObject => ({
  attestation_id: "a-1",
  client_id: "c-1",
  client_user_id: "u-1",
  customer_account_id: "acct-1",
  session_id: "s-1",
  preview_environment_id: "env-1",
  preview_runtime_allocation_id: "alloc-1",
  preview_version_id: null,
  document_attachment_id: "doc-1",
  origin: "https://ab12cd.preview.utrace.dev",
  document_nonce: "nonce-1",
  sdk_installation_id: "shopalytics-preview",
  protocol_version: "1",
  schema_version: "1",
  annotation_manifest_version: "2026.09.20-1",
  annotation_manifest_digest: `sha256:${"a".repeat(64)}`,
  redaction_version: "r-1",
  policy_version: "p-1",
  issued_at: rfc3339(NOW),
  expires_at: rfc3339(NOW + 900_000),
  ...overrides,
});

const binding = {
  config,
  documentOrigin: "https://ab12cd.preview.utrace.dev",
  documentNonce: "nonce-1",
  nowMs: NOW,
};

describe("packaged trust anchor", () => {
  it("ships only public keys and resolves by key id", () => {
    expect(PACKAGED_SIGNING_KEYS.length).toBeGreaterThan(0);
    for (const key of PACKAGED_SIGNING_KEYS) {
      expect(key.algorithm).toBe("ed25519");
      expect(key.public_key_b64url).toMatch(/^[A-Za-z0-9_-]{43}$/);
    }
    expect(findPackagedKey("a-key-uTrace-does-not-hold")).toBeNull();
    expect(findPackagedKey(PACKAGED_SIGNING_KEYS[0]?.key_id ?? "")).not.toBeNull();
  });
});

describe("attestation binding", () => {
  it("accepts an attestation that binds this exact document and build", () => {
    expect(() => {
      assertAttestationBinding(decodeAttestation(payload({})), binding);
    }).not.toThrow();
  });

  it("rejects another origin, another nonce, and another installation", () => {
    expect(() => {
      assertAttestationBinding(decodeAttestation(payload({ origin: "https://elsewhere.example" })), binding);
    }).toThrow(/origin does not bind this document/);
    expect(() => {
      assertAttestationBinding(decodeAttestation(payload({ document_nonce: "another" })), binding);
    }).toThrow(/document_nonce does not bind this document/);
    expect(() => {
      assertAttestationBinding(decodeAttestation(payload({ sdk_installation_id: "another-app" })), binding);
    }).toThrow(/sdk_installation_id does not bind this document/);
  });

  it("rejects an incompatible protocol or schema with no substitution", () => {
    expect(() => {
      assertAttestationBinding(decodeAttestation(payload({ protocol_version: "2" })), binding);
    }).toThrow(/incompatible_protocol_or_schema/);
    expect(() => {
      assertAttestationBinding(decodeAttestation(payload({ schema_version: "9" })), binding);
    }).toThrow(/incompatible_protocol_or_schema/);
  });

  it("rejects an expired attestation", () => {
    expect(() => {
      assertAttestationBinding(decodeAttestation(payload({ expires_at: rfc3339(NOW - 1) })), binding);
    }).toThrow(/attestation is expired/);
  });

  it("requires the manifest the attestation pinned", () => {
    const attestation = decodeAttestation(payload({}));
    expect(() => {
      assertManifestPinned(attestation, "2026.09.20-1", `sha256:${"a".repeat(64)}`);
    }).not.toThrow();
    expect(() => {
      assertManifestPinned(attestation, "2026.09.19-4", `sha256:${"a".repeat(64)}`);
    }).toThrow(/manifest version is not the pinned one/);
    expect(() => {
      assertManifestPinned(attestation, "2026.09.20-1", `sha256:${"b".repeat(64)}`);
    }).toThrow(/manifest digest is not the pinned one/);
  });

  it("rejects a payload that is missing a required claim", () => {
    const incomplete = Object.fromEntries(
      Object.entries(payload({})).filter(([key]) => key !== "client_id"),
    );
    expect(() => decodeAttestation(incomplete)).toThrow(/client_id/);
  });
});

describe("telemetry credential", () => {
  const limits = {
    max_observation_bytes: 262144,
    max_batch_bytes: 1048576,
    max_bytes_per_second: 1_000_000,
    max_records_per_second: 500,
    max_in_flight_batches: 4,
    acknowledgement_lag_limit_ms: 5000,
  };

  it("bounds the activation gate so a hung front door cannot hold hydration", () => {
    expect(ACTIVATION_REQUEST_TIMEOUT_MS).toBeGreaterThan(0);
    expect(ACTIVATION_REQUEST_TIMEOUT_MS).toBeLessThanOrEqual(30_000);
  });

  it("accepts only the documented browser ingestion endpoint", () => {
    expect(INGEST_PATH).toBe("/v1/ingest/browser");
    expect(
      new TelemetryCredential("token123", rfc3339(NOW + 60_000), `wss://ingest.utrace.dev${INGEST_PATH}`, limits)
        .ingestUrl,
    ).toContain(INGEST_PATH);
    expect(() => new TelemetryCredential("token123", rfc3339(NOW), "https://ingest.utrace.dev/v1/ingest/browser", limits)).toThrow(
      /not a WebSocket URL/,
    );
    expect(() => new TelemetryCredential("token123", rfc3339(NOW), "wss://ingest.utrace.dev/other", limits)).toThrow(
      /not the browser ingestion endpoint/,
    );
    expect(
      () => new TelemetryCredential("token123", rfc3339(NOW), `wss://ingest.utrace.dev${INGEST_PATH}/extra`, limits),
    ).toThrow(/not the browser ingestion endpoint/);
    expect(
      () => new TelemetryCredential("token123", rfc3339(NOW), `wss://ingest.utrace.dev${INGEST_PATH}?token=x`, limits),
    ).toThrow(/no query or fragment/);
  });

  it("accepts the ingestion endpoint under a base URL path prefix", () => {
    const ingestUrl = `wss://api.utrace.dev/compute/v1/api${INGEST_PATH}`;
    expect(new TelemetryCredential("token123", rfc3339(NOW + 60_000), ingestUrl, limits).ingestUrl).toBe(ingestUrl);
  });

  it("refuses a credential that cannot travel as a subprotocol token", () => {
    expect(
      () => new TelemetryCredential("has spaces", rfc3339(NOW), `wss://ingest.utrace.dev${INGEST_PATH}`, limits),
    ).toThrow(/valid WebSocket subprotocol token/);
  });

  it("never serializes and reports expiry", () => {
    const credential = new TelemetryCredential(
      "token123",
      rfc3339(NOW + 60_000),
      `wss://ingest.utrace.dev${INGEST_PATH}`,
      limits,
    );
    expect(() => JSON.stringify({ credential })).toThrow(/write-only/);
    expect(String(credential)).toBe("[uTrace telemetry credential]");
    expect(credential.subprotocolValue()).toBe("utrace.credential.token123");
    expect(credential.expired(NOW)).toBe(false);
    expect(credential.expired(NOW + 120_000)).toBe(true);
  });

  it("clamps a negotiated limit that exceeds a platform ceiling", () => {
    const credential = decodeCredential({
      credential: {
        token: "token123",
        expires_at: rfc3339(NOW + 60_000),
        ingest_url: `wss://ingest.utrace.dev${INGEST_PATH}`,
      },
      limits: { ...limits, max_observation_bytes: 99_999_999, max_batch_bytes: 99_999_999 },
    });
    expect(credential.limits.max_observation_bytes).toBe(262144);
    expect(credential.limits.max_batch_bytes).toBe(1048576);
  });
});
