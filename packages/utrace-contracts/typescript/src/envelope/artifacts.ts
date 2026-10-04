/**
 * The immutable artifact reference.
 *
 * Large evidence never travels inside a domain message. It becomes an opaque
 * reference that carries identity, exact type, digest, size, creation time,
 * tenant scope, and retention class, and nothing else. There is deliberately no
 * bucket name, filesystem path, permanent URL, or credential: resolution goes
 * through the control plane, which rechecks tenant and object linkage and
 * verifies type, size, and digest before returning anything. The strict object
 * is what enforces that here, so an attempt to smuggle a path in is a parse
 * error.
 */

import { z } from "zod";

import { uuidV7Schema } from "../ids/ids.js";
import {
  HEX_SHA256_PATTERN,
  clientScopeSchema,
  snakeCaseNameSchema,
  timestampSchema,
} from "./values.js";

const MEDIA_TYPE_PATTERN =
  /^[a-z][a-z0-9!#$&^_.+-]{0,126}\/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}(; *[a-z0-9!#$&^_.+-]+=[^;]+)*$/;

/**
 * Which retention, export, and deletion rules govern the artifact.
 *
 * `participant_personal` is a participant's own audio and transcripts, included
 * in participant export and removed by participant deletion. `client_evidence`
 * is client-scoped product evidence and output, immutable after acceptance and
 * excluded from participant export. `operational` is build, check, and journal
 * material on the shortest retention. `audit_minimized` is the minimized audit
 * fact deletion preserves with references tombstoned. The class is fixed at
 * creation; hold and deletion state live on the durable row, not here.
 */
export const RETENTION_CLASSES = [
  "participant_personal",
  "client_evidence",
  "operational",
  "audit_minimized",
] as const;

export const retentionClassSchema = z.enum(RETENTION_CLASSES);

export type RetentionClass = z.infer<typeof retentionClassSchema>;

/** SHA-256 over the artifact's exact stored bytes. */
export const artifactDigestSchema = z.strictObject({
  algorithm: z.literal("sha256"),
  value: z.string().regex(HEX_SHA256_PATTERN),
});

export type ArtifactDigest = z.infer<typeof artifactDigestSchema>;

/** An opaque, immutable reference to one stored artifact. */
export const artifactReferenceSchema = z.strictObject({
  artifact_id: uuidV7Schema,
  artifact_type: snakeCaseNameSchema,
  schema_version: z.int().min(1),
  media_type: z.string().regex(MEDIA_TYPE_PATTERN).max(255),
  digest: artifactDigestSchema,
  byte_size: z.int().min(0),
  created_at: timestampSchema,
  client_scope: clientScopeSchema,
  retention_class: retentionClassSchema,
});

export type ArtifactReference = z.infer<typeof artifactReferenceSchema>;

export { MEDIA_TYPE_PATTERN };
