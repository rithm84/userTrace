/** Public parsers for the ids area and its binding registry. */

import type { ContractBindings } from "../registry.js";
import {
  domainIdsSchema,
  identifierNameSchema,
  uuidV7Schema,
  type DomainIds,
  type IdentifierName,
  type UuidV7,
} from "./ids.js";

/** Validate one canonical lowercase UUIDv7 string. */
export function parseUuidV7(value: unknown): UuidV7 {
  return uuidV7Schema.parse(value);
}

/** Validate one first-class identifier name. */
export function parseIdentifierName(value: unknown): IdentifierName {
  return identifierNameSchema.parse(value);
}

/** Validate a typed domain identifier object. */
export function parseIdentifiers(value: unknown): DomainIds {
  return domainIdsSchema.parse(value);
}

export const areaContracts: ContractBindings = {
  "uuid-v7.v1": parseUuidV7,
  "identifier-name.v1": parseIdentifierName,
  "identifiers.v1": parseIdentifiers,
};
