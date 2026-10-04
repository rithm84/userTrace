/**
 * A test control plane for `WS /v1/server-sdk/control`.
 *
 * The real control plane is built by another workstream. This server
 * implements the documented handshake the server SDK depends on: bootstrap
 * credential authentication, activation, credential rotation, health
 * acknowledgement, and the pause, resume, and terminate commands.
 */

import { createServer, type Server } from 'node:http';
import { randomUUID } from 'node:crypto';

import { WebSocketServer, type WebSocket } from 'ws';

import {
  controlClientMessageSchema,
  type ActivateMessage,
  type ControlClientMessage,
  type ControlServerMessage,
} from '../../src/contracts/control.js';
import type { ServerActivation, ExportLimits } from '../../src/contracts/activation.js';
import type { SignedServerInstrumentationManifest } from '../../src/contracts/manifest.js';

/** Limits a test activation hands out, beneath the packaged ceilings. */
export const TEST_LIMITS: ExportLimits = {
  max_batch_spans: 64,
  max_batch_bytes: 1_048_576,
  max_queue_batches: 16,
  max_queue_spans: 512,
  readiness_high_water_ratio: 0.8,
  max_in_flight_batches: 2,
  export_interval_millis: 25,
  acknowledgement_lag_millis: 5_000,
  flush_timeout_millis: 5_000,
  retry: {
    initial_backoff_millis: 10,
    max_backoff_millis: 200,
    multiplier: 2,
    jitter_ratio: 0.2,
    max_attempts: 4,
  },
};

/** What the test wants the activation record to contain. */
export interface ActivationTemplate {
  readonly signedManifest: SignedServerInstrumentationManifest;
  readonly previewEntryToken: string;
  readonly requiredHops: ServerActivation['probe']['required_hops'];
  readonly probeRouteTemplate: string;
  readonly probeMethod: ServerActivation['probe']['method'];
  readonly limits?: ExportLimits;
  readonly credentialLifetimeMillis?: number;
  readonly resourceOverride?: Partial<ServerActivation['resource']>;
  readonly streamIdOverride?: string;
}

/** In-process control plane for tests. */
export class TestControlPlane {
  private readonly httpServer: Server;
  private readonly wss: WebSocketServer;
  private readonly clientMessages: ControlClientMessage[] = [];
  private template: ActivationTemplate | undefined;
  private expectedCredential: string;
  private readonly sockets = new Set<WebSocket>();
  private rejectActivation = false;

  public constructor(expectedCredential: string) {
    this.expectedCredential = expectedCredential;
    this.httpServer = createServer();
    this.wss = new WebSocketServer({ server: this.httpServer, path: '/v1/server-sdk/control' });
    this.wss.on('connection', (socket, request) => {
      const authorization = request.headers['authorization'];
      if (authorization !== `Bearer ${this.expectedCredential}`) {
        socket.close(4401, 'credential_rejected');
        return;
      }
      this.sockets.add(socket);
      socket.on('close', () => {
        this.sockets.delete(socket);
      });
      socket.on('message', (data: Buffer) => {
        this.onMessage(socket, data);
      });
    });
  }

  /** Every frame the SDK sent, in order. */
  public get messages(): readonly ControlClientMessage[] {
    return this.clientMessages;
  }

  /** Frames of one type the SDK sent. */
  public messagesOfType<T extends ControlClientMessage['type']>(
    type: T,
  ): Extract<ControlClientMessage, { type: T }>[] {
    return this.clientMessages.filter(
      (message): message is Extract<ControlClientMessage, { type: T }> => message.type === type,
    );
  }

  public useActivation(template: ActivationTemplate): void {
    this.template = template;
  }

  public failNextActivation(): void {
    this.rejectActivation = true;
  }

  public listen(port: number): Promise<URL> {
    return new Promise((resolve) => {
      this.httpServer.listen(port, '127.0.0.1', () => {
        resolve(new URL(`ws://127.0.0.1:${String(port)}`));
      });
    });
  }

  public async close(): Promise<void> {
    for (const client of this.wss.clients) {
      client.terminate();
    }
    await new Promise<void>((resolve) => {
      this.wss.close(() => {
        resolve();
      });
    });
    await new Promise<void>((resolve) => {
      this.httpServer.closeAllConnections();
      this.httpServer.close(() => {
        resolve();
      });
    });
  }

  /** Sends a pause command and returns its command id. */
  public pause(reason: 'session_paused' | 'policy_hold' | 'ingestion_backpressure' | 'operator_stop'): string {
    const commandId = randomUUID();
    this.push({ type: 'pause', command_id: commandId, reason });
    return commandId;
  }

  /** Sends a resume command and returns its command id. */
  public resume(): string {
    const commandId = randomUUID();
    this.push({ type: 'resume', command_id: commandId });
    return commandId;
  }

  /** Sends a terminate command and returns its command id. */
  public terminate(
    reason: 'session_ended' | 'activation_superseded' | 'credential_revoked' | 'client_case_stop',
  ): string {
    const commandId = randomUUID();
    this.push({ type: 'terminate', command_id: commandId, reason });
    return commandId;
  }

  private push(message: ControlServerMessage): void {
    for (const socket of this.sockets) {
      socket.send(JSON.stringify(message));
    }
  }

  private onMessage(socket: WebSocket, data: Buffer): void {
    const parsed: unknown = JSON.parse(data.toString('utf8'));
    const validated = controlClientMessageSchema.safeParse(parsed);
    if (!validated.success) {
      socket.send(
        JSON.stringify({
          type: 'error',
          failure: {
            code: 'incompatible_runtime_or_contract',
            detected_at: new Date().toISOString(),
            retry: 'never',
            readiness_consequence: 'fail_stream',
            detail: { reason: 'client_frame_schema_violation' },
          },
        }),
      );
      return;
    }
    const message = validated.data;
    this.clientMessages.push(message);

    switch (message.type) {
      case 'activate':
        this.onActivate(socket, message);
        return;
      case 'rotate':
        socket.send(
          JSON.stringify({
            type: 'rotated',
            request_id: message.request_id,
            credential: {
              token: `rotated-${randomUUID()}`,
              expires_at: new Date(Date.now() + 600_000).toISOString(),
            },
          }),
        );
        return;
      case 'health':
        socket.send(JSON.stringify({ type: 'health_ack', request_id: message.request_id }));
        return;
      default:
        return;
    }
  }

  private onActivate(socket: WebSocket, message: ActivateMessage): void {
    const requestId = message.request_id;
    const template = this.template;
    if (template === undefined || this.rejectActivation) {
      this.rejectActivation = false;
      socket.send(
        JSON.stringify({
          type: 'error',
          request_id: requestId,
          failure: {
            code: 'invalid_activation',
            detected_at: new Date().toISOString(),
            retry: 'never',
            readiness_consequence: 'fail_stream',
            detail: { reason: 'no_activation_available' },
          },
        }),
      );
      return;
    }

    const service = template.signedManifest.manifest.services.find(
      (candidate) => candidate.service_key === message.service_name,
    );
    if (service === undefined) {
      throw new Error(`the test manifest declares no service ${message.service_name}`);
    }
    const activation: ServerActivation = {
      activation_id: randomUUID(),
      client_id: template.signedManifest.manifest.client_id,
      client_user_id: randomUUID(),
      customer_account_id: randomUUID(),
      session_id: randomUUID(),
      preview_environment_id: randomUUID(),
      preview_runtime_allocation_id: randomUUID(),
      preview_version_id: randomUUID(),
      server_stream_id: template.streamIdOverride ?? message.server_stream_id,
      service_instance_id: message.service_instance_id,
      service_key: service.service_key,
      resource: {
        service_name: service.service_name,
        service_namespace: service.service_namespace,
        service_version: service.service_version,
        deployment_environment_name: 'preview',
        ...template.resourceOverride,
      },
      credential: {
        token: `service-${randomUUID()}`,
        expires_at: new Date(
          Date.now() + (template.credentialLifetimeMillis ?? 900_000),
        ).toISOString(),
      },
      ingest: {
        traces_path: '/v1/ingest/otlp/v1/traces',
        preview_entry_token: template.previewEntryToken,
      },
      limits: template.limits ?? TEST_LIMITS,
      probe: {
        probe_id: randomUUID(),
        route_template: template.probeRouteTemplate,
        method: template.probeMethod,
        required_hops: [...template.requiredHops],
      },
      signed_manifest: template.signedManifest,
    };
    socket.send(JSON.stringify({ type: 'activated', request_id: requestId, activation }));
  }
}
