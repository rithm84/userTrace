/**
 * Where the door routes the proxied product's own Next.js assets.
 *
 * The door is itself a Next.js deployment, and Vercel serves `/_next/*` from a
 * deployment's static layer before any route handler runs, so a proxied Next.js
 * product's scripts, styles, and fonts would never reach the proxy. A before-files
 * rewrite in `next.config.ts` moves them under this prefix, and `productPath`
 * restores the product's path before the request is forwarded. The door has no
 * build assets of its own under `/_next`.
 */
export const PROXIED_NEXT_PREFIX = "/__utrace/next";

/** The product path one door request stands for. */
export function productPath(pathname: string): string {
  if (pathname === PROXIED_NEXT_PREFIX || pathname.startsWith(`${PROXIED_NEXT_PREFIX}/`)) {
    return `/_next${pathname.slice(PROXIED_NEXT_PREFIX.length)}`;
  }
  return pathname;
}
