/**
 * The request protocol between the popup or settings page and the service
 * worker.
 *
 * These surfaces read status, run the one-time account link, revoke the
 * installation, request the host permissions, and change global action
 * preferences. They cannot start an interview, bind a tab, or issue a product
 * command: no request here can reach the command path.
 */
import { z } from "zod";

import { parseWith, revisionSchema } from "../protocol/common";
import { orbSettingsSchema } from "./portMessages";
import type { StatusSnapshot } from "../background/session";

export const preferencesUpdateSchema = z.strictObject({
  expected_preferences_revision: revisionSchema,
  visual_guidance_enabled: z.boolean(),
  agent_cursor_enabled: z.boolean(),
});

export const extensionRequestSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("status") }),
  z.strictObject({ kind: z.literal("link_account") }),
  z.strictObject({ kind: z.literal("revoke_installation") }),
  z.strictObject({
    kind: z.literal("update_preferences"),
    preferences: preferencesUpdateSchema,
  }),
  z.strictObject({ kind: z.literal("update_orb"), orb: orbSettingsSchema }),
]);

export type ExtensionRequest = z.infer<typeof extensionRequestSchema>;
export type PreferencesUpdate = z.infer<typeof preferencesUpdateSchema>;

export type ExtensionResponse =
  | Readonly<{ kind: "status"; status: StatusSnapshot }>
  | Readonly<{ kind: "failure"; message: string }>;

export function parseExtensionRequest(candidate: unknown): ExtensionRequest {
  return parseWith(
    extensionRequestSchema,
    candidate,
    "invalid_bridge_frame",
    "The extension page sent a request the service worker cannot accept",
  );
}

export async function requestFromWorker(
  request: ExtensionRequest,
): Promise<ExtensionResponse> {
  const response: unknown = await chrome.runtime.sendMessage(request);
  if (
    response === null ||
    typeof response !== "object" ||
    !("kind" in response) ||
    typeof response.kind !== "string"
  ) {
    throw new Error("The service worker returned an unusable response.");
  }
  return response as ExtensionResponse;
}
