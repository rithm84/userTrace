/**
 * Envelope assembly, identifiers, and sequence allocation.
 */

import { describe, expect, it } from "vitest";
import { uuidv7, spanId, traceId, webCryptoRandom, nonce, opaqueId } from "../../src/core/ids.js";
import { rfc3339, isExpired } from "../../src/core/time.js";
import { buildEnvelope, buildObservation, UNASSIGNED_EXPORT_SEQUENCE } from "../../src/observation/envelope.js";
import { priorityOf } from "../../src/observation/types.js";
import { AcknowledgementTracker, SequenceAllocator } from "../../src/export/sequences.js";
import { encodeObservation, decodeServerFrame, jsonByteLength } from "../../src/export/protocol.js";
import { formatTraceparent, TraceCoordinator, UTRACE_TRACESTATE } from "../../src/trace/context.js";

const random = webCryptoRandom(globalThis.crypto);

const identity = { runtime_id: "runtime-1", document_attachment_id: "attachment-1", frame_id: "top" };

describe("identifiers", () => {
  it("produces sortable UUIDv7 values with the right version and variant", () => {
    const earlier = uuidv7(1_700_000_000_000, random);
    const later = uuidv7(1_800_000_000_000, random);
    expect(earlier).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(earlier < later).toBe(true);
  });

  it("produces non-zero W3C trace and span identifiers", () => {
    expect(traceId(random)).toMatch(/^[0-9a-f]{32}$/);
    expect(spanId(random)).toMatch(/^[0-9a-f]{16}$/);
    expect(traceId(random)).not.toBe("0".repeat(32));
  });

  it("produces opaque document-scoped identifiers that encode the ordinal", () => {
    expect(nonce(random)).toMatch(/^[0-9a-f]{48}$/);
    expect(opaqueId("tgt", 7, random)).toMatch(/^tgt_7_[0-9a-f]{16}$/);
  });
});

describe("time", () => {
  it("formats RFC 3339 UTC with millisecond precision", () => {
    expect(rfc3339(0)).toBe("1970-01-01T00:00:00.000Z");
  });

  it("treats an unparsable or past expiry as expired", () => {
    expect(isExpired("not a date", Date.now())).toBe(true);
    expect(isExpired(rfc3339(Date.now() - 1000), Date.now())).toBe(true);
    expect(isExpired(rfc3339(Date.now() + 60_000), Date.now())).toBe(false);
  });
});

describe("envelope", () => {
  const base = {
    observation_id: uuidv7(Date.now(), random),
    observation_type: "route.change" as const,
    identity,
    source_sequence: 4,
    occurred_at: rfc3339(Date.now()),
    monotonic_offset: 1234.5,
    route_revision: 2,
    visibility_state: "visible" as const,
  };

  it("omits optional fields rather than sending null", () => {
    const envelope = buildEnvelope({ ...base, target_id: null, target_revision: null, trace_id: null, span_id: null });
    expect(envelope).not.toHaveProperty("target_id");
    expect(envelope).not.toHaveProperty("trace_id");
    expect(envelope.export_sequence).toBe(UNASSIGNED_EXPORT_SEQUENCE);
  });

  it("carries target and trace identity when they apply", () => {
    const envelope = buildEnvelope({
      ...base,
      target_id: "tgt_1_aaaaaaaaaaaaaaaa",
      target_revision: 3,
      trace_id: traceId(random),
      span_id: spanId(random),
    });
    expect(envelope.target_revision).toBe(3);
    expect(envelope.trace_id).toMatch(/^[0-9a-f]{32}$/);
  });

  it("refuses an envelope whose type disagrees with its payload", () => {
    const envelope = buildEnvelope({ ...base, target_id: null, target_revision: null, trace_id: null, span_id: null });
    expect(() =>
      buildObservation(envelope, {
        type: "dom.change",
        body: { changes: [], coalesced_count: 1, window_start_offset: 0, window_end_offset: 1, truncated: false },
      }),
    ).toThrow(/does not match payload type/);
  });

  it("assigns delivery priority by observation type", () => {
    expect(priorityOf("interaction.activation")).toBe("critical");
    expect(priorityOf("observation.gap")).toBe("critical");
    expect(priorityOf("dom.change")).toBe("normal");
    expect(priorityOf("performance.measurement")).toBe("low");
  });

  it("serializes the wire record with the assigned export sequence", () => {
    const envelope = buildEnvelope({ ...base, target_id: null, target_revision: null, trace_id: null, span_id: null });
    const wire = encodeObservation({ envelope: { ...envelope, export_sequence: 9 }, body: { committed: true } });
    expect(wire["export_sequence"]).toBe(9);
    expect(wire["payload"]).toEqual({ committed: true });
    expect(jsonByteLength(wire)).toBeGreaterThan(0);
  });
});

describe("sequences", () => {
  it("starts both streams at one and never repeats", () => {
    const allocator = new SequenceAllocator();
    expect(allocator.produced).toBe(0);
    expect(allocator.allocate()).toBe(1);
    expect(allocator.allocate()).toBe(2);
    expect(allocator.produced).toBe(2);
  });

  it("accepts a contiguous acknowledgement and rejects an impossible one", () => {
    const tracker = new AcknowledgementTracker();
    expect(tracker.accept(3, 5)).toEqual({ ok: true });
    expect(tracker.acknowledged).toBe(3);
    expect(tracker.accept(6, 5).ok).toBe(false);
    expect(tracker.accept(2, 5).ok).toBe(false);
    expect(tracker.acknowledged).toBe(3);
  });

  it("reports why an acknowledgement is impossible", () => {
    const tracker = new AcknowledgementTracker();
    const beyond = tracker.accept(9, 4);
    expect(beyond.ok).toBe(false);
    if (beyond.ok) return;
    expect(beyond.reason).toContain("beyond produced");
  });
});

describe("ingestion frames", () => {
  it("decodes each documented server frame", () => {
    expect(decodeServerFrame(JSON.stringify({ kind: "ack", browser_stream_id: "s", accepted_through_export_sequence: 3 })))
      .toMatchObject({ kind: "ack", accepted_through_export_sequence: 3 });
    expect(decodeServerFrame(JSON.stringify({ kind: "flow_control", state: "pause", retry_after_ms: 10 })))
      .toMatchObject({ kind: "flow_control", state: "pause" });
    expect(
      decodeServerFrame(JSON.stringify({ kind: "error", code: "quota", message: "slow down", retryable: true })),
    ).toMatchObject({ kind: "error", retryable: true, batch_id: null });
  });

  it("fails closed on an unknown frame kind or a missing field", () => {
    expect(() => decodeServerFrame(JSON.stringify({ kind: "browser.command" }))).toThrow(/one of/);
    expect(() => decodeServerFrame(JSON.stringify({ kind: "ack", browser_stream_id: "s" }))).toThrow(/finite number/);
    expect(() => decodeServerFrame("not json")).toThrow(/JSON text/);
  });
});

describe("trace context", () => {
  it("always marks an interview trace sampled", () => {
    expect(formatTraceparent({ trace_id: "a".repeat(32), span_id: "b".repeat(16) })).toBe(
      `00-${"a".repeat(32)}-${"b".repeat(16)}-01`,
    );
  });

  it("parents a request span under the interaction that caused it", () => {
    const now = 0;
    const coordinator = new TraceCoordinator(random, () => now);
    const interaction = coordinator.startInteractionSpan();
    const child = coordinator.createClientSpan();
    expect(child.trace_id).toBe(interaction.trace_id);
    expect(child.parent_span_id).toBe(interaction.span_id);
    expect(child.tracestate).toBe(UTRACE_TRACESTATE);
  });

  it("starts its own trace for a background request with no defensible parent", () => {
    let now = 0;
    const coordinator = new TraceCoordinator(random, () => now);
    const interaction = coordinator.startInteractionSpan();
    now = 5000;
    const background = coordinator.createClientSpan();
    expect(background.trace_id).not.toBe(interaction.trace_id);
    expect(background.parent_span_id).toBeNull();
  });
});
