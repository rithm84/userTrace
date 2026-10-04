/**
 * Short-lived, write-only browser telemetry credential.
 *
 * The credential is held in a private field for the lifetime of one activation.
 * It is never placed in a URL, browser storage, page markup, or an
 * application-visible global, and it is never serialized: `toJSON` throws so an
 * accidental `JSON.stringify` of runtime state cannot leak it.
 */

import { ActivationError } from "../core/failure.js";
import type { JsonObject } from "../core/json.js";
import { requireInteger, requireObject, requireString } from "../core/json.js";
import { clampLimit, MAX_BATCH_BYTES, MAX_OBSERVATION_BYTES } from "../core/limits.js";
import { isExpired } from "../core/time.js";
import { INGEST_CREDENTIAL_SUBPROTOCOL_PREFIX } from "../core/versions.js";

/** Operational limits negotiated at activation, clamped to platform ceilings. */
export interface NegotiatedLimits {
  readonly max_observation_bytes: number;
  readonly max_batch_bytes: number;
  readonly max_bytes_per_second: number;
  readonly max_records_per_second: number;
  readonly max_in_flight_batches: number;
  readonly acknowledgement_lag_limit_ms: number;
}

const decodeLimits = (source: JsonObject, path: string): NegotiatedLimits => ({
  max_observation_bytes: clampLimit(requireInteger(source, "max_observation_bytes", path), MAX_OBSERVATION_BYTES),
  max_batch_bytes: clampLimit(requireInteger(source, "max_batch_bytes", path), MAX_BATCH_BYTES),
  max_bytes_per_second: requireInteger(source, "max_bytes_per_second", path),
  max_records_per_second: requireInteger(source, "max_records_per_second", path),
  max_in_flight_batches: requireInteger(source, "max_in_flight_batches", path),
  acknowledgement_lag_limit_ms: requireInteger(source, "acknowledgement_lag_limit_ms", path),
});

const SUBPROTOCOL_TOKEN = /^[A-Za-z0-9!#$%&'*+.^_`|~-]+$/;

/**
 * The one browser telemetry ingestion endpoint. The server-issued URL's origin
 * and any base path prefix vary; its path always ends with this.
 */
export const INGEST_PATH = "/v1/ingest/browser";

const assertIngestUrl = (ingestUrl: string): string => {
  let parsed: URL;
  try {
    parsed = new URL(ingestUrl);
  } catch {
    throw new ActivationError("credential_rejected", "ingest_url is not a URL", { ingest_url_length: ingestUrl.length });
  }
  if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") {
    throw new ActivationError("credential_rejected", "ingest_url is not a WebSocket URL", {
      protocol: parsed.protocol,
    });
  }
  if (!parsed.pathname.endsWith(INGEST_PATH)) {
    throw new ActivationError("credential_rejected", "ingest_url is not the browser ingestion endpoint", {
      expected: INGEST_PATH,
      received: parsed.pathname,
    });
  }
  if (parsed.search.length > 0 || parsed.hash.length > 0) {
    throw new ActivationError("credential_rejected", "ingest_url must carry no query or fragment", {
      ingest_url_length: ingestUrl.length,
    });
  }
  return ingestUrl;
};

export class TelemetryCredential {
  readonly #token: string;
  readonly expiresAt: string;
  readonly ingestUrl: string;
  readonly limits: NegotiatedLimits;

  constructor(token: string, expiresAt: string, ingestUrl: string, limits: NegotiatedLimits) {
    if (!SUBPROTOCOL_TOKEN.test(token)) {
      throw new ActivationError("credential_rejected", "credential is not a valid WebSocket subprotocol token", {
        token_length: token.length,
      });
    }
    this.#token = token;
    this.expiresAt = expiresAt;
    this.ingestUrl = assertIngestUrl(ingestUrl);
    this.limits = limits;
  }

  expired(nowMs: number): boolean {
    return isExpired(this.expiresAt, nowMs);
  }

  /**
   * The credential travels as a WebSocket subprotocol value, which Chrome sends
   * in the `Sec-WebSocket-Protocol` request header. The browser WebSocket API
   * cannot set other request headers, and the URL is not an allowed carrier.
   */
  subprotocolValue(): string {
    return `${INGEST_CREDENTIAL_SUBPROTOCOL_PREFIX}${this.#token}`;
  }

  toJSON(): never {
    throw new Error("uTrace telemetry credential is write-only and must not be serialized");
  }

  toString(): string {
    return "[uTrace telemetry credential]";
  }
}

export const decodeCredential = (body: JsonObject): TelemetryCredential => {
  const path = "telemetry_credential";
  const credential = requireObject(body, "credential", path);
  return new TelemetryCredential(
    requireString(credential, "token", `${path}.credential`),
    requireString(credential, "expires_at", `${path}.credential`),
    requireString(credential, "ingest_url", `${path}.credential`),
    decodeLimits(requireObject(body, "limits", path), `${path}.limits`),
  );
};
