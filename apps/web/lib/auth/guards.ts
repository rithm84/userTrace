import "server-only";

import { redirect } from "next/navigation";

import { activeClientId, createControlPlaneClient } from "@/lib/api/server";
import type { ControlPlaneClient } from "@/lib/api/client";
import type { Client } from "@/lib/api/schemas";
import {
  PARTICIPANT_SIGN_IN_PATH,
  STAFF_SIGN_IN_PATH,
  USER_SETTINGS_PATH,
  DASHBOARD_PATH,
  signInPath,
} from "@/lib/routes";

import { readViewer, type Viewer } from "./viewer";

export type StaffContext = Readonly<{
  viewer: Extract<Viewer, { kind: "staff" }>;
  clientId: string;
  client: Client;
  api: ControlPlaneClient;
}>;

export type ParticipantContext = Readonly<{
  viewer: Extract<Viewer, { kind: "participant" }>;
  api: ControlPlaneClient;
}>;

/**
 * Staff access to `nextPath`. A signed-out visitor is sent to email sign-in and
 * a participant to their own settings; a signed-in staff member's authority
 * comes from the control plane's membership check, which this call performs.
 */
export async function requireStaff(nextPath: string): Promise<StaffContext> {
  const viewer = await readViewer();
  if (viewer === null) {
    redirect(signInPath(STAFF_SIGN_IN_PATH, nextPath));
  }
  if (viewer.kind !== "staff") {
    redirect(USER_SETTINGS_PATH);
  }
  const api = createControlPlaneClient(viewer.accessToken);
  const clientId = activeClientId();
  const client = await api.getClient(clientId);
  return { viewer, clientId, client, api };
}

/** Participant access to `nextPath`, used by the session tab and user settings. */
export async function requireParticipant(nextPath: string): Promise<ParticipantContext> {
  const viewer = await readViewer();
  if (viewer === null) {
    redirect(signInPath(PARTICIPANT_SIGN_IN_PATH, nextPath));
  }
  if (viewer.kind !== "participant") {
    redirect(DASHBOARD_PATH);
  }
  return { viewer, api: createControlPlaneClient(viewer.accessToken) };
}

/** True when the viewer may take the owner-only actions in client settings. */
export function isOwner(client: Client): boolean {
  return client.membership.role === "owner" && client.membership.status === "active";
}
