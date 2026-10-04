/**
 * The landing-only gate. While `LANDING_ONLY=1` (set on Vercel's production environment until
 * the product opens), the landing at `/` is the only page that answers; every other path,
 * typed or linked, is sent back to it. Without the variable, missing routes return 404.
 * Static files never reach this decision: the proxy's matcher excludes
 * `/_next/*` and any path with a file extension.
 */

export const LANDING_ONLY_VARIABLE = "LANDING_ONLY";

export function isLandingOnly(env: Readonly<Record<string, string | undefined>>): boolean {
  return env[LANDING_ONLY_VARIABLE]?.trim() === "1";
}

/** True when a request for `pathname` must be redirected to the landing. */
export function landingOnlyRedirects(pathname: string, env: Readonly<Record<string, string | undefined>>): boolean {
  if (!isLandingOnly(env)) return false;
  return pathname !== "/";
}
