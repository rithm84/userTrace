/**
 * Packaged uTrace signing trust anchor.
 *
 * The SDK ships only public keys. uTrace holds the corresponding private keys;
 * they are never present in this repository, a client build, or a browser. Key
 * rotation ships as a new SDK release that adds the new `key_id` and, after the
 * overlap window, removes the retired one.
 *
 * Development builds sign fixtures with a locally generated key produced by
 * `scripts/dev-generate-key.mjs`; that key is not listed here, so a fixture
 * signed with it cannot verify against a production build.
 *
 * Verification runs through WebCrypto's Ed25519 implementation, which Chrome
 * only ships from 137. A Chrome between the extension's 116 baseline and 136
 * therefore cannot verify any of these keys and fails the activation gate; the
 * demo and pilot browsers must be Chrome 137 or newer.
 */

export interface PackagedSigningKey {
  readonly key_id: string;
  readonly algorithm: "ed25519";
  /** Raw 32-byte Ed25519 public key, base64url encoded. */
  readonly public_key_b64url: string;
}

export const PACKAGED_SIGNING_KEYS: readonly PackagedSigningKey[] = Object.freeze([
  Object.freeze({
    key_id: "utrace-browser-2026-09",
    algorithm: "ed25519",
    public_key_b64url: "4t-E1-BsWr3jTs22ROdm1Gqn2sPw5YNPVjwV1Rfo8u4",
  }),
]);

export const findPackagedKey = (keyId: string): PackagedSigningKey | null =>
  PACKAGED_SIGNING_KEYS.find((key) => key.key_id === keyId) ?? null;
