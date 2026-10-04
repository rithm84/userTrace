#!/usr/bin/env node
/**
 * Signed manifest verifier.
 *
 * Checks the detached Ed25519 signature against a trusted public key, then
 * validates the payload against the published annotation-manifest schema, and
 * prints the digest the attestation must pin. It is the same sequence the
 * runtime performs, so a manifest this script rejects cannot activate.
 *
 * Usage:
 *   node scripts/verify-manifest.mjs \
 *     --signed build/utrace-manifest.signed.json \
 *     --public-key <base64url raw key>   # or --key scripts/keys/dev-signing-key.json
 */

import { createHash, createPublicKey, verify } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const here = dirname(fileURLToPath(import.meta.url));
const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(String(process.argv[index]).slice(2), process.argv[index + 1]);
}
const fail = (message) => {
  process.stderr.write(`verify-manifest: ${message}\n`);
  process.exit(1);
};
if (!args.has("signed")) fail("--signed is required");

const envelope = JSON.parse(readFileSync(args.get("signed"), "utf8"));
if (envelope.algorithm !== "ed25519") fail(`unsupported algorithm ${String(envelope.algorithm)}`);

const publicKeyB64Url = args.has("public-key")
  ? args.get("public-key")
  : args.has("key")
    ? JSON.parse(readFileSync(args.get("key"), "utf8")).public_key_b64url
    : fail("--public-key or --key is required");

const spki = Buffer.concat([
  Buffer.from("302a300506032b6570032100", "hex"),
  Buffer.from(publicKeyB64Url, "base64url"),
]);
const publicKey = createPublicKey({ key: spki, format: "der", type: "spki" });
const payload = Buffer.from(envelope.payload_b64url, "base64url");
if (!verify(null, payload, publicKey, Buffer.from(envelope.signature_b64url, "base64url"))) {
  fail("signature does not verify against the supplied key");
}

const schemasDir = resolve(here, "../../utrace-contracts/schemas/browser-observation");
const ajv = new Ajv2020({ strict: true, allErrors: true, allowUnionTypes: true });
addFormats(ajv);
for (const file of readdirSync(schemasDir).filter((name) => name.endsWith(".schema.json"))) {
  ajv.addSchema(JSON.parse(readFileSync(join(schemasDir, file), "utf8")));
}
const validate = ajv.getSchema("https://utrace.dev/schemas/browser-observation/annotation-manifest/v1");
const manifest = JSON.parse(payload.toString("utf8"));
if (!validate(manifest)) fail(`payload violates the manifest schema: ${JSON.stringify(validate.errors)}`);

process.stdout.write(`verify-manifest: signature and schema are valid for key ${String(envelope.key_id)}\n`);
process.stdout.write(
  `verify-manifest: annotation_manifest_digest sha256:${createHash("sha256").update(payload).digest("hex")}\n`,
);
