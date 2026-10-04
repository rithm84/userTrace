import {
  activationRequestSchema,
  propagationProbeRequestSchema,
  telemetryCredentialRequestSchema,
} from "./contracts";
import {
  enterDoor,
  finish,
  readRelayRequest,
  relayFailureResponse,
  relayResponse,
  unavailable,
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

/** Relay the browser SDK's document activation and return its signed attestation. */
export async function handleActivationRelay(
  request: Request,
): Promise<Response> {
  const startedAt = Date.now();
  const entered = enterDoor({ request, startedAt, json: true });
  if (entered.kind === "refused") {
    return entered.response;
  }
  const { client, config, origin } = entered.entry;

  const body = await readRelayRequest({
    request,
    startedAt,
    parse: (payload) => activationRequestSchema.safeParse(payload),
  });
  if (body.kind === "refused") {
    return body.response;
  }

  const sessionCredential = readSessionCredential({
    cookieHeader: request.headers.get("cookie"),
    cookieName: config.sessionCookieName,
  });
  if (sessionCredential === null) {
    return finish({
      request,
      startedAt,
      response: relayFailureResponse({ reason: "unauthorized", status: 403 }),
      outcome: "rejected",
      reason: "missing_session_cookie",
      route: null,
    });
  }

  try {
    const relayed = await client.relayActivation({
      publicPreviewHost: origin.publicPreviewHost,
      sessionCredential,
      activationRequest: body.value,
    });
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
      reason: "activation_relay_failed",
      status: 502,
      json: true,
      error,
    });
  }
}

/** Relay the one-time exchange of a document attestation for a telemetry credential. */
export async function handleTelemetryCredentialRelay(
  request: Request,
): Promise<Response> {
  const startedAt = Date.now();
  const entered = enterDoor({ request, startedAt, json: true });
  if (entered.kind === "refused") {
    return entered.response;
  }
  const { client, config, origin } = entered.entry;

  const body = await readRelayRequest({
    request,
    startedAt,
    parse: (payload) => telemetryCredentialRequestSchema.safeParse(payload),
  });
  if (body.kind === "refused") {
    return body.response;
  }

  const sessionCredential = readSessionCredential({
    cookieHeader: request.headers.get("cookie"),
    cookieName: config.sessionCookieName,
  });
  if (sessionCredential === null) {
    return finish({
      request,
      startedAt,
      response: relayFailureResponse({ reason: "unauthorized", status: 403 }),
      outcome: "rejected",
      reason: "missing_session_cookie",
      route: null,
    });
  }

  try {
    const relayed = await client.relayTelemetryCredential({
      publicPreviewHost: origin.publicPreviewHost,
      sessionCredential,
      credentialRequest: body.value,
    });
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
      reason: "telemetry_credential_relay_failed",
      status: 502,
      json: true,
      error,
    });
  }
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
  const entered = enterDoor({ request, startedAt, json: true });
  if (entered.kind === "refused") {
    return entered.response;
  }
  const { config } = entered.entry;

  const body = await readRelayRequest({
    request,
    startedAt,
    parse: (payload) => propagationProbeRequestSchema.safeParse(payload),
  });
  if (body.kind === "refused") {
    return body.response;
  }

  const sessionCredential = readSessionCredential({
    cookieHeader: request.headers.get("cookie"),
    cookieName: config.sessionCookieName,
  });
  if (sessionCredential === null) {
    return finish({
      request,
      startedAt,
      response: relayFailureResponse({ reason: "unauthorized", status: 403 }),
      outcome: "rejected",
      reason: "missing_session_cookie",
      route: null,
    });
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
