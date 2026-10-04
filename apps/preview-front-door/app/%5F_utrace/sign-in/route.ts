import { handleSignInRequest } from "@/lib/door";

/**
 * `GET /__utrace/sign-in` — the product sign-in the claim redirects to.
 *
 * The door signs the claimed browser into the client product with a one-time token it
 * posts server-side, then redirects to where the product lands the signed-in user.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const GET = handleSignInRequest;
