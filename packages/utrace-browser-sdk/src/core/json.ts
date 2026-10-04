/**
 * Minimal typed JSON model plus decoding primitives.
 *
 * Every value that crosses a trust boundary (activation responses, ingestion
 * frames, extension bridge messages, signed manifests, DOM attributes) is
 * decoded through these helpers so the SDK never carries `any` or `unknown`
 * into its own types and never silently accepts a missing or mistyped field.
 */

export type JsonPrimitive = string | number | boolean | null;
export type JsonArray = readonly JsonValue[];
export type JsonValue = JsonPrimitive | JsonArray | JsonObject;
export type JsonObject = { readonly [key: string]: JsonValue | undefined };
/**
 * A member may be `undefined` because the contracts omit an optional value
 * rather than sending `null`, and `JSON.stringify` drops such a key. Decoded
 * data never produces one; only values the SDK builds for export do.
 */

const isJsonObject = (value: JsonValue): value is JsonObject =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isJsonArray = (value: JsonValue): value is JsonArray => Array.isArray(value);

/** Raised when external data does not match the contract the SDK requires. */
export class ContractViolationError extends Error {
  readonly path: string;
  readonly received: string;

  constructor(path: string, expected: string, received: string) {
    super(`uTrace contract violation at ${path}: expected ${expected}, received ${received}`);
    this.name = "ContractViolationError";
    this.path = path;
    this.received = received;
  }
}

const describe = (value: JsonValue | undefined): string => {
  if (value === undefined) return "undefined";
  if (value === null) return "null";
  if (Array.isArray(value)) return `array(${String(value.length)})`;
  return typeof value;
};

export const parseJson = (text: string, path: string): JsonValue => {
  try {
    return JSON.parse(text) as JsonValue;
  } catch (cause) {
    throw new ContractViolationError(path, "JSON text", `unparsable (${String(cause)})`);
  }
};

export const asObject = (value: JsonValue | undefined, path: string): JsonObject => {
  if (value === undefined || !isJsonObject(value)) {
    throw new ContractViolationError(path, "object", describe(value));
  }
  return value;
};

export const asArray = (value: JsonValue | undefined, path: string): JsonArray => {
  if (value === undefined || !isJsonArray(value)) throw new ContractViolationError(path, "array", describe(value));
  return value;
};

export const requireString = (source: JsonObject, key: string, path: string): string => {
  const value = source[key];
  if (typeof value !== "string" || value.length === 0) {
    throw new ContractViolationError(`${path}.${key}`, "non-empty string", describe(value));
  }
  return value;
};

export const requireNumber = (source: JsonObject, key: string, path: string): number => {
  const value = source[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ContractViolationError(`${path}.${key}`, "finite number", describe(value));
  }
  return value;
};

export const requireInteger = (source: JsonObject, key: string, path: string): number => {
  const value = requireNumber(source, key, path);
  if (!Number.isSafeInteger(value)) {
    throw new ContractViolationError(`${path}.${key}`, "safe integer", String(value));
  }
  return value;
};

export const requireBoolean = (source: JsonObject, key: string, path: string): boolean => {
  const value = source[key];
  if (typeof value !== "boolean") {
    throw new ContractViolationError(`${path}.${key}`, "boolean", describe(value));
  }
  return value;
};

export const requireObject = (source: JsonObject, key: string, path: string): JsonObject =>
  asObject(source[key], `${path}.${key}`);

export const requireArray = (source: JsonObject, key: string, path: string): JsonArray =>
  asArray(source[key], `${path}.${key}`);

export const requireStringArray = (source: JsonObject, key: string, path: string): readonly string[] =>
  requireArray(source, key, path).map((entry, index) => {
    if (typeof entry !== "string" || entry.length === 0) {
      throw new ContractViolationError(`${path}.${key}[${String(index)}]`, "non-empty string", describe(entry));
    }
    return entry;
  });

export const requireEnum = <T extends string>(
  source: JsonObject,
  key: string,
  allowed: readonly T[],
  path: string,
): T => {
  const value = requireString(source, key, path);
  const match = allowed.find((candidate) => candidate === value);
  if (match === undefined) {
    throw new ContractViolationError(`${path}.${key}`, `one of ${allowed.join("|")}`, value);
  }
  return match;
};

/** Reads an optional string. A present-but-wrong-typed value still fails closed. */
export const optionalString = (source: JsonObject, key: string, path: string): string | null => {
  const value = source[key];
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || value.length === 0) {
    throw new ContractViolationError(`${path}.${key}`, "non-empty string or absent", describe(value));
  }
  return value;
};
