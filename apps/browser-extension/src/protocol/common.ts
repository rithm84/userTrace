/**
 * Runtime validation primitives shared by every extension boundary.
 *
 * These mirror `packages/utrace-contracts/schemas/extension-protocol/common.v1.schema.json`.
 * The shared fixtures in `packages/utrace-contracts/fixtures/extension-protocol/`
 * are checked against both this binding and the JSON Schemas, so drift between
 * them fails the protocol test rather than reaching a session.
 *
 * `unknown` appears only as the input type of a parse boundary, which is the
 * only place untrusted data is allowed to exist unvalidated.
 */
import { z } from "zod";

export const PROTOCOL_VERSION = "1.0";
export const SCHEMA_VERSION = 1;

export const UUID_V7_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
export const RFC3339_UTC_PATTERN =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$/u;
const PROTOCOL_VERSION_PATTERN = /^1\.[0-9]{1,3}$/u;
const REASON_CODE_PATTERN = /^[a-z][a-z0-9_]{2,63}$/u;
const OPAQUE_ID_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/u;
const BASE64URL_PATTERN = /^[A-Za-z0-9_-]{16,4096}$/u;
const ORIGIN_PATTERN = /^https?:\/\/[^/?#]+$/u;
const EXTENSION_VERSION_PATTERN = /^[0-9]+(\.[0-9]+){0,3}$/u;

export const uuidSchema = z.string().regex(UUID_V7_PATTERN);
export const timestampSchema = z.string().regex(RFC3339_UTC_PATTERN);
export const revisionSchema = z.int().min(0);
export const sequenceSchema = z.int().min(1);
export const protocolVersionSchema = z.string().regex(PROTOCOL_VERSION_PATTERN);
export const extensionVersionSchema = z.string().regex(EXTENSION_VERSION_PATTERN);
export const reasonCodeSchema = z.string().regex(REASON_CODE_PATTERN);
export const opaqueIdSchema = z.string().regex(OPAQUE_ID_PATTERN);
export const base64UrlSchema = z.string().regex(BASE64URL_PATTERN);
export const originSchema = z.string().regex(ORIGIN_PATTERN);
export const unitIntervalSchema = z.number().min(0).max(1);
export const signedUnitIntervalSchema = z.number().min(-1).max(1);
export const schemaVersionSchema = z.literal(SCHEMA_VERSION);

export const tabLocatorSchema = z.strictObject({
  tab_id: z.int().min(0),
  frame_id: z.literal(0),
});

export const targetReferenceSchema = z.strictObject({
  target_id: opaqueIdSchema,
  target_revision: revisionSchema,
});

export const viewportRectSchema = z.strictObject({
  x: z.number(),
  y: z.number(),
  width: z.number().min(0),
  height: z.number().min(0),
});

/**
 * The embedded SDK's signed document proof, forwarded exactly as the control
 * plane issued it: an Ed25519 detached signature over a base64url JSON claim
 * payload. The extension never treats it as authority; it only decodes the
 * payload to check that the document it probed is the one the proof describes.
 */
export const pageProofSchema = z.strictObject({
  algorithm: z.literal("ed25519"),
  key_id: opaqueIdSchema,
  payload_b64url: z.string().min(16).max(8192).regex(/^[A-Za-z0-9_-]+$/u),
  signature_b64url: z.string().regex(/^[A-Za-z0-9_-]{86}$/u),
});

/** The claim set carried inside `page_proof.payload_b64url`. */
export const pageProofClaimsSchema = z.strictObject({
  client_id: uuidSchema,
  session_id: uuidSchema,
  preview_environment_id: uuidSchema,
  preview_runtime_allocation_id: uuidSchema,
  origin: originSchema,
  document_attachment_id: uuidSchema,
  document_nonce: opaqueIdSchema,
  sdk_protocol_version: protocolVersionSchema,
  issued_at: timestampSchema,
  expires_at: timestampSchema,
});

/**
 * Decodes the claims a page proof carries. A payload that is not base64url
 * JSON matching the claim set is a protocol failure, never a partially trusted
 * proof.
 */
export function readPageProofClaims(proof: PageProof): PageProofClaims {
  let decoded: unknown;
  try {
    const padded = proof.payload_b64url.replaceAll("-", "+").replaceAll("_", "/");
    decoded = JSON.parse(atob(padded.padEnd(padded.length + ((4 - (padded.length % 4)) % 4), "=")));
  } catch (cause) {
    throw new ProtocolError("invalid_bridge_frame", "The page proof payload is not base64url JSON", [
      cause instanceof Error ? cause.message : String(cause),
    ]);
  }
  return parseWith(
    pageProofClaimsSchema,
    decoded,
    "invalid_bridge_frame",
    "The page proof carried claims the extension cannot accept",
  );
}

/**
 * The nine semantic character states the extension renders. They are a subset
 * of the rig's fourteen-value `state` enum; the extension never projects the
 * states that belong to the session tab's longer story.
 */
export const EXTENSION_CHARACTER_STATES = [
  "idle",
  "listening",
  "thinking",
  "speaking",
  "acknowledgement",
  "concern",
  "paused",
  "reconnecting",
  "completion",
] as const;

export const characterStateSchema = z.enum(EXTENSION_CHARACTER_STATES);

export const COMMAND_OUTCOMES = [
  "applied",
  "rejected",
  "failed",
  "stale_target",
  "canceled",
  "timed_out",
] as const;

export const commandOutcomeSchema = z.enum(COMMAND_OUTCOMES);

export const CONTROL_ACTIONS = ["mute", "unmute", "pause", "resume", "end"] as const;
export const controlActionSchema = z.enum(CONTROL_ACTIONS);

export type PageProof = z.infer<typeof pageProofSchema>;
export type PageProofClaims = z.infer<typeof pageProofClaimsSchema>;
export type TargetReference = z.infer<typeof targetReferenceSchema>;
export type ViewportRect = z.infer<typeof viewportRectSchema>;
export type CommandOutcome = (typeof COMMAND_OUTCOMES)[number];
export type ControlAction = (typeof CONTROL_ACTIONS)[number];

export type ProtocolErrorCode =
  | "invalid_inbound_frame"
  | "invalid_outbound_frame"
  | "invalid_bridge_frame"
  | "invalid_installation_message"
  | "invalid_binding_offer";

/** Every validation failure at a boundary raises this; nothing is coerced. */
export class ProtocolError extends Error {
  readonly code: ProtocolErrorCode;
  readonly issues: readonly string[];

  constructor(code: ProtocolErrorCode, message: string, issues: readonly string[]) {
    super(`${message} (${issues.join("; ")})`);
    this.name = "ProtocolError";
    this.code = code;
    this.issues = issues;
  }
}

/**
 * Reject a repeated entry in a list the schema marks `uniqueItems`.
 *
 * The binding has to enforce exactly what the schema enforces; a duplicate
 * target id or reason code that this accepts and the schema rejects is a
 * disagreement between the two, not a harmless difference.
 */
export function unique<Item extends string>(
  schema: z.ZodType<Item[]>,
): z.ZodType<Item[]> {
  return schema.refine((values) => new Set(values).size === values.length, {
    error: "entries must be unique",
  });
}

export function parseWith<Output>(
  schema: z.ZodType<Output>,
  candidate: unknown,
  code: ProtocolErrorCode,
  message: string,
): Output {
  const result = schema.safeParse(candidate);
  if (result.success) {
    return result.data;
  }
  throw new ProtocolError(
    code,
    message,
    result.error.issues.map((issue) => `${issue.path.join(".")}: ${issue.message}`),
  );
}
