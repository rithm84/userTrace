import { z } from "zod";

/**
 * A response field the control plane sends as `null` when it holds no value.
 * The web reads an empty value one way, as absent, so `null` and a missing key
 * parse to the same `undefined`.
 */
export function absentWhenNull<Schema extends z.ZodType>(schema: Schema) {
  return z.preprocess((value) => value ?? undefined, schema.optional());
}
