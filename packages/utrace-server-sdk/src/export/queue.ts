/**
 * The bounded, memory-only export queue.
 *
 * It holds already-redacted spans and the immutable batches built from them.
 * Nothing is written to the application filesystem, database, cache, or queue.
 * Reaching the hard bound is an explicit stream integrity failure: the SDK
 * never evicts older or supposedly lower-value spans and continues.
 */

import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';

import type { ExportLimits } from '../contracts/activation.js';
import type { BufferStatus } from '../contracts/health.js';
import type { ExportBatch } from './batch.js';

/** What `admit` decided about one redacted span. */
export type AdmissionOutcome =
  | { readonly kind: 'admitted'; readonly highWaterReached: boolean }
  | { readonly kind: 'exhausted'; readonly queuedSpans: number };

/** Bounded in-memory queue of redacted spans and unacknowledged batches. */
export class ExportQueue {
  private readonly limits: ExportLimits;
  private pendingSpans: ReadableSpan[] = [];
  private readonly unacknowledged = new Map<number, ExportBatch>();
  private producedSequence = 0;
  private acknowledgedSequence = 0;
  private highWaterReached = false;

  public constructor(limits: ExportLimits) {
    this.limits = limits;
  }

  /** Spans awaiting batching plus spans inside unacknowledged batches. */
  public get queuedSpans(): number {
    let inBatches = 0;
    for (const batch of this.unacknowledged.values()) {
      inBatches += batch.spanCount;
    }
    return this.pendingSpans.length + inBatches;
  }

  public get queuedBatches(): number {
    return this.unacknowledged.size;
  }

  public get lastProducedSequence(): number {
    return this.producedSequence;
  }

  public get lastAcknowledgedSequence(): number {
    return this.acknowledgedSequence;
  }

  public get utilization(): number {
    const spanRatio = this.queuedSpans / this.limits.max_queue_spans;
    const batchRatio = this.queuedBatches / this.limits.max_queue_batches;
    return Math.min(1, Math.max(spanRatio, batchRatio));
  }

  /** True once the high-water mark has been crossed and not yet cleared. */
  public get isAboveHighWater(): boolean {
    return this.highWaterReached;
  }

  /**
   * Admits one redacted span.
   *
   * Returns `exhausted` when the hard bound is reached; the caller turns that
   * into a stream integrity failure rather than dropping telemetry quietly.
   */
  public admit(span: ReadableSpan): AdmissionOutcome {
    if (this.queuedSpans >= this.limits.max_queue_spans) {
      return { kind: 'exhausted', queuedSpans: this.queuedSpans };
    }
    this.pendingSpans.push(span);
    if (this.utilization >= this.limits.readiness_high_water_ratio) {
      this.highWaterReached = true;
    }
    return { kind: 'admitted', highWaterReached: this.highWaterReached };
  }

  /** Removes up to `max_batch_spans` spans for the next batch. */
  public takeBatchSpans(): readonly ReadableSpan[] {
    const taken = this.pendingSpans.slice(0, this.limits.max_batch_spans);
    this.pendingSpans = this.pendingSpans.slice(taken.length);
    return taken;
  }

  /** True when a batch can be formed right now. */
  public get hasPendingSpans(): boolean {
    return this.pendingSpans.length > 0;
  }

  /** Allocates the next stream-scoped, gapless batch sequence. */
  public nextSequence(): number {
    this.producedSequence += 1;
    return this.producedSequence;
  }

  /** Records a batch as produced and awaiting acknowledgement. */
  public registerBatch(batch: ExportBatch): void {
    this.unacknowledged.set(batch.envelope.batch_sequence, batch);
  }

  /** True when another batch may be in flight under the activation's limit. */
  public canSendAnother(inFlight: number): boolean {
    return inFlight < this.limits.max_in_flight_batches;
  }

  /**
   * Acknowledges one batch.
   *
   * The acknowledged watermark advances only across a contiguous run, so an
   * acknowledgement gap can never be reported as a continuous stream.
   */
  public acknowledge(sequence: number): void {
    this.unacknowledged.delete(sequence);
    let next = this.acknowledgedSequence + 1;
    while (next <= this.producedSequence && !this.unacknowledged.has(next)) {
      this.acknowledgedSequence = next;
      next += 1;
    }
    if (this.utilization < this.limits.readiness_high_water_ratio) {
      this.highWaterReached = false;
    }
  }

  /** Operational snapshot for the health report. */
  public status(): BufferStatus {
    return {
      queued_batches: this.queuedBatches,
      queued_spans: this.queuedSpans,
      max_queue_batches: this.limits.max_queue_batches,
      max_queue_spans: this.limits.max_queue_spans,
      utilization: this.utilization,
      high_water_reached: this.highWaterReached,
      last_produced_sequence: this.producedSequence,
      last_acknowledged_sequence: this.acknowledgedSequence,
    };
  }
}
