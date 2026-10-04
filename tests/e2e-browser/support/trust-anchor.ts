/**
 * The trust anchor the journey's preview verifies against.
 *
 * The browser SDK ships only public keys, and the control plane holds the
 * private half of the key it packages. The journey cannot sign with that key,
 * so it generates its own pair and installs the public half into the copy of
 * the SDK the client checkout has vendored, for the length of the run.
 *
 * This is the same thing the browser SDK's own Chrome suite does, and for the
 * same reason: swapping the anchor keeps real signature verification in the
 * path rather than bypassing it, so an attestation this run produces has to
 * verify through WebCrypto exactly as a control-plane one would. The packaged
 * key itself is never edited, and `restore` puts the installed file back.
 */

import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const MODULE_PATH = "node_modules/@utrace/browser-sdk/dist/activation/signing-keys.js";

export interface InstalledTrustAnchor {
  readonly restore: () => void;
}

/**
 * Replaces the packaged key list in the client's installed SDK with one key.
 *
 * It refuses a file it does not recognise, so a changed SDK fails the journey
 * rather than silently running against the production anchor it cannot sign
 * for.
 */
export function installTrustAnchor(params: {
  readonly clientRoot: string;
  readonly keyId: string;
  readonly publicKeyB64Url: string;
}): InstalledTrustAnchor {
  const path = resolve(params.clientRoot, MODULE_PATH);
  const original = readFileSync(path, "utf8");
  if (!original.includes("PACKAGED_SIGNING_KEYS")) {
    throw new Error(
      `${path} does not look like the browser SDK's packaged trust anchor; the journey will not replace it.`,
    );
  }
  copyFileSync(path, `${path}.journey-backup`);
  writeFileSync(
    path,
    [
      "// Replaced for one uTrace journey run. The original is beside this file.",
      "export const PACKAGED_SIGNING_KEYS = Object.freeze([",
      `  Object.freeze({ key_id: ${JSON.stringify(params.keyId)}, algorithm: "ed25519", public_key_b64url: ${JSON.stringify(params.publicKeyB64Url)} }),`,
      "]);",
      "export const findPackagedKey = (keyId) => PACKAGED_SIGNING_KEYS.find((key) => key.key_id === keyId) ?? null;",
      "",
    ].join("\n"),
  );
  return {
    restore: (): void => {
      writeFileSync(path, original);
    },
  };
}
