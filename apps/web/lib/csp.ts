/**
 * The site's Content-Security-Policy.
 *
 * The landing loads its fonts, artwork and Rive WASM from the same origin and
 * needs nothing else. The authenticated product surfaces additionally talk to
 * one Supabase project: the browser client for sign-in and the session tab's
 * Realtime socket. Everything else the product does — the control plane, the
 * GPT-Live session setup — goes through this origin's Server Actions, so no other
 * host is allowed here. Without `NEXT_PUBLIC_SUPABASE_URL` the policy is
 * exactly the landing's.
 */
export function buildContentSecurityPolicy(env: NodeJS.ProcessEnv): string {
  const production = env.NODE_ENV === "production";

  return [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    [
      "script-src",
      "'self'",
      "'unsafe-inline'",
      "'wasm-unsafe-eval'",
      ...(production ? [] : ["'unsafe-eval'"]),
    ].join(" "),
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    "img-src 'self' data: blob: https:",
    // The session tab plays the agent's audio from a live MediaStream.
    "media-src 'self' blob: mediastream:",
    [
      "connect-src",
      "'self'",
      ...supabaseConnectSources(env.NEXT_PUBLIC_SUPABASE_URL),
      ...(production ? [] : ["ws://localhost:*", "ws://127.0.0.1:*"]),
    ].join(" "),
    "worker-src 'self' blob:",
    "frame-src 'none'",
  ].join("; ");
}

/** The Supabase project's HTTPS origin and its Realtime WebSocket origin. */
function supabaseConnectSources(supabaseUrl: string | undefined): readonly string[] {
  const trimmed = supabaseUrl?.trim();
  if (trimmed === undefined || trimmed.length === 0) {
    return [];
  }
  let origin: string;
  try {
    origin = new URL(trimmed).origin;
  } catch (error) {
    throw new TypeError(
      `NEXT_PUBLIC_SUPABASE_URL must be an absolute URL to appear in the Content-Security-Policy; received ${trimmed}.`,
      { cause: error },
    );
  }
  return [origin, origin.replace(/^https:/u, "wss:")];
}
