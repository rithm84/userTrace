/**
 * Small shared helpers for the integration suites.
 */

import { randomBytes } from 'node:crypto';

import type { JitterSource } from '../../src/export/backoff.js';

/** Jitter that always returns the midpoint, so retry delays are deterministic. */
export const fixedJitter: JitterSource = {
  next: (): number => 0.5,
};

/** Waits until `predicate` holds or the deadline passes. */
export async function waitFor(
  predicate: () => boolean,
  timeoutMillis: number,
  description: string,
): Promise<void> {
  const deadline = Date.now() + timeoutMillis;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 10);
    });
  }
  throw new Error(`timed out waiting for ${description}`);
}

/** Builds a browser-style W3C `traceparent` for a sampled remote CLIENT span. */
export function browserTraceparent(): { traceparent: string; traceId: string; spanId: string } {
  const traceId = randomBytes(16).toString('hex');
  const spanId = randomBytes(8).toString('hex');
  return { traceparent: `00-${traceId}-${spanId}-01`, traceId, spanId };
}
