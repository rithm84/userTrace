"use server";

import { z } from "zod";

import { ControlPlaneError } from "@/lib/api/errors";
import {
  preflightReportSchema,
  sessionControlActionSchema,
  type BindingOffer,
  type PreflightResult,
  type PreviewHandoff,
  type SessionControlAck,
  type SessionProjection,
  type VoiceConnectionRecord,
} from "@/lib/api/schemas";
import { requireParticipant } from "@/lib/auth/guards";
import { sessionPath } from "@/lib/routes";

import type { ActionResult } from "./results";

const sessionIdSchema = z.string().min(1).max(128);
const sdpOfferSchema = z.string().min(1).max(200_000);

async function run<Value>(
  sessionId: string,
  work: (api: Awaited<ReturnType<typeof requireParticipant>>["api"]) => Promise<Value>,
): Promise<ActionResult<Value>> {
  const { api } = await requireParticipant(sessionPath(sessionId));
  try {
    return { ok: true, value: await work(api) };
  } catch (error) {
    if (error instanceof ControlPlaneError) {
      return { ok: false, message: describe(error) };
    }
    throw error;
  }
}

function describe(error: ControlPlaneError): string {
  if (error.code === "forbidden" || error.code === "unauthenticated") {
    return "This session is not yours to open. Sign in with the Discord account the invitation was sent to.";
  }
  if (error.code === "not_found") {
    return "That session no longer exists.";
  }
  if (error.code === "conflict") {
    return "The session moved on before that request arrived. Reload to see where it stands.";
  }
  if (error.code === "transport" || error.code === "unavailable") {
    return "uTrace could not be reached. Your session is safe; try again in a moment.";
  }
  return error.message;
}

/**
 * Re-reads the committed session projection.
 *
 * Realtime is a hint, so the tab falls back to this when a delivery is missed
 * or a control changes a revision it has not seen. The route answers the same
 * document the subscription carries, under the session's own participant
 * authority.
 */
export async function refreshSessionProjection(
  sessionId: string,
): Promise<ActionResult<SessionProjection>> {
  const id = sessionIdSchema.parse(sessionId);
  return run(id, (api) => api.getSessionProjection(id));
}

/** Submits the participant compatibility preflight this browser collected. */
export async function submitSessionPreflight(
  sessionId: string,
  report: unknown,
): Promise<ActionResult<PreflightResult>> {
  const id = sessionIdSchema.parse(sessionId);
  const parsed = preflightReportSchema.parse(report);
  return run(id, (api) => api.submitPreflight(id, parsed));
}

/** Posts the browser's SDP offer and returns the answer the API obtained. */
export async function openVoiceConnection(
  sessionId: string,
  sdpOffer: string,
): Promise<ActionResult<VoiceConnectionRecord>> {
  const id = sessionIdSchema.parse(sessionId);
  const offer = sdpOfferSchema.parse(sdpOffer);
  return run(id, (api) => api.createVoiceConnection(id, offer));
}

/** Applies a session control and returns the state revision the control plane recorded. */
export async function sendSessionControl(
  sessionId: string,
  action: unknown,
): Promise<ActionResult<SessionControlAck>> {
  const id = sessionIdSchema.parse(sessionId);
  const parsed = sessionControlActionSchema.parse(action);
  return run(id, (api) => api.sendControl(id, parsed));
}

/** Requests the one-time front-door handoff for the preview tab. */
export async function openPreviewHandoff(
  sessionId: string,
): Promise<ActionResult<PreviewHandoff>> {
  const id = sessionIdSchema.parse(sessionId);
  return run(id, (api) => api.requestPreviewHandoff(id));
}

/** Requests the single-use binding offer this tab delivers to the extension. */
export async function fetchBindingOffer(sessionId: string): Promise<ActionResult<BindingOffer>> {
  const id = sessionIdSchema.parse(sessionId);
  return run(id, (api) => api.requestBindingOffer(id));
}
