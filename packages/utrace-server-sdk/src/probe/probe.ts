/**
 * The end-to-end propagation probe.
 *
 * The probe enters through the authenticated preview path and must prove
 * browser `CLIENT` to server `SERVER` parentage plus every hop the smoke
 * workflow requires. Duplicate spans, missing hops, rejected fields, or an
 * unexpected export destination fail it, and readiness waits for the
 * acknowledgement of the batch that carried the probe spans.
 */

import { SpanKind } from '@opentelemetry/api';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';

import type { ProbeHop, PropagationProbeSpec } from '../contracts/activation.js';
import type { ProbeFailureReason, ProbeResult } from '../contracts/health.js';

const MAX_TRACKED_TRACES = 128;

/** Observes redacted spans and decides whether the probe passed. */
export class PropagationProbe {
  private readonly spec: PropagationProbeSpec;
  private readonly expectedServerSpanName: string;
  private readonly hopsByTrace = new Map<string, Set<ProbeHop>>();
  private readonly serverSpansByTrace = new Map<string, number>();
  private probeTraceId: string | undefined;
  private observedHops = new Set<ProbeHop>();
  private batchSequence: number | undefined;
  private awaitingSince: number | undefined;
  private state: ProbeResult['state'] = 'awaiting_spans';
  private failureReason: ProbeFailureReason | undefined;

  public constructor(spec: PropagationProbeSpec) {
    this.spec = spec;
    this.expectedServerSpanName = `${spec.method} ${spec.route_template}`;
  }

  /** True once the probe has passed. */
  public get hasPassed(): boolean {
    return this.state === 'passed';
  }

  /** Records one accepted, redacted span. */
  public observe(span: ReadableSpan, hop: ProbeHop | undefined): void {
    if (this.state === 'passed' || this.state === 'failed') {
      return;
    }
    const traceId = span.spanContext().traceId;
    if (span.kind === SpanKind.SERVER && span.name === this.expectedServerSpanName) {
      const seen = (this.serverSpansByTrace.get(traceId) ?? 0) + 1;
      this.serverSpansByTrace.set(traceId, seen);
      if (seen > 1) {
        this.fail('duplicate_server_span');
        return;
      }
      if (span.parentSpanContext?.isRemote !== true) {
        // A probe request that did not inherit the browser CLIENT span has not
        // proved propagation, so it is not adopted as the probe trace.
        return;
      }
      if (this.probeTraceId === undefined) {
        this.probeTraceId = traceId;
        this.observedHops = new Set(this.hopsByTrace.get(traceId) ?? []);
      }
    }
    if (hop === undefined) {
      return;
    }
    if (this.probeTraceId === traceId) {
      this.observedHops.add(hop);
      return;
    }
    this.rememberPending(traceId, hop);
  }

  /** Notes the batch sequence that carried the probe's spans. */
  public noteBatch(spans: readonly ReadableSpan[], sequence: number): void {
    if (this.probeTraceId === undefined || this.state === 'failed' || this.state === 'passed') {
      return;
    }
    const carriesProbe = spans.some((span) => span.spanContext().traceId === this.probeTraceId);
    if (!carriesProbe) {
      return;
    }
    this.batchSequence = sequence;
    this.awaitingSince ??= Date.now();
    if (this.allHopsObserved()) {
      this.state = 'awaiting_acknowledgement';
    }
  }

  /** Advances the probe when its batch has been acknowledged. */
  public acknowledge(acknowledgedSequence: number): void {
    if (this.state === 'failed' || this.state === 'passed') {
      return;
    }
    if (!this.allHopsObserved() || this.batchSequence === undefined) {
      return;
    }
    if (acknowledgedSequence >= this.batchSequence) {
      this.state = 'passed';
    }
  }

  /**
   * Fails the probe once its acknowledgement lag is exceeded.
   *
   * A probe whose spans were produced but never completed or acknowledged is a
   * readiness failure, not a slow success.
   */
  public checkDeadline(nowMillis: number, acknowledgementLagMillis: number): void {
    if (this.state === 'passed' || this.state === 'failed' || this.awaitingSince === undefined) {
      return;
    }
    if (nowMillis - this.awaitingSince < acknowledgementLagMillis) {
      return;
    }
    this.fail(this.allHopsObserved() ? 'acknowledgement_timeout' : 'missing_hop');
  }

  /** Fails the probe with an exact reason. */
  public fail(reason: ProbeFailureReason): void {
    this.state = 'failed';
    this.failureReason = reason;
  }

  /** Current probe result for the health report. */
  public result(): ProbeResult {
    return {
      state: this.state,
      probe_id: this.spec.probe_id,
      observed_hops: [...this.observedHops],
      required_hops: [...this.spec.required_hops],
      ...(this.failureReason === undefined ? {} : { failure_reason: this.failureReason }),
    };
  }

  private allHopsObserved(): boolean {
    return this.spec.required_hops.every((hop) => this.observedHops.has(hop));
  }

  private rememberPending(traceId: string, hop: ProbeHop): void {
    const existing = this.hopsByTrace.get(traceId);
    if (existing !== undefined) {
      existing.add(hop);
      return;
    }
    if (this.hopsByTrace.size >= MAX_TRACKED_TRACES) {
      const oldest = this.hopsByTrace.keys().next();
      if (!oldest.done) {
        this.hopsByTrace.delete(oldest.value);
      }
    }
    this.hopsByTrace.set(traceId, new Set([hop]));
  }
}
