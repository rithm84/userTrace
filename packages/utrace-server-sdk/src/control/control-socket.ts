/**
 * The authenticated control connection.
 *
 * One WebSocket to `WS /v1/server-sdk/control` carries activation, credential
 * rotation, health, pause, resume, and termination. It never carries product
 * telemetry: raw spans go to the ingestion edge only.
 */

import { randomUUID } from 'node:crypto';

import { WebSocket } from 'ws';

import type { ServerActivation, ServiceCredential } from '../contracts/activation.js';
import {
  controlServerMessageSchema,
  type ControlClientMessage,
  type ControlServerMessage,
  type PauseMessage,
  type TerminateMessage,
} from '../contracts/control.js';
import type { ServerSdkHealthReport } from '../contracts/health.js';
import { CONTRACT_MAJOR, SDK_CAPABILITIES, SDK_VERSION } from '../constants.js';
import type { LifecycleState, ServerSdkFailure } from '../errors.js';
import { ServerSdkError, createFailure, failureError } from '../errors.js';
import type { StructuredLogger } from '../logging.js';

/** Callbacks the orchestrator installs for unsolicited control frames. */
export interface ControlSocketHandlers {
  onPause(message: PauseMessage): void;
  onResume(commandId: string): void;
  onTerminate(message: TerminateMessage): void;
  onDisconnect(code: number, reason: string): void;
  onControlFailure(failure: ServerSdkFailure): void;
}

/** Process-created stream identity presented during activation. */
export interface ActivationRequestIdentity {
  readonly serviceName: string;
  readonly serverStreamId: string;
  readonly serviceInstanceId: string;
}

interface PendingRequest {
  readonly resolve: (message: ControlServerMessage) => void;
  readonly reject: (error: Error) => void;
}

/** Relative, so it resolves under the control base URL's path prefix. */
const CONTROL_PATH = 'v1/server-sdk/control';

/** The control WebSocket URL under a base URL that ends in `/`. */
export function controlSocketUrl(controlBaseUrl: URL): URL {
  return new URL(CONTROL_PATH, controlBaseUrl);
}

/** Authenticated control WebSocket client. */
export class ControlSocket {
  private readonly url: URL;
  private readonly bootstrapCredential: string;
  private readonly logger: StructuredLogger;
  private readonly handlers: ControlSocketHandlers;
  private readonly pending = new Map<string, PendingRequest>();
  private socket: WebSocket | undefined;
  private closedIntentionally = false;

  public constructor(
    controlBaseUrl: URL,
    bootstrapCredential: string,
    logger: StructuredLogger,
    handlers: ControlSocketHandlers,
  ) {
    this.url = controlSocketUrl(controlBaseUrl);
    this.bootstrapCredential = bootstrapCredential;
    this.logger = logger;
    this.handlers = handlers;
  }

  /** True while the socket is open. */
  public get isOpen(): boolean {
    return this.socket !== undefined && this.socket.readyState === WebSocket.OPEN;
  }

  /** Opens the connection, presenting the bootstrap credential on the handshake. */
  public connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(this.url, {
        headers: {
          authorization: `Bearer ${this.bootstrapCredential}`,
          'user-agent': `utrace-server-sdk/${SDK_VERSION} contract/${String(CONTRACT_MAJOR)}`,
        },
      });
      this.socket = socket;
      this.closedIntentionally = false;

      socket.once('open', () => {
        resolve();
      });
      socket.once('error', (error: Error) => {
        reject(
          failureError('The control connection could not be established.', {
            code: 'control_connection_lost',
            retry: 'after_delay',
            readiness_consequence: 'withdraw_readiness',
            detail: { reason: error.name },
          }, new Date()),
        );
      });
      socket.on('message', (data: Buffer) => {
        this.receive(data);
      });
      socket.on('close', (code: number, reason: Buffer) => {
        this.rejectPending(code);
        if (!this.closedIntentionally) {
          this.handlers.onDisconnect(code, reason.toString('utf8'));
        }
      });
    });
  }

  /** Exchanges the bootstrap credential for the activation record. */
  public async activate(identity: ActivationRequestIdentity): Promise<ServerActivation> {
    const requestId = randomUUID();
    const reply = await this.exchange({
      type: 'activate',
      request_id: requestId,
      bootstrap_credential: this.bootstrapCredential,
      service_name: identity.serviceName,
      server_stream_id: identity.serverStreamId,
      service_instance_id: identity.serviceInstanceId,
      sdk: {
        version: SDK_VERSION,
        contract_major: CONTRACT_MAJOR,
        capabilities: [...SDK_CAPABILITIES],
      },
      runtime: {
        node_version: process.versions.node,
        platform: process.platform,
        arch: process.arch,
        pid: process.pid,
      },
    });
    if (reply.type !== 'activated') {
      throw this.unexpectedReply(reply, 'activated');
    }
    return reply.activation;
  }

  /** Requests fresh short-lived service-instance credentials. */
  public async rotate(serverStreamId: string): Promise<ServiceCredential> {
    const reply = await this.exchange({
      type: 'rotate',
      request_id: randomUUID(),
      server_stream_id: serverStreamId,
    });
    if (reply.type !== 'rotated') {
      throw this.unexpectedReply(reply, 'rotated');
    }
    return reply.credential;
  }

  /** Sends a health report and waits for its acknowledgement. */
  public async reportHealth(report: ServerSdkHealthReport): Promise<void> {
    const reply = await this.exchange({
      type: 'health',
      request_id: randomUUID(),
      report,
    });
    if (reply.type !== 'health_ack') {
      throw this.unexpectedReply(reply, 'health_ack');
    }
  }

  /** Acknowledges a pause, resume, or terminate command. */
  public acknowledgeCommand(commandId: string, state: LifecycleState): void {
    this.send({ type: 'command_ack', command_id: commandId, lifecycle_state: state });
  }

  /** Sends the terminal stream report. */
  public sendStreamReport(
    serverStreamId: string,
    clean: boolean,
    lastProduced: number,
    lastAcknowledged: number,
    failures: readonly ServerSdkFailure[],
  ): void {
    this.send({
      type: 'stream_report',
      request_id: randomUUID(),
      server_stream_id: serverStreamId,
      clean,
      last_produced_sequence: lastProduced,
      last_acknowledged_sequence: lastAcknowledged,
      failures: [...failures],
    });
  }

  /** Closes the socket without reporting a disconnect. */
  public close(): void {
    this.closedIntentionally = true;
    this.socket?.close(1000, 'terminated');
    this.socket = undefined;
  }

  private exchange(message: ControlClientMessage): Promise<ControlServerMessage> {
    if (!('request_id' in message)) {
      return Promise.reject(
        failureError('Only request-bearing control frames can be exchanged.', {
          code: 'invalid_activation',
          retry: 'never',
          readiness_consequence: 'fail_stream',
          detail: { message_type: message.type },
        }, new Date()),
      );
    }
    const requestId = message.request_id;
    return new Promise<ControlServerMessage>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
      try {
        this.send(message);
      } catch (error) {
        this.pending.delete(requestId);
        reject(error instanceof Error ? error : new Error('control_send_failed'));
      }
    });
  }

  private send(message: ControlClientMessage): void {
    const socket = this.socket;
    if (socket === undefined || socket.readyState !== WebSocket.OPEN) {
      throw failureError('The control connection is not open.', {
        code: 'control_connection_lost',
        retry: 'after_delay',
        readiness_consequence: 'withdraw_readiness',
        detail: { message_type: message.type },
      }, new Date());
    }
    socket.send(JSON.stringify(message));
  }

  private receive(data: Buffer): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(data.toString('utf8'));
    } catch {
      this.handlers.onControlFailure(
        createFailure({
          code: 'incompatible_runtime_or_contract',
          retry: 'never',
          readiness_consequence: 'withdraw_readiness',
          detail: { reason: 'control_frame_not_json' },
        }, new Date()),
      );
      return;
    }
    const validated = controlServerMessageSchema.safeParse(parsed);
    if (!validated.success) {
      this.handlers.onControlFailure(
        createFailure({
          code: 'incompatible_runtime_or_contract',
          retry: 'never',
          readiness_consequence: 'withdraw_readiness',
          detail: { reason: 'control_frame_schema_violation' },
        }, new Date()),
      );
      return;
    }
    this.dispatch(validated.data);
  }

  private dispatch(message: ControlServerMessage): void {
    switch (message.type) {
      case 'pause':
        this.handlers.onPause(message);
        return;
      case 'resume':
        this.handlers.onResume(message.command_id);
        return;
      case 'terminate':
        this.handlers.onTerminate(message);
        return;
      case 'error': {
        const waiting = message.request_id === undefined
          ? undefined
          : this.pending.get(message.request_id);
        if (waiting !== undefined && message.request_id !== undefined) {
          this.pending.delete(message.request_id);
          waiting.reject(new ServerSdkError('The control plane rejected the request.', message.failure));
          return;
        }
        this.handlers.onControlFailure(message.failure);
        return;
      }
      default: {
        const waiting = this.pending.get(message.request_id);
        if (waiting === undefined) {
          this.logger.warn('control_reply_unmatched', { message_type: message.type });
          return;
        }
        this.pending.delete(message.request_id);
        waiting.resolve(message);
      }
    }
  }

  private rejectPending(code: number): void {
    for (const [requestId, waiting] of this.pending) {
      this.pending.delete(requestId);
      waiting.reject(
        failureError('The control connection closed before the reply arrived.', {
          code: 'control_connection_lost',
          retry: 'after_delay',
          readiness_consequence: 'withdraw_readiness',
          detail: { close_code: code },
        }, new Date()),
      );
    }
  }

  private unexpectedReply(message: ControlServerMessage, expected: string): ServerSdkError {
    return failureError('The control plane returned an unexpected reply.', {
      code: 'incompatible_runtime_or_contract',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { expected, received: message.type },
    }, new Date());
  }
}
