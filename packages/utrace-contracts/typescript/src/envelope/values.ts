/**
 * Envelope value objects: scope, producer, authority, lineage, ordering, digest.
 *
 * These are the parts of the envelope that are not the payload. They are
 * separate schemas because area schemas compose them directly, and because
 * keeping tenant scope, authority references, and stream position as distinct
 * typed objects is what stops a generic correlation identifier from standing in
 * for a relationship.
 */

import { z } from "zod";

import { uuidV7Schema } from "../ids/ids.js";

/** RFC 3339 instant in UTC with a literal `Z`. Local offsets are not accepted. */
export const TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$/;

/** W3C traceparent, version 00, with non-zero trace and parent identifiers. */
export const TRACEPARENT_PATTERN =
  /^00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$/;

/** A snake_case contract name: stage names, reason codes, component names. */
export const SNAKE_CASE_PATTERN = /^[a-z][a-z0-9_]*$/;

const HEX_SHA256_PATTERN = /^[0-9a-f]{64}$/;

export const timestampSchema = z.string().regex(TIMESTAMP_PATTERN);

export const snakeCaseNameSchema = z.string().regex(SNAKE_CASE_PATTERN).max(64);

export type Timestamp = z.infer<typeof timestampSchema>;

export const AUTHORITY_KINDS = [
  "configuration_release",
  "implementation_gate_decision",
  "async_authorization",
  "environment_lease",
  "invitation",
  "validation_result",
  "outreach_offer_reservation",
] as const;

export const STREAM_TYPES = [
  "intake_item",
  "product_signal",
  "outreach_attempt",
  "interview_session",
  "preview_runtime_allocation",
  "environment_lease",
  "implementation_attempt",
  "preview_version",
  "validation_attempt",
  "case_file",
  "delivery",
  "job",
  "job_run",
  "command",
] as const;

export const authorityKindSchema = z.enum(AUTHORITY_KINDS);
export const streamTypeSchema = z.enum(STREAM_TYPES);

export type AuthorityKind = z.infer<typeof authorityKindSchema>;
export type StreamType = z.infer<typeof streamTypeSchema>;

/** The software that emitted a message, distinct from the actor it acted for. */
export const producerSchema = z.strictObject({
  component: snakeCaseNameSchema,
  component_version: z.string().min(1).max(128),
  instance_id: z.string().min(1).max(128).optional(),
});

export type Producer = z.infer<typeof producerSchema>;

/** Scoped to one uTrace Client. One client must never see another client's records. */
export const clientTenantScopeSchema = z.strictObject({
  scope: z.literal("client"),
  client_id: uuidV7Schema,
});

/** Scoped to the platform itself, carrying no client records. */
export const platformScopeSchema = z.strictObject({
  scope: z.literal("platform"),
});

export const clientScopeSchema = z.discriminatedUnion("scope", [
  clientTenantScopeSchema,
  platformScopeSchema,
]);

export type ClientTenantScope = z.infer<typeof clientTenantScopeSchema>;
export type PlatformScope = z.infer<typeof platformScopeSchema>;
export type ClientScope = z.infer<typeof clientScopeSchema>;

/**
 * One pinned record a message relies on for authority.
 *
 * The reference is a claim, not a grant: the control plane rechecks the record,
 * its revision, and any fencing token before it lets an effect happen.
 */
export const authorityReferenceSchema = z.strictObject({
  kind: authorityKindSchema,
  reference_id: uuidV7Schema,
  revision: z.int().min(0).optional(),
  fencing_token: z.int().min(1).optional(),
});

export type AuthorityReference = z.infer<typeof authorityReferenceSchema>;

/** Message lineage, preserved through historical replay. It confers no authority. */
export const causationSchema = z.strictObject({
  caused_by_message_id: uuidV7Schema.optional(),
  root_message_id: uuidV7Schema.optional(),
});

export type Causation = z.infer<typeof causationSchema>;

/** W3C trace context describing technical execution, never product identity. */
export const traceContextSchema = z.strictObject({
  traceparent: z.string().regex(TRACEPARENT_PATTERN),
  tracestate: z.string().max(512).optional(),
});

export type TraceContext = z.infer<typeof traceContextSchema>;

/**
 * Position in one primary lifecycle stream.
 *
 * `expected_revision` is what a state-sensitive message was built against; a
 * mismatch rejects before side effects. `sequence` is assigned by the control
 * plane after acceptance, so a producer leaves it out.
 */
export const streamPositionSchema = z.strictObject({
  stream_type: streamTypeSchema,
  stream_id: uuidV7Schema,
  expected_revision: z.int().min(0).optional(),
  sequence: z.int().min(1).optional(),
});

export type StreamPosition = z.infer<typeof streamPositionSchema>;

/** SHA-256 over the RFC 8785 canonical form of the payload. */
export const payloadDigestSchema = z.strictObject({
  algorithm: z.literal("sha256"),
  canonicalization: z.literal("jcs"),
  value: z.string().regex(HEX_SHA256_PATTERN),
});

export type PayloadDigest = z.infer<typeof payloadDigestSchema>;

export { HEX_SHA256_PATTERN };
