import { handleClaimRequest } from "@/lib/door";

/**
 * `GET /__utrace/claim?code=<one-time code>` — the authenticated claim.
 *
 * The session tab requests a one-time front-door handoff and sends the participant here.
 * The door exchanges the code with the control plane for the origin-bound session cookie
 * and redirects to the preview root. A bare or leaked URL without a valid code gets the
 * neutral interstitial and no cookie.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const GET = handleClaimRequest;
