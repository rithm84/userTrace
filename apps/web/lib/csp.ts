/**
 * The site's Content-Security-Policy.
 *
 * The landing loads its fonts, artwork and Rive WASM from the same origin and
 * needs nothing else. The authenticated product surfaces additionally talk to
 * one Supabase project, for sign-in and the session tab's Realtime socket, and
 * to Daily, which carries the session tab's voice call. Everything else the
 * product does, including the control plane and opening the voice connection,
 * goes through this origin's Server Actions, so no other host is allowed here.
 * Without `NEXT_PUBLIC_SUPABASE_URL` there are no product surfaces and the
 * policy is exactly the landing's.
 */
export function buildContentSecurityPolicy(env: NodeJS.ProcessEnv): string {
  const production = env.NODE_ENV === "production";
  const supabase = supabaseConnectSources(env.NEXT_PUBLIC_SUPABASE_URL);
  const product = supabase.length > 0;

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
      ...(product ? DAILY_SCRIPT_SOURCES : []),
    ].join(" "),
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    "img-src 'self' data: blob: https:",
    // The session tab plays the agent's audio from a live MediaStream.
    "media-src 'self' blob: mediastream:",
    [
      "connect-src",
      "'self'",
      ...supabase,
      ...(product ? DAILY_CONNECT_SOURCES : []),
      ...(production ? [] : ["ws://localhost:*", "ws://127.0.0.1:*"]),
    ].join(" "),
    "worker-src 'self' blob:",
    "frame-src 'none'",
  ].join("; ");
}

/**
 * Daily's call-object requirements, from
 * https://docs.daily.co/guides/privacy-and-security/content-security-policy.
 *
 * The tab creates the call object with `avoidEval`, so Daily's call bundle loads
 * through a script tag from Daily's domain or one of its fallback domains, and
 * `'unsafe-eval'` is not needed. `connect-src` is the guide's narrowed list,
 * which names Daily's domains for `wss:` instead of the whole scheme. Daily's
 * `daily-js` itself is bundled into this origin's scripts, and its `blob:`
 * workers are already allowed. Banuba (video effects) and Krisp (noise
 * cancellation) are not used, so their sources are left out.
 */
const DAILY_SCRIPT_SOURCES: readonly string[] = [
  "https://*.daily.co",
  "https://*.dailywebrtc.com",
  "https://*.dailywebrtc.net",
];
const DAILY_CONNECT_SOURCES: readonly string[] = [
  "https://*.daily.co",
  "https://*.dailywebrtc.com",
  "https://*.dailywebrtc.net",
  "https://prod-ks.pluot.blue",
  "wss://*.daily.co",
  "wss://*.dailywebrtc.com",
  "wss://*.dailywebrtc.net",
];

/** The Supabase project's origin and its Realtime WebSocket origin (`wss:`, or `ws:` locally). */
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
  return [origin, origin.replace(/^http/u, "ws")];
}
