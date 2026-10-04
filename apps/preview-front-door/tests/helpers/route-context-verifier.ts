import { z } from "zod";

/**
 * The runtime-side verifier, mirrored.
 *
 * This is the logic a client preview runs on every request
 * (`src/lib/server/utrace-route-context.ts` plus its `openToken` envelope in the
 * Shopalytics fixture repository): the `v1.<base64url payload>.<base64url signature>`
 * envelope, an HMAC-SHA256 signature over the encoded payload bytes, the
 * `utrace-preview-route-v1` claim set, a bounded clock skew, and an unexpired token. The
 * session origin comes from the verified `public_preview_host` claim, because the Vercel
 * Sandbox port proxy sets `Host` and `x-forwarded-host` to its own host. The test runtime
 * server verifies the door's header with it, so a change to the door's token format fails
 * these tests before it reaches a client.
 */

const routeContextSchema = z
  .object({
    schema_version: z.literal("utrace_preview_route_v1"),
    preview_route_id: z.uuid(),
    client_id: z.uuid(),
    product_signal_id: z.uuid(),
    preview_version_id: z.uuid(),
    preview_runtime_allocation_id: z.uuid(),
    public_preview_host: z.string().min(1),
    route_revision: z.int().positive(),
    issued_at: z.iso.datetime({ offset: true }),
    expires_at: z.iso.datetime({ offset: true }),
  })
  .strict();

export type VerifiedRouteContext = z.infer<typeof routeContextSchema>;

const issuedAtSkewMs = 30_000;

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(Buffer.from(value, "base64url"));
}

export async function verifyUTraceRouteContext(
  token: string,
  signingSecret: string,
  now: Date,
): Promise<VerifiedRouteContext> {
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new Error("token envelope must have exactly three segments");
  }
  const [version, payload, signature] = parts as [string, string, string];
  if (version !== "v1") {
    throw new Error(`token version must be "v1", received "${version}"`);
  }
  if (payload === "" || signature === "" || signingSecret === "") {
    throw new Error("token payload, signature and secret must all be present");
  }
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(signingSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    fromBase64Url(signature),
    encoder.encode(payload),
  );
  if (!valid) {
    throw new Error("token signature does not match");
  }
  const claims = routeContextSchema.parse(
    JSON.parse(decoder.decode(fromBase64Url(payload))),
  );
  if (
    new Date(claims.issued_at).getTime() > now.getTime() + issuedAtSkewMs ||
    new Date(claims.expires_at).getTime() <= now.getTime()
  ) {
    throw new Error("invalid route authority");
  }
  return claims;
}
