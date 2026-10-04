/**
 * The single authenticated control connection, owned by the service worker.
 *
 * Authentication is a proof of possession of the installation's device key,
 * carried in the WebSocket subprotocol because a browser WebSocket cannot set
 * headers; the control plane checks freshness and single use. The socket
 * carries binding, readiness, character state, controls, commands, results,
 * cancellation, and recovery only. Product telemetry never travels here.
 *
 * The heartbeat runs only while a session needs the connection, which is also
 * what keeps the service worker alive on the supported Chrome baseline for that
 * session and no longer.
 * A reconnect resumes from the last acknowledged control sequence, and a gap in
 * the sequence is an integrity failure rather than a reconnect hint. Each
 * connection names the session and pending commands current when it opens, so
 * the control plane re-projects, and pushes to, the session the worker serves
 * now rather than the one it served when the socket was created.
 * The control plane's refusal of the installation itself, as unknown or
 * revoked, arrives as its own close code and ends reconnecting; any other close
 * or network failure is only a lost connection.
 */
import { createChallenge, loadDeviceKeyPair, provePossession } from "./deviceKey";
import { controlSocketUrl, CONTROL_SOCKET_AUDIENCE, EXTENSION_VERSION } from "../shared/buildConfig";
import { newUuidV7, nowTimestamp } from "../shared/identifiers";
import { logWarning } from "../shared/log";
import { PROTOCOL_VERSION, SCHEMA_VERSION, ProtocolError } from "../protocol/common";
import {
  parseInboundFrameJson,
  parseOutboundFrame,
  type InboundFrame,
  type OutboundFrame,
} from "../protocol/control";

const HEARTBEAT_INTERVAL_MS = 20_000;
const RECONNECT_BASE_DELAY_MS = 1_000;
const RECONNECT_MAX_DELAY_MS = 30_000;
const SEQUENCE_GAP_CLOSE_CODE = 4002;
const INSTALLATION_NOT_LINKED_CLOSE_CODE = 4003;

export type ConnectionStatus = "idle" | "connecting" | "open" | "reconnecting" | "failed";

export type ControlSocketOptions = Readonly<{
  installationId: string;
  acknowledgedSequence: number;
  pendingCommandIds: () => readonly string[];
  sessionId: () => string | null;
  onFrame: (frame: InboundFrame) => void;
  onStatusChange: (status: ConnectionStatus, reason: string | null) => void;
  onSequenceAdvance: (sequence: number) => void;
  onInstallationRefused: (reason: string) => void;
}>;

export class ControlSocketError extends Error {
  readonly reason: string;

  constructor(reason: string, message: string) {
    super(message);
    this.name = "ControlSocketError";
    this.reason = reason;
  }
}

export class ControlSocket {
  private readonly options: ControlSocketOptions;
  private socket: WebSocket | null = null;
  private opening: Promise<void> | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempt = 0;
  private acknowledged: number;
  private sessionNeeded = false;
  private closedDeliberately = false;
  private statusValue: ConnectionStatus = "idle";

  constructor(options: ControlSocketOptions) {
    this.options = options;
    this.acknowledged = options.acknowledgedSequence;
  }

  get status(): ConnectionStatus {
    return this.statusValue;
  }

  get acknowledgedSequence(): number {
    return this.acknowledged;
  }

  /** Opens the connection unless it is open or already opening. */
  async connect(): Promise<void> {
    if (this.socket !== null) {
      return;
    }
    this.opening ??= this.open().finally(() => {
      this.opening = null;
    });
    await this.opening;
  }

  private async open(): Promise<void> {
    this.closedDeliberately = false;
    this.setStatus("connecting", null);

    const pair = await loadDeviceKeyPair();
    const proof = await provePossession(
      pair,
      createChallenge(CONTROL_SOCKET_AUDIENCE, "control_socket", this.options.installationId),
    );

    const socket = new WebSocket(controlSocketUrl(), [
      `utrace.extension.v${PROTOCOL_VERSION.split(".")[0] ?? "1"}`,
      `utrace.installation.${this.options.installationId}`,
      `utrace.pop.${proof.challenge_b64u}.${proof.signature_b64u}`,
    ]);
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.reconnectAttempt = 0;
      this.setStatus("open", null);
      this.sendRecoveryRequest();
      this.startHeartbeat();
    });
    socket.addEventListener("message", (event: MessageEvent) => {
      this.receive(event.data);
    });
    socket.addEventListener("close", (event: CloseEvent) => {
      if (event.code === INSTALLATION_NOT_LINKED_CLOSE_CODE) {
        this.disconnect(event.reason);
        this.options.onInstallationRefused(event.reason);
        return;
      }
      this.handleClose(`socket_closed_${String(event.code)}`);
    });
    socket.addEventListener("error", () => {
      this.handleClose("socket_error");
    });
  }

  /** Stops the heartbeat and the connection once no session needs them. */
  disconnect(reason: string): void {
    this.sessionNeeded = false;
    this.closedDeliberately = true;
    this.stopHeartbeat();
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    if (socket !== null) {
      socket.close(1000, reason.slice(0, 120));
    }
    this.setStatus("idle", reason);
  }

  setSessionNeeded(sessionNeeded: boolean): void {
    this.sessionNeeded = sessionNeeded;
    if (sessionNeeded) {
      this.startHeartbeat();
    } else {
      this.stopHeartbeat();
    }
  }

  send(frame: OutboundFrame): void {
    const socket = this.socket;
    if (socket === null || socket.readyState !== WebSocket.OPEN) {
      throw new ControlSocketError(
        "control_connection_unavailable",
        `The control connection is ${this.statusValue}, so a ${frame.kind} frame cannot be sent.`,
      );
    }
    socket.send(JSON.stringify(parseOutboundFrame(frame)));
  }

  private receive(data: unknown): void {
    if (typeof data !== "string") {
      this.fail("non_text_frame", "The control plane sent a non-text frame.");
      return;
    }

    let frame: InboundFrame;
    try {
      frame = parseInboundFrameJson(data);
    } catch (error) {
      if (error instanceof ProtocolError) {
        this.fail("invalid_frame", error.message);
        return;
      }
      throw error;
    }

    if (frame.kind === "heartbeat") {
      return;
    }

    if (frame.sequence <= this.acknowledged) {
      return;
    }
    if (frame.sequence !== this.acknowledged + 1) {
      this.fail(
        "sequence_gap",
        `The control plane sent sequence ${String(frame.sequence)} after ${String(this.acknowledged)}.`,
      );
      return;
    }

    this.acknowledged = frame.sequence;
    this.options.onSequenceAdvance(frame.sequence);
    this.options.onFrame(frame);
    this.send({
      schema_version: SCHEMA_VERSION,
      kind: "control_ack",
      message_id: newUuidV7(),
      sent_at: nowTimestamp(),
      payload: { acknowledged_sequence: frame.sequence },
    });
  }

  private sendRecoveryRequest(): void {
    const sessionId = this.options.sessionId();
    this.send({
      schema_version: SCHEMA_VERSION,
      kind: "recovery_request",
      message_id: newUuidV7(),
      sent_at: nowTimestamp(),
      payload: {
        installation_id: this.options.installationId,
        protocol_version: PROTOCOL_VERSION,
        extension_version: EXTENSION_VERSION,
        resume_from_sequence: this.acknowledged,
        ...(sessionId === null ? {} : { session_id: sessionId }),
        pending_command_ids: [...this.options.pendingCommandIds()],
      },
    });
  }

  private startHeartbeat(): void {
    if (this.heartbeat !== null || !this.sessionNeeded) {
      return;
    }
    this.heartbeat = setInterval(() => {
      if (this.socket?.readyState !== WebSocket.OPEN) {
        return;
      }
      this.send({
        schema_version: SCHEMA_VERSION,
        kind: "heartbeat",
        message_id: newUuidV7(),
        sent_at: nowTimestamp(),
        payload: { acknowledged_sequence: this.acknowledged },
      });
    }, HEARTBEAT_INTERVAL_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeat !== null) {
      clearInterval(this.heartbeat);
      this.heartbeat = null;
    }
  }

  private fail(reason: string, message: string): void {
    logWarning("utrace.extension.control_socket_failure", { reason, message });
    const socket = this.socket;
    this.socket = null;
    socket?.close(SEQUENCE_GAP_CLOSE_CODE, reason);
    this.handleClose(reason);
  }

  private handleClose(reason: string): void {
    this.stopHeartbeat();
    this.socket = null;
    if (this.closedDeliberately) {
      return;
    }
    if (!this.sessionNeeded) {
      this.setStatus("idle", reason);
      return;
    }
    this.setStatus("reconnecting", reason);
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) {
      return;
    }
    const delayMs = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** this.reconnectAttempt,
      RECONNECT_MAX_DELAY_MS,
    );
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect().catch((error: unknown) => {
        this.setStatus("failed", error instanceof Error ? error.message : String(error));
        this.scheduleReconnect();
      });
    }, delayMs);
  }

  private setStatus(status: ConnectionStatus, reason: string | null): void {
    this.statusValue = status;
    this.options.onStatusChange(status, reason);
  }
}
