import "server-only";

import { ControlPlaneError } from "@/lib/api/errors";
import type { SessionProjection } from "@/lib/api/schemas";
import { requireParticipant } from "@/lib/auth/guards";
import { sessionPath } from "@/lib/routes";

/**
 * The committed session projection the tab renders.
 *
 * `GET /v1/sessions/{id}/projection` answers the same row Supabase Realtime
 * distributes: the document and its monotonic revision. The control plane
 * recomputes `public.session_projections` inside the transaction that caused
 * the change, so the first render and every later revision come from one
 * record. Authority is the session's own participant dependency, which is what
 * proves the reader may be here.
 */
export type ProjectionReadFailure = "missing" | "rejected";

export type ProjectionRead =
  | Readonly<{ ok: true; projection: SessionProjection }>
  | Readonly<{ ok: false; reason: ProjectionReadFailure; detail: string }>;

export async function readSessionProjection(sessionId: string): Promise<ProjectionRead> {
  const { api } = await requireParticipant(sessionPath(sessionId));
  try {
    return { ok: true, projection: await api.getSessionProjection(sessionId) };
  } catch (error) {
    if (error instanceof ControlPlaneError) {
      return { ok: false, reason: failureFor(error), detail: describe(error) };
    }
    throw error;
  }
}

function failureFor(error: ControlPlaneError): ProjectionReadFailure {
  return error.code === "invalid_response" ? "rejected" : "missing";
}

function describe(error: ControlPlaneError): string {
  if (error.code === "not_found") {
    return "uTrace has not published a projection for this session yet.";
  }
  if (error.code === "invalid_response") {
    return `This session's projection does not match the uTrace contract: ${error.message}`;
  }
  return `uTrace could not read this session's projection: ${error.message}`;
}
