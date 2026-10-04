/**
 * RFC 8785 (JSON Canonicalization Scheme) serialization.
 *
 * The manifest signature covers the canonical form so the SDK and the signer
 * agree on the exact bytes without depending on key order or JSON formatting.
 */

import { ServerSdkError, createFailure } from '../errors.js';

function canonicalizeString(value: string): string {
  return JSON.stringify(value);
}

function canonicalizeNumber(value: number, now: Date): string {
  if (!Number.isFinite(value)) {
    throw new ServerSdkError(
      'Canonical JSON cannot represent a non-finite number.',
      createFailure(
        {
          code: 'manifest_signature_invalid',
          retry: 'never',
          readiness_consequence: 'fail_stream',
          detail: { reason: 'non_finite_number' },
        },
        now,
      ),
    );
  }
  // JCS mandates ECMAScript `Number::toString`, which `JSON.stringify` uses.
  return JSON.stringify(value);
}

/**
 * Serializes `value` to its canonical JSON form.
 *
 * Object members are sorted by UTF-16 code unit order, `undefined` members are
 * omitted, and no insignificant whitespace is emitted.
 */
export function canonicalJson(value: unknown, now: Date): string {
  if (value === null) {
    return 'null';
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false';
  }
  if (typeof value === 'number') {
    return canonicalizeNumber(value, now);
  }
  if (typeof value === 'string') {
    return canonicalizeString(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item, now)).join(',')}]`;
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const members = Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${canonicalizeString(key)}:${canonicalJson(record[key], now)}`);
    return `{${members.join(',')}}`;
  }
  throw new ServerSdkError(
    'Canonical JSON cannot represent this value type.',
    createFailure(
      {
        code: 'manifest_signature_invalid',
        retry: 'never',
        readiness_consequence: 'fail_stream',
        detail: { reason: 'unsupported_type', value_type: typeof value },
      },
      now,
    ),
  );
}
