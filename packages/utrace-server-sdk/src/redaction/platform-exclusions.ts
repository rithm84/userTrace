/**
 * Non-overridable platform exclusions.
 *
 * This is the first and highest-precedence redaction layer. No signed client
 * policy, annotation definition, or adapter can re-enable a key it removes.
 */

import {
  NEVER_COLLECT_ATTRIBUTE_KEYS,
  NEVER_COLLECT_ATTRIBUTE_PREFIXES,
} from '../constants.js';

const EXCLUDED_KEYS: ReadonlySet<string> = new Set(NEVER_COLLECT_ATTRIBUTE_KEYS);

/** True when the attribute key can never be exported. */
export function isPlatformExcluded(key: string): boolean {
  if (EXCLUDED_KEYS.has(key)) {
    return true;
  }
  return NEVER_COLLECT_ATTRIBUTE_PREFIXES.some((prefix) => key.startsWith(prefix));
}
