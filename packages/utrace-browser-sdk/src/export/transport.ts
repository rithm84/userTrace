/**
 * Dedicated authenticated telemetry WebSocket.
 *
 * This socket is separate from the extension's control WebSocket. It accepts
 * telemetry writes plus acknowledgements and flow control, never browser
 * commands. If it is unavailable the runtime enters bounded recovery; it never
 * falls back to HTTP export, an extension relay, or an uncorrelated provider
 * endpoint.
 *
 * One transport carries one stream with one credential. When the socket drops,
 * the transport reports the disconnect and the runtime decides how to
 * reconnect; a reopened stream retransmits from ingestion's last contiguous
 * acknowledgement.
 */

import type { BrowserView } from "../core/dom.js";
import type { RuntimeFailureCode } from "../core/failure.js";
import { ExportError } from "../core/failure.js";
import type { JsonObject } from "../core/json.js";
import { PAGEHIDE_FLUSH_BYTES } from "../core/limits.js";
import type { ObservationScope } from "../observation/types.js";
import type { ExportRecord } from "./record.js";
import { INGEST_SUBPROTOCOL, SUPPORTED_PROTOCOL_VERSION, SUPPORTED_SCHEMA_VERSION } from "../core/versions.js";
import type { TelemetryCredential } from "../activation/credential.js";
import type { ExportBuffer, InFlightRecord } from "./buffer.js";
import type { AcknowledgementTracker, SequenceAllocator } from "./sequences.js";
import type { ErrorFrame, ServerFrame, StreamEndFrame } from "./protocol.js";
import { decodeServerFrame, encodeClientFrame, encodeObservation } from "./protocol.js";

/** A stream that ingestion does not accept within this window is unavailable. */
const STREAM_OPEN_TIMEOUT_MS = 10_000;
const MAX_RECORDS_PER_BATCH = 256;

/**
 * Maps ingestion's refusal of `stream.open` onto the runtime vocabulary. Only a
 * credential failure calls for a fresh attestation. Every other non-retryable
 * refusal names a stream identity ingestion will not accept, which is an
 * integrity failure rather than a reconnect hint.
 */
const openRefusalCode = (frame: ErrorFrame): RuntimeFailureCode => {
  if (frame.retryable) return "ingestion_unavailable";
  if (frame.code === "credential_rejected") return "credential_rejected";
  if (frame.code === "schema_incompatible") return "incompatible_protocol_or_schema";
  return "acknowledgement_integrity_failure";
};

export interface StreamIdentity {
  readonly browser_stream_id: string;
  readonly runtime_id: string;
  readonly document_attachment_id: string;
}

export interface TransportCallbacks {
  readonly onAcknowledged: (acknowledgedThrough: number) => void;
  readonly onFlowControl: (state: "pause" | "resume") => void;
  readonly onFailure: (error: ExportError) => void;
  /** The accepted stream's socket closed without a terminal sequence. */
  readonly onDisconnected: () => void;
  readonly batchId: () => string;
}

export class IngestTransport {
  readonly #view: BrowserView;
  readonly #scope: ObservationScope;
  readonly #identity: StreamIdentity;
  readonly #buffer: ExportBuffer;
  readonly #exportSequences: SequenceAllocator;
  readonly #acks: AcknowledgementTracker;
  readonly #callbacks: TransportCallbacks;
  #socket: WebSocket | null = null;
  #credential: TelemetryCredential | null = null;
  #accepted = false;
  #paused = false;
  #inFlightBatches = 0;
  #terminated = false;

  constructor(
    view: BrowserView,
    scope: ObservationScope,
    identity: StreamIdentity,
    buffer: ExportBuffer,
    exportSequences: SequenceAllocator,
    acks: AcknowledgementTracker,
    callbacks: TransportCallbacks,
  ) {
    this.#view = view;
    this.#scope = scope;
    this.#identity = identity;
    this.#buffer = buffer;
    this.#exportSequences = exportSequences;
    this.#acks = acks;
    this.#callbacks = callbacks;
  }

  get connected(): boolean {
    return this.#accepted && this.#socket !== null && this.#socket.readyState === this.#view.WebSocket.OPEN;
  }

  get flowControlPaused(): boolean {
    return this.#paused;
  }

  /**
   * Opens the socket and waits, within a bound, for ingestion to accept the
   * stream. A refused or failed open closes its socket before rejecting.
   */
  async open(credential: TelemetryCredential): Promise<void> {
    this.#credential = credential;
    const socket = new this.#view.WebSocket(credential.ingestUrl, [
      INGEST_SUBPROTOCOL,
      credential.subprotocolValue(),
    ]);
    this.#socket = socket;
    this.#accepted = false;
    await new Promise<void>((resolve, reject) => {
      const refuse = (error: ExportError): void => {
        cleanup();
        socket.close();
        reject(error);
      };
      const timer = this.#view.setTimeout(() => {
        refuse(
          new ExportError("ingestion_unavailable", "ingestion did not accept the stream in time", {
            timeout_ms: STREAM_OPEN_TIMEOUT_MS,
            ready_state: socket.readyState,
          }),
        );
      }, STREAM_OPEN_TIMEOUT_MS);
      const onOpen = (): void => {
        socket.send(
          encodeClientFrame({
            kind: "stream.open",
            schema_version: SUPPORTED_SCHEMA_VERSION,
            protocol_version: SUPPORTED_PROTOCOL_VERSION,
            browser_stream_id: this.#identity.browser_stream_id,
            runtime_id: this.#identity.runtime_id,
            document_attachment_id: this.#identity.document_attachment_id,
            scope: this.#scope,
            resume_from_export_sequence: this.#acks.acknowledged,
          }),
        );
      };
      const onFirstMessage = (event: MessageEvent<string>): void => {
        let frame: ServerFrame;
        try {
          frame = decodeServerFrame(event.data);
        } catch (cause) {
          refuse(
            new ExportError("ingestion_unavailable", "ingestion sent an undecodable frame during stream open", {
              cause: String(cause),
            }),
          );
          return;
        }
        if (frame.kind === "error") {
          refuse(
            new ExportError(openRefusalCode(frame), frame.message, {
              code: frame.code,
              retryable: frame.retryable,
              browser_stream_id: this.#identity.browser_stream_id,
            }),
          );
          return;
        }
        if (frame.kind !== "stream.accepted") {
          refuse(
            new ExportError("ingestion_unavailable", "ingestion did not accept the stream before sending data", {
              received_kind: frame.kind,
            }),
          );
          return;
        }
        if (frame.browser_stream_id !== this.#identity.browser_stream_id) {
          refuse(
            new ExportError("acknowledgement_integrity_failure", "ingestion accepted a different browser stream", {
              expected: this.#identity.browser_stream_id,
              received: frame.browser_stream_id,
            }),
          );
          return;
        }
        const accepted = this.#acks.accept(frame.accepted_through_export_sequence, this.#exportSequences.produced);
        if (!accepted.ok) {
          refuse(
            new ExportError("acknowledgement_integrity_failure", accepted.reason, {
              frame: "stream.accepted",
              produced: this.#exportSequences.produced,
            }),
          );
          return;
        }
        cleanup();
        this.#buffer.acknowledge(this.#acks.acknowledged);
        this.#accepted = true;
        this.#paused = false;
        socket.addEventListener("message", this.#onMessage);
        socket.addEventListener("close", this.#onClose);
        resolve();
      };
      const onError = (): void => {
        refuse(
          new ExportError("ingestion_unavailable", "telemetry WebSocket failed before the stream was accepted", {
            ready_state: socket.readyState,
          }),
        );
      };
      const cleanup = (): void => {
        this.#view.clearTimeout(timer);
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("message", onFirstMessage);
        socket.removeEventListener("error", onError);
        socket.removeEventListener("close", onError);
      };
      socket.addEventListener("open", onOpen);
      socket.addEventListener("message", onFirstMessage);
      socket.addEventListener("error", onError);
      socket.addEventListener("close", onError);
    });
    this.#retransmit(credential.limits.max_batch_bytes);
  }

  readonly #onMessage = (event: MessageEvent<string>): void => {
    let frame: ServerFrame;
    try {
      frame = decodeServerFrame(event.data);
    } catch (cause) {
      this.#callbacks.onFailure(
        new ExportError("ingestion_unavailable", "ingestion sent an undecodable frame", { cause: String(cause) }),
      );
      return;
    }
    switch (frame.kind) {
      case "ack": {
        if (frame.browser_stream_id !== this.#identity.browser_stream_id) {
          this.#callbacks.onFailure(
            new ExportError("acknowledgement_integrity_failure", "acknowledgement names another browser stream", {
              expected: this.#identity.browser_stream_id,
              received: frame.browser_stream_id,
            }),
          );
          return;
        }
        const accepted = this.#acks.accept(frame.accepted_through_export_sequence, this.#exportSequences.produced);
        if (!accepted.ok) {
          this.#callbacks.onFailure(
            new ExportError("acknowledgement_integrity_failure", accepted.reason, {
              produced: this.#exportSequences.produced,
            }),
          );
          return;
        }
        this.#buffer.acknowledge(this.#acks.acknowledged);
        this.#inFlightBatches = Math.max(0, this.#inFlightBatches - 1);
        this.#callbacks.onAcknowledged(this.#acks.acknowledged);
        void this.flush();
        return;
      }
      case "flow_control": {
        this.#paused = frame.state === "pause";
        this.#callbacks.onFlowControl(frame.state);
        if (!this.#paused) void this.flush();
        return;
      }
      case "error": {
        this.#callbacks.onFailure(
          new ExportError(frame.retryable ? "ingestion_unavailable" : "baseline_rejected", frame.message, {
            code: frame.code,
            retryable: frame.retryable,
            batch_id: frame.batch_id ?? "",
          }),
        );
        return;
      }
      case "stream.accepted": {
        this.#callbacks.onFailure(
          new ExportError("acknowledgement_integrity_failure", "ingestion accepted the stream twice", {
            browser_stream_id: frame.browser_stream_id,
          }),
        );
      }
    }
  };

  readonly #onClose = (): void => {
    this.#accepted = false;
    if (this.#terminated) return;
    this.#callbacks.onDisconnected();
  };

  /** Sends as many batches as flow control and the in-flight bound allow. */
  async flush(): Promise<void> {
    if (!this.connected || this.#paused || this.#credential === null) return;
    const limits = this.#credential.limits;
    while (this.#buffer.hasPending() && this.#inFlightBatches < limits.max_in_flight_batches) {
      const batch = this.#buffer.drainBatch(
        limits.max_batch_bytes,
        MAX_RECORDS_PER_BATCH,
        false,
        () => this.#exportSequences.allocate(),
        (record, exportSequence) => this.#encode(record, exportSequence),
      );
      if (batch.length === 0) return;
      this.#send(batch.map((record) => record.encoded), batch);
    }
    await Promise.resolve();
  }

  /** Best-effort bounded flush on `pagehide`. Critical records only. */
  flushOnPagehide(): void {
    if (!this.connected || this.#credential === null) return;
    const batch = this.#buffer.drainBatch(
      Math.min(PAGEHIDE_FLUSH_BYTES, this.#credential.limits.max_batch_bytes),
      MAX_RECORDS_PER_BATCH,
      true,
      () => this.#exportSequences.allocate(),
      (record, exportSequence) => this.#encode(record, exportSequence),
    );
    if (batch.length > 0) this.#send(batch.map((record) => record.encoded), batch);
    this.#buffer.dropDroppable("pagehide_truncated");
    this.#end("pagehide");
  }

  #encode(record: ExportRecord, exportSequence: number): JsonObject {
    return encodeObservation({ envelope: { ...record.envelope, export_sequence: exportSequence }, body: record.body });
  }

  #send(observations: readonly JsonObject[], batch: readonly { readonly export_sequence: number }[]): void {
    const socket = this.#socket;
    const first = batch[0];
    const last = batch.at(-1);
    if (socket === null || first === undefined || last === undefined) return;
    socket.send(
      encodeClientFrame({
        kind: "observation.batch",
        batch_id: this.#callbacks.batchId(),
        browser_stream_id: this.#identity.browser_stream_id,
        first_export_sequence: first.export_sequence,
        last_export_sequence: last.export_sequence,
        observations,
      }),
    );
    this.#inFlightBatches += 1;
  }

  /**
   * Resends unacknowledged records with their original ids and sequences, in
   * batches within the same byte and record bounds as a regular flush.
   */
  #retransmit(maxBatchBytes: number): void {
    this.#inFlightBatches = 0;
    let batch: InFlightRecord[] = [];
    let bytes = 0;
    for (const record of this.#buffer.unacknowledged(this.#acks.acknowledged)) {
      if (batch.length > 0 && (bytes + record.bytes > maxBatchBytes || batch.length >= MAX_RECORDS_PER_BATCH)) {
        this.#send(
          batch.map((entry) => entry.encoded),
          batch,
        );
        batch = [];
        bytes = 0;
      }
      batch.push(record);
      bytes += record.bytes;
    }
    if (batch.length > 0) {
      this.#send(
        batch.map((entry) => entry.encoded),
        batch,
      );
    }
  }

  #end(reason: StreamEndFrame["reason"]): void {
    const socket = this.#socket;
    if (socket === null || socket.readyState !== this.#view.WebSocket.OPEN) return;
    socket.send(
      encodeClientFrame({
        kind: "stream.end",
        browser_stream_id: this.#identity.browser_stream_id,
        terminal_export_sequence: this.#exportSequences.produced,
        reason,
      }),
    );
  }

  /**
   * Stops this transport without recording a terminal sequence, so ingestion
   * keeps the stream's last contiguous acknowledgement as the end of what it
   * delivered. The runtime abandons a dropped transport before reconnecting.
   */
  abandon(): void {
    this.#terminated = true;
    this.#socket?.close();
    this.#socket = null;
    this.#accepted = false;
  }

  /** Records a terminal sequence and closes this transport. */
  close(reason: StreamEndFrame["reason"]): void {
    this.#terminated = true;
    this.#end(reason);
    this.#socket?.close(1000, reason);
    this.#socket = null;
    this.#accepted = false;
  }
}
