/**
 * The compatibility manifest components advertise during authenticated readiness.
 *
 * Envelope and payload majors are independent. Additive optional fields may stay
 * inside a major; removed, renamed, newly required, or semantically changed
 * fields require a new one. A peer that cannot meet a required major fails
 * readiness with the `incompatible_version` category, because there is no
 * lower-fidelity substitution.
 *
 * `supported_majors` is ordered most preferred first rather than carrying a
 * separate preference field, so "the preferred major is supported" is structural
 * and both bindings enforce it without a cross-field rule the JSON Schema could
 * not express.
 */

import { z } from "zod";

import { producerSchema, timestampSchema } from "./values.js";

/** Area-namespaced payload name, for example `product_signal.created`. */
export const PAYLOAD_TYPE_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/;

export const payloadTypeSchema = z.string().regex(PAYLOAD_TYPE_PATTERN).max(128);

export type PayloadType = z.infer<typeof payloadTypeSchema>;

/** One announced window. After `sunset_at` the major stops being accepted. */
export const deprecationWindowSchema = z.strictObject({
  deprecated_at: timestampSchema,
  sunset_at: timestampSchema,
  replacement_major: z.int().min(1),
});

/** One supported major, with its deprecation window when one is announced. */
export const majorSupportSchema = z.strictObject({
  major: z.int().min(1),
  deprecation: deprecationWindowSchema.optional(),
});

/** The majors one contract supports, most preferred first. */
export const contractSupportSchema = z.strictObject({
  supported_majors: z.array(majorSupportSchema).min(1).max(16),
});

/** Support for one area-namespaced payload type. */
export const payloadSupportSchema = z.strictObject({
  payload_type: payloadTypeSchema,
  support: contractSupportSchema,
});

/** What one component supports. A payload type absent from `payloads` is unsupported. */
export const compatibilityManifestSchema = z.strictObject({
  component: producerSchema,
  generated_at: timestampSchema,
  envelope: contractSupportSchema,
  payloads: z.array(payloadSupportSchema).max(512),
});

export type DeprecationWindow = z.infer<typeof deprecationWindowSchema>;
export type MajorSupport = z.infer<typeof majorSupportSchema>;
export type ContractSupport = z.infer<typeof contractSupportSchema>;
export type PayloadSupport = z.infer<typeof payloadSupportSchema>;
export type CompatibilityManifest = z.infer<typeof compatibilityManifestSchema>;
