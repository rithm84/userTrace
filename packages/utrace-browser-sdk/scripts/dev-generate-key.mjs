#!/usr/bin/env node
/**
 * Development signing key generator.
 *
 * Production manifests and attestations are signed by uTrace with a key that
 * never leaves uTrace infrastructure and never appears in this repository. This
 * script creates a local development key so a client can sign fixture manifests
 * while integrating. A fixture signed with it cannot verify against a release
 * build, because its key id is not in src/activation/signing-keys.ts.
 *
 * Usage: node scripts/dev-generate-key.mjs --key-id dev-local --out scripts/keys/dev-signing-key.json
 */

import { generateKeyPairSync } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(String(process.argv[index]).slice(2), process.argv[index + 1]);
}
const keyId = args.get("key-id") ?? "dev-local";
const out = args.get("out") ?? "scripts/keys/dev-signing-key.json";

const { publicKey, privateKey } = generateKeyPairSync("ed25519");
const spki = publicKey.export({ type: "spki", format: "der" });
const raw = spki.subarray(spki.length - 32);

mkdirSync(dirname(out), { recursive: true });
writeFileSync(
  out,
  `${JSON.stringify(
    {
      key_id: keyId,
      algorithm: "ed25519",
      public_key_b64url: raw.toString("base64url"),
      private_key_pkcs8_b64: privateKey.export({ type: "pkcs8", format: "der" }).toString("base64"),
      warning: "development only; never commit this file or use it for a client release",
    },
    null,
    2,
  )}\n`,
  { mode: 0o600 },
);
process.stdout.write(`dev-generate-key: wrote ${out}\n`);
process.stdout.write(`dev-generate-key: public key ${raw.toString("base64url")}\n`);
