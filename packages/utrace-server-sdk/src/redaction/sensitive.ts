/**
 * Defensive sensitive-data detection.
 *
 * By the time this layer runs, every surviving string should be a manifest or
 * enumeration value, so a hit means an earlier layer leaked. The detector may
 * only restrict: it removes the value and reports a contract failure.
 */

const DETECTORS: readonly (readonly [string, RegExp])[] = [
  ['private_key_block', /-----BEGIN [A-Z ]*PRIVATE KEY-----/u],
  ['json_web_token', /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/u],
  ['bearer_credential', /\bBearer\s+[A-Za-z0-9._~+/-]{12,}/iu],
  ['basic_credential', /\bBasic\s+[A-Za-z0-9+/]{12,}={0,2}/iu],
  ['api_key_assignment', /\b(api[_-]?key|secret|password|passwd|token)\b\s*[=:]\s*\S+/iu],
  ['url_userinfo', /\b[a-z][a-z0-9+.-]*:\/\/[^\s/@]+:[^\s/@]+@/iu],
  ['email_address', /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/u],
  ['payment_card_number', /\b(?:\d[ -]?){13,19}\b/u],
];

/** Returns the name of the first detector that matches, if any. */
export function detectSensitiveValue(value: string): string | undefined {
  for (const [name, pattern] of DETECTORS) {
    if (pattern.test(value)) {
      return name;
    }
  }
  return undefined;
}
