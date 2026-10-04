/** Identifier formats, the first-class identifier registry, and `newId`. */

export {
  IDENTIFIER_NAMES,
  UUID_V7_PATTERN,
  domainIdsSchema,
  identifierNameSchema,
  newId,
  uuidV7Schema,
  type DomainIds,
  type IdentifierName,
  type UuidV7,
} from "./ids.js";
export {
  areaContracts,
  parseIdentifierName,
  parseIdentifiers,
  parseUuidV7,
} from "./contracts.js";
