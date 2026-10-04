/**
 * Document topology: instrumented child frames.
 *
 * Every instrumented top-level document or iframe has one local runtime. The
 * top-document runtime is the coordinator and the only browser telemetry
 * exporter for that page. An instrumented child frame authenticates its own
 * document and sends normalized records to the coordinator over an
 * authenticated `MessageChannel`. An uninstrumented frame is opaque to
 * observation and cannot supply structured targets.
 */

import type { BrowserView } from "../core/dom.js";
import type { JsonObject } from "../core/json.js";
import { asObject, parseJson, requireEnum, requireInteger, requireNumber, requireObject, requireString } from "../core/json.js";
import { UTraceRuntimeError } from "../core/failure.js";
import type { SignedEnvelope } from "../activation/signed-envelope.js";
import { decodeSignedEnvelope, verifySignedEnvelope } from "../activation/signed-envelope.js";
import { decodeAttestation } from "../activation/attestation.js";
import type { ObservationType, VisibilityState } from "../observation/types.js";

export const FRAME_HELLO_KIND = "utrace.frame.hello";
export const FRAME_ACCEPT_KIND = "utrace.frame.accept";
export const FRAME_RECORD_KIND = "utrace.frame.record";

export interface FrameAttachment {
  readonly frame_id: string;
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly port: MessagePort;
}

/**
 * A normalized record produced by a child frame runtime. The coordinator does
 * not re-interpret the payload body, which ingestion validates against the same
 * schema, but it does validate every envelope field it re-stamps.
 */
export interface ChildRecord {
  readonly observation_id: string;
  readonly observation_type: ObservationType;
  readonly source_sequence: number;
  readonly occurred_at: string;
  readonly monotonic_offset: number;
  readonly route_revision: number;
  readonly visibility_state: VisibilityState;
  readonly body: JsonObject;
}

export interface FrameCoordinatorCallbacks {
  /** Accepts a normalized record produced by a child frame runtime. */
  readonly onChildObservation: (attachment: FrameAttachment, record: ChildRecord) => void;
  readonly onAttachmentLost: (attachment: FrameAttachment) => void;
  readonly onRejected: (reason: string) => void;
}

interface HelloMessage {
  readonly schema_version: string;
  readonly frame_id: string;
  readonly runtime_id: string;
  readonly attestation: SignedEnvelope;
}

const OBSERVATION_TYPES: readonly ObservationType[] = [
  "baseline.snapshot",
  "baseline.chunk",
  "interaction.activation",
  "interaction.focus_change",
  "input.metadata",
  "form.submission",
  "route.change",
  "request.started",
  "request.completed",
  "error.frontend",
  "performance.measurement",
  "product.milestone",
  "target.registry_change",
  "dom.change",
  "observation.gap",
];

const decodeChildRecord = (source: JsonObject): ChildRecord => {
  const path = "frame_record.record";
  return {
    observation_id: requireString(source, "observation_id", path),
    observation_type: requireEnum(source, "observation_type", OBSERVATION_TYPES, path),
    source_sequence: requireInteger(source, "source_sequence", path),
    occurred_at: requireString(source, "occurred_at", path),
    monotonic_offset: requireNumber(source, "monotonic_offset", path),
    route_revision: requireInteger(source, "route_revision", path),
    visibility_state: requireEnum(source, "visibility_state", ["visible", "hidden"] as const, path),
    body: requireObject(source, "body", path),
  };
};

const decodeHello = (data: JsonObject): HelloMessage => {
  const path = "frame_hello";
  return {
    schema_version: requireString(data, "schema_version", path),
    frame_id: requireString(data, "frame_id", path),
    runtime_id: requireString(data, "runtime_id", path),
    attestation: decodeSignedEnvelope(requireObject(data, "attestation", path), `${path}.attestation`),
  };
};

/**
 * Coordinator side. Accepts only a child whose own attestation binds the same
 * client, user, customer account, session, preview environment, and runtime
 * allocation as this document's attestation.
 */
export class FrameCoordinator {
  readonly #view: BrowserView;
  readonly #subtle: SubtleCrypto;
  readonly #schemaVersion: string;
  readonly #expectedScope: Readonly<Record<string, string>>;
  readonly #callbacks: FrameCoordinatorCallbacks;
  readonly #attachments = new Map<string, FrameAttachment>();
  #listening = false;

  constructor(
    view: BrowserView,
    subtle: SubtleCrypto,
    schemaVersion: string,
    expectedScope: Readonly<Record<string, string>>,
    callbacks: FrameCoordinatorCallbacks,
  ) {
    this.#view = view;
    this.#subtle = subtle;
    this.#schemaVersion = schemaVersion;
    this.#expectedScope = expectedScope;
    this.#callbacks = callbacks;
  }

  get attachmentCount(): number {
    return this.#attachments.size;
  }

  listen(): () => void {
    if (this.#listening) {
      throw new UTraceRuntimeError("duplicate_runtime_or_hook", "frame coordinator is already listening", {});
    }
    this.#listening = true;
    const onMessage = (event: MessageEvent): void => {
      void this.#onMessage(event);
    };
    this.#view.addEventListener("message", onMessage);
    return (): void => {
      this.#view.removeEventListener("message", onMessage);
      for (const attachment of this.#attachments.values()) attachment.port.close();
      this.#attachments.clear();
      this.#listening = false;
    };
  }

  async #onMessage(event: MessageEvent): Promise<void> {
    if (event.origin !== this.#view.location.origin) return;
    const port = event.ports[0];
    if (port === undefined) return;
    let hello: HelloMessage;
    try {
      const data = asObject(parseJson(String(event.data), "frame_hello"), "frame_hello");
      if (data["kind"] !== FRAME_HELLO_KIND) return;
      hello = decodeHello(data);
    } catch (cause) {
      this.#callbacks.onRejected(`undecodable frame hello: ${String(cause)}`);
      return;
    }
    if (hello.schema_version !== this.#schemaVersion) {
      this.#callbacks.onRejected(`frame speaks schema ${hello.schema_version}`);
      return;
    }
    let attachment: FrameAttachment;
    try {
      const payload = await verifySignedEnvelope(hello.attestation, this.#subtle, "frame_hello.attestation");
      const attestation = decodeAttestation(payload);
      for (const [field, expected] of Object.entries(this.#expectedScope)) {
        const value = payload[field];
        const received = typeof value === "string" ? value : "";
        if (received !== expected) {
          this.#callbacks.onRejected(`frame attestation ${field} does not match the coordinator`);
          return;
        }
      }
      attachment = {
        frame_id: hello.frame_id,
        runtime_id: hello.runtime_id,
        document_attachment_id: attestation.document_attachment_id,
        port,
      };
    } catch (cause) {
      this.#callbacks.onRejected(`frame attestation rejected: ${String(cause)}`);
      return;
    }
    this.#attachments.set(attachment.frame_id, attachment);
    port.addEventListener("message", (message: MessageEvent) => {
      this.#onRecord(attachment, message);
    });
    port.start();
    port.postMessage(JSON.stringify({ kind: FRAME_ACCEPT_KIND, schema_version: this.#schemaVersion }));
  }

  #onRecord(attachment: FrameAttachment, event: MessageEvent): void {
    let child: ChildRecord;
    try {
      const message = asObject(parseJson(String(event.data), "frame_record"), "frame_record");
      if (message["kind"] !== FRAME_RECORD_KIND) return;
      child = decodeChildRecord(requireObject(message, "record", "frame_record"));
    } catch (cause) {
      this.#callbacks.onRejected(`undecodable frame record: ${String(cause)}`);
      return;
    }
    // The coordinator re-envelopes child records; it never trusts a child's
    // export sequence or scope fields.
    this.#callbacks.onChildObservation(attachment, child);
  }

  detach(frameId: string): void {
    const attachment = this.#attachments.get(frameId);
    if (attachment === undefined) return;
    attachment.port.close();
    this.#attachments.delete(frameId);
    this.#callbacks.onAttachmentLost(attachment);
  }
}

/** Child side: offers this frame's runtime to the top-document coordinator. */
export const connectToCoordinator = (
  view: BrowserView,
  frameId: string,
  runtimeId: string,
  schemaVersion: string,
  attestation: SignedEnvelope,
): MessagePort => {
  const top = view.top;
  if (top === null || top === view) {
    throw new UTraceRuntimeError("required_frame_or_adapter_missing", "this document is not a child frame", {
      frame_id: frameId,
    });
  }
  const channel = new MessageChannel();
  top.postMessage(
    JSON.stringify({
      kind: FRAME_HELLO_KIND,
      schema_version: schemaVersion,
      frame_id: frameId,
      runtime_id: runtimeId,
      attestation,
    }),
    view.location.origin,
    [channel.port2],
  );
  channel.port1.start();
  return channel.port1;
};
