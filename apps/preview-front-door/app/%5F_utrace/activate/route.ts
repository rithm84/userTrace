import { handleActivationRelay } from "@/lib/relays";

/**
 * `POST /__utrace/activate` — the reserved same-origin activation endpoint the embedded
 * browser SDK bootstrap calls with a fresh per-document nonce. The door relays it to the
 * control plane with the session cookie context and returns the signed document
 * attestation unchanged.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const POST = handleActivationRelay;
