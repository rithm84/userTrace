/**
 * The identities and secrets one journey run uses.
 *
 * Every identifier the extension protocol touches is a UUIDv7, because that is
 * what its schemas require. They are fixed rather than random so a failure is
 * reproducible and the web application's own fixture can name the same
 * Interview Session without the two having to negotiate.
 */

import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";

/** Matches `FIXTURE_SESSION_ID` in `apps/web/lib/api/fixture-data.ts`. */
export const SESSION_ID = "0199a0c0-0000-7aaa-8000-0000000055e1";
export const CLIENT_ID = "0199a0c0-0000-7aaa-8000-00000000c101";
export const INSTALLATION_ID = "0199a0c0-0000-7aaa-8000-000000001457";
export const ALLOCATION_ID = "0199a0c0-0000-7aaa-8000-00000000a110";
/** The candidate runtime the session origin advances to once a new version is ready. */
export const CANDIDATE_ALLOCATION_ID = "0199a0c0-0000-7aaa-8000-00000000a111";
export const CANDIDATE_VERSION_ID = "0199a0c0-0000-7aaa-8000-00000000d002";
export const CANDIDATE_BINDING_ID = "0199a0c0-0000-7aaa-8000-0000000b1d02";
export const BINDING_ID = "0199a0c0-0000-7aaa-8000-0000000b1d01";
export const PREVIEW_ENVIRONMENT_ID = "0199a0c0-0000-7aaa-8000-00000000e111";
export const PREVIEW_VERSION_ID = "0199a0c0-0000-7aaa-8000-00000000d001";
export const PRODUCT_SIGNAL_ID = "0199a0c0-0000-7aaa-8000-00000000ff01";
export const PREVIEW_ROUTE_ID = "0199a0c0-0000-7aaa-8000-00000000cc01";
export const CLIENT_USER_ID = "0199a0c0-0000-7aaa-8000-00000000b001";
export const CUSTOMER_ACCOUNT_ID = "0199a0c0-0000-7aaa-8000-00000000ac01";

/** The seeded Dudulemon growth lead the preview handoff signs in. */
export const GROWTH_LEAD_EMAIL = "maya@dudulemon.example.com";

/** The client release the signed annotation manifest was compiled for. */
export const CLIENT_RELEASE_ID = "shopalytics-utrace-sdk-integration";
export const SDK_INSTALLATION_ID = "shopalytics-preview";

/** Secrets the prepared environment injects. Generated per run, never stored. */
export const secrets = {
  controlPlaneServiceToken: randomBytes(24).toString("base64url"),
  sessionCredential: randomBytes(48).toString("base64url"),
  handoffCode: randomBytes(24).toString("base64url"),
  routeContextSigningSecret: randomBytes(24).toString("base64url"),
  previewHandoffSecret: randomBytes(32).toString("base64url"),
  candidateHandoffSecret: randomBytes(32).toString("base64url"),
  previewEntryToken: randomBytes(24).toString("base64url"),
  serverBootstrapCredential: randomBytes(32).toString("base64url"),
  telemetryToken: randomBytes(24).toString("base64url"),
} as const;

/** One Ed25519 identity, used to sign the browser activation envelopes. */
export interface SigningIdentity {
  readonly keyId: string;
  readonly publicKeyB64Url: string;
  readonly sign: (payload: object) => SignedEnvelope;
  readonly digest: (payload: object) => string;
}

export interface SignedEnvelope {
  readonly algorithm: "ed25519";
  readonly key_id: string;
  readonly payload_b64url: string;
  readonly signature_b64url: string;
}

/**
 * The Ed25519 identity the journey signs activation envelopes with.
 *
 * The control plane holds the private half of the key the SDK packages, so the
 * journey cannot sign with it. It generates its own pair per run and installs
 * the public half into the client's vendored SDK for the length of the run
 * (see `support/trust-anchor.ts`), which keeps real WebCrypto verification in
 * the path instead of bypassing it.
 */
export function createBrowserSigningIdentity(keyId: string): SigningIdentity {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const spki = publicKey.export({ type: "spki", format: "der" });
  const raw = spki.subarray(spki.length - 32);
  const privateKeyPem = privateKey.export({ type: "pkcs8", format: "pem" }).toString();
  return {
    keyId,
    publicKeyB64Url: Buffer.from(raw).toString("base64url"),
    sign: (payload: object): SignedEnvelope => {
      const body = Buffer.from(JSON.stringify(payload), "utf8");
      return {
        algorithm: "ed25519",
        key_id: keyId,
        payload_b64url: body.toString("base64url"),
        signature_b64url: sign(null, body, privateKeyPem).toString("base64url"),
      };
    },
    digest: (payload: object): string =>
      `sha256:${createHash("sha256").update(Buffer.from(JSON.stringify(payload), "utf8")).digest("hex")}`,
  };
}

/** A throwaway Ed25519 identity, for the server instrumentation manifest. */
export function createServerSigningIdentity(keyId: string): {
  readonly keyId: string;
  readonly privateKeyPem: string;
  readonly trustAnchorEnvValue: string;
} {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const spki = Buffer.from(publicKey.export({ format: "der", type: "spki" })).toString("base64");
  return {
    keyId,
    privateKeyPem: privateKey.export({ format: "pem", type: "pkcs8" }).toString(),
    trustAnchorEnvValue: Buffer.from(
      JSON.stringify({ key_id: keyId, algorithm: "ed25519", public_key_spki_base64: spki }),
      "utf8",
    ).toString("base64url"),
  };
}
