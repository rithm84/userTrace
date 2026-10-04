/**
 * Ed25519 verification of the signed server instrumentation manifest.
 *
 * Verification is mandatory: an unsigned, wrongly signed, or wrongly keyed
 * manifest fails activation rather than being accepted at lower fidelity.
 */

import { createPublicKey, verify as verifySignature } from 'node:crypto';

import { canonicalJson } from './canonical-json.js';
import type {
  ServerInstrumentationManifest,
  SignedServerInstrumentationManifest,
} from './manifest.js';
import type { ManifestTrustAnchor } from '../environment.js';
import { failureError } from '../errors.js';

/**
 * Verifies the detached signature over the manifest's canonical JSON form and
 * returns the manifest it covers.
 */
export function verifySignedManifest(
  signed: SignedServerInstrumentationManifest,
  anchor: ManifestTrustAnchor,
  now: Date,
): ServerInstrumentationManifest {
  if (signed.signature.key_id !== anchor.keyId) {
    throw failureError('The manifest is signed by an untrusted key.', {
      code: 'manifest_signature_invalid',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { reason: 'unknown_key_id', presented_key_id: signed.signature.key_id },
    }, now);
  }

  const payload = Buffer.from(canonicalJson(signed.manifest, now), 'utf8');
  const signatureBytes = Buffer.from(signed.signature.value, 'base64');

  let publicKey;
  try {
    publicKey = createPublicKey({
      key: Buffer.from(anchor.publicKeySpkiBase64, 'base64'),
      format: 'der',
      type: 'spki',
    });
  } catch {
    throw failureError('The configured manifest trust anchor is not a usable SPKI key.', {
      code: 'manifest_signature_invalid',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { reason: 'trust_anchor_unreadable' },
    }, now);
  }

  if (!verifySignature(null, payload, publicKey, signatureBytes)) {
    throw failureError('The manifest signature does not verify.', {
      code: 'manifest_signature_invalid',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { reason: 'signature_mismatch', key_id: anchor.keyId },
    }, now);
  }

  return signed.manifest;
}
