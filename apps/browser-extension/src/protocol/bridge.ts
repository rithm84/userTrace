/**
 * The main-world bridge frames, mirroring `bridge-frame.v1`.
 *
 * Every message is plain JSON `{schema_version, kind, phase, exchange_id,
 * payload}` sent over a fresh per-document `MessageChannel`. The kinds are the
 * five the embedded browser SDK implements: `page_proof`, `target_query`,
 * `target_geometry`, `detail_query`, and `pause_notice`. `target_geometry` is
 * the response to a `target_query` with the same `exchange_id`.
 */
import { z } from "zod";

import {
  opaqueIdSchema,
  pageProofSchema,
  parseWith,
  protocolVersionSchema,
  reasonCodeSchema,
  revisionSchema,
  schemaVersionSchema,
  unique,
  unitIntervalSchema,
  uuidSchema,
  viewportRectSchema,
} from "./common";

const pageProofRequestSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("page_proof"),
  phase: z.literal("request"),
  exchange_id: uuidSchema,
  payload: z.strictObject({ extension_protocol_version: protocolVersionSchema }),
});

const pageProofResponseSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("page_proof"),
  phase: z.literal("response"),
  exchange_id: uuidSchema,
  payload: z.strictObject({
    page_proof: pageProofSchema,
    document_attachment_id: uuidSchema,
    registry_revision: revisionSchema,
  }),
});

const targetQuerySchema = z.union([
  z.strictObject({
    by: z.literal("target_ids"),
    target_ids: unique(z.array(opaqueIdSchema).min(1).max(12)),
  }),
  z.strictObject({
    by: z.literal("definition"),
    definition_id: opaqueIdSchema,
    entity_ref: opaqueIdSchema.optional(),
    effect: z.enum(["observe", "visual_guidance", "any"]),
    route_template: z.string().max(256).optional(),
    visibility: z.enum(["visible", "offscreen", "any"]),
  }),
]);

const targetQueryFrameSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("target_query"),
  phase: z.literal("request"),
  exchange_id: uuidSchema,
  payload: z.strictObject({
    document_attachment_id: uuidSchema,
    query: targetQuerySchema,
    deadline_ms: z.int().min(10).max(5000),
  }),
});

export const resolvedTargetSchema = z.strictObject({
  target_id: opaqueIdSchema,
  target_revision: revisionSchema,
  layout_revision: revisionSchema,
  frame_id: opaqueIdSchema,
  rect: viewportRectSchema,
  visible_fraction: unitIntervalSchema,
  occluded: z.boolean(),
  in_viewport: z.boolean(),
  supports_visual_guidance: z.boolean(),
});

const targetGeometryFrameSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("target_geometry"),
  phase: z.literal("response"),
  exchange_id: uuidSchema,
  payload: z.strictObject({
    document_attachment_id: uuidSchema,
    registry_revision: revisionSchema,
    viewport: z.strictObject({ width: z.number().min(0), height: z.number().min(0) }),
    targets: z.array(resolvedTargetSchema).max(12),
    not_found: unique(z.array(opaqueIdSchema).max(12)),
    ambiguous: unique(z.array(opaqueIdSchema).max(12)),
  }),
});

const detailQueryRequestSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("detail_query"),
  phase: z.literal("request"),
  exchange_id: uuidSchema,
  payload: z.strictObject({
    document_attachment_id: uuidSchema,
    expected_registry_revision: revisionSchema,
    target_id: opaqueIdSchema,
    fields: unique(z.array(opaqueIdSchema).min(1).max(24)),
    depth_limit: z.int().min(0).max(4),
    size_limit_bytes: z.int().min(256).max(65536),
    deadline_ms: z.int().min(10).max(5000),
  }),
});

const detailQueryResponseSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("detail_query"),
  phase: z.literal("response"),
  exchange_id: uuidSchema,
  payload: z
    .strictObject({
      disposition: z.enum(["resolved", "failed"]),
      target_revision: revisionSchema.optional(),
      fields: z
        .record(z.string(), z.union([z.string().max(512), z.number(), z.boolean()]))
        .optional(),
      reason: reasonCodeSchema.optional(),
    })
    .refine(
      (payload) =>
        payload.disposition === "failed"
          ? payload.reason !== undefined
          : payload.target_revision !== undefined && payload.fields !== undefined,
      { error: "a resolved detail query returns fields and a revision; a failed one returns a reason" },
    ),
});

const pauseNoticeFrameSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("pause_notice"),
  phase: z.literal("notice"),
  exchange_id: uuidSchema,
  payload: z.strictObject({
    session_id: uuidSchema,
    document_attachment_id: uuidSchema,
    pause_state: z.enum(["paused", "running", "ended"]),
    session_control_revision: revisionSchema,
  }),
});

export const bridgeFrameSchema = z.union([
  pageProofRequestSchema,
  pageProofResponseSchema,
  targetQueryFrameSchema,
  targetGeometryFrameSchema,
  detailQueryRequestSchema,
  detailQueryResponseSchema,
  pauseNoticeFrameSchema,
]);

export type BridgeFrame = z.infer<typeof bridgeFrameSchema>;
export type PageProofResponse = z.infer<typeof pageProofResponseSchema>;
export type TargetGeometryFrame = z.infer<typeof targetGeometryFrameSchema>;
export type DetailQueryRequest = z.infer<typeof detailQueryRequestSchema>;
export type DetailQueryResponse = z.infer<typeof detailQueryResponseSchema>;
export type ResolvedTarget = z.infer<typeof resolvedTargetSchema>;

export function parseBridgeFrame(candidate: unknown): BridgeFrame {
  return parseWith(
    bridgeFrameSchema,
    candidate,
    "invalid_bridge_frame",
    "The page bridge carried a message the extension cannot accept",
  );
}

export function parsePageProofResponse(candidate: unknown): PageProofResponse {
  return parseWith(
    pageProofResponseSchema,
    candidate,
    "invalid_bridge_frame",
    "The embedded SDK answered the page-proof probe with an unusable message",
  );
}

