/**
 * Sequence allocation.
 *
 * `source_sequence` is monotonic within one local runtime. `export_sequence` is
 * assigned by the top-document exporter across the browser stream. Together
 * they give a stable delivery order without claiming a false physical total
 * order between simultaneous frames or workers.
 *
 * Every stream starts at one. A retry reuses the sequence the record already
 * holds, so ingestion deduplicates rather than treating a retry as new evidence.
 */

export class SequenceAllocator {
  #next = 1;

  /** Returns the next sequence and advances. Never returns the same value twice. */
  allocate(): number {
    const value = this.#next;
    this.#next += 1;
    return value;
  }

  /** Highest sequence produced so far; zero before the first allocation. */
  get produced(): number {
    return this.#next - 1;
  }
}

/**
 * Tracks the contiguous acknowledgement watermark for one browser stream and
 * rejects acknowledgements that cannot be true.
 */
export class AcknowledgementTracker {
  #acknowledged = 0;

  get acknowledged(): number {
    return this.#acknowledged;
  }

  /**
   * Accepts a new contiguous watermark. A value beyond what was produced, or a
   * regression below the current watermark, is an integrity failure rather than
   * a reconnect hint: this returns the reason instead of silently clamping.
   */
  accept(acknowledgedThrough: number, produced: number): { readonly ok: true } | { readonly ok: false; readonly reason: string } {
    if (!Number.isSafeInteger(acknowledgedThrough) || acknowledgedThrough < 0) {
      return { ok: false, reason: "acknowledgement is not a non-negative integer" };
    }
    if (acknowledgedThrough > produced) {
      return { ok: false, reason: `acknowledged ${String(acknowledgedThrough)} beyond produced ${String(produced)}` };
    }
    if (acknowledgedThrough < this.#acknowledged) {
      return {
        ok: false,
        reason: `acknowledgement regressed from ${String(this.#acknowledged)} to ${String(acknowledgedThrough)}`,
      };
    }
    this.#acknowledged = acknowledgedThrough;
    return { ok: true };
  }
}
