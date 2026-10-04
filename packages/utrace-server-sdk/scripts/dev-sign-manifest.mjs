#!/usr/bin/env node
/**
 * Development manifest signer.
 *
 * Produces the signed server instrumentation manifest the prepared-environment
 * runtime hands the SDK. The signature covers the manifest's RFC 8785 canonical
 * form, produced by the SDK's own `canonicalJson`, so the signer and the
 * verifier cannot disagree about the bytes.
 *
 * Usage:
 *   node scripts/dev-sign-manifest.mjs \
 *     --manifest build/server-manifest.json \
 *     --key build/server-signing-key.json \
 *     --out build/server-manifest.signed.json
 */

import { createPrivateKey, sign } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

import { canonicalJson } from '../dist/contracts/canonical-json.js';

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(String(process.argv[index]).slice(2), process.argv[index + 1]);
}
for (const required of ['manifest', 'key', 'out']) {
  if (!args.has(required)) {
    process.stderr.write(`dev-sign-manifest: --${required} is required\n`);
    process.exit(1);
  }
}

const manifest = JSON.parse(readFileSync(args.get('manifest'), 'utf8'));
const key = JSON.parse(readFileSync(args.get('key'), 'utf8'));
const privateKey = createPrivateKey(key.private_key_pkcs8_pem);

const payload = Buffer.from(canonicalJson(manifest, new Date()), 'utf8');
const signed = {
  manifest,
  signature: {
    key_id: key.key_id,
    algorithm: 'ed25519',
    value: sign(null, payload, privateKey).toString('base64'),
  },
};

mkdirSync(dirname(args.get('out')), { recursive: true });
writeFileSync(args.get('out'), `${JSON.stringify(signed, null, 2)}\n`);
process.stdout.write(`dev-sign-manifest: wrote ${args.get('out')}\n`);
