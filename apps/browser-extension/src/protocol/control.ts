/**
 * The `WS /v1/extension/control` frames, mirroring `inbound-frame.v1` and
 * `outbound-frame.v1`.
 *
 * Inbound frames other than `heartbeat` carry the control plane's monotonic
 * sequence. The extension acknowledges the highest contiguous sequence and
 * resumes from it, so a reconnect never assumes missed delivery succeeded.
 */
import { z } from "zod";

import {
  base64UrlSchema,
  characterStateSchema,
  commandOutcomeSchema,
  controlActionSchema,
  extensionVersionSchema,
  opaqueIdSchema,
  originSchema,
  pageProofSchema,
  parseWith,
  ProtocolError,
  protocolVersionSchema,
  reasonCodeSchema,
  revisionSchema,
  schemaVersionSchema,
  sequenceSchema,
  signedUnitIntervalSchema,
  tabLocatorSchema,
  targetReferenceSchema,
  timestampSchema,
  unique,
  unitIntervalSchema,
  uuidSchema,
} from "./common";

const sequencedEnvelope = {
  schema_version: schemaVersionSchema,
  message_id: uuidSchema,
  sent_at: timestampSchema,
  sequence: sequenceSchema,
} as const;

const unsequencedEnvelope = {
  schema_version: schemaVersionSchema,
  message_id: uuidSchema,
  sent_at: timestampSchema,
} as const;

const bindingStatePayloadSchema = z
  .strictObject({
    binding_revision: revisionSchema,
    session_id: uuidSchema,
    status: z.enum(["bound", "revoked", "rejected"]),
    client_id: uuidSchema.optional(),
    extension_binding_id: uuidSchema.optional(),
    document_attachment_id: uuidSchema.optional(),
    preview_runtime_allocation_id: uuidSchema.optional(),
    reason: reasonCodeSchema.optional(),
  })
  .refine(
    (payload) =>
      payload.status !== "bound" ||
      (payload.client_id !== undefined &&
        payload.extension_binding_id !== undefined &&
        payload.document_attachment_id !== undefined &&
        payload.preview_runtime_allocation_id !== undefined),
    { error: "a bound binding_state must identify its client, binding, attachment, and allocation" },
  )
  .refine((payload) => payload.status === "bound" || payload.reason !== undefined, {
    error: "a revoked or rejected binding_state must carry a reason",
  });

export const bindingStateFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("binding_state"),
  payload: bindingStatePayloadSchema,
});

export const readinessFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("readiness"),
  payload: z.strictObject({
    readiness_revision: revisionSchema,
    session_id: uuidSchema,
    extension_readiness: z.enum(["ready", "recovering", "withdrawn"]),
    combined_readiness: z.enum(["ready", "blocked"]),
    blockers: unique(z.array(reasonCodeSchema).max(16)),
  }),
});

export const characterStateFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("character_state"),
  payload: z.strictObject({
    character_revision: revisionSchema,
    session_id: uuidSchema,
    state: characterStateSchema,
    gaze_x: signedUnitIntervalSchema,
    gaze_y: signedUnitIntervalSchema,
    speech_energy: unitIntervalSchema,
    motion_scale: unitIntervalSchema,
  }),
});

const sceneStepSchema = z.discriminatedUnion("step", [
  z.strictObject({
    step: z.literal("move"),
    target_id: opaqueIdSchema,
    target_revision: revisionSchema,
  }),
  z.strictObject({
    step: z.literal("point"),
    target_id: opaqueIdSchema,
    target_revision: revisionSchema,
    dwell_ms: z.int().min(0).max(5000),
  }),
  z.strictObject({
    step: z.literal("circle"),
    target_id: opaqueIdSchema,
    target_revision: revisionSchema,
    retain: z.boolean(),
  }),
  z.strictObject({
    step: z.literal("trace"),
    target_id: opaqueIdSchema,
    target_revision: revisionSchema,
    edge: z.enum(["top", "bottom", "leading", "trailing"]),
    retain: z.boolean(),
  }),
  z.strictObject({
    step: z.literal("label"),
    target_id: opaqueIdSchema,
    target_revision: revisionSchema,
    text: z.string().min(1).max(80),
    placement: z.enum(["above", "below", "leading", "trailing"]),
    retain: z.boolean(),
  }),
  z.strictObject({ step: z.literal("clear") }),
]);

export const sceneSchema = z.strictObject({
  scene_id: uuidSchema,
  steps: z.array(sceneStepSchema).min(1).max(12),
});

const visualCommandBodySchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("scene"), scene: sceneSchema }),
  z.strictObject({
    type: z.literal("scroll_into_view"),
    target_id: opaqueIdSchema,
    target_revision: revisionSchema,
    alignment: z.enum(["start", "center", "end", "nearest"]),
  }),
  z.strictObject({ type: z.literal("clear_scene") }),
]);

export const visualCommandFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("visual_command"),
  payload: z.strictObject({
    command_id: uuidSchema,
    client_id: uuidSchema,
    session_id: uuidSchema,
    extension_binding_id: uuidSchema,
    document_attachment_id: uuidSchema,
    expected_lifecycle_revision: revisionSchema,
    deadline_at: timestampSchema,
    command: visualCommandBodySchema,
  }),
});

export const cancelFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("cancel"),
  payload: z.strictObject({ command_id: uuidSchema, reason: reasonCodeSchema }),
});

export const controlActionResultFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("control_action_result"),
  payload: z.strictObject({
    action_id: uuidSchema,
    session_id: uuidSchema,
    disposition: z.enum(["accepted", "rejected", "failed"]),
    session_control_revision: revisionSchema,
    mute_state: z.enum(["muted", "unmuted"]),
    pause_state: z.enum(["running", "paused", "ended"]),
    reason: reasonCodeSchema.optional(),
  }),
});

export const recoveryStateFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("recovery_state"),
  payload: z.strictObject({
    disposition: z.enum(["resumed", "restarted", "rejected"]),
    next_sequence: sequenceSchema,
    session_id: uuidSchema.optional(),
    protocol_version: protocolVersionSchema,
    reason: reasonCodeSchema.optional(),
  }),
});

export const preferencesStateFrameSchema = z.strictObject({
  ...sequencedEnvelope,
  kind: z.literal("preferences_state"),
  payload: z.strictObject({
    preferences_revision: revisionSchema,
    visual_guidance_enabled: z.boolean(),
    agent_cursor_enabled: z.boolean(),
  }),
});

export const heartbeatFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("heartbeat"),
  payload: z.strictObject({ acknowledged_sequence: z.int().min(0) }),
});

export const inboundFrameSchema = z.discriminatedUnion("kind", [
  bindingStateFrameSchema,
  readinessFrameSchema,
  characterStateFrameSchema,
  visualCommandFrameSchema,
  cancelFrameSchema,
  controlActionResultFrameSchema,
  recoveryStateFrameSchema,
  preferencesStateFrameSchema,
  heartbeatFrameSchema,
]);

export const bindingOfferSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  offer_token: base64UrlSchema,
  session_id: uuidSchema,
  client_id: uuidSchema,
  extension_installation_id: uuidSchema,
  preview_runtime_allocation_id: uuidSchema,
  protocol_version: protocolVersionSchema,
  expires_at: timestampSchema,
});

/**
 * A first binding consumes the session tab's single-use offer; focusing another
 * valid tab of the same session transfers the existing binding instead, so the
 * consumed offer is never replayed.
 */
export const bindingRequestSchema = z.discriminatedUnion("kind", [
  z.strictObject({ kind: z.literal("offer"), offer: bindingOfferSchema }),
  z.strictObject({
    kind: z.literal("rebind"),
    session_id: uuidSchema,
    extension_binding_id: uuidSchema,
  }),
]);

export const bindingOfferFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("binding_offer"),
  payload: z.strictObject({
    request: bindingRequestSchema,
    page_proof: pageProofSchema,
    tab_locator: tabLocatorSchema,
    document_origin: originSchema,
  }),
});

export const commandResultFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("command_result"),
  payload: z
    .strictObject({
      command_id: uuidSchema,
      outcome: commandOutcomeSchema,
      reason: reasonCodeSchema.optional(),
      extension_binding_id: uuidSchema,
      document_attachment_id: uuidSchema,
      observed_targets: z.array(targetReferenceSchema).max(12),
      completed_at: timestampSchema,
    })
    .refine((payload) => payload.outcome === "applied" || payload.reason !== undefined, {
      error: "every non-applied command outcome must carry a reason",
    }),
});

export const controlActionFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("control_action"),
  payload: z
    .strictObject({
      action_id: uuidSchema,
      session_id: uuidSchema,
      action: controlActionSchema,
      expected_session_control_revision: revisionSchema,
      requested_at: timestampSchema,
      locally_confirmed: z.boolean(),
    })
    .refine((payload) => payload.action !== "end" || payload.locally_confirmed, {
      error: "End interview requires the local confirmation before control-plane closure",
    }),
});

export const recoveryRequestFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("recovery_request"),
  payload: z.strictObject({
    installation_id: uuidSchema,
    protocol_version: protocolVersionSchema,
    extension_version: extensionVersionSchema,
    resume_from_sequence: z.int().min(0),
    session_id: uuidSchema.optional(),
    pending_command_ids: unique(z.array(uuidSchema).max(32)),
  }),
});

export const readinessReportFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("readiness_report"),
  payload: z.strictObject({
    session_id: uuidSchema,
    report: z.enum(["withdrawn", "restored"]),
    reason: reasonCodeSchema,
    document_attachment_id: uuidSchema.optional(),
    observed_at: timestampSchema,
  }),
});

export const preferencesUpdateFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("preferences_update"),
  payload: z.strictObject({
    expected_preferences_revision: revisionSchema,
    visual_guidance_enabled: z.boolean(),
    agent_cursor_enabled: z.boolean(),
  }),
});

export const controlAckFrameSchema = z.strictObject({
  ...unsequencedEnvelope,
  kind: z.literal("control_ack"),
  payload: z.strictObject({ acknowledged_sequence: sequenceSchema }),
});

export const outboundFrameSchema = z.discriminatedUnion("kind", [
  bindingOfferFrameSchema,
  commandResultFrameSchema,
  controlActionFrameSchema,
  recoveryRequestFrameSchema,
  readinessReportFrameSchema,
  preferencesUpdateFrameSchema,
  controlAckFrameSchema,
  heartbeatFrameSchema,
]);

export type InboundFrame = z.infer<typeof inboundFrameSchema>;
export type OutboundFrame = z.infer<typeof outboundFrameSchema>;
export type BindingStateFrame = z.infer<typeof bindingStateFrameSchema>;
export type ReadinessFrame = z.infer<typeof readinessFrameSchema>;
export type CharacterStateFrame = z.infer<typeof characterStateFrameSchema>;
export type VisualCommandFrame = z.infer<typeof visualCommandFrameSchema>;
export type BindingOffer = z.infer<typeof bindingOfferSchema>;
export type BindingRequest = z.infer<typeof bindingRequestSchema>;
export type Scene = z.infer<typeof sceneSchema>;
export type SceneStep = z.infer<typeof sceneStepSchema>;

export function parseInboundFrame(candidate: unknown): InboundFrame {
  return parseWith(
    inboundFrameSchema,
    candidate,
    "invalid_inbound_frame",
    "The control plane sent a frame the extension cannot accept",
  );
}

export function parseOutboundFrame(candidate: unknown): OutboundFrame {
  return parseWith(
    outboundFrameSchema,
    candidate,
    "invalid_outbound_frame",
    "The extension produced a frame the control socket cannot carry",
  );
}

export function parseInboundFrameJson(text: string): InboundFrame {
  let candidate: unknown;
  try {
    candidate = JSON.parse(text);
  } catch (error) {
    throw new ProtocolError("invalid_inbound_frame", "The control plane sent malformed JSON", [
      error instanceof Error ? error.message : String(error),
    ]);
  }
  return parseInboundFrame(candidate);
}
