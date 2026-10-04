/**
 * Door logs.
 *
 * A door log records method, safe path template, status, latency, client and session
 * identifiers, allocation, and route revision. It never records a request or response
 * body, a cookie, a handoff code, a session credential, provider credentials, a query
 * string, or a customer field value. Raw paths are never logged either: a preview path
 * can carry customer identifiers, so only its shape is kept.
 */

/** Outcome of one door request. `proxied` is the only outcome that reached the runtime. */
export type DoorOutcome =
  | "proxied"
  | "claimed"
  | "signed_in"
  | "redirected_to_sign_in"
  | "relayed"
  | "neutral"
  | "ended"
  | "reconnecting"
  | "rejected"
  | "failed";

export interface DoorRequestLog {
  readonly method: string;
  readonly pathTemplate: string;
  readonly status: number;
  readonly latencyMs: number;
  readonly outcome: DoorOutcome;
  readonly reason?: string;
  readonly clientId?: string;
  readonly interviewSessionId?: string;
  readonly previewRuntimeAllocationId?: string;
  readonly previewVersionId?: string;
  readonly routeRevision?: number;
}

export interface DoorWarningLog {
  readonly reason: string;
  readonly endpoint: string;
  readonly attempt: number;
  readonly attempts: number;
  readonly status?: number;
}

const reservedPathTemplates: ReadonlySet<string> = new Set([
  "/__utrace/claim",
  "/__utrace/sign-in",
  "/__utrace/activate",
  "/__utrace/telemetry-credential",
]);

const maxLoggedSegments = 12;

/**
 * Reduce a request path to a safe template: reserved door paths keep their literal
 * spelling, every product path segment becomes `:seg`, and depth is bounded.
 */
export function safePathTemplate(pathname: string): string {
  if (reservedPathTemplates.has(pathname)) {
    return pathname;
  }
  const segments = pathname.split("/").filter((segment) => segment.length > 0);
  if (segments.length === 0) {
    return "/";
  }
  const bounded = segments.slice(0, maxLoggedSegments).map(() => ":seg");
  const suffix = segments.length > maxLoggedSegments ? "/..." : "";
  return `/${bounded.join("/")}${suffix}`;
}

export function logDoorRequest(entry: DoorRequestLog): void {
  process.stdout.write(
    `${JSON.stringify({ event: "preview_door_request", ...entry })}\n`,
  );
}

/**
 * Record an internal fault behind an unavailable answer. Typed door errors carry only the
 * endpoint, status, and typed reason in their message, so the entry stays safe to keep.
 */
export function logDoorFailure(entry: {
  readonly reason: string;
  readonly error: unknown;
}): void {
  const error = entry.error;
  process.stderr.write(
    `${JSON.stringify({
      event: "preview_door_failure",
      reason: entry.reason,
      errorName: error instanceof Error ? error.name : "unknown",
      errorMessage: error instanceof Error ? error.message : String(error),
      causeMessage:
        error instanceof Error && error.cause instanceof Error
          ? error.cause.message
          : undefined,
    })}\n`,
  );
}

export function logDoorWarning(entry: DoorWarningLog): void {
  process.stderr.write(
    `${JSON.stringify({ event: "preview_door_warning", ...entry })}\n`,
  );
}
