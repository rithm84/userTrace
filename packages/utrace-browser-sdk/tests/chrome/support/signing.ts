/**
 * Ed25519 signing for the test control plane.
 *
 * Each run generates its own key pair. The public half is written into a
 * generated module that replaces the packaged trust anchor when the fixture is
 * bundled, so the tests exercise real signature verification instead of a
 * bypass, and a fixture signed here cannot verify against a release build.
 */

import { createHash, generateKeyPairSync, sign } from "node:crypto";

export interface SignedEnvelope {
  readonly algorithm: "ed25519";
  readonly key_id: string;
  readonly payload_b64url: string;
  readonly signature_b64url: string;
}

export interface TestSigningKey {
  readonly keyId: string;
  readonly publicKeyB64Url: string;
  readonly sign: (payload: object) => SignedEnvelope;
  readonly digest: (payload: object) => string;
  /** Signs with a different key so the runtime must reject the envelope. */
  readonly signWithForeignKey: (payload: object) => SignedEnvelope;
}

const canonical = (payload: object): Buffer => Buffer.from(JSON.stringify(payload), "utf8");

export const createSigningKey = (keyId: string): TestSigningKey => {
  const pair = generateKeyPairSync("ed25519");
  const spki = pair.publicKey.export({ type: "spki", format: "der" });
  const raw = spki.subarray(spki.length - 32);
  const privateKey = pair.privateKey;
  const foreign = generateKeyPairSync("ed25519").privateKey;

  return {
    keyId,
    publicKeyB64Url: raw.toString("base64url"),
    sign: (payload: object): SignedEnvelope => {
      const body = canonical(payload);
      return {
        algorithm: "ed25519",
        key_id: keyId,
        payload_b64url: body.toString("base64url"),
        signature_b64url: sign(null, body, privateKey).toString("base64url"),
      };
    },
    signWithForeignKey: (payload: object): SignedEnvelope => {
      const body = canonical(payload);
      return {
        algorithm: "ed25519",
        key_id: keyId,
        payload_b64url: body.toString("base64url"),
        signature_b64url: sign(null, body, foreign).toString("base64url"),
      };
    },
    digest: (payload: object): string => `sha256:${createHash("sha256").update(canonical(payload)).digest("hex")}`,
  };
};
