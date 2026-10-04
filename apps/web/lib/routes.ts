/**
 * The product route map. `proxy.ts` uses it to decide which requests need a
 * Supabase session refresh; the landing at `/` never does, which keeps the
 * marketing site deployable without any authentication configuration.
 */

export const STAFF_SIGN_IN_PATH = "/sign-in";
export const PARTICIPANT_SIGN_IN_PATH = "/sign-in/participant";
export const AUTH_CALLBACK_PATH = "/auth/callback";
export const DASHBOARD_PATH = "/dashboard";
export const DASHBOARD_SETTINGS_PATH = "/dashboard/settings";
export const USER_SETTINGS_PATH = "/user-settings";
export const EXTENSION_LINK_PATH = "/extension/link";

const PRODUCT_PATH_PREFIXES: readonly string[] = [
  DASHBOARD_PATH,
  "/case-files",
  "/sessions",
  "/invitations",
  "/extension",
  USER_SETTINGS_PATH,
  STAFF_SIGN_IN_PATH,
  "/auth",
];

/** True when `pathname` is served by a product surface rather than the landing. */
export function isProductPath(pathname: string): boolean {
  return PRODUCT_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function casePath(productSignalId: string): string {
  return `${DASHBOARD_PATH}/cases/${encodeURIComponent(productSignalId)}`;
}

export function caseFilePath(caseFileId: string): string {
  return `/case-files/${encodeURIComponent(caseFileId)}`;
}

export function invitationPath(invitationToken: string): string {
  return `/invitations/${encodeURIComponent(invitationToken)}`;
}

export function sessionPath(sessionId: string): string {
  return `/sessions/${encodeURIComponent(sessionId)}`;
}

/** The route parameter each identifier-bearing path prefix is followed by. */
const IDENTIFIER_SEGMENTS: ReadonlyMap<string, string> = new Map([
  [`${DASHBOARD_PATH}/cases/`, "[productSignalId]"],
  ["/case-files/", "[caseFileId]"],
  ["/invitations/", "[token]"],
  ["/sessions/", "[sessionId]"],
]);

/**
 * `url` as it may leave the site in a page-view or performance report: the
 * origin and path with each identifier replaced by its route parameter, and no
 * query or fragment. An invitation path carries a bearer token, and query
 * strings carry OAuth codes and return paths.
 */
export function redactedPageUrl(url: string): string {
  const { origin, pathname } = new URL(url);
  for (const [prefix, parameter] of IDENTIFIER_SEGMENTS) {
    if (pathname.startsWith(prefix)) {
      const rest = pathname.slice(prefix.length);
      const next = rest.indexOf("/");
      return `${origin}${prefix}${parameter}${next === -1 ? "" : rest.slice(next)}`;
    }
  }
  return `${origin}${pathname}`;
}

/** Sign-in destination carrying the path the visitor asked for. */
export function signInPath(signInRoute: string, nextPath: string): string {
  return `${signInRoute}?next=${encodeURIComponent(nextPath)}`;
}
