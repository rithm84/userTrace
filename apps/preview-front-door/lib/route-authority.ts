import type { PreviewRouteRecord } from "./contracts";
import { UpstreamBindingError } from "./errors";

/**
 * Route authority checks the door performs on top of the control plane's answer.
 *
 * The control plane commits one route revision per request and the door never caches its
 * answer. These checks are the door's own fail-closed guards: an expired mapping, a
 * superseded revision, and an upstream that is not the client application port are all
 * refused rather than forwarded.
 */

/** Refusals the door can reach once a host and a session credential are present. */
export type RouteRefusal =
  | "unknown_host"
  | "unauthorized_session"
  | "session_ended"
  | "route_expired"
  | "stale_route_revision";

const loopbackHostnames: ReadonlySet<string> = new Set([
  "localhost",
  "127.0.0.1",
  "[::1]",
  "::1",
]);

const servedRevisions = new Map<string, number>();
const servedRevisionsLimit = 1_000;

/** A route mapping that has reached its expiry fails closed. */
export function isRouteExpired(params: {
  record: PreviewRouteRecord;
  now: Date;
}): boolean {
  return new Date(params.record.expires_at).getTime() <= params.now.getTime();
}

/**
 * Reject a route revision lower than one this instance already served for the same origin.
 *
 * The control plane owns revision advancement; this guard only ever refuses, so a cold
 * instance is never more permissive than a warm one, and a superseded mapping cannot be
 * served after the door has already followed the newer one.
 */
export function acceptRouteRevision(params: {
  publicPreviewHost: string;
  routeRevision: number;
}): boolean {
  const served = servedRevisions.get(params.publicPreviewHost);
  if (served !== undefined && params.routeRevision < served) {
    return false;
  }
  if (served === undefined && servedRevisions.size >= servedRevisionsLimit) {
    const oldest = servedRevisions.keys().next();
    if (!oldest.done) {
      servedRevisions.delete(oldest.value);
    }
  }
  servedRevisions.set(params.publicPreviewHost, params.routeRevision);
  return true;
}

/** The Vercel Sandbox proxy domain every runtime port URL is served under. */
const sandboxPortDomainSuffix = ".vercel.run";

/**
 * Verify that the resolved upstream is a Vercel Sandbox port URL for the configured
 * client application port, and return the base URL to forward to.
 *
 * Each exposed sandbox port has its own `*.vercel.run` host, so the record's stated port
 * must be the configured application port and the host must be on the sandbox proxy
 * domain: the door never follows a route record to any other origin with its route
 * claim and entry token attached. A loopback upstream is accepted only for a local
 * runtime; every other upstream must be HTTPS.
 */
export function assertUpstreamBinding(params: {
  record: PreviewRouteRecord;
  applicationPort: number;
}): URL {
  const record = params.record;
  if (record.upstream.application_port !== params.applicationPort) {
    throw new UpstreamBindingError({
      publicPreviewHost: record.public_preview_host,
      applicationPort: params.applicationPort,
      detail: `route record states port ${record.upstream.application_port}`,
    });
  }
  const upstream = new URL(record.upstream.port_url);
  if (loopbackHostnames.has(upstream.hostname)) {
    return upstream;
  }
  if (upstream.protocol !== "https:") {
    throw new UpstreamBindingError({
      publicPreviewHost: record.public_preview_host,
      applicationPort: params.applicationPort,
      detail: `upstream port URL is not HTTPS (${upstream.protocol})`,
    });
  }
  if (!upstream.hostname.endsWith(sandboxPortDomainSuffix) || upstream.port !== "") {
    throw new UpstreamBindingError({
      publicPreviewHost: record.public_preview_host,
      applicationPort: params.applicationPort,
      detail: "upstream is not a Vercel Sandbox port URL",
    });
  }
  return upstream;
}
