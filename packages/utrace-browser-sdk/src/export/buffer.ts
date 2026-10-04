/**
 * Bounded memory-only export buffer.
 *
 * The buffer holds only normalized, already-redacted records. Nothing is
 * written to `localStorage`, `sessionStorage`, IndexedDB, Cache Storage, or
 * extension storage.
 *
 * Records wait in per-priority pending queues and receive their
 * `export_sequence` when the exporter drains them, so interactions, committed
 * routes, product requests, errors, milestones, targets, and target
 * observations leave first, DOM change records follow, and performance
 * measurements are the first eligible records to drop. Because a drop happens
 * before an `export_sequence` exists, the export stream stays contiguous and a
 * dropped range is reported explicitly against `source_sequence`.
 *
 * Drained records stay in flight until ingestion acknowledges them, so a retry
 * preserves the original observation ids and sequence numbers.
 */

import { ExportError } from "../core/failure.js";
import type { JsonObject } from "../core/json.js";
import { MAX_BUFFERED_BYTES, MAX_BUFFERED_RECORDS } from "../core/limits.js";
import type { ObservationPriority } from "../observation/types.js";
import { priorityOf } from "../observation/types.js";
import { jsonByteLength } from "./protocol.js";
import type { ExportRecord } from "./record.js";

export interface PendingRecord {
  readonly record: ExportRecord;
  readonly priority: ObservationPriority;
  readonly bytes: number;
}

export interface InFlightRecord {
  readonly record: ExportRecord;
  readonly encoded: JsonObject;
  readonly export_sequence: number;
  readonly bytes: number;
}

export interface DroppedRange {
  readonly runtime_id: string;
  readonly first_source_sequence: number;
  readonly last_source_sequence: number;
  readonly count: number;
  readonly observation_class: ObservationPriority;
  readonly reason: "buffer_pressure" | "flow_control" | "transport_loss" | "pagehide_truncated";
}

const DROPPABLE_ORDER: readonly ObservationPriority[] = ["low", "normal"];
const DRAIN_ORDER: readonly ObservationPriority[] = ["critical", "normal", "low"];

export class ExportBuffer {
  readonly #maxObservationBytes: number;
  readonly #pending: Record<ObservationPriority, PendingRecord[]> = { critical: [], normal: [], low: [] };
  readonly #inFlight: InFlightRecord[] = [];
  readonly #drops: DroppedRange[] = [];
  #bytes = 0;

  constructor(maxObservationBytes: number) {
    this.#maxObservationBytes = maxObservationBytes;
  }

  get bufferedBytes(): number {
    return this.#bytes;
  }

  get bufferedRecords(): number {
    return this.#pending.critical.length + this.#pending.normal.length + this.#pending.low.length + this.#inFlight.length;
  }

  get pendingRecords(): number {
    return this.#pending.critical.length + this.#pending.normal.length + this.#pending.low.length;
  }

  get inFlightRecords(): number {
    return this.#inFlight.length;
  }

  /** Utilization against the record and byte bounds, whichever is higher. */
  get utilization(): number {
    return Math.max(this.bufferedRecords / MAX_BUFFERED_RECORDS, this.#bytes / MAX_BUFFERED_BYTES);
  }

  /**
   * Admits one already-redacted record.
   *
   * A record larger than the negotiated per-observation limit is rejected; the
   * baseline path splits oversized payloads into chunks before enqueuing. When
   * the buffer is full, low-priority then normal-priority records are dropped
   * with an explicit range. A critical record that still does not fit exhausts
   * the buffer, which withdraws readiness rather than discarding it silently.
   */
  enqueue(record: ExportRecord): void {
    const priority = priorityOf(record.envelope.observation_type);
    const bytes = jsonByteLength({ observation_id: record.envelope.observation_id, payload: record.body });
    if (bytes > this.#maxObservationBytes) {
      throw new ExportError("buffer_exhaustion", "single observation exceeds the negotiated observation limit", {
        observation_type: record.envelope.observation_type,
        bytes,
        limit: this.#maxObservationBytes,
      });
    }
    this.#makeRoom(bytes, priority);
    this.#pending[priority].push({ record, priority, bytes });
    this.#bytes += bytes;
  }

  #makeRoom(bytes: number, incoming: ObservationPriority): void {
    while (this.#bytes + bytes > MAX_BUFFERED_BYTES || this.bufferedRecords + 1 > MAX_BUFFERED_RECORDS) {
      if (!this.#dropOldestDroppable("buffer_pressure", incoming)) {
        throw new ExportError("buffer_exhaustion", "buffer is full of critical records that cannot be dropped", {
          buffered_records: this.bufferedRecords,
          buffered_bytes: this.#bytes,
          incoming_priority: incoming,
        });
      }
    }
  }

  /** Drops the oldest droppable record and records its range. Returns false when none exists. */
  #dropOldestDroppable(reason: DroppedRange["reason"], incoming: ObservationPriority): boolean {
    for (const priority of DROPPABLE_ORDER) {
      if (incoming === "low" && priority === "normal") continue;
      const queue = this.#pending[priority];
      const victim = queue.shift();
      if (victim === undefined) continue;
      this.#bytes -= victim.bytes;
      this.#recordDrop(victim, reason);
      return true;
    }
    return false;
  }

  #recordDrop(record: PendingRecord, reason: DroppedRange["reason"]): void {
    const runtimeId = record.record.envelope.runtime_id;
    const sequence = record.record.envelope.source_sequence;
    const last = this.#drops.at(-1);
    if (
      last !== undefined &&
      last.runtime_id === runtimeId &&
      last.observation_class === record.priority &&
      last.reason === reason &&
      last.last_source_sequence + 1 >= sequence
    ) {
      this.#drops[this.#drops.length - 1] = {
        ...last,
        last_source_sequence: Math.max(last.last_source_sequence, sequence),
        count: last.count + 1,
      };
      return;
    }
    this.#drops.push({
      runtime_id: runtimeId,
      first_source_sequence: sequence,
      last_source_sequence: sequence,
      count: 1,
      observation_class: record.priority,
      reason,
    });
  }

  /** Removes and returns the accumulated dropped ranges for gap reporting. */
  takeDroppedRanges(): readonly DroppedRange[] {
    return this.#drops.splice(0, this.#drops.length);
  }

  hasPending(): boolean {
    return this.pendingRecords > 0;
  }

  /**
   * Drains one batch in priority order, assigning `export_sequence` through the
   * supplied allocator. `criticalOnly` supports the bounded `pagehide` flush.
   */
  drainBatch(
    maxBytes: number,
    maxRecords: number,
    criticalOnly: boolean,
    allocate: () => number,
    encode: (record: ExportRecord, exportSequence: number) => JsonObject,
  ): readonly InFlightRecord[] {
    const batch: InFlightRecord[] = [];
    let bytes = 0;
    for (const priority of DRAIN_ORDER) {
      if (criticalOnly && priority !== "critical") break;
      const queue = this.#pending[priority];
      while (queue.length > 0 && batch.length < maxRecords) {
        const next = queue[0];
        if (next === undefined) break;
        if (bytes + next.bytes > maxBytes && batch.length > 0) return batch;
        queue.shift();
        const exportSequence = allocate();
        const encoded = encode(next.record, exportSequence);
        const inFlight: InFlightRecord = {
          record: next.record,
          encoded,
          export_sequence: exportSequence,
          bytes: next.bytes,
        };
        this.#inFlight.push(inFlight);
        batch.push(inFlight);
        bytes += next.bytes;
      }
    }
    return batch;
  }

  /** Releases every in-flight record at or below the contiguous watermark. */
  acknowledge(acknowledgedThrough: number): number {
    let released = 0;
    while (this.#inFlight.length > 0) {
      const head = this.#inFlight[0];
      if (head === undefined || head.export_sequence > acknowledgedThrough) break;
      this.#inFlight.shift();
      this.#bytes -= head.bytes;
      released += 1;
    }
    return released;
  }

  /** In-flight records above the watermark, for retransmission after reconnect. */
  unacknowledged(acknowledgedThrough: number): readonly InFlightRecord[] {
    return this.#inFlight.filter((record) => record.export_sequence > acknowledgedThrough);
  }

  /** Discards pending low and normal records, reporting them as an explicit gap. */
  dropDroppable(reason: DroppedRange["reason"]): void {
    for (const priority of DROPPABLE_ORDER) {
      const queue = this.#pending[priority];
      while (queue.length > 0) {
        const victim = queue.shift();
        if (victim === undefined) break;
        this.#bytes -= victim.bytes;
        this.#recordDrop(victim, reason);
      }
    }
  }
}
