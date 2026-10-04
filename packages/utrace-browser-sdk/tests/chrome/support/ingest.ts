/**
 * Test browser telemetry ingestion edge.
 *
 * Telemetry ingestion belongs to another workstream. This server implements its
 * documented contract so the export path is exercised for real: it
 * authenticates the credential subprotocol, accepts one browser stream, tracks
 * the highest contiguous accepted `export_sequence`, acknowledges only that
 * watermark, deduplicates a retried observation id, and can apply flow control
 * on demand. A dropped range arrives as an explicit `observation.gap` record
 * rather than a hole, because the exporter assigns `export_sequence` at drain
 * time, so the export stream itself is always contiguous.
 */

import { WebSocketServer, type WebSocket } from "ws";

export interface ReceivedObservation {
  readonly observation_id: string;
  readonly observation_type: string;
  readonly source_sequence: number;
  readonly export_sequence: number;
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly frame_id: string;
  readonly occurred_at: string;
  readonly monotonic_offset: number;
  readonly route_revision: number;
  readonly visibility_state: string;
  readonly trace_id?: string;
  readonly target_id?: string;
  readonly payload: Record<string, unknown>;
}

export interface ReceivedBatch {
  readonly batch_id: string;
  readonly first_export_sequence: number;
  readonly last_export_sequence: number;
  readonly count: number;
}

export interface IngestServer {
  readonly url: string;
  readonly observations: readonly ReceivedObservation[];
  readonly batches: readonly ReceivedBatch[];
  readonly streamIds: readonly string[];
  readonly acknowledged: () => number;
  readonly credentialsSeen: readonly string[];
  readonly resumeRequests: readonly number[];
  readonly streamEnds: readonly { readonly reason: string; readonly terminal_export_sequence: number }[];
  /** Applies or releases flow control on the live connection. */
  readonly setFlowControl: (state: "pause" | "resume") => void;
  /** Sends an acknowledgement beyond what the browser produced. */
  readonly sendImpossibleAck: (beyond: number) => void;
  /** Cuts the live connection without a close frame, as a network proxy does. */
  readonly dropConnection: () => void;
  readonly close: () => Promise<void>;
}

const CREDENTIAL_PREFIX = "utrace.credential.";

export const startIngest = async (port: number): Promise<IngestServer> => {
  const observations: ReceivedObservation[] = [];
  const batches: ReceivedBatch[] = [];
  const streamIds: string[] = [];
  const credentialsSeen: string[] = [];
  const resumeRequests: number[] = [];
  const streamEnds: { reason: string; terminal_export_sequence: number }[] = [];
  let paused = false;
  let active: WebSocket | null = null;
  let streamId: string | null = null;
  /** Acknowledgement state is per browser stream: a reload starts a new one. */
  const streams = new Map<string, { accepted: Set<number>; acknowledged: number }>();

  const streamState = (id: string): { accepted: Set<number>; acknowledged: number } => {
    const existing = streams.get(id);
    if (existing !== undefined) return existing;
    const created = { accepted: new Set<number>(), acknowledged: 0 };
    streams.set(id, created);
    return created;
  };

  const server = new WebSocketServer({
    port,
    host: "127.0.0.1",
    handleProtocols: (protocols: Set<string>): string | false => {
      const credential = [...protocols].find((value) => value.startsWith(CREDENTIAL_PREFIX));
      if (credential === undefined || !protocols.has("utrace.browser.v1")) return false;
      credentialsSeen.push(credential.slice(CREDENTIAL_PREFIX.length));
      return "utrace.browser.v1";
    },
  });

  /** Highest contiguous accepted sequence for one stream. */
  const advance = (state: { accepted: Set<number>; acknowledged: number }): void => {
    while (state.accepted.has(state.acknowledged + 1)) state.acknowledged += 1;
  };

  server.on("connection", (socket) => {
    active = socket;
    socket.on("message", (data: Buffer) => {
      const frame = JSON.parse(data.toString("utf8")) as Record<string, unknown>;
      if (frame["kind"] === "stream.open") {
        streamId = String(frame["browser_stream_id"]);
        streamIds.push(streamId);
        resumeRequests.push(Number(frame["resume_from_export_sequence"]));
        socket.send(
          JSON.stringify({
            kind: "stream.accepted",
            browser_stream_id: streamId,
            accepted_through_export_sequence: streamState(streamId).acknowledged,
          }),
        );
        return;
      }
      if (frame["kind"] === "observation.batch") {
        const state = streamState(String(frame["browser_stream_id"]));
        const received = frame["observations"] as ReceivedObservation[];
        batches.push({
          batch_id: String(frame["batch_id"]),
          first_export_sequence: Number(frame["first_export_sequence"]),
          last_export_sequence: Number(frame["last_export_sequence"]),
          count: received.length,
        });
        for (const observation of received) {
          const duplicate = observations.some((seen) => seen.observation_id === observation.observation_id);
          if (!duplicate) observations.push(observation);
          state.accepted.add(observation.export_sequence);
        }
        advance(state);
        if (!paused) {
          socket.send(
            JSON.stringify({
              kind: "ack",
              browser_stream_id: streamId,
              accepted_through_export_sequence: state.acknowledged,
            }),
          );
        }
        return;
      }
      if (frame["kind"] === "stream.end") {
        streamEnds.push({
          reason: String(frame["reason"]),
          terminal_export_sequence: Number(frame["terminal_export_sequence"]),
        });
      }
    });
    socket.on("close", () => {
      if (active === socket) active = null;
    });
  });

  await new Promise<void>((resolve) => {
    server.on("listening", resolve);
  });

  return {
    url: `ws://localhost:${String(port)}/v1/ingest/browser`,
    observations,
    batches,
    streamIds,
    credentialsSeen,
    resumeRequests,
    streamEnds,
    acknowledged: (): number => (streamId === null ? 0 : streamState(streamId).acknowledged),
    setFlowControl: (state): void => {
      paused = state === "pause";
      active?.send(JSON.stringify({ kind: "flow_control", state, retry_after_ms: 50 }));
      if (state === "resume") {
        active?.send(
          JSON.stringify({
            kind: "ack",
            browser_stream_id: streamId,
            accepted_through_export_sequence: streamId === null ? 0 : streamState(streamId).acknowledged,
          }),
        );
      }
    },
    sendImpossibleAck: (beyond): void => {
      active?.send(
        JSON.stringify({
          kind: "ack",
          browser_stream_id: streamId,
          accepted_through_export_sequence: beyond,
        }),
      );
    },
    dropConnection: (): void => {
      if (active === null) throw new Error("no live browser telemetry connection to drop");
      active.terminate();
    },
    close: (): Promise<void> =>
      new Promise<void>((resolve, reject) => {
        for (const client of server.clients) client.terminate();
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
      }),
  };
};
