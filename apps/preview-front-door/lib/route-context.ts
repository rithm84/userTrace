import { createHmac } from "node:crypto";

import {
  routeContextClaimsSchema,
  type PreviewRouteRecord,
  type RouteContextClaims,
} from "./contracts";

/**
 * Short-lived signed uTrace route context.
 *
 * The token is `v1.<base64url payload>.<base64url HMAC-SHA256 signature>`, the signature
 * covers the encoded payload, and the payload holds the claims the preview runtime
 * middleware verifies independently of the sandbox hop. The envelope and claim set are
 * fixed by the runtime-side verifier; changing either is a versioned contract change.
 */

export const routeContextHeader = "x-utrace-route-context";

const tokenVersion = "v1";

/**
 * Build the claims for one request. The context never outlives the route mapping itself,
 * so its expiry is the earlier of the configured lifetime and the route's own expiry.
 */
export function buildRouteContextClaims(params: {
  record: PreviewRouteRecord;
  issuedAt: Date;
  ttlSeconds: number;
}): RouteContextClaims {
  const routeExpiresAt = new Date(params.record.expires_at).getTime();
  const ttlExpiresAt = params.issuedAt.getTime() + params.ttlSeconds * 1_000;
  const expiresAt = Math.min(routeExpiresAt, ttlExpiresAt);
  if (expiresAt <= params.issuedAt.getTime()) {
    throw new RangeError(
      `route context would already be expired: route_expires_at=${params.record.expires_at} issued_at=${params.issuedAt.toISOString()}`,
    );
  }
  return routeContextClaimsSchema.parse({
    schema_version: "utrace_preview_route_v1",
    preview_route_id: params.record.preview_route_id,
    client_id: params.record.client_id,
    product_signal_id: params.record.product_signal_id,
    preview_version_id: params.record.preview_version_id,
    preview_runtime_allocation_id: params.record.preview_runtime_allocation_id,
    public_preview_host: params.record.public_preview_host,
    route_revision: params.record.route_revision,
    issued_at: params.issuedAt.toISOString(),
    expires_at: new Date(expiresAt).toISOString(),
  });
}

/** Sign route-context claims with the route's signing secret. */
export function signRouteContext(params: {
  claims: RouteContextClaims;
  signingSecret: string;
}): string {
  const payload = Buffer.from(
    JSON.stringify(params.claims),
    "utf8",
  ).toString("base64url");
  const signature = createHmac("sha256", params.signingSecret)
    .update(payload, "utf8")
    .digest("base64url");
  return `${tokenVersion}.${payload}.${signature}`;
}
