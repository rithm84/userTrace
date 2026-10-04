#!/usr/bin/env node
/**
 * Development manifest signer.
 *
 * Produces the signed envelope the activation response carries, plus the
 * SHA-256 digest the document attestation pins. uTrace holds the production
 * key; this signer exists so a client can exercise the gate locally.
 *
 * Usage:
 *   node scripts/dev-sign-manifest.mjs \
 *     --manifest build/utrace-manifest.json \
 *     --key scripts/keys/dev-signing-key.json \
 *     --out build/utrace-manifest.signed.json
 */

import { createHash, createPrivateKey, sign } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(String(process.argv[index]).slice(2), process.argv[index + 1]);
}
for (const required of ["manifest", "key", "out"]) {
  if (!args.has(required)) {
    process.stderr.write(`dev-sign-manifest: --${required} is required\n`);
    process.exit(1);
  }
}

const key = JSON.parse(readFileSync(args.get("key"), "utf8"));
const payload = Buffer.from(readFileSync(args.get("manifest"), "utf8").trim(), "utf8");
const privateKey = createPrivateKey({
  key: Buffer.from(key.private_key_pkcs8_b64, "base64"),
  format: "der",
  type: "pkcs8",
});

const envelope = {
  algorithm: "ed25519",
  key_id: key.key_id,
  payload_b64url: payload.toString("base64url"),
  signature_b64url: sign(null, payload, privateKey).toString("base64url"),
};

mkdirSync(dirname(args.get("out")), { recursive: true });
writeFileSync(args.get("out"), `${JSON.stringify(envelope, null, 2)}\n`);
const digest = `sha256:${createHash("sha256").update(payload).digest("hex")}`;
process.stdout.write(`dev-sign-manifest: wrote ${args.get("out")}\n`);
process.stdout.write(`dev-sign-manifest: annotation_manifest_digest ${digest}\n`);
