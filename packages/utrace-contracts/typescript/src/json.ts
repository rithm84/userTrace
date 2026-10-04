/** The decoded-JSON value types the contracts are written against. */

/** Any decoded-JSON value. Recursive, so a nested payload stays typed end to end. */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

/** An open JSON object an area schema constrains, such as a payload or details block. */
export type JsonObject = { readonly [key: string]: JsonValue };

/**
 * What the canonicalizer accepts.
 *
 * Identical to {@link JsonValue} except that an object property may be
 * `undefined`, which is how TypeScript models an omitted optional field and
 * which JSON serialization drops. Python has no equivalent, so this widening
 * does not change what the two bindings canonicalize.
 */
export type CanonicalizableValue =
  | string
  | number
  | boolean
  | null
  | readonly CanonicalizableValue[]
  | { readonly [key: string]: CanonicalizableValue | undefined };
