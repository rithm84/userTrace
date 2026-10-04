import type { PreviewDenialReason, PreviewRouteRecord } from "./contracts";
import { readDoorConfig, type DoorConfig } from "./config";
import { ControlPlaneClient } from "./control-plane";
import {
  logDoorFailure,
  logDoorRequest,
  safePathTemplate,
  type DoorOutcome,
  type DoorRequestLog,
} from "./log";
import { renderInterstitial, type InterstitialKind } from "./interstitials";
import type { RouteRefusal } from "./route-authority";
import { readSessionOrigin, type SessionOrigin } from "./session-origin";

/**
 * Door entry and its answer surfaces.
 *
 * Every handler starts here: validated configuration, the session origin read from the
 * Host header, one control-plane connector, and the honest surface for each way a request
 * can end. The neutral page carries no authority, the ended page is terminal, the
 * reconnecting page follows the route's durable readiness, and the unavailable page
 * admits a fault on the uTrace side without claiming anything about the session.
 */

const reconnectRetryAfterSeconds = 5;

export interface SurfaceDecision {
  readonly kind: "neutral" | "ended" | "reconnecting";
  readonly status: number;
}

export const refusalSurfaces: Record<RouteRefusal, SurfaceDecision> = {
  unknown_host: { kind: "neutral", status: 404 },
  unauthorized_session: { kind: "neutral", status: 404 },
  session_ended: { kind: "ended", status: 410 },
  route_expired: { kind: "ended", status: 410 },
  // A revision below one this door already served means the control plane's answer and the
  // door's own history disagree. The door refuses to route and says so without claiming
  // the session ended.
  stale_route_revision: { kind: "reconnecting", status: 503 },
};

export const denialRefusals: Record<PreviewDenialReason, RouteRefusal> = {
  unknown_host: "unknown_host",
  unknown_handoff_code: "unauthorized_session",
  handoff_consumed: "unauthorized_session",
  handoff_expired: "unauthorized_session",
  host_mismatch: "unauthorized_session",
  unauthorized_session: "unauthorized_session",
  session_ended: "session_ended",
  route_expired: "route_expired",
  activation_rejected: "unauthorized_session",
};

export function interstitialResponse(params: {
  kind: InterstitialKind;
  status: number;
}): Response {
  const headers = new Headers({
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    vary: "Cookie",
  });
  if (params.kind === "reconnecting") {
    headers.set("retry-after", String(reconnectRetryAfterSeconds));
  }
  return new Response(renderInterstitial(params.kind), {
    status: params.status,
    headers,
  });
}

export function relayResponse(params: { body: unknown; status: number }): Response {
  return new Response(JSON.stringify(params.body), {
    status: params.status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      vary: "Cookie",
    },
  });
}

export function relayFailureResponse(params: {
  reason: "unauthorized" | "invalid_request" | "unavailable";
  status: number;
}): Response {
  return relayResponse({
    body: {
      schema_version: "utrace_preview_relay_failure_v1",
      reason: params.reason,
    },
    status: params.status,
  });
}

/** Fields a door log inherits from a resolved route. */
function routeLogFields(record: PreviewRouteRecord): Partial<DoorRequestLog> {
  return {
    clientId: record.client_id,
    interviewSessionId: record.interview_session_id,
    previewRuntimeAllocationId: record.preview_runtime_allocation_id,
    previewVersionId: record.preview_version_id,
    routeRevision: record.route_revision,
  };
}

/** Emit one door log line and return the response it describes. */
export function finish(params: {
  request: Request;
  startedAt: number;
  response: Response;
  outcome: DoorOutcome;
  reason: string | null;
  route: PreviewRouteRecord | null;
}): Response {
  const pathname = new URL(params.request.url).pathname;
  logDoorRequest({
    method: params.request.method,
    pathTemplate: safePathTemplate(pathname),
    status: params.response.status,
    latencyMs: Date.now() - params.startedAt,
    outcome: params.outcome,
    ...(params.reason === null ? {} : { reason: params.reason }),
    ...(params.route === null ? {} : routeLogFields(params.route)),
  });
  return params.response;
}

/** Report an internal fault without claiming anything about the participant's session. */
export function unavailable(params: {
  request: Request;
  startedAt: number;
  reason: string;
  status: number;
  json: boolean;
  error: unknown;
}): Response {
  logDoorFailure({ reason: params.reason, error: params.error });
  const response = params.json
    ? relayFailureResponse({ reason: "unavailable", status: params.status })
    : interstitialResponse({ kind: "unavailable", status: params.status });
  return finish({
    request: params.request,
    startedAt: params.startedAt,
    response,
    outcome: "failed",
    reason: params.reason,
    route: null,
  });
}

export interface DoorEntry {
  readonly config: DoorConfig;
  readonly client: ControlPlaneClient;
  readonly origin: SessionOrigin;
}

export type DoorEntryResult =
  | { readonly kind: "entered"; readonly entry: DoorEntry }
  | { readonly kind: "refused"; readonly response: Response };

/** Validate configuration and resolve the session origin, or answer with a surface. */
export function enterDoor(params: {
  request: Request;
  startedAt: number;
  json: boolean;
}): DoorEntryResult {
  let config: DoorConfig;
  try {
    config = readDoorConfig(process.env);
  } catch (error) {
    return {
      kind: "refused",
      response: unavailable({
        request: params.request,
        startedAt: params.startedAt,
        reason: "invalid_configuration",
        status: 500,
        json: params.json,
        error,
      }),
    };
  }
  const origin = readSessionOrigin({
    hostHeader: params.request.headers.get("host"),
    previewApex: config.previewApex,
  });
  if (origin === null) {
    return {
      kind: "refused",
      response: finish({
        request: params.request,
        startedAt: params.startedAt,
        response: params.json
          ? relayFailureResponse({ reason: "unauthorized", status: 403 })
          : interstitialResponse({ kind: "neutral", status: 404 }),
        outcome: params.json ? "rejected" : "neutral",
        reason: "unknown_host",
        route: null,
      }),
    };
  }
  return {
    kind: "entered",
    entry: {
      config,
      origin,
      client: new ControlPlaneClient({
        baseUrl: config.controlPlaneBaseUrl,
        serviceToken: config.controlPlaneServiceToken,
        timeoutMs: config.controlPlaneTimeoutMs,
        attempts: config.controlPlaneAttempts,
      }),
    },
  };
}

/** A same-origin POST from the preview document, carrying the session cookie. */
export function isSameOriginPost(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (origin === null || host === null) {
    return false;
  }
  try {
    return new URL(origin).host.toLowerCase() === host.trim().toLowerCase();
  } catch {
    return false;
  }
}

export type RelayBodyResult<T> =
  | { readonly kind: "parsed"; readonly value: T }
  | { readonly kind: "refused"; readonly response: Response };

export async function readRelayRequest<T>(params: {
  request: Request;
  startedAt: number;
  parse: (payload: unknown) => { success: true; data: T } | { success: false };
}): Promise<RelayBodyResult<T>> {
  if (!isSameOriginPost(params.request)) {
    return {
      kind: "refused",
      response: finish({
        request: params.request,
        startedAt: params.startedAt,
        response: relayFailureResponse({
          reason: "invalid_request",
          status: 400,
        }),
        outcome: "rejected",
        reason: "not_same_origin",
        route: null,
      }),
    };
  }
  const payload: unknown = await params.request.json().catch(() => null);
  const parsed = params.parse(payload);
  if (!parsed.success) {
    return {
      kind: "refused",
      response: finish({
        request: params.request,
        startedAt: params.startedAt,
        response: relayFailureResponse({
          reason: "invalid_request",
          status: 400,
        }),
        outcome: "rejected",
        reason: "unusable_relay_body",
        route: null,
      }),
    };
  }
  return { kind: "parsed", value: parsed.data };
}

