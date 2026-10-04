"use server";

import { redirect } from "next/navigation";
import { z } from "zod";

import { ControlPlaneError } from "@/lib/api/errors";
import { requireParticipant } from "@/lib/auth/guards";
import { invitationPath, sessionPath } from "@/lib/routes";

import type { ClaimState } from "./claim-state";

const tokenSchema = z.string().min(1).max(256);

/**
 * Claims a private invitation and opens its Interview Session.
 *
 * The invitation is bound server-side to one Discord identity, so claiming is
 * a write that only the invited person can make; a refusal is shown here rather
 * than thrown, because it is an answer the visitor needs to read.
 */
export async function claimInvitation(
  _previous: ClaimState,
  formData: FormData,
): Promise<ClaimState> {
  const token = tokenSchema.safeParse(formData.get("token"));
  if (!token.success) {
    return { status: "error", message: "That invitation link is not readable." };
  }

  const { api } = await requireParticipant(invitationPath(token.data));

  let sessionId: string;
  try {
    const claim = await api.claimInvitation(token.data);
    sessionId = claim.session_id;
  } catch (error) {
    if (error instanceof ControlPlaneError) {
      return { status: "error", message: describeClaimFailure(error) };
    }
    throw error;
  }

  redirect(sessionPath(sessionId));
}

function describeClaimFailure(error: ControlPlaneError): string {
  if (error.code === "not_found") {
    return "This invitation has expired or has already been withdrawn.";
  }
  if (error.code === "forbidden" || error.code === "unauthenticated") {
    return "This invitation belongs to a different Discord account. Sign in as the account it was sent to.";
  }
  if (error.code === "conflict") {
    return "This invitation has already been claimed.";
  }
  return error.message;
}
