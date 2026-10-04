import type { z } from "zod";

import {
  activationSchema,
  handoffExchangeSchema,
  previewDenialSchema,
  previewRouteRecordSchema,
  productSignInSchema,
  telemetryCredentialSchema,
  type Activation,
  type ActivationRequest,
  type HandoffExchange,
  type PreviewDenialReason,
  type PreviewRouteRecord,
  type ProductSignIn,
  type TelemetryCredential,
  type TelemetryCredentialRequest,
} from "./contracts";
import {
  ControlPlaneContractError,
  ControlPlaneUnavailableError,
} from "./errors";
import { logDoorWarning } from "./log";

/**
 * Connector to the uTrace control plane.
 *
 * The control plane is the authority on handoff codes, session credentials, and route
 * mappings; the door only presents evidence and enforces the answer. Every call carries
 * the door's service token, disables caching, refuses redirects, retries transport and
 * server failures with a warning before raising the last one, and validates the response
 * against its published contract. A response that does not match the contract is a
 * failure, never a partially trusted result.
 */

/** A control-plane answer: the contract-valid value, or the typed reason it was refused. */
export type ControlPlaneOutcome<T> =
  | { readonly outcome: "ok"; readonly value: T }
  | { readonly outcome: "denied"; readonly reason: PreviewDenialReason };

const deniableStatuses: ReadonlySet<number> = new Set([
  400, 401, 403, 404, 409, 410,
]);

const retryableStatuses: ReadonlySet<number> = new Set([
  408, 425, 429, 500, 502, 503, 504,
]);

const backoffStepMs = 100;

const delay = (milliseconds: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });

interface ControlPlaneRequest<T> {
  readonly endpoint: string;
  readonly path: string;
  readonly method: "GET" | "POST";
  readonly body: unknown | null;
  readonly sessionCredential: string | null;
  readonly schema: z.ZodType<T>;
}

export class ControlPlaneClient {
  readonly #baseUrl: string;
  readonly #serviceToken: string;
  readonly #timeoutMs: number;
  readonly #attempts: number;

  constructor(params: {
    baseUrl: string;
    serviceToken: string;
    timeoutMs: number;
    attempts: number;
  }) {
    this.#baseUrl = params.baseUrl.replace(/\/+$/, "");
    this.#serviceToken = params.serviceToken;
    this.#timeoutMs = params.timeoutMs;
    this.#attempts = params.attempts;
  }

  /** Exchange a one-time front-door handoff code for a session credential. */
  async exchangeHandoff(params: {
    code: string;
    publicPreviewHost: string;
  }): Promise<ControlPlaneOutcome<HandoffExchange>> {
    return this.#send({
      endpoint: "POST /v1/preview/handoffs/{code}/exchange",
      path: `/v1/preview/handoffs/${encodeURIComponent(params.code)}/exchange`,
      method: "POST",
      body: {
        schema_version: "utrace_preview_handoff_exchange_request_v1",
        public_preview_host: params.publicPreviewHost,
      },
      sessionCredential: null,
      schema: handoffExchangeSchema,
    });
  }

  /** Resolve the committed route for one session origin, authorised by the session credential. */
  async resolveRoute(params: {
    publicPreviewHost: string;
    sessionCredential: string;
  }): Promise<ControlPlaneOutcome<PreviewRouteRecord>> {
    return this.#send({
      endpoint: "GET /v1/preview/routes/{host}",
      path: `/v1/preview/routes/${encodeURIComponent(params.publicPreviewHost)}`,
      method: "GET",
      body: null,
      sessionCredential: params.sessionCredential,
      schema: previewRouteRecordSchema,
    });
  }

  /** Mint the one-time token that signs this session's participant into the routed product. */
  async mintProductSignIn(params: {
    publicPreviewHost: string;
    sessionCredential: string;
  }): Promise<ControlPlaneOutcome<ProductSignIn>> {
    return this.#send({
      endpoint: "POST /v1/preview/routes/{host}/product-sign-in",
      path: `/v1/preview/routes/${encodeURIComponent(params.publicPreviewHost)}/product-sign-in`,
      method: "POST",
      body: null,
      sessionCredential: params.sessionCredential,
      schema: productSignInSchema,
    });
  }

  /**
   * Relay a browser SDK document activation. The door adds the session origin
   * it resolved and forwards the browser's own request untouched; the answer
   * goes back to the browser exactly as the control plane wrote it.
   */
  async relayActivation(params: {
    publicPreviewHost: string;
    sessionCredential: string;
    activationRequest: ActivationRequest;
  }): Promise<ControlPlaneOutcome<Activation>> {
    return this.#send({
      endpoint: "POST /v1/preview/activations",
      path: "/v1/preview/activations",
      method: "POST",
      body: {
        schema_version: "utrace_preview_activation_relay_request_v1",
        public_preview_host: params.publicPreviewHost,
        activation_request: params.activationRequest,
      },
      sessionCredential: params.sessionCredential,
      schema: activationSchema,
    });
  }

  /** Relay the one-time exchange of a document attestation for a browser telemetry credential. */
  async relayTelemetryCredential(params: {
    publicPreviewHost: string;
    sessionCredential: string;
    credentialRequest: TelemetryCredentialRequest;
  }): Promise<ControlPlaneOutcome<TelemetryCredential>> {
    return this.#send({
      endpoint: "POST /v1/preview/telemetry-credentials",
      path: "/v1/preview/telemetry-credentials",
      method: "POST",
      body: {
        schema_version:
          "utrace_preview_telemetry_credential_relay_request_v1",
        public_preview_host: params.publicPreviewHost,
        credential_request: params.credentialRequest,
      },
      sessionCredential: params.sessionCredential,
      schema: telemetryCredentialSchema,
    });
  }

  async #send<T>(
    request: ControlPlaneRequest<T>,
  ): Promise<ControlPlaneOutcome<T>> {
    let lastFailure: unknown = null;
    for (let attempt = 1; attempt <= this.#attempts; attempt += 1) {
      try {
        const response = await this.#fetchOnce(request);
        if (retryableStatuses.has(response.status)) {
          lastFailure = new Error(
            `control plane returned a retryable status: endpoint=${request.endpoint} status=${response.status}`,
          );
          logDoorWarning({
            reason: "control_plane_retryable_status",
            endpoint: request.endpoint,
            attempt,
            attempts: this.#attempts,
            status: response.status,
          });
        } else {
          return await this.#readOutcome({ request, response });
        }
      } catch (cause) {
        if (cause instanceof ControlPlaneContractError) {
          throw cause;
        }
        lastFailure = cause;
        logDoorWarning({
          reason: "control_plane_transport_failure",
          endpoint: request.endpoint,
          attempt,
          attempts: this.#attempts,
        });
      }
      if (attempt < this.#attempts) {
        await delay(attempt * backoffStepMs);
      }
    }
    throw new ControlPlaneUnavailableError({
      endpoint: request.endpoint,
      attempts: this.#attempts,
      cause: lastFailure,
    });
  }

  async #fetchOnce<T>(request: ControlPlaneRequest<T>): Promise<Response> {
    const headers = new Headers({
      accept: "application/json",
      authorization: `Bearer ${this.#serviceToken}`,
    });
    if (request.sessionCredential !== null) {
      headers.set("x-utrace-session-credential", request.sessionCredential);
    }
    if (request.body !== null) {
      headers.set("content-type", "application/json");
    }
    return fetch(`${this.#baseUrl}${request.path}`, {
      method: request.method,
      headers,
      body: request.body === null ? null : JSON.stringify(request.body),
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(this.#timeoutMs),
    });
  }

  async #readOutcome<T>(params: {
    request: ControlPlaneRequest<T>;
    response: Response;
  }): Promise<ControlPlaneOutcome<T>> {
    const payload: unknown = await params.response.json().catch(() => null);
    if (params.response.status === 200) {
      const parsed = params.request.schema.safeParse(payload);
      if (!parsed.success) {
        throw new ControlPlaneContractError({
          endpoint: params.request.endpoint,
          status: params.response.status,
          issues: parsed.error.issues.map(
            (issue) => `${issue.path.join(".")}: ${issue.message}`,
          ),
        });
      }
      return { outcome: "ok", value: parsed.data };
    }
    if (deniableStatuses.has(params.response.status)) {
      const denial = previewDenialSchema.safeParse(payload);
      if (!denial.success) {
        throw new ControlPlaneContractError({
          endpoint: params.request.endpoint,
          status: params.response.status,
          issues: denial.error.issues.map(
            (issue) => `${issue.path.join(".")}: ${issue.message}`,
          ),
        });
      }
      return { outcome: "denied", reason: denial.data.reason };
    }
    throw new ControlPlaneContractError({
      endpoint: params.request.endpoint,
      status: params.response.status,
      issues: ["status is neither a contract answer nor a typed denial"],
    });
  }
}
