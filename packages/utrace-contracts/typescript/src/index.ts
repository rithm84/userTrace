/**
 * Versioned uTrace contract bindings.
 *
 * `@utrace/contracts/envelope` and `@utrace/contracts/ids` are the base every
 * other area composes with. Area workstreams add `src/<area>/index.ts` and
 * `src/<area>/contracts.ts`; the package's wildcard subpath export publishes it
 * and the conformance suite discovers it, so nothing here needs editing to
 * register one.
 *
 * `areaContracts` is deliberately not re-exported here: it is per-area discovery
 * metadata, reached through the area's own subpath, not a flat symbol that two
 * areas would collide on.
 *
 * The JSON Schemas under `packages/utrace-contracts/schemas` are the source of
 * truth. These zod schemas are the binding application code holds, and the
 * shared fixtures prove the two agree in both languages.
 */

export { DIGEST_ALGORITHM, canonicalize, payloadDigest } from "./canonical.js";
export {
  CanonicalizationError,
  ContractError,
  DigestMismatchError,
  UnknownContractError,
} from "./errors.js";
export type { CanonicalizableValue, JsonObject, JsonValue } from "./json.js";
export { FIXTURES_ROOT, SCHEMAS_ROOT } from "./locations.js";
export { AREA_CONTRACTS_EXPORT, type ContractBindings, type ContractParser } from "./registry.js";

export * from "./envelope/actors.js";
export * from "./envelope/artifacts.js";
export * from "./envelope/compatibility.js";
export * from "./envelope/failures.js";
export * from "./envelope/messages.js";
export * from "./envelope/values.js";
export {
  parseActor,
  parseArtifactReference,
  parseCommand,
  parseCompatibilityManifest,
  parseEnvelope,
  parseEvent,
  parseFailure,
  parseResult,
  parseResultBody,
} from "./envelope/contracts.js";

export * from "./ids/ids.js";
export { parseIdentifierName, parseIdentifiers, parseUuidV7 } from "./ids/contracts.js";
