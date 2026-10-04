/**
 * Re-signing the client's server instrumentation manifest for one run.
 *
 * The Shopalytics checkout signs its manifest with a development key it
 * generated, which the journey does not hold. uTrace signs a release manifest
 * in production, so the journey plays that part: it takes the client's manifest
 * document unchanged and signs it with the identity the preview's trust anchor
 * names. The manifest itself is never edited — its content is the client's
 * declaration, and re-writing it here would prove nothing about the client.
 *
 * The signature covers the manifest's RFC 8785 canonical form, which is what
 * the Server SDK verifies.
 */

import { sign } from "node:crypto";

import { canonicalJson } from "../../../packages/utrace-server-sdk/src/contracts/canonical-json.js";
import type {
  ServerInstrumentationManifest,
  SignedServerInstrumentationManifest,
} from "../../../packages/utrace-server-sdk/src/contracts/manifest.js";

export interface ManifestSigner {
  readonly keyId: string;
  readonly privateKeyPem: string;
}

/** Reads the client's signed manifest and re-signs its payload. */
export function resignServerManifest(
  clientSigned: unknown,
  signer: ManifestSigner,
): SignedServerInstrumentationManifest {
  const manifest = (clientSigned as { manifest?: unknown }).manifest;
  if (manifest === undefined || manifest === null || typeof manifest !== "object") {
    throw new TypeError("the client's signed manifest carries no manifest document");
  }
  const payload = Buffer.from(canonicalJson(manifest, new Date()), "utf8");
  return {
    manifest: manifest as ServerInstrumentationManifest,
    signature: {
      key_id: signer.keyId,
      algorithm: "ed25519",
      value: sign(null, payload, signer.privateKeyPem).toString("base64"),
    },
  };
}
