import "server-only";

import { z } from "zod";

import { usesFixtureParticipant } from "@/lib/env";
import { createSupabaseReaderClient } from "@/lib/supabase/server";

/**
 * Who is asking, resolved from a verified Supabase JWT.
 *
 * `kind` is a routing decision, not an authorization one: it decides which
 * sign-in a signed-out visitor is sent to and which shell renders. Authority
 * over client records stays with the control plane, which re-resolves
 * membership for every request (see `docs/arch-components/identity-and-access.md`).
 */
export type Viewer =
  | Readonly<{ kind: "staff"; userId: string; email: string; accessToken: string }>
  | Readonly<{
      kind: "participant";
      userId: string;
      displayName: string | null;
      accessToken: string;
    }>;

/**
 * Only fields GoTrue owns are read for the decision. `user_metadata` is
 * user-editable, so it is used for a display name and nothing else.
 */
const claimsSchema = z.object({
  sub: z.string().min(1),
  email: z.string().min(1).optional(),
  app_metadata: z
    .object({ provider: z.string().min(1).optional() })
    .loose()
    .optional(),
  user_metadata: z
    .object({ full_name: z.string().min(1).optional(), user_name: z.string().min(1).optional() })
    .loose()
    .optional(),
});

export type AuthErrorCode = "claims_unverified" | "unknown_provider";

export class AuthError extends Error {
  readonly code: AuthErrorCode;

  constructor(code: AuthErrorCode, message: string) {
    super(message);
    this.name = "AuthError";
    this.code = code;
  }
}

/**
 * The fixture participant. It exists only for `UTRACE_PARTICIPANT_FIXTURE=1`
 * alongside the fixture control plane, which is what lets a browser-driven test
 * reach the session tab without Discord OAuth. Its access token is not a
 * credential: the fixture control-plane client never presents one.
 */
const FIXTURE_PARTICIPANT: Viewer = Object.freeze({
  kind: "participant",
  userId: "fixture-participant",
  displayName: "Maya Okonkwo",
  accessToken: "fixture-participant",
});

/** The signed-in viewer, or `null` when this request carries no session. */
export async function readViewer(): Promise<Viewer | null> {
  if (usesFixtureParticipant()) {
    return FIXTURE_PARTICIPANT;
  }
  const supabase = await createSupabaseReaderClient();
  const session = await supabase.auth.getSession();
  if (session.error !== null) {
    throw new AuthError("claims_unverified", `Supabase rejected the stored session: ${session.error.message}`);
  }
  const accessToken = session.data.session?.access_token;
  if (accessToken === undefined) {
    return null;
  }

  const verified = await supabase.auth.getClaims(accessToken);
  if (verified.error !== null) {
    throw new AuthError("claims_unverified", `Supabase could not verify the access token: ${verified.error.message}`);
  }
  if (verified.data === null) {
    return null;
  }

  const claims = claimsSchema.parse(verified.data.claims);
  const provider = claims.app_metadata?.provider;

  if (provider === "discord") {
    const metadata = claims.user_metadata;
    return {
      kind: "participant",
      userId: claims.sub,
      displayName: metadata?.full_name ?? metadata?.user_name ?? null,
      accessToken,
    };
  }

  if (provider === "email") {
    if (claims.email === undefined) {
      throw new AuthError("unknown_provider", "An email identity arrived without an email claim.");
    }
    return { kind: "staff", userId: claims.sub, email: claims.email, accessToken };
  }

  throw new AuthError(
    "unknown_provider",
    `uTrace admits email sign-in for client staff and Discord sign-in for participants; this session used ${provider ?? "no provider"}.`,
  );
}
