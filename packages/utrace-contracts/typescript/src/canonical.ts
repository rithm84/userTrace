/**
 * Payload canonicalization and digesting.
 *
 * Messages cross two languages, several transports, and a durable log, so the
 * digest has to be reproducible from the payload alone. The envelope therefore
 * pins one algorithm pair: RFC 8785 JSON Canonicalization Scheme, then SHA-256
 * over its UTF-8 bytes, rendered as lowercase hexadecimal.
 *
 * JCS is the only canonicalization here because it is the published standard
 * for exactly this problem: it fixes key ordering by UTF-16 code unit, number
 * formatting by the ECMAScript `Number::toString` algorithm, and string
 * escaping, which is what makes the Python and TypeScript bindings agree byte
 * for byte. `fixtures/_digest/vectors.json` is the shared proof.
 */

import { createHash } from "node:crypto";
import serialize from "canonicalize";

import { CanonicalizationError } from "./errors.js";
import type { CanonicalizableValue } from "./json.js";

/** The one digest algorithm the envelope contract allows. */
export const DIGEST_ALGORITHM = "sha256";

/**
 * Return the RFC 8785 canonical form of `payload`.
 *
 * @throws {CanonicalizationError} if the value is not canonicalizable JSON.
 */
export function canonicalize(payload: CanonicalizableValue): string {
  const canonical = serialize(payload);
  if (canonical === undefined) {
    throw new CanonicalizationError(
      "the value canonicalizes to nothing, which happens for undefined and for a " +
        "function or symbol reached through a property",
    );
  }
  return canonical;
}

/** Return the lowercase hex SHA-256 of the canonical form of `payload`. */
export function payloadDigest(payload: CanonicalizableValue): string {
  return createHash(DIGEST_ALGORITHM).update(canonicalize(payload), "utf8").digest("hex");
}
