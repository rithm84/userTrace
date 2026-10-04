/**
 * The Shopalytics preview authentication handoff.
 *
 * uTrace signs the one-time token in its own control plane; the client
 * runtime's contract is `v1.<base64url(claims)>.<base64url(HMAC-SHA256)>`,
 * documented in the Shopalytics checkout's `src/lib/utrace/preview-handoff.ts`.
 * The journey signs one so the seeded growth lead is signed in for real,
 * because an unauthenticated preview must not look like an authenticated one
 * in the evidence.
 */

import { createHmac, randomUUID } from "node:crypto";

/** A handoff is a few seconds of authority; the runtime caps its lifetime. */
const LIFETIME_MS = 60_000;

export interface HandoffRequest {
  readonly subjectEmail: string;
  /** The session origin host the token is bound to, without scheme or port. */
  readonly audience: string;
  readonly secret: string;
}

export function signHandoffToken(request: HandoffRequest): Promise<string> {
  const issuedAt = new Date();
  const claims = {
    schema_version: "shopalytics-preview-handoff-v1",
    token_id: randomUUID(),
    subject_email: request.subjectEmail,
    audience: request.audience,
    issued_at: issuedAt.toISOString(),
    expires_at: new Date(issuedAt.getTime() + LIFETIME_MS).toISOString(),
  };
  const payload = Buffer.from(JSON.stringify(claims), "utf8").toString("base64url");
  const signature = createHmac("sha256", request.secret).update(payload, "utf8").digest("base64url");
  return Promise.resolve(`v1.${payload}.${signature}`);
}
