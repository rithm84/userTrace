/** Wall-clock and monotonic time used by every observation envelope. */

import type { BrowserView } from "./dom.js";

export interface Clock {
  /** RFC 3339 UTC wall time of the current instant. */
  readonly wallNowMs: () => number;
  /** Document monotonic performance clock, in fractional milliseconds. */
  readonly monotonicNowMs: () => number;
  /** `performance.timeOrigin`, used to report the measured wall-clock offset. */
  readonly timeOriginMs: number;
}

export const documentClock = (view: BrowserView): Clock => ({
  wallNowMs: (): number => view.Date.now(),
  monotonicNowMs: (): number => view.performance.now(),
  timeOriginMs: view.performance.timeOrigin,
});

/** RFC 3339 UTC with millisecond precision. */
export const rfc3339 = (epochMs: number): string => new Date(epochMs).toISOString();

/**
 * Wall-clock offset needed to interpret `monotonic_offset` against `occurred_at`
 * without rewriting either value. Reported on the runtime health record only.
 */
export const measureWallClockOffsetMs = (clock: Clock): number =>
  clock.wallNowMs() - (clock.timeOriginMs + clock.monotonicNowMs());

export const isExpired = (expiresAt: string, nowMs: number): boolean => {
  const expiryMs = Date.parse(expiresAt);
  return !Number.isFinite(expiryMs) || expiryMs <= nowMs;
};
