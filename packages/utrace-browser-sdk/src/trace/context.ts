/**
 * W3C trace context creation and propagation scope.
 *
 * A trace represents one causal product operation, not the whole Interview
 * Session. A user or authorized agent interaction starts an interaction span;
 * an eligible product request creates a browser CLIENT span beneath it. A
 * background request without a defensible parent starts its own trace; the
 * runtime never fabricates a parent to make a waterfall look complete.
 *
 * Active interview traces are not sampled in the browser, so the sampled flag is
 * always set. Downstream retention or evaluation sampling happens after
 * ingestion.
 */

import type { RandomSource } from "../core/ids.js";
import { spanId as newSpanId, traceId as newTraceId } from "../core/ids.js";

export interface SpanContext {
  readonly trace_id: string;
  readonly span_id: string;
}

export interface ClientSpan extends SpanContext {
  readonly parent_span_id: string | null;
  readonly traceparent: string;
  readonly tracestate: string;
}

export const formatTraceparent = (context: SpanContext): string =>
  `00-${context.trace_id}-${context.span_id}-01`;

/** Vendor entry for `tracestate`. It carries no client, user, account, or session id. */
export const UTRACE_TRACESTATE = "utrace=1";

/**
 * Window after an interaction during which a request is treated as caused by it.
 * Beyond the window a request starts its own trace rather than inheriting a
 * parent the runtime cannot defend.
 */
const INTERACTION_CAUSALITY_WINDOW_MS = 1000;

export class TraceCoordinator {
  readonly #random: RandomSource;
  readonly #monotonicNowMs: () => number;
  #interaction: SpanContext | null = null;
  #interactionAtMs = 0;

  constructor(random: RandomSource, monotonicNowMs: () => number) {
    this.#random = random;
    this.#monotonicNowMs = monotonicNowMs;
  }

  /** Starts the interaction span that a user or agent activation roots. */
  startInteractionSpan(): SpanContext {
    const context: SpanContext = { trace_id: newTraceId(this.#random), span_id: newSpanId(this.#random) };
    this.#interaction = context;
    this.#interactionAtMs = this.#monotonicNowMs();
    return context;
  }

  endInteractionSpan(): void {
    this.#interaction = null;
  }

  /** The current defensible parent, or null when none applies. */
  currentParent(): SpanContext | null {
    if (this.#interaction === null) return null;
    if (this.#monotonicNowMs() - this.#interactionAtMs > INTERACTION_CAUSALITY_WINDOW_MS) return null;
    return this.#interaction;
  }

  /** Creates the browser CLIENT span for one product request. */
  createClientSpan(): ClientSpan {
    const parent = this.currentParent();
    const context: SpanContext = {
      trace_id: parent === null ? newTraceId(this.#random) : parent.trace_id,
      span_id: newSpanId(this.#random),
    };
    return {
      ...context,
      parent_span_id: parent === null ? null : parent.span_id,
      traceparent: formatTraceparent(context),
      tracestate: UTRACE_TRACESTATE,
    };
  }
}

/**
 * Injection scope: same-origin requests, or origins the signed release manifest
 * approved as server-SDK endpoints. Static resources, `sendBeacon`, and
 * unapproved third-party traffic are observation-only.
 */
export const resolveRequestOrigin = (url: string, documentOrigin: string): string | null => {
  try {
    return new URL(url, documentOrigin).origin;
  } catch {
    return null;
  }
};
