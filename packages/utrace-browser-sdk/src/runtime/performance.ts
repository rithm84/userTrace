/**
 * Bounded performance measurement.
 *
 * Performance observations are bounded, aggregated, and lower priority than
 * interactions, routes, product requests, errors, milestones, targets, and
 * action results. Individual entries are never exported; each window exports
 * count, sum, and maximum per metric.
 */

import type { BrowserView } from "../core/dom.js";
import { PERFORMANCE_BATCH_WINDOW_MS } from "../core/limits.js";
import type { PerformanceMeasurement, PerformanceMeasurementPayload } from "../observation/types.js";

const OBSERVED_ENTRY_TYPES: readonly string[] = ["largest-contentful-paint", "longtask", "event", "resource"];

/** Bounds the number of distinct metric names one window may carry. */
const MAX_METRICS_PER_WINDOW = 16;

interface Aggregate {
  count: number;
  sum: number;
  max: number;
}

export type PerformanceWindowListener = (payload: PerformanceMeasurementPayload) => void;

export class PerformanceCollector {
  readonly #view: BrowserView;
  readonly #monotonicNowMs: () => number;
  readonly #listener: PerformanceWindowListener;
  readonly #aggregates = new Map<string, Aggregate>();
  #observer: PerformanceObserver | null = null;
  #timer: number | null = null;
  #windowStart = 0;

  constructor(view: BrowserView, monotonicNowMs: () => number, listener: PerformanceWindowListener) {
    this.#view = view;
    this.#monotonicNowMs = monotonicNowMs;
    this.#listener = listener;
  }

  start(): void {
    this.#windowStart = this.#monotonicNowMs();
    this.#observer = new this.#view.PerformanceObserver((list) => {
      for (const entry of list.getEntries()) this.#record(entry.entryType, entry.duration);
      this.#schedule();
    });
    for (const type of OBSERVED_ENTRY_TYPES) {
      try {
        this.#observer.observe({ type, buffered: true });
      } catch {
        // An entry type this Chrome build does not support is simply not
        // collected. Performance data is bounded and lower priority, so a
        // missing entry type never withdraws readiness.
      }
    }
  }

  #record(metric: string, durationMs: number): void {
    const existing = this.#aggregates.get(metric);
    if (existing === undefined) {
      if (this.#aggregates.size >= MAX_METRICS_PER_WINDOW) return;
      this.#aggregates.set(metric, { count: 1, sum: durationMs, max: durationMs });
      return;
    }
    existing.count += 1;
    existing.sum += durationMs;
    existing.max = Math.max(existing.max, durationMs);
  }

  #schedule(): void {
    if (this.#timer !== null) return;
    this.#timer = this.#view.setTimeout(() => {
      this.#timer = null;
      this.flush();
    }, PERFORMANCE_BATCH_WINDOW_MS);
  }

  flush(): void {
    if (this.#aggregates.size === 0) return;
    const measurements: PerformanceMeasurement[] = [...this.#aggregates.entries()].map(([metric, aggregate]) => ({
      metric,
      count: aggregate.count,
      sum_ms: Math.round(aggregate.sum),
      max_ms: Math.round(aggregate.max),
    }));
    const windowEnd = this.#monotonicNowMs();
    this.#aggregates.clear();
    const payload: PerformanceMeasurementPayload = {
      measurements,
      window_start_offset: Math.round(this.#windowStart),
      window_end_offset: Math.round(windowEnd),
    };
    this.#windowStart = windowEnd;
    this.#listener(payload);
  }

  stop(): void {
    this.#observer?.disconnect();
    this.#observer = null;
    if (this.#timer !== null) {
      this.#view.clearTimeout(this.#timer);
      this.#timer = null;
    }
  }
}
