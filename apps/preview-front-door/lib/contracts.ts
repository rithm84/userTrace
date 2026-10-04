import { z } from "zod";

/**
 * Runtime validation for every message the preview front door sends or accepts.
 *
 * These schemas mirror the JSON Schemas in `packages/utrace-contracts/schemas/preview/`
 * one field at a time. The generated bindings in `@utrace/contracts` do not cover the
 * `preview` area yet, so the door carries its own zod mirrors and the shared fixtures
 * are replayed against them in `tests/contracts.test.ts`.
 */

const rfc3339 = z.iso.datetime({ offset: true });
const opaqueCredential = z.string().regex(/^[A-Za-z0-9._~-]{32,2048}$/);
const signedToken = z.string().regex(/^[A-Za-z0-9._~+/=-]{32,8192}$/);
const previewHost = z
  .string()
  .regex(
    /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/,
    "must be a lowercase host with at least two labels",
  );

/** One-time handoff code carried in `/__utrace/claim?code=`. */
export const handoffCodeSchema = z.string().regex(/^[A-Za-z0-9._~-]{24,512}$/);

/** Document nonce minted by the browser SDK bootstrap for one document activation. */
export const documentNonceSchema = z.string().regex(/^[A-Za-z0-9_-]{16,128}$/);

/** Reasons the control plane may refuse a handoff exchange, a route, or a relay. */
export const previewDenialReasonSchema = z.enum([
  "unknown_host",
  "unknown_handoff_code",
  "handoff_consumed",
  "handoff_expired",
  "host_mismatch",
  "unauthorized_session",
  "session_ended",
  "route_expired",
  "activation_rejected",
]);
export type PreviewDenialReason = z.infer<typeof previewDenialReasonSchema>;

export const previewDenialSchema = z
  .object({
    schema_version: z.literal("utrace_preview_denial_v1"),
    reason: previewDenialReasonSchema,
  })
  .strict();
export type PreviewDenial = z.infer<typeof previewDenialSchema>;

export const handoffExchangeRequestSchema = z
  .object({
    schema_version: z.literal("utrace_preview_handoff_exchange_request_v1"),
    public_preview_host: previewHost,
  })
  .strict();
export type HandoffExchangeRequest = z.infer<
  typeof handoffExchangeRequestSchema
>;

export const handoffExchangeSchema = z
  .object({
    schema_version: z.literal("utrace_preview_handoff_exchange_v1"),
    public_preview_host: previewHost,
    client_id: z.uuid(),
    interview_session_id: z.uuid(),
    session_credential: z
      .object({ value: opaqueCredential, expires_at: rfc3339 })
      .strict(),
  })
  .strict();
export type HandoffExchange = z.infer<typeof handoffExchangeSchema>;

/** Same-origin path: a single leading slash, so it can never name another host. */
const sameOriginPath = z
  .string()
  .max(256)
  .regex(/^\/(?!\/)[A-Za-z0-9._~/-]*$/, "must be a same-origin path");

export const productSignInSchema = z
  .object({
    schema_version: z.literal("utrace_preview_product_sign_in_v1"),
    path: sameOriginPath,
    token: z
      .string()
      .max(4096)
      .regex(/^v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/),
  })
  .strict();
export type ProductSignIn = z.infer<typeof productSignInSchema>;

/**
 * The product's answer to a successful sign-in. The pinned template owns this
 * endpoint; the door reads only where the product lands the signed-in user.
 */
export const productSignedInSchema = z.object({
  redirect_to: sameOriginPath,
});

export const previewRouteRecordSchema = z
  .object({
    schema_version: z.literal("utrace_preview_route_record_v1"),
    preview_route_id: z.uuid(),
    client_id: z.uuid(),
    interview_session_id: z.uuid(),
    product_signal_id: z.uuid(),
    preview_version_id: z.uuid(),
    preview_runtime_allocation_id: z.uuid(),
    public_preview_host: previewHost,
    route_revision: z.int().positive(),
    readiness_state: z.enum(["ready", "reconnecting"]),
    upstream: z
      .object({
        port_url: z.url({ protocol: /^https?$/ }),
        application_port: z.int().min(1).max(65_535),
      })
      .strict(),
    /**
     * Activation-scoped token the door places in `x-utrace-preview-entry` on
     * every forwarded request. The Server SDK accepts a remote `traceparent`
     * as a parent only when it matches, so a syntactically valid one that did
     * not enter through the door never becomes a parent. It is never
     * distributed to a browser.
     */
    preview_entry_token: z.string().regex(/^[A-Za-z0-9._~-]{16,512}$/),
    route_context_signing_secret: signedToken,
    expires_at: rfc3339,
  })
  .strict();
export type PreviewRouteRecord = z.infer<typeof previewRouteRecordSchema>;

/**
 * Claims of the `x-utrace-route-context` token. The shape, the `v1.<payload>.<signature>`
 * envelope, and the HMAC-SHA256 signature are fixed by the runtime-side verifier that
 * client previews run (`utrace-route-context.ts` in the product repository).
 */
export const routeContextClaimsSchema = z
  .object({
    schema_version: z.literal("utrace_preview_route_v1"),
    preview_route_id: z.uuid(),
    client_id: z.uuid(),
    product_signal_id: z.uuid(),
    preview_version_id: z.uuid(),
    preview_runtime_allocation_id: z.uuid(),
    public_preview_host: z.string().min(1),
    route_revision: z.int().positive(),
    issued_at: rfc3339,
    expires_at: rfc3339,
  })
  .strict();
export type RouteContextClaims = z.infer<typeof routeContextClaimsSchema>;

/**
 * A detached Ed25519 signature over a base64url JSON payload. The browser SDK
 * verifies it with a packaged public key; the door only carries it.
 */
export const signedEnvelopeSchema = z
  .object({
    algorithm: z.literal("ed25519"),
    key_id: z.string().regex(/^[A-Za-z0-9_.:-]{1,128}$/),
    payload_b64url: z.string().regex(/^[A-Za-z0-9_-]+$/),
    signature_b64url: z.string().regex(/^[A-Za-z0-9_-]{86}$/),
  })
  .strict();
export type SignedEnvelope = z.infer<typeof signedEnvelopeSchema>;

/**
 * The browser SDK's own activation request. Its `schema_version` is the
 * browser observation schema major the SDK speaks, not a preview envelope
 * discriminator, because the browser SDK owns this message.
 */
export const activationRequestSchema = z
  .object({
    sdk_installation_id: z.string().min(1).max(128),
    environment_name: z.string().min(1).max(64),
    client_release_id: z.string().min(1).max(128),
    schema_version: z.string().regex(/^\d{1,3}$/),
    protocol_version: z.string().regex(/^\d{1,3}$/),
    document_nonce: documentNonceSchema,
    document_origin: z.string().regex(/^https?:\/\/[^/?#]+$/),
    /**
     * The browser observation schema major the runtime speaks, when the SDK
     * pins it separately from its own. The attestation binds it, so the door
     * forwards it; an SDK that does not send it is answered from
     * `schema_version` alone.
     */
    observation_schema_version: z.string().regex(/^\d{1,3}$/).optional(),
  })
  .strict();
export type ActivationRequest = z.infer<typeof activationRequestSchema>;

export const activationRelayRequestSchema = z
  .object({
    schema_version: z.literal("utrace_preview_activation_relay_request_v1"),
    public_preview_host: previewHost,
    activation_request: activationRequestSchema,
  })
  .strict();
export type ActivationRelayRequest = z.infer<
  typeof activationRelayRequestSchema
>;

/**
 * The control plane's answer, returned to the browser unchanged. It is the
 * browser-observation activation response: three signed envelopes the SDK
 * verifies itself. The door never mints, inspects, or caches them.
 */
export const activationSchema = z
  .object({
    attestation: signedEnvelopeSchema,
    annotation_manifest: signedEnvelopeSchema,
    page_proof: signedEnvelopeSchema,
  })
  .strict();
export type Activation = z.infer<typeof activationSchema>;

export const telemetryCredentialRequestSchema = z
  .object({
    attestation: signedEnvelopeSchema,
    document_nonce: documentNonceSchema,
  })
  .strict();
export type TelemetryCredentialRequest = z.infer<
  typeof telemetryCredentialRequestSchema
>;

export const telemetryCredentialRelayRequestSchema = z
  .object({
    schema_version: z.literal(
      "utrace_preview_telemetry_credential_relay_request_v1",
    ),
    public_preview_host: previewHost,
    credential_request: telemetryCredentialRequestSchema,
  })
  .strict();
export type TelemetryCredentialRelayRequest = z.infer<
  typeof telemetryCredentialRelayRequestSchema
>;

/**
 * The short-lived, write-only browser telemetry credential and the one
 * ingestion endpoint it is good for, with the negotiated limits. The door
 * passes all of it through untouched.
 */
export const telemetryCredentialSchema = z
  .object({
    credential: z
      .object({
        token: z.string().regex(/^[A-Za-z0-9!#$%&'*+.^_`|~-]+$/),
        expires_at: rfc3339,
        ingest_url: z.url({ protocol: /^wss?$/ }),
      })
      .strict(),
    limits: z
      .object({
        max_observation_bytes: z.int().min(1).max(262_144),
        max_batch_bytes: z.int().min(1).max(1_048_576),
        max_bytes_per_second: z.int().min(1),
        max_records_per_second: z.int().min(1),
        max_in_flight_batches: z.int().min(1),
        acknowledgement_lag_limit_ms: z.int().min(1),
      })
      .strict(),
  })
  .strict();
export type TelemetryCredential = z.infer<typeof telemetryCredentialSchema>;

/**
 * The trace propagation probe body. It carries no product data: the probe's
 * whole content is the `traceparent` the browser's own injection path put on
 * the request, which the door reads from the headers.
 */
export const propagationProbeRequestSchema = z
  .object({ probe: z.literal("trace_propagation") })
  .strict();
export type PropagationProbeRequest = z.infer<
  typeof propagationProbeRequestSchema
>;

/** Body the door returns to the browser SDK when a relay is refused. */
export const relayFailureSchema = z
  .object({
    schema_version: z.literal("utrace_preview_relay_failure_v1"),
    reason: z.enum(["unauthorized", "invalid_request", "unavailable"]),
  })
  .strict();
export type RelayFailure = z.infer<typeof relayFailureSchema>;
