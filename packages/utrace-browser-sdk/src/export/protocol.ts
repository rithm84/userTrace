/**
 * Browser telemetry ingestion protocol.
 *
 * The dedicated authenticated WebSocket accepts telemetry writes plus
 * acknowledgements and flow-control messages. It never carries browser
 * commands, detail queries, or control-plane state.
 *
 * Client frames: `stream.open`, `observation.batch`, `stream.end`.
 * Server frames: `stream.accepted`, `ack`, `flow_control`, `error`.
 */

import type { JsonObject, JsonValue } from "../core/json.js";
import { asObject, parseJson, requireBoolean, requireEnum, requireInteger, requireString, optionalString } from "../core/json.js";
import type { ObservationScope } from "../observation/types.js";
import type { ExportRecord } from "./record.js";

export interface StreamOpenFrame {
  readonly kind: "stream.open";
  readonly schema_version: string;
  readonly protocol_version: string;
  readonly browser_stream_id: string;
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly scope: ObservationScope;
  /** Zero on a new stream; the last contiguous acknowledgement on a resume. */
  readonly resume_from_export_sequence: number;
}

export interface ObservationBatchFrame {
  readonly kind: "observation.batch";
  readonly batch_id: string;
  readonly browser_stream_id: string;
  readonly first_export_sequence: number;
  readonly last_export_sequence: number;
  readonly observations: readonly JsonObject[];
}

export interface StreamEndFrame {
  readonly kind: "stream.end";
  readonly browser_stream_id: string;
  readonly terminal_export_sequence: number;
  readonly reason: "pagehide" | "terminated" | "paused";
}

export type ClientFrame = StreamOpenFrame | ObservationBatchFrame | StreamEndFrame;

export interface StreamAcceptedFrame {
  readonly kind: "stream.accepted";
  readonly browser_stream_id: string;
  readonly accepted_through_export_sequence: number;
}

export interface AckFrame {
  readonly kind: "ack";
  readonly browser_stream_id: string;
  readonly accepted_through_export_sequence: number;
}

export interface FlowControlFrame {
  readonly kind: "flow_control";
  readonly state: "pause" | "resume";
  readonly retry_after_ms: number;
}

export interface ErrorFrame {
  readonly kind: "error";
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  readonly batch_id: string | null;
}

export type ServerFrame = StreamAcceptedFrame | AckFrame | FlowControlFrame | ErrorFrame;

/** Serializes one export record into its wire object. */
export const encodeObservation = (record: ExportRecord): JsonObject => {
  const envelope = record.envelope;
  const wire: Record<string, JsonValue> = {
    observation_id: envelope.observation_id,
    observation_type: envelope.observation_type,
    runtime_id: envelope.runtime_id,
    document_attachment_id: envelope.document_attachment_id,
    frame_id: envelope.frame_id,
    source_sequence: envelope.source_sequence,
    export_sequence: envelope.export_sequence,
    occurred_at: envelope.occurred_at,
    monotonic_offset: envelope.monotonic_offset,
    route_revision: envelope.route_revision,
    visibility_state: envelope.visibility_state,
    payload: record.body,
  };
  if (envelope.target_id !== undefined) wire["target_id"] = envelope.target_id;
  if (envelope.target_revision !== undefined) wire["target_revision"] = envelope.target_revision;
  if (envelope.trace_id !== undefined) wire["trace_id"] = envelope.trace_id;
  if (envelope.span_id !== undefined) wire["span_id"] = envelope.span_id;
  return wire;
};

const encoder = new TextEncoder();

export const jsonByteLength = (value: JsonObject): number => encoder.encode(JSON.stringify(value)).byteLength;

export const encodeClientFrame = (frame: ClientFrame): string => JSON.stringify(frame);

/** Decodes one server frame. An unknown kind or missing field fails closed. */
export const decodeServerFrame = (text: string): ServerFrame => {
  const path = "ingest_frame";
  const frame = asObject(parseJson(text, path), path);
  const kind = requireEnum(frame, "kind", ["stream.accepted", "ack", "flow_control", "error"] as const, path);
  switch (kind) {
    case "stream.accepted":
      return {
        kind,
        browser_stream_id: requireString(frame, "browser_stream_id", path),
        accepted_through_export_sequence: requireInteger(frame, "accepted_through_export_sequence", path),
      };
    case "ack":
      return {
        kind,
        browser_stream_id: requireString(frame, "browser_stream_id", path),
        accepted_through_export_sequence: requireInteger(frame, "accepted_through_export_sequence", path),
      };
    case "flow_control":
      return {
        kind,
        state: requireEnum(frame, "state", ["pause", "resume"] as const, path),
        retry_after_ms: requireInteger(frame, "retry_after_ms", path),
      };
    case "error":
      return {
        kind,
        code: requireString(frame, "code", path),
        message: requireString(frame, "message", path),
        retryable: requireBoolean(frame, "retryable", path),
        batch_id: optionalString(frame, "batch_id", path),
      };
  }
};
