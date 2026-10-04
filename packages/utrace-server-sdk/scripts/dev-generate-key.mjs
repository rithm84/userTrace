#!/usr/bin/env node
/**
 * Development manifest signing key.
 *
 * uTrace signs a client release's server instrumentation manifest with a key
 * that never leaves uTrace infrastructure. This script creates a local Ed25519
 * key so a client can sign a manifest while integrating and start a prepared
 * preview against it: the SDK verifies whatever key the prepared environment
 * names in `UTRACE_SERVER_MANIFEST_TRUST_ANCHOR`, so a development key works
 * only where that variable points at it.
 *
 * Usage:
 *   node scripts/dev-generate-key.mjs --key-id dev-local --out build/server-signing-key.json
 */

import { generateKeyPairSync } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(String(process.argv[index]).slice(2), process.argv[index + 1]);
}
const keyId = args.get('key-id') ?? 'dev-local';
const out = args.get('out') ?? 'build/server-signing-key.json';

const { publicKey, privateKey } = generateKeyPairSync('ed25519');
const publicKeySpkiBase64 = Buffer.from(publicKey.export({ type: 'spki', format: 'der' })).toString(
  'base64',
);
const trustAnchor = Buffer.from(
  JSON.stringify({ key_id: keyId, algorithm: 'ed25519', public_key_spki_base64: publicKeySpkiBase64 }),
  'utf8',
).toString('base64url');

mkdirSync(dirname(out), { recursive: true });
writeFileSync(
  out,
  `${JSON.stringify(
    {
      key_id: keyId,
      algorithm: 'ed25519',
      public_key_spki_base64: publicKeySpkiBase64,
      private_key_pkcs8_pem: privateKey.export({ type: 'pkcs8', format: 'pem' }).toString(),
      trust_anchor_env_value: trustAnchor,
      warning: 'development only; never commit this file or use it for a client release',
    },
    null,
    2,
  )}\n`,
  { mode: 0o600 },
);
process.stdout.write(`dev-generate-key: wrote ${out}\n`);
process.stdout.write(`dev-generate-key: UTRACE_SERVER_MANIFEST_TRUST_ANCHOR ${trustAnchor}\n`);
