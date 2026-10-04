/**
 * Export buffer, priorities, gap accounting, and baseline chunking.
 */

import { describe, expect, it } from "vitest";
import { ExportBuffer } from "../../src/export/buffer.js";
import { chunkBaseline } from "../../src/export/chunking.js";
import { encodeObservation } from "../../src/export/protocol.js";
import type { ExportRecord } from "../../src/export/record.js";
import { MAX_BUFFERED_RECORDS, MAX_OBSERVATION_BYTES } from "../../src/core/limits.js";
import type { ObservationType } from "../../src/observation/types.js";

let sourceSequence = 0;

const record = (type: ObservationType, bytes: number): ExportRecord => {
  sourceSequence += 1;
  return {
    envelope: {
      observation_id: `id-${String(sourceSequence)}`,
      observation_type: type,
      runtime_id: "runtime-1",
      document_attachment_id: "attachment-1",
      frame_id: "top",
      source_sequence: sourceSequence,
      export_sequence: 0,
      occurred_at: "2026-10-03T09:00:00.000Z",
      monotonic_offset: 1,
      route_revision: 1,
      visibility_state: "visible",
    },
    body: { filler: "x".repeat(bytes) },
  };
};

const drain = (buffer: ExportBuffer, criticalOnly: boolean): readonly number[] => {
  let next = 0;
  return buffer
    .drainBatch(
      MAX_OBSERVATION_BYTES,
      64,
      criticalOnly,
      () => {
        next += 1;
        return next;
      },
      (entry, exportSequence) =>
        encodeObservation({ envelope: { ...entry.envelope, export_sequence: exportSequence }, body: entry.body }),
    )
    .map((entry) => entry.record.envelope.source_sequence);
};

describe("priority ordering", () => {
  it("drains critical records before normal and low ones", () => {
    const buffer = new ExportBuffer(MAX_OBSERVATION_BYTES);
    buffer.enqueue(record("performance.measurement", 8));
    buffer.enqueue(record("dom.change", 8));
    buffer.enqueue(record("interaction.activation", 8));
    const order = drain(buffer, false);
    expect(order).toEqual([3, 2, 1]);
  });

  it("drains only critical records for the bounded pagehide flush", () => {
    const buffer = new ExportBuffer(MAX_OBSERVATION_BYTES);
    buffer.enqueue(record("dom.change", 8));
    buffer.enqueue(record("route.change", 8));
    expect(drain(buffer, true)).toHaveLength(1);
    expect(buffer.pendingRecords).toBe(1);
  });
});

describe("bounded buffer and gaps", () => {
  it("rejects a single observation larger than the negotiated limit", () => {
    const buffer = new ExportBuffer(1024);
    expect(() => { buffer.enqueue(record("dom.change", 4096)); }).toThrow(/buffer_exhaustion/);
  });

  it("sheds low-priority records first and reports one merged range", () => {
    const buffer = new ExportBuffer(MAX_OBSERVATION_BYTES);
    for (let index = 0; index < MAX_BUFFERED_RECORDS; index += 1) {
      buffer.enqueue(record("performance.measurement", 4));
    }
    const firstDropped = sourceSequence - MAX_BUFFERED_RECORDS + 1;
    buffer.enqueue(record("interaction.activation", 4));
    const ranges = buffer.takeDroppedRanges();
    expect(ranges).toHaveLength(1);
    expect(ranges[0]?.observation_class).toBe("low");
    expect(ranges[0]?.reason).toBe("buffer_pressure");
    expect(ranges[0]?.first_source_sequence).toBe(firstDropped);
    expect(ranges[0]?.count).toBe(1);
    expect(buffer.takeDroppedRanges()).toHaveLength(0);
  });

  it("drops every droppable record under flow control and keeps critical ones", () => {
    const buffer = new ExportBuffer(MAX_OBSERVATION_BYTES);
    buffer.enqueue(record("dom.change", 4));
    buffer.enqueue(record("performance.measurement", 4));
    buffer.enqueue(record("error.frontend", 4));
    buffer.dropDroppable("flow_control");
    const ranges = buffer.takeDroppedRanges();
    expect(ranges.map((range) => range.observation_class).sort()).toEqual(["low", "normal"]);
    expect(buffer.pendingRecords).toBe(1);
    expect(drain(buffer, false)).toHaveLength(1);
  });

  it("keeps records in flight until acknowledged so a retry reuses their sequences", () => {
    const buffer = new ExportBuffer(MAX_OBSERVATION_BYTES);
    buffer.enqueue(record("route.change", 4));
    buffer.enqueue(record("route.change", 4));
    drain(buffer, false);
    expect(buffer.inFlightRecords).toBe(2);
    expect(buffer.unacknowledged(1)).toHaveLength(1);
    expect(buffer.acknowledge(2)).toBe(2);
    expect(buffer.inFlightRecords).toBe(0);
    expect(buffer.bufferedBytes).toBe(0);
  });
});

describe("baseline chunking", () => {
  const snapshot = {
    baseline_id: "b-1",
    reason: "activation" as const,
    route: {
      route_template: "/dashboard/conversions",
      unknown_route: false,
      segment_count: 2,
      has_query: false,
      has_fragment: false,
      params: [],
    },
    landmarks: Array.from({ length: 200 }, (_unused, index) => ({
      role: "region",
      label: {
        output_class: "bounded_safe_value" as const,
        value: `Region ${String(index)} with a long enough label to grow the baseline`,
        length_bucket: "len_31_100" as const,
        rule_id: "safe.button_label",
      },
      depth: 1,
      child_count: 3,
    })),
    targets: [],
    surfaces: ["conversion_dashboard"],
    entities: [],
    node_budget_exhausted: false,
  };

  it("keeps a small baseline in one observation", async () => {
    const chunks = await chunkBaseline("b-1", { ...snapshot, landmarks: [] }, MAX_OBSERVATION_BYTES, globalThis.crypto.subtle);
    expect(chunks).toHaveLength(0);
  });

  it("splits an oversized baseline into ordered, individually hashed chunks", async () => {
    const chunks = await chunkBaseline("b-1", snapshot, 4096, globalThis.crypto.subtle);
    expect(chunks.length).toBeGreaterThan(1);
    chunks.forEach((chunk, index) => {
      expect(chunk.chunk_index).toBe(index);
      expect(chunk.chunk_count).toBe(chunks.length);
      expect(chunk.chunk_hash).toMatch(/^sha256:[0-9a-f]{64}$/);
    });
    expect(chunks.at(-1)?.aggregate_hash).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(chunks.slice(0, -1).every((chunk) => chunk.aggregate_hash === undefined)).toBe(true);
    expect(chunks.map((chunk) => chunk.chunk_body).join("")).toBe(JSON.stringify(snapshot));
  });
});
