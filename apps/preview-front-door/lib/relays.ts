import {
  activationRequestSchema,
  propagationProbeRequestSchema,
  telemetryCredentialRequestSchema,
} from "./contracts";
import type { ControlPlaneOutcome } from "./control-plane";
import {
  enterDoor,
  finish,
  readRelayRequest,
  relayFailureResponse,
  relayResponse,
  unavailable,
  type DoorEntry,
} from "./door-entry";
import { readSessionCredential } from "./session-cookie";

/**
 * The reserved same-origin endpoints the embedded browser SDK bootstrap calls.
 *
 * The door relays them to the control plane with the session cookie context and returns
 * the control plane's signed answer unchanged. It never mints, inspects, or caches an
 * attestation or a telemetry credential, and it never discloses the control plane's typed
 * denial to the browser.
 */

type RelayEntry<T> =
  | {
      readonly kind: "entered";
      readonly entry: DoorEntry;
      readonly body: T;
      readonly sessionCredential: string;
    }
  | { readonly kind: "refused"; readonly response: Response };

/**
 * Admit one relay request: door entry, a same-origin body that matches its contract, then
 * the session cookie. Otherwise answer with the refusal for the first check that fails.
 */
async function enterRelay<T>(params: {
  request: Request;
  startedAt: number;
  parse: (payload: unknown) => { success: true; data: T } | { success: false };
}): Promise<RelayEntry<T>> {
  const { request, startedAt } = params;
  const entered = enterDoor({ request, startedAt, json: true });
  if (entered.kind === "refused") {
    return entered;
  }

  const body = await readRelayRequest({ request, startedAt, parse: params.parse });
  if (body.kind === "refused") {
    return body;
  }

  const sessionCredential = readSessionCredential({
    cookieHeader: request.headers.get("cookie"),
    cookieName: entered.entry.config.sessionCookieName,
  });
  if (sessionCredential === null) {
    return {
      kind: "refused",
      response: finish({
        request,
        startedAt,
        response: relayFailureResponse({ reason: "unauthorized", status: 403 }),
        outcome: "rejected",
        reason: "missing_session_cookie",
        route: null,
      }),
    };
  }
  return { kind: "entered", entry: entered.entry, body: body.value, sessionCredential };
}

/** Return the control plane's answer unchanged; a typed denial reaches the browser only as `unauthorized`. */
async function relayAnswer<T>(params: {
  request: Request;
  startedAt: number;
  failureReason: string;
  call: () => Promise<ControlPlaneOutcome<T>>;
}): Promise<Response> {
  const { request, startedAt } = params;
  try {
    const relayed = await params.call();
    if (relayed.outcome === "denied") {
      return finish({
        request,
        startedAt,
        response: relayFailureResponse({ reason: "unauthorized", status: 403 }),
        outcome: "rejected",
        reason: relayed.reason,
        route: null,
      });
    }
    return finish({
      request,
      startedAt,
      response: relayResponse({ body: relayed.value, status: 200 }),
      outcome: "relayed",
      reason: null,
      route: null,
    });
  } catch (error) {
    return unavailable({
      request,
      startedAt,
      reason: params.failureReason,
      status: 502,
      json: true,
      error,
    });
  }
}

/** Relay the browser SDK's document activation and return its signed attestation. */
export async function handleActivationRelay(
  request: Request,
): Promise<Response> {
  const startedAt = Date.now();
  const relay = await enterRelay({
    request,
    startedAt,
    parse: (payload) => activationRequestSchema.safeParse(payload),
  });
  if (relay.kind === "refused") {
    return relay.response;
  }
  return relayAnswer({
    request,
    startedAt,
    failureReason: "activation_relay_failed",
    call: () =>
      relay.entry.client.relayActivation({
        publicPreviewHost: relay.entry.origin.publicPreviewHost,
        sessionCredential: relay.sessionCredential,
        activationRequest: relay.body,
      }),
  });
}

/** Relay the one-time exchange of a document attestation for a telemetry credential. */
export async function handleTelemetryCredentialRelay(
  request: Request,
): Promise<Response> {
  const startedAt = Date.now();
  const relay = await enterRelay({
    request,
    startedAt,
    parse: (payload) => telemetryCredentialRequestSchema.safeParse(payload),
  });
  if (relay.kind === "refused") {
    return relay.response;
  }
  return relayAnswer({
    request,
    startedAt,
    failureReason: "telemetry_credential_relay_failed",
    call: () =>
      relay.entry.client.relayTelemetryCredential({
        publicPreviewHost: relay.entry.origin.publicPreviewHost,
        sessionCredential: relay.sessionCredential,
        credentialRequest: relay.body,
      }),
  });
}

/**
 * `POST /__utrace/propagation-probe` — the trace propagation probe.
 *
 * Runtime readiness requires a successful probe without duplicate injectors,
 * so the door echoes exactly what it received: the `traceparent`, the
 * `tracestate`, and how many `traceparent` values arrived. Two means a second
 * injector is active, which is a correlation failure rather than a tolerable
 * duplicate.
 *
 * The probe stops at the door. It reaches no control plane and no preview
 * runtime, because what it proves is that the browser's own injection path
 * produced one header on a real same-origin request; forwarding it would
 * measure the runtime's propagation instead, which the Server SDK's own probe
 * owns. It still requires the session cookie, so a bare preview URL cannot use
 * it to learn that an origin exists.
 */
export async function handlePropagationProbe(request: Request): Promise<Response> {
  const startedAt = Date.now();
  const relay = await enterRelay({
    request,
    startedAt,
    parse: (payload) => propagationProbeRequestSchema.safeParse(payload),
  });
  if (relay.kind === "refused") {
    return relay.response;
  }

  const traceparent = request.headers.get("traceparent");
  return finish({
    request,
    startedAt,
    response: relayResponse({
      body: {
        traceparent: traceparent ?? "",
        tracestate: request.headers.get("tracestate") ?? "",
        traceparent_count: countHeaderValues(traceparent),
      },
      status: 200,
    }),
    outcome: "relayed",
    reason: null,
    route: null,
  });
}

/**
 * How many values a header carried. `Headers.get` joins repeated values with
 * `, `, and a list-valued header may also arrive comma-separated, so both
 * shapes count the same way. An absent header is zero.
 */
function countHeaderValues(value: string | null): number {
  if (value === null || value.trim().length === 0) {
    return 0;
  }
  return value.split(",").filter((part) => part.trim().length > 0).length;
}
