/**
 * The server SDK runtime.
 *
 * It owns the lifecycle `dormant | activating | ready | paused | recovering |
 * failed | terminated`, the single provider, the redaction processor, the
 * bounded export queue, the authenticated control connection, and the
 * propagation probe. Without a prepared-preview bootstrap credential it does
 * none of this and stays dormant.
 */

import { randomUUID } from 'node:crypto';

import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import type { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';

import { resolveActivation, type ActiveObservation } from './activation.js';
import {
  ANNOTATION_SCOPE,
  bindAnnotationRuntime,
  clearAnnotationRuntime,
} from './annotations/api.js';
import type { ProbeHop } from './contracts/activation.js';
import type { ServerSdkHealthReport } from './contracts/health.js';
import type { PauseMessage, TerminateMessage } from './contracts/control.js';
import { ControlSocket } from './control/control-socket.js';
import {
  resolveEnvironment,
  type BootstrapEnvironment,
} from './environment.js';
import {
  ServerSdkError,
  createFailure,
  type LifecycleState,
  type ServerSdkFailure,
  type ServerSdkFailureInput,
} from './errors.js';
import { createBatch } from './export/batch.js';
import { OtlpTraceExporter } from './export/exporter.js';
import { ExportQueue } from './export/queue.js';
import { backoffDelayMillis, systemJitter, type JitterSource } from './export/backoff.js';
import { evaluateReadiness } from './health/readiness.js';
import {
  UNACTIVATED_BUFFER,
  UNSTARTED_PROBE,
  buildHealthReport,
} from './health/report.js';
import { StructuredLogger, stderrSink } from './logging.js';
import { PropagationProbe } from './probe/probe.js';
import { compileRedactionPolicy, type RedactionPolicy } from './redaction/policy.js';
import { RedactionSpanProcessor, type RedactedSpanSink } from './redaction/processor.js';
import { installProvider, type ProviderInstallation } from './provider/provider.js';

/** Interval between unsolicited health reports on the control connection. */
const HEALTH_INTERVAL_MILLIS = 5_000;

/** How long before expiry the service-instance credential is rotated. */
const CREDENTIAL_ROTATION_LEAD_MILLIS = 60_000;

/** What `activateServerSdk` returns to a bootstrap path. */
export interface ServerSdkHandle {
  /** Current lifecycle state. */
  readonly state: LifecycleState;
  /** The stream this process created, or `undefined` while dormant. */
  readonly serverStreamId: string | undefined;
  /** Current operational health, or `undefined` while dormant. */
  health(): ServerSdkHealthReport | undefined;
  /** Bounded graceful flush and terminal stream report. */
  shutdown(): Promise<void>;
}

/** Injectable collaborators, so tests drive real behaviour deterministically. */
export interface ServerSdkOptions {
  readonly environment: Readonly<Record<string, string | undefined>>;
  readonly logger: StructuredLogger;
  readonly jitter: JitterSource;
}

const DORMANT_HANDLE: ServerSdkHandle = {
  state: 'dormant',
  serverStreamId: undefined,
  health: () => undefined,
  shutdown: () => Promise.resolve(),
};

class ServerSdkRuntime implements RedactedSpanSink {
  private readonly bootstrap: BootstrapEnvironment;
  private readonly environment: Readonly<Record<string, string | undefined>>;
  private readonly logger: StructuredLogger;
  private readonly jitter: JitterSource;
  private readonly serverStreamId = randomUUID();
  private readonly serviceInstanceId = randomUUID();
  private readonly failures: ServerSdkFailure[] = [];

  private lifecycle: LifecycleState = 'activating';
  private control: ControlSocket | undefined;
  private observation: ActiveObservation | undefined;
  private policy: RedactionPolicy | undefined;
  private queue: ExportQueue | undefined;
  private exporter: OtlpTraceExporter | undefined;
  private probe: PropagationProbe | undefined;
  private installation: ProviderInstallation | undefined;
  private credentialToken = '';
  private credentialExpiresAt = 0;
  private previewEntryToken: string | undefined;
  private inFlight = 0;
  private pumpTimer: NodeJS.Timeout | undefined;
  private healthTimer: NodeJS.Timeout | undefined;
  private stopping = false;
  private excludedSpans = 0;
  private hasBeenReady = false;
  private reconnecting = false;

  public constructor(bootstrap: BootstrapEnvironment, options: ServerSdkOptions) {
    this.bootstrap = bootstrap;
    this.environment = options.environment;
    this.logger = options.logger;
    this.jitter = options.jitter;
  }

  public get state(): LifecycleState {
    return this.lifecycle;
  }

  public get streamId(): string {
    return this.serverStreamId;
  }

  /** Connects, activates, and installs the provider before app modules load. */
  public async start(): Promise<void> {
    const control = new ControlSocket(
      this.bootstrap.controlUrl,
      this.bootstrap.bootstrapCredential,
      this.logger.child('control'),
      {
        onPause: (message): void => {
          this.onPause(message);
        },
        onResume: (commandId): void => {
          this.onResume(commandId);
        },
        onTerminate: (message): void => {
          void this.onTerminate(message);
        },
        onDisconnect: (code, reason): void => {
          this.onDisconnect(code, reason);
        },
        onControlFailure: (failure): void => {
          this.recordFailure(failure);
        },
      },
    );
    this.control = control;
    await control.connect();

    const activation = await control.activate({
      serviceName: this.bootstrap.serviceName,
      serverStreamId: this.serverStreamId,
      serviceInstanceId: this.serviceInstanceId,
    });

    const observation = resolveActivation(
      activation,
      this.bootstrap.manifestTrustAnchor,
      {
        serverStreamId: this.serverStreamId,
        serviceInstanceId: this.serviceInstanceId,
        serviceKey: this.bootstrap.serviceName,
      },
      new Date(),
    );

    this.observation = observation;
    this.policy = compileRedactionPolicy(observation);
    this.queue = new ExportQueue(observation.limits);
    this.probe = new PropagationProbe(observation.activation.probe);
    this.credentialToken = observation.activation.credential.token;
    this.credentialExpiresAt = Date.parse(observation.activation.credential.expires_at);
    this.previewEntryToken = observation.activation.ingest.preview_entry_token;
    this.exporter = new OtlpTraceExporter(
      this.bootstrap.ingestUrl,
      { currentToken: (): string => this.credentialToken },
      this.logger.child('export'),
      this.jitter,
    );

    const installation = installProvider(
      observation,
      this.policy,
      new RedactionSpanProcessor(this.policy, this),
      { currentToken: (): string | undefined => this.previewEntryToken },
      this.environment,
      new Date(),
    );
    this.installation = installation;

    if (installation.missingRequiredAdapters.length > 0) {
      const failure = createFailure(
        {
          code: 'required_adapter_missing',
          retry: 'never',
          readiness_consequence: 'fail_stream',
          detail: { adapters: installation.missingRequiredAdapters.join(',') },
        },
        new Date(),
      );
      this.recordFailure(failure);
      // A required library with no tested adapter blocks the release: the
      // preview must not boot as an unobserved substitute.
      throw new ServerSdkError('A manifest-required adapter is missing.', failure);
    }

    bindAnnotationRuntime({
      tracer: installation.provider.getTracer(ANNOTATION_SCOPE.name, ANNOTATION_SCOPE.version),
      definitionsByName: this.policy.definitionsByName,
      registeredOperations: this.policy.registeredOperations,
      reportFailure: (failure): void => {
        this.recordFailure(createFailure(failure, new Date()));
      },
      isObservationWindowActive: (): boolean =>
        this.lifecycle === 'activating' || this.lifecycle === 'ready',
    });

    this.pumpTimer = setInterval((): void => {
      void this.pump();
    }, observation.limits.export_interval_millis);
    this.pumpTimer.unref();
    this.healthTimer = setInterval((): void => {
      void this.maintain();
    }, HEALTH_INTERVAL_MILLIS);
    this.healthTimer.unref();

    this.logger.info('activation_complete', {
      service_key: observation.service.service_key,
      server_stream_id: this.serverStreamId,
      manifest_version: observation.manifest.manifest_version,
    });
  }

  public accept(span: ReadableSpan, hop: ProbeHop | undefined): void {
    const queue = this.queue;
    if (queue === undefined || this.lifecycle === 'terminated' || this.lifecycle === 'failed') {
      return;
    }
    const admission = queue.admit(span);
    if (admission.kind === 'exhausted') {
      this.recordFailure(
        createFailure(
          {
            code: 'buffer_exhaustion',
            retry: 'never',
            readiness_consequence: 'fail_stream',
            detail: { queued_spans: admission.queuedSpans },
          },
          new Date(),
        ),
      );
      return;
    }
    this.probe?.observe(span, hop);
    if (admission.highWaterReached) {
      this.withdrawReadiness('export_queue_above_high_water');
    }
  }

  public reject(failure: ServerSdkFailureInput): void {
    // A rejected field or an unapproved destination also fails the probe: an
    // incomplete or unauthorized smoke trace is not proof of propagation.
    if (failure.code === 'redaction_or_schema_failure') {
      this.probe?.fail('rejected_field');
    }
    if (failure.code === 'unauthorized_destination') {
      this.probe?.fail('unexpected_destination');
    }
    this.recordFailure(createFailure(failure, new Date()));
  }

  public excluded(reason: string): void {
    this.excludedSpans += 1;
    this.logger.debug('span_excluded', { reason, excluded_total: this.excludedSpans });
  }

  public async flush(): Promise<void> {
    const queue = this.queue;
    const observation = this.observation;
    if (queue === undefined || observation === undefined) {
      return;
    }
    const deadline = Date.now() + observation.limits.flush_timeout_millis;
    while (Date.now() < deadline) {
      await this.pump();
      if (!queue.hasPendingSpans && this.inFlight === 0 && queue.queuedBatches === 0) {
        return;
      }
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 10).unref();
      });
    }
  }

  public async stop(): Promise<void> {
    if (this.stopping) {
      return;
    }
    this.stopping = true;
    if (this.pumpTimer !== undefined) {
      clearInterval(this.pumpTimer);
    }
    if (this.healthTimer !== undefined) {
      clearInterval(this.healthTimer);
    }
    await this.flush();

    const queue = this.queue;
    const clean = queue !== undefined
      && queue.lastAcknowledgedSequence === queue.lastProducedSequence
      && !queue.hasPendingSpans;
    if (!clean) {
      this.recordFailure(
        createFailure(
          {
            code: 'unclean_stream_end',
            retry: 'never',
            readiness_consequence: 'terminate_stream',
            detail: {
              last_produced_sequence: queue?.lastProducedSequence ?? 0,
              last_acknowledged_sequence: queue?.lastAcknowledgedSequence ?? 0,
            },
          },
          new Date(),
        ),
      );
    }
    if (this.control?.isOpen === true && queue !== undefined) {
      this.control.sendStreamReport(
        this.serverStreamId,
        clean,
        queue.lastProducedSequence,
        queue.lastAcknowledgedSequence,
        this.failures,
      );
    }
    clearAnnotationRuntime();
    this.installation?.uninstall();
    this.control?.close();
    this.lifecycle = 'terminated';
  }

  /** Public shutdown entry: flushes through the provider, then reports. */
  public async shutdown(): Promise<void> {
    const provider: NodeTracerProvider | undefined = this.installation?.provider;
    if (provider === undefined) {
      await this.stop();
      return;
    }
    await provider.shutdown();
  }

  /** Builds the current operational health report. */
  public health(): ServerSdkHealthReport {
    return buildHealthReport({
      serviceKey: this.bootstrap.serviceName,
      serviceInstanceId: this.serviceInstanceId,
      serverStreamId: this.serverStreamId,
      lifecycle: this.lifecycle,
      observation: this.observation,
      buffer: this.queue?.status() ?? UNACTIVATED_BUFFER,
      credentialExpiresAt: this.credentialExpiresAt,
      probe: this.probe?.result() ?? UNSTARTED_PROBE,
      adapters: this.installation?.adapterStatuses ?? [],
      failures: this.failures,
    });
  }

  private async pump(): Promise<void> {
    const queue = this.queue;
    const exporter = this.exporter;
    const observation = this.observation;
    if (queue === undefined || exporter === undefined || observation === undefined) {
      return;
    }
    if (this.lifecycle === 'paused' || this.lifecycle === 'failed' || this.lifecycle === 'terminated') {
      return;
    }
    const sends: Promise<void>[] = [];
    while (queue.hasPendingSpans && queue.canSendAnother(this.inFlight)) {
      const spans = queue.takeBatchSpans();
      if (spans.length === 0) {
        break;
      }
      const sequence = queue.nextSequence();
      let batch;
      try {
        batch = createBatch(spans, sequence, observation, new Date());
      } catch (error) {
        this.recordError(error);
        return;
      }
      queue.registerBatch(batch);
      this.probe?.noteBatch(spans, sequence);
      this.inFlight += 1;
      sends.push(
        exporter
          .deliver(batch, observation.limits)
          .then((outcome) => {
            this.inFlight -= 1;
            if (outcome.kind === 'acknowledged') {
              this.clearFailures('ingestion_unavailable');
              queue.acknowledge(sequence);
              this.probe?.acknowledge(queue.lastAcknowledgedSequence);
              this.reconsiderReadiness();
              return;
            }
            this.recordFailure(createFailure(outcome.failure, new Date()));
          })
          .catch((error: unknown) => {
            this.inFlight -= 1;
            this.recordError(error);
          }),
      );
    }
    await Promise.all(sends);
  }

  private async maintain(): Promise<void> {
    const control = this.control;
    if (control === undefined || !control.isOpen) {
      return;
    }
    try {
      if (
        this.credentialExpiresAt !== 0
        && this.credentialExpiresAt - Date.now() < CREDENTIAL_ROTATION_LEAD_MILLIS
      ) {
        const credential = await control.rotate(this.serverStreamId);
        this.credentialToken = credential.token;
        this.credentialExpiresAt = Date.parse(credential.expires_at);
        this.logger.info('credential_rotated', { expires_at: credential.expires_at });
      }
      const observation = this.observation;
      if (observation !== undefined) {
        this.probe?.checkDeadline(Date.now(), observation.limits.acknowledgement_lag_millis);
      }
      this.reconsiderReadiness();
      await control.reportHealth(this.health());
    } catch (error) {
      this.recordError(error);
    }
  }

  private reconsiderReadiness(): void {
    if (this.lifecycle === 'failed' || this.lifecycle === 'terminated' || this.lifecycle === 'paused') {
      return;
    }
    const queue = this.queue;
    const decision = evaluateReadiness({
      activationBound: this.observation !== undefined,
      credentialValid: this.credentialExpiresAt > Date.now(),
      providerInstalled: this.installation !== undefined,
      missingRequiredAdapters: this.installation?.missingRequiredAdapters.length ?? 1,
      controlConnected: this.control?.isOpen === true,
      bufferAboveHighWater: queue?.isAboveHighWater ?? false,
      probePassed: this.probe?.hasPassed ?? false,
      openFailures: this.failures.length,
      paused: false,
    });
    const next: LifecycleState = decision.ready
      ? 'ready'
      : this.failures.length > 0 || this.hasBeenReady
        ? 'recovering'
        : 'activating';
    if (next === 'ready') {
      this.hasBeenReady = true;
    }
    if (next !== this.lifecycle) {
      this.logger.info('lifecycle_transition', {
        from: this.lifecycle,
        to: next,
        blockers: decision.blockers.join(','),
      });
      this.lifecycle = next;
    }
  }

  private withdrawReadiness(reason: string): void {
    if (this.lifecycle === 'ready' || this.lifecycle === 'activating') {
      this.lifecycle = 'recovering';
      this.logger.warn('readiness_withdrawn', { reason });
    }
  }

  private recordError(error: unknown): void {
    if (error instanceof ServerSdkError) {
      this.recordFailure(error.failure);
      return;
    }
    this.recordFailure(
      createFailure(
        {
          code: 'ingestion_unavailable',
          retry: 'after_delay',
          readiness_consequence: 'withdraw_readiness',
          detail: { reason: error instanceof Error ? error.name : 'unknown_error' },
        },
        new Date(),
      ),
    );
  }

  /**
   * Clears open failures of one code.
   *
   * Only a failure whose retry advice is not `never` can clear, and only after
   * the stage it names has succeeded again. A permanent contract failure stays
   * open so readiness cannot silently return.
   */
  private clearFailures(code: ServerSdkFailure['code']): void {
    for (let index = this.failures.length - 1; index >= 0; index -= 1) {
      const failure = this.failures[index];
      if (failure !== undefined && failure.code === code && failure.retry !== 'never') {
        this.failures.splice(index, 1);
      }
    }
  }

  private recordFailure(failure: ServerSdkFailure): void {
    this.failures.push(failure);
    this.logger.error('failure_recorded', {
      code: failure.code,
      readiness_consequence: failure.readiness_consequence,
      retry: failure.retry,
    });
    switch (failure.readiness_consequence) {
      case 'withdraw_readiness':
        this.withdrawReadiness(failure.code);
        return;
      case 'fail_stream':
        this.lifecycle = 'failed';
        return;
      case 'terminate_stream':
        this.lifecycle = 'terminated';
        return;
      default:
        return;
    }
  }

  private onPause(message: PauseMessage): void {
    this.lifecycle = 'paused';
    this.logger.info('paused', { reason: message.reason });
    this.control?.acknowledgeCommand(message.command_id, this.lifecycle);
  }

  private onResume(commandId: string): void {
    if (this.lifecycle === 'paused') {
      this.lifecycle = 'activating';
      this.reconsiderReadiness();
    }
    this.control?.acknowledgeCommand(commandId, this.lifecycle);
  }

  private async onTerminate(message: TerminateMessage): Promise<void> {
    this.logger.info('terminate_requested', { reason: message.reason });
    this.control?.acknowledgeCommand(message.command_id, this.lifecycle);
    await this.shutdown();
  }

  private onDisconnect(code: number, reason: string): void {
    this.logger.warn('control_disconnected', { close_code: code, reason });
    this.recordFailure(
      createFailure(
        {
          code: 'control_connection_lost',
          retry: 'after_delay',
          readiness_consequence: 'withdraw_readiness',
          detail: { close_code: code },
        },
        new Date(),
      ),
    );
    void this.recoverControl();
  }

  private isStopping(): boolean {
    return this.stopping;
  }

  /**
   * Bounded control recovery.
   *
   * The activation itself survives a disconnect; recovery re-opens the socket
   * and takes fresh service-instance credentials before readiness can return.
   * Exhausting the bound fails the stream rather than running unobserved.
   */
  private async recoverControl(): Promise<void> {
    const observation = this.observation;
    const control = this.control;
    if (
      this.reconnecting
      || this.stopping
      || observation === undefined
      || control === undefined
      || this.lifecycle === 'terminated'
      || this.lifecycle === 'failed'
    ) {
      return;
    }
    this.reconnecting = true;
    const retry = observation.limits.retry;
    try {
      for (let attempt = 1; attempt <= retry.max_attempts; attempt += 1) {
        await new Promise<void>((resolve) => {
          setTimeout(resolve, backoffDelayMillis(attempt, retry, undefined, this.jitter)).unref();
        });
        if (this.isStopping()) {
          return;
        }
        try {
          await control.connect();
          const credential = await control.rotate(this.serverStreamId);
          this.credentialToken = credential.token;
          this.credentialExpiresAt = Date.parse(credential.expires_at);
          this.clearFailures('control_connection_lost');
          this.logger.info('control_reconnected', { attempt });
          this.reconsiderReadiness();
          return;
        } catch {
          this.logger.warn('control_reconnect_failed', { attempt });
        }
      }
      this.recordFailure(
        createFailure(
          {
            code: 'control_connection_lost',
            retry: 'never',
            readiness_consequence: 'fail_stream',
            detail: { reason: 'recovery_bound_exhausted', attempts: retry.max_attempts },
          },
          new Date(),
        ),
      );
    } finally {
      this.reconnecting = false;
    }
  }
}

/**
 * Activates the server SDK, or returns the dormant handle.
 *
 * Bootstrap paths must await this before loading application modules so
 * instrumentation is installed first.
 */
export async function activateServerSdk(options: ServerSdkOptions): Promise<ServerSdkHandle> {
  const resolution = resolveEnvironment(options.environment, new Date());
  if (resolution.kind === 'dormant') {
    return DORMANT_HANDLE;
  }
  const runtime = new ServerSdkRuntime(resolution.environment, options);
  await runtime.start();
  return {
    get state(): LifecycleState {
      return runtime.state;
    },
    get serverStreamId(): string {
      return runtime.streamId;
    },
    health: (): ServerSdkHealthReport => runtime.health(),
    shutdown: (): Promise<void> => runtime.shutdown(),
  };
}

/** Default collaborators for a preload or `register()` bootstrap. */
export function defaultOptions(): ServerSdkOptions {
  return {
    environment: process.env,
    logger: new StructuredLogger('utrace.server-sdk', 'info', stderrSink),
    jitter: systemJitter,
  };
}
