/**
 * The named `runtime.Port` protocol between the service worker and the content
 * script of the bound document.
 *
 * It is internal to the extension, so it is not part of the contract package,
 * but every message is still validated on arrival: a port is accepted only when
 * the sender tab, frame, document attachment, and nonce match, and a malformed
 * message is an explicit failure rather than a partially applied instruction.
 */
import { z } from "zod";

import {
  characterStateSchema,
  commandOutcomeSchema,
  controlActionSchema,
  reasonCodeSchema,
  revisionSchema,
  signedUnitIntervalSchema,
  targetReferenceSchema,
  unitIntervalSchema,
  uuidSchema,
  parseWith,
} from "../protocol/common";
import { visualCommandFrameSchema } from "../protocol/control";

export const CONTENT_PORT_NAME = "utrace.agent-surface.v1";

export const characterProjectionSchema = z.strictObject({
  revision: revisionSchema,
  state: characterStateSchema,
  gazeX: signedUnitIntervalSchema,
  gazeY: signedUnitIntervalSchema,
  speechEnergy: unitIntervalSchema,
  motionScale: unitIntervalSchema,
});

/**
 * The orb diameter the settings page offers, in whole CSS pixels. Every value
 * is a multiple of the step, so the orb's radius and its pills' offsets stay
 * on whole pixels.
 */
export const ORB_DIAMETER = Object.freeze({ min: 96, max: 176, step: 8, initial: 120 });

export const orbSettingsSchema = z.strictObject({
  anchorXRatio: unitIntervalSchema,
  anchorYRatio: unitIntervalSchema,
  diameter: z.int().min(ORB_DIAMETER.min).max(ORB_DIAMETER.max).multipleOf(ORB_DIAMETER.step),
});

export const controlsStateSchema = z.strictObject({
  sessionControlRevision: revisionSchema,
  mute: z.enum(["muted", "unmuted"]),
  pause: z.enum(["running", "paused", "ended"]),
  connected: z.boolean(),
  visualGuidanceEnabled: z.boolean(),
  pendingAction: controlActionSchema.nullable(),
});

export const workerMessageSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("init"),
    nonce: uuidSchema,
    sessionId: uuidSchema,
    documentAttachmentId: uuidSchema,
    projection: characterProjectionSchema,
    orb: orbSettingsSchema,
    controls: controlsStateSchema,
  }),
  z.strictObject({ kind: z.literal("character_state"), projection: characterProjectionSchema }),
  z.strictObject({ kind: z.literal("controls_state"), controls: controlsStateSchema }),
  z.strictObject({ kind: z.literal("command"), frame: visualCommandFrameSchema }),
  z.strictObject({
    kind: z.literal("withdraw_command"),
    commandId: uuidSchema,
    reason: reasonCodeSchema,
  }),
  z.strictObject({ kind: z.literal("clear_scenes"), reason: reasonCodeSchema }),
  z.strictObject({ kind: z.literal("orb_settings"), orb: orbSettingsSchema }),
  z.strictObject({ kind: z.literal("teardown"), reason: reasonCodeSchema }),
]);

export const surfaceMessageSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("surface_ready"), nonce: uuidSchema }),
  z.strictObject({
    kind: z.literal("command_result"),
    commandId: uuidSchema,
    outcome: commandOutcomeSchema,
    reason: reasonCodeSchema.optional(),
    observedTargets: z.array(targetReferenceSchema).max(12),
  }),
  z.strictObject({ kind: z.literal("control_action"), action: controlActionSchema }),
  z.strictObject({ kind: z.literal("return_to_session") }),
  z.strictObject({
    kind: z.literal("readiness_report"),
    report: z.enum(["withdrawn", "restored"]),
    reason: reasonCodeSchema,
  }),
  z.strictObject({ kind: z.literal("orb_settings"), orb: orbSettingsSchema }),
]);

export type CharacterProjectionMessage = z.infer<typeof characterProjectionSchema>;
export type OrbSettings = z.infer<typeof orbSettingsSchema>;
export type ControlsState = z.infer<typeof controlsStateSchema>;
export type WorkerMessage = z.infer<typeof workerMessageSchema>;
export type SurfaceMessage = z.infer<typeof surfaceMessageSchema>;

export function parseWorkerMessage(candidate: unknown): WorkerMessage {
  return parseWith(
    workerMessageSchema,
    candidate,
    "invalid_bridge_frame",
    "The service worker sent the surface a message it cannot accept",
  );
}

export function parseSurfaceMessage(candidate: unknown): SurfaceMessage {
  return parseWith(
    surfaceMessageSchema,
    candidate,
    "invalid_bridge_frame",
    "The surface sent the service worker a message it cannot accept",
  );
}

export const DEFAULT_ORB_SETTINGS: OrbSettings = Object.freeze({
  anchorXRatio: 0.92,
  anchorYRatio: 0.82,
  diameter: ORB_DIAMETER.initial,
});
