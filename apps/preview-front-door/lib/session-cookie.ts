/**
 * The session cookie the front door issues at claim time.
 *
 * The cookie carries the `__Host-` prefix, so a browser accepts it only over HTTPS, only
 * with `Path=/`, and only without a `Domain` attribute. That pins it to exactly one
 * session origin: `<opaque>.preview.utrace.dev` cannot set or read a sibling session's
 * cookie, and neither can `preview.utrace.dev`. `SameSite=Lax` keeps it off cross-site
 * subresource and form-post traffic, and `HttpOnly` keeps client preview code away from it.
 *
 * The cookie value is the opaque session credential the control plane minted; the door
 * never interprets it and never forwards it to the client product runtime.
 *
 * A second door cookie records which runtime the product signed the browser into. The
 * product's own session is signed with a secret that belongs to one runtime, so when the
 * origin advances to a candidate the browser must sign in again before the candidate sees
 * a page request; this cookie is how the door knows.
 */

const sessionCredentialPattern = /^[A-Za-z0-9._~-]{32,2048}$/;
const runtimeIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** Read one cookie by name from a `Cookie` header, without interpreting its value. */
function readCookie(params: {
  cookieHeader: string | null;
  name: string;
}): string | null {
  if (params.cookieHeader === null) {
    return null;
  }
  for (const pair of params.cookieHeader.split(";")) {
    const separator = pair.indexOf("=");
    if (separator === -1) {
      continue;
    }
    if (pair.slice(0, separator).trim() === params.name) {
      return pair.slice(separator + 1).trim();
    }
  }
  return null;
}

/**
 * Read the session credential presented by the request. Returns `null` when the cookie is
 * absent or does not have the shape of a credential this door issued; the control plane
 * remains the authority on whether a well-formed credential is still valid.
 */
export function readSessionCredential(params: {
  cookieHeader: string | null;
  cookieName: string;
}): string | null {
  const value = readCookie({
    cookieHeader: params.cookieHeader,
    name: params.cookieName,
  });
  if (value === null || !sessionCredentialPattern.test(value)) {
    return null;
  }
  return value;
}

/** Build the `Set-Cookie` value for a claimed session. */
export function buildSessionCookie(params: {
  cookieName: string;
  sessionCredential: string;
  maxAgeSeconds: number;
}): string {
  if (!sessionCredentialPattern.test(params.sessionCredential)) {
    throw new TypeError(
      "session credential does not have the shape the front door issues",
    );
  }
  if (!Number.isInteger(params.maxAgeSeconds) || params.maxAgeSeconds <= 0) {
    throw new TypeError(
      `session cookie lifetime must be a positive whole number of seconds, received ${params.maxAgeSeconds}`,
    );
  }
  return [
    `${params.cookieName}=${params.sessionCredential}`,
    "Path=/",
    `Max-Age=${params.maxAgeSeconds}`,
    "Secure",
    "HttpOnly",
    "SameSite=Lax",
  ].join("; ");
}

/** The name of the cookie recording which runtime the product signed this browser into. */
export function runtimeCookieName(sessionCookieName: string): string {
  return `${sessionCookieName}-runtime`;
}

/** Read which runtime the product signed this browser into, or `null` when unknown. */
export function readSignedInRuntime(params: {
  cookieHeader: string | null;
  sessionCookieName: string;
}): string | null {
  const value = readCookie({
    cookieHeader: params.cookieHeader,
    name: runtimeCookieName(params.sessionCookieName),
  });
  if (value === null || !runtimeIdPattern.test(value)) {
    return null;
  }
  return value;
}

/** Build the `Set-Cookie` value recording the runtime the product just signed in to. */
export function buildRuntimeCookie(params: {
  sessionCookieName: string;
  previewRuntimeAllocationId: string;
  maxAgeSeconds: number;
}): string {
  if (!runtimeIdPattern.test(params.previewRuntimeAllocationId)) {
    throw new TypeError(
      `runtime identity must be a lowercase UUID, received ${params.previewRuntimeAllocationId}`,
    );
  }
  return [
    `${runtimeCookieName(params.sessionCookieName)}=${params.previewRuntimeAllocationId}`,
    "Path=/",
    `Max-Age=${params.maxAgeSeconds}`,
    "Secure",
    "HttpOnly",
    "SameSite=Lax",
  ].join("; ");
}

/**
 * Remove the door's own cookies from a `Cookie` header before the request is forwarded
 * to the client product runtime. The product keeps its own cookies; it never sees these.
 */
export function withoutDoorCookies(params: {
  cookieHeader: string;
  sessionCookieName: string;
}): string | null {
  const doorCookies = new Set([
    params.sessionCookieName,
    runtimeCookieName(params.sessionCookieName),
  ]);
  const retained = params.cookieHeader
    .split(";")
    .map((pair) => pair.trim())
    .filter((pair) => {
      const separator = pair.indexOf("=");
      const name = separator === -1 ? pair : pair.slice(0, separator);
      return !doorCookies.has(name.trim());
    })
    .filter((pair) => pair.length > 0);
  return retained.length === 0 ? null : retained.join("; ");
}
