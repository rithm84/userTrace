/**
 * Signed envelope verification.
 *
 * The document attestation, annotation manifest, and extension page proof all
 * arrive as detached Ed25519 signatures over a base64url JSON payload. The
 * bootstrap verifies them with a packaged public key through WebCrypto before
 * any field is read. An unverifiable envelope is a failure, never a warning.
 */

import { decodeBase64Url, utf8Decode } from "../core/base64.js";
import { ActivationError } from "../core/failure.js";
import type { JsonObject } from "../core/json.js";
import { asObject, parseJson, requireEnum, requireString } from "../core/json.js";
import type { PackagedSigningKey } from "./signing-keys.js";
import { findPackagedKey } from "./signing-keys.js";

export interface SignedEnvelope {
  readonly algorithm: "ed25519";
  readonly key_id: string;
  readonly payload_b64url: string;
  readonly signature_b64url: string;
}

export const decodeSignedEnvelope = (source: JsonObject, path: string): SignedEnvelope => ({
  algorithm: requireEnum(source, "algorithm", ["ed25519"] as const, path),
  key_id: requireString(source, "key_id", path),
  payload_b64url: requireString(source, "payload_b64url", path),
  signature_b64url: requireString(source, "signature_b64url", path),
});

/** Resolves a packaged key, or reports which key the envelope named. */
const resolveKey = (envelope: SignedEnvelope, path: string): PackagedSigningKey => {
  const key = findPackagedKey(envelope.key_id);
  if (key === null) {
    throw new ActivationError("attestation_invalid_or_expired", `${path} names an unpackaged signing key`, {
      key_id: envelope.key_id,
      path,
    });
  }
  return key;
};

/**
 * Verifies the detached signature and returns the decoded payload object.
 *
 * Chrome exposes Ed25519 through WebCrypto from version 137. On an older
 * browser `importKey` rejects and the runtime fails closed with
 * `attestation_algorithm_unsupported` rather than skipping verification.
 */
export const verifySignedEnvelope = async (
  envelope: SignedEnvelope,
  subtle: SubtleCrypto,
  path: string,
): Promise<JsonObject> => {
  const key = resolveKey(envelope, path);
  const publicKeyBytes = decodeBase64Url(key.public_key_b64url, `${path}.packaged_key`);
  const signature = decodeBase64Url(envelope.signature_b64url, `${path}.signature_b64url`);
  const payloadBytes = decodeBase64Url(envelope.payload_b64url, `${path}.payload_b64url`);

  let cryptoKey: CryptoKey;
  try {
    cryptoKey = await subtle.importKey("raw", publicKeyBytes, { name: "Ed25519" }, false, ["verify"]);
  } catch (cause) {
    throw new ActivationError(
      "attestation_algorithm_unsupported",
      `this browser cannot verify Ed25519 signatures through WebCrypto (${String(cause)})`,
      { path, key_id: envelope.key_id, algorithm: envelope.algorithm },
    );
  }

  const verified = await subtle.verify({ name: "Ed25519" }, cryptoKey, signature, payloadBytes);
  if (!verified) {
    throw new ActivationError("attestation_invalid_or_expired", `${path} signature does not verify`, {
      path,
      key_id: envelope.key_id,
      payload_bytes: payloadBytes.byteLength,
    });
  }
  return asObject(parseJson(utf8Decode(payloadBytes), `${path}.payload`), `${path}.payload`);
};

/** SHA-256 digest of the signed payload bytes, formatted as the manifest digest. */
export const payloadDigest = async (envelope: SignedEnvelope, subtle: SubtleCrypto, path: string): Promise<string> => {
  const payloadBytes = decodeBase64Url(envelope.payload_b64url, `${path}.payload_b64url`);
  const digest = new Uint8Array(await subtle.digest("SHA-256", payloadBytes));
  let hex = "";
  for (const byte of digest) hex += byte.toString(16).padStart(2, "0");
  return `sha256:${hex}`;
};
