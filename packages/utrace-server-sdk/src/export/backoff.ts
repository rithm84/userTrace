/**
 * Bounded exponential backoff with jitter.
 *
 * The SDK retries the same immutable batch. It never switches transport or
 * destination, and a `Retry-After` header always wins over the computed delay.
 */

import type { ExportLimits } from '../contracts/activation.js';

/** Source of the random factor, injected so tests are deterministic. */
export interface JitterSource {
  next(): number;
}

/** `Math.random`-backed jitter. */
export const systemJitter: JitterSource = {
  next(): number {
    return Math.random();
  },
};

/**
 * Computes the delay before retry `attempt` (1-based).
 *
 * `retryAfterMillis` is honoured exactly when the server supplied one; it is
 * still bounded by the activation's maximum backoff so a hostile or mistaken
 * header cannot stall the stream indefinitely.
 */
export function backoffDelayMillis(
  attempt: number,
  retry: ExportLimits['retry'],
  retryAfterMillis: number | undefined,
  jitter: JitterSource,
): number {
  if (retryAfterMillis !== undefined) {
    return Math.min(retryAfterMillis, retry.max_backoff_millis);
  }
  const exponential = retry.initial_backoff_millis * retry.multiplier ** (attempt - 1);
  const bounded = Math.min(exponential, retry.max_backoff_millis);
  const jitterRange = bounded * retry.jitter_ratio;
  const offset = jitterRange * (jitter.next() * 2 - 1);
  return Math.max(0, Math.round(bounded + offset));
}

/**
 * Parses an HTTP `Retry-After` header.
 *
 * Both the delay-seconds and HTTP-date forms are accepted; anything else
 * returns `undefined` so the computed backoff applies.
 */
export function parseRetryAfter(header: string | undefined, now: Date): number | undefined {
  if (header === undefined) {
    return undefined;
  }
  const trimmed = header.trim();
  if (/^\d+$/u.test(trimmed)) {
    return Number.parseInt(trimmed, 10) * 1000;
  }
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) {
    return undefined;
  }
  return Math.max(0, date - now.getTime());
}
