import type {
  Activation,
  ActivationRequest,
  PreviewRouteRecord,
  SignedEnvelope,
  TelemetryCredential,
} from "@/lib/contracts";

/**
 * Shared values for the door suites: the configuration the door reads from the
 * environment, and a canonical route record and session credential to build cases from.
 */

export const serviceToken = "test-service-token-0123456789abcdef";
export const previewApex = "preview.localtest";
export const sessionCookieName = "__Host-utrace-preview-session";
export const sessionCredential =
  "Kd8s-2Ff4LmQ9tR1vXz3Yb7Nc5Pe0Wg6Hj4Ku2Sx8Tn1Vq7Zr3Bd5Mf9Cy2Lp6Aw";
export const routeContextSigningSecret =
  "Rt7Yh2Qm9Zx4Cb8Nv1Ks6Jd3Lf0Pw5Ag";
export const identities = {
  previewRouteId: "0192f3c4-d345-7b8d-806e-92a3b4c5d6e7",
  clientId: "0192f3c4-8a21-7c3e-9b6a-1d2e3f405162",
  interviewSessionId: "0192f3c4-9b10-7d4f-8c2a-5e6f70819243",
  productSignalId: "0192f3c4-a012-7e5a-9d3b-6f708192a3b4",
  previewVersionId: "0192f3c4-b123-7f6b-ae4c-708192a3b4c5",
  previewRuntimeAllocationId: "0192f3c4-c234-7a7c-bf5d-8192a3b4c5d6",
} as const;

export const previewEntryToken = "pe_9Rb4Nk8Jd3Lf0Pw5Ag1Ts6Yh2Qm7Zx";

/** The document nonce the browser SDK bootstrap mints for one activation. */
export const documentNonce = "b4f2c9a1d8e30576a7c1e2f30415a6b7";

/**
 * A signed envelope in the shape the control plane issues and the door relays
 * unchanged. The signature bytes are filler: only the browser SDK verifies
 * them, against its packaged public key.
 */
export function signedEnvelope(payload: Readonly<Record<string, string>>): SignedEnvelope {
  return {
    algorithm: "ed25519",
    key_id: "utrace-browser-2026-09-01",
    payload_b64url: Buffer.from(JSON.stringify(payload), "utf8").toString("base64url"),
    signature_b64url: "a".repeat(86),
  };
}

export const documentAttestation = signedEnvelope({
  attestation_id: identities.previewRouteId,
});
export const activationResponse: Activation = {
  attestation: documentAttestation,
  annotation_manifest: signedEnvelope({ manifest_version: "2026.09.20-1" }),
  page_proof: signedEnvelope({ document_nonce: documentNonce }),
};
export const telemetryCredential: TelemetryCredential = {
  credential: {
    token: "x7Qm2Zv9Rb4Nk8Jd3Lf0Pw5Ag1Ts6Yh",
    expires_at: "2126-09-20T17:09:00Z",
    ingest_url: "wss://ingest.localtest/v1/ingest/browser",
  },
  limits: {
    max_observation_bytes: 65_536,
    max_batch_bytes: 262_144,
    max_bytes_per_second: 1_000_000,
    max_records_per_second: 500,
    max_in_flight_batches: 4,
    acknowledgement_lag_limit_ms: 5_000,
  },
};

/** The activation request the browser SDK bootstrap posts. */
export const activationRequest: ActivationRequest = {
  sdk_installation_id: "shopalytics-preview",
  environment_name: "preview",
  client_release_id: "shopalytics-main-8f21",
  schema_version: "1",
  protocol_version: "1",
  document_nonce: documentNonce,
  document_origin: "https://k7m2q9x4v1z8.preview.localtest",
};


/** Install the door's environment for one suite. */
export function configureDoor(params: {
  controlPlaneBaseUrl: string;
  applicationPort: number;
  routeContextTtlSeconds: number;
  controlPlaneAttempts: number;
}): void {
  process.env.UTRACE_CONTROL_PLANE_BASE_URL = params.controlPlaneBaseUrl;
  process.env.UTRACE_CONTROL_PLANE_SERVICE_TOKEN = serviceToken;
  process.env.UTRACE_CONTROL_PLANE_TIMEOUT_MS = "2000";
  process.env.UTRACE_CONTROL_PLANE_ATTEMPTS = String(
    params.controlPlaneAttempts,
  );
  process.env.UTRACE_PREVIEW_APEX = previewApex;
  process.env.UTRACE_SESSION_COOKIE_NAME = sessionCookieName;
  process.env.UTRACE_SESSION_COOKIE_MAX_AGE_SECONDS = "3600";
  process.env.UTRACE_UPSTREAM_APPLICATION_PORT = String(
    params.applicationPort,
  );
  process.env.UTRACE_ROUTE_CONTEXT_TTL_SECONDS = String(
    params.routeContextTtlSeconds,
  );
}

/** A route record for one session origin, ready to be adjusted per case. */
export function routeRecord(params: {
  publicPreviewHost: string;
  upstreamOrigin: string;
  applicationPort: number;
  routeRevision: number;
  readinessState: "ready" | "reconnecting";
  expiresAt: Date;
}): PreviewRouteRecord {
  return {
    schema_version: "utrace_preview_route_record_v1",
    preview_route_id: identities.previewRouteId,
    client_id: identities.clientId,
    interview_session_id: identities.interviewSessionId,
    product_signal_id: identities.productSignalId,
    preview_version_id: identities.previewVersionId,
    preview_runtime_allocation_id: identities.previewRuntimeAllocationId,
    public_preview_host: params.publicPreviewHost,
    route_revision: params.routeRevision,
    readiness_state: params.readinessState,
    upstream: {
      port_url: params.upstreamOrigin,
      application_port: params.applicationPort,
    },
    preview_entry_token: previewEntryToken,
    route_context_signing_secret: routeContextSigningSecret,
    expires_at: params.expiresAt.toISOString(),
  };
}

/** The `Cookie` header a claimed browser sends back. */
export function sessionCookieHeader(credential: string): string {
  return `${sessionCookieName}=${credential}`;
}
