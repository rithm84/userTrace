/**
 * The content script's end of the main-world bridge.
 *
 * It opens a fresh per-document `MessageChannel` to the injected bridge, which
 * relays the five JSON message kinds to the embedded SDK runtime. The bridge
 * supplies structured target identity and geometry; this side owns no product
 * observation and holds no credential.
 */
import {
  parseBridgeFrame,
  type BridgeFrame,
  type DetailQueryRequest,
  type DetailQueryResponse,
  type PageProofResponse,
  type TargetGeometryFrame,
} from "../protocol/bridge";
import { SCHEMA_VERSION } from "../protocol/common";
import { createAttachMessage } from "../shared/bridgeHandshake";
import { newUuidV7 } from "../shared/identifiers";

const ATTACH_ATTEMPTS = 3;
const ATTACH_TIMEOUT_MS = 400;
const EXCHANGE_TIMEOUT_MS = 1_200;

export type BridgeErrorCode = "page_bridge_unavailable" | "bridge_exchange_timeout";

export class BridgeError extends Error {
  readonly code: BridgeErrorCode;

  constructor(code: BridgeErrorCode, message: string) {
    super(message);
    this.name = "BridgeError";
    this.code = code;
  }
}

type PendingExchange = Readonly<{
  resolve: (frame: BridgeFrame) => void;
  reject: (error: BridgeError) => void;
  timer: ReturnType<typeof setTimeout>;
}>;

export class PageBridge {
  private readonly port: MessagePort;
  private readonly pending = new Map<string, PendingExchange>();
  private attachment: PageProofResponse["payload"];

  private constructor(port: MessagePort, attachment: PageProofResponse["payload"]) {
    this.port = port;
    this.attachment = attachment;
    this.port.onmessage = (event: MessageEvent): void => {
      this.receive(event.data);
    };
  }

  get documentAttachmentId(): string {
    return this.attachment.document_attachment_id;
  }

  get registryRevision(): number {
    return this.attachment.registry_revision;
  }

  /** Attaches to the injected bridge, retrying the handshake a bounded number of times. */
  static async attach(nonce: string): Promise<PageBridge> {
    for (let attempt = 1; attempt <= ATTACH_ATTEMPTS; attempt += 1) {
      const channel = new MessageChannel();
      const attached = await PageBridge.handshake(channel, nonce);
      if (attached !== null) {
        return attached;
      }
      channel.port1.close();
    }
    throw new BridgeError(
      "page_bridge_unavailable",
      "The embedded uTrace SDK did not answer the page bridge handshake.",
    );
  }

  private static handshake(
    channel: MessageChannel,
    nonce: string,
  ): Promise<PageBridge | null> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        channel.port1.onmessage = null;
        resolve(null);
      }, ATTACH_TIMEOUT_MS);

      channel.port1.onmessage = (event: MessageEvent): void => {
        const frame = parseBridgeFrame(event.data);
        if (frame.kind !== "page_proof" || frame.phase !== "response") {
          return;
        }
        clearTimeout(timer);
        resolve(new PageBridge(channel.port1, frame.payload));
      };
      channel.port1.start();

      window.postMessage(createAttachMessage(nonce), window.origin, [channel.port2]);
    });
  }

  /** Refreshes the geometry and revisions of already registered targets. */
  async queryTargets(targetIds: readonly string[]): Promise<TargetGeometryFrame["payload"]> {
    const frame = await this.exchange({
      schema_version: SCHEMA_VERSION,
      kind: "target_query",
      phase: "request",
      exchange_id: newUuidV7(),
      payload: {
        document_attachment_id: this.documentAttachmentId,
        query: { by: "target_ids", target_ids: [...targetIds] },
        deadline_ms: 400,
      },
    });
    if (frame.kind !== "target_geometry") {
      throw new BridgeError(
        "bridge_exchange_timeout",
        `A target query was answered with ${frame.kind}.`,
      );
    }
    return frame.payload;
  }

  /** Relays one control-plane authorized detail query to the SDK. */
  async detailQuery(
    request: Omit<DetailQueryRequest["payload"], "document_attachment_id">,
  ): Promise<DetailQueryResponse["payload"]> {
    const frame = await this.exchange({
      schema_version: SCHEMA_VERSION,
      kind: "detail_query",
      phase: "request",
      exchange_id: newUuidV7(),
      payload: { document_attachment_id: this.documentAttachmentId, ...request },
    });
    if (frame.kind !== "detail_query" || frame.phase !== "response") {
      throw new BridgeError(
        "bridge_exchange_timeout",
        `A detail query was answered with ${frame.kind}.`,
      );
    }
    return frame.payload;
  }

  /** Tells the shared runtime about an acknowledged pause transition. */
  notifyPause(
    sessionId: string,
    pauseState: "paused" | "running" | "ended",
    sessionControlRevision: number,
  ): void {
    this.port.postMessage({
      schema_version: SCHEMA_VERSION,
      kind: "pause_notice",
      phase: "notice",
      exchange_id: newUuidV7(),
      payload: {
        session_id: sessionId,
        document_attachment_id: this.documentAttachmentId,
        pause_state: pauseState,
        session_control_revision: sessionControlRevision,
      },
    });
  }

  close(): void {
    for (const exchange of this.pending.values()) {
      clearTimeout(exchange.timer);
      exchange.reject(
        new BridgeError("page_bridge_unavailable", "The page bridge closed before answering."),
      );
    }
    this.pending.clear();
    this.port.onmessage = null;
    this.port.close();
  }

  private exchange(frame: BridgeFrame): Promise<BridgeFrame> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(frame.exchange_id);
        reject(
          new BridgeError(
            "bridge_exchange_timeout",
            `The embedded SDK did not answer ${frame.kind} within ${String(EXCHANGE_TIMEOUT_MS)}ms.`,
          ),
        );
      }, EXCHANGE_TIMEOUT_MS);

      this.pending.set(frame.exchange_id, { resolve, reject, timer });
      this.port.postMessage(frame);
    });
  }

  private receive(data: unknown): void {
    const frame = parseBridgeFrame(data);
    if (frame.kind === "page_proof" && frame.phase === "response") {
      this.attachment = frame.payload;
      return;
    }
    const exchange = this.pending.get(frame.exchange_id);
    if (exchange === undefined) {
      return;
    }
    clearTimeout(exchange.timer);
    this.pending.delete(frame.exchange_id);
    exchange.resolve(frame);
  }
}
