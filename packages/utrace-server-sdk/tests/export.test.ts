/**
 * Batch identity, acknowledgement, retry, and buffer bounds.
 *
 * Each scenario runs its own preview process so a failed stream cannot mask
 * the next assertion, and every response comes from the real ingestion edge.
 */

import { randomBytes } from 'node:crypto';

import { SpanKind } from '@opentelemetry/api';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { PREVIEW_ENTRY_HEADER } from '../src/constants.js';
import { ENVIRONMENT_VARIABLES } from '../src/environment.js';
import { ExportQueue } from '../src/export/queue.js';
import type { ExportBatch } from '../src/export/batch.js';
import { TestControlPlane, TEST_LIMITS } from './helpers/control-server.js';
import { TestOtlpReceiver, type ReceivedBatch, type ReceiverBehaviour } from './helpers/otlp-receiver.js';
import { buildManifest, createSigningIdentity, signManifest } from './helpers/manifest.js';
import { browserTraceparent, waitFor } from './helpers/support.js';
import { callApp, startApp, type AppProcess } from './helpers/app-process.js';
import { readableSpan } from './helpers/spans.js';

const CONTROL_PORT = 8313;
const INGEST_PORT = 8323;

const SERVICE_KEY = 'shopalytics_api_export';
const IDENTITY = createSigningIdentity('utrace-export-key');
const BOOTSTRAP_CREDENTIAL = `bootstrap-${randomBytes(8).toString('hex')}`;
const PREVIEW_ENTRY_TOKEN = `preview-entry-${randomBytes(12).toString('hex')}`;

/** Slower backoff than the shared defaults so `Retry-After` is distinguishable. */
const SLOW_RETRY_LIMITS = {
  ...TEST_LIMITS,
  retry: {
    initial_backoff_millis: 200,
    max_backoff_millis: 4_000,
    multiplier: 2,
    jitter_ratio: 0,
    max_attempts: 4,
  },
};

const MANIFEST = buildManifest({
  services: [
    {
      serviceKey: SERVICE_KEY,
      requiredAdapters: ['node_http'],
      registeredRoutes: [
        {
          route_template: '/api/saved-views/:view_id',
          methods: ['GET'],
          operation_name: 'saved_view.read',
          required: true,
        },
        {
          route_template: '/api/internal/echo',
          methods: ['GET'],
          operation_name: 'saved_view.echo',
          required: true,
        },
      ],
      operationalNoise: [{ path_pattern: '/healthz', methods: ['GET'] }],
    },
  ],
  propagationTargets: [
    { scheme: 'http', host: '127.0.0.1', port: 8334 },
    { scheme: 'http', host: '127.0.0.1', port: 8335 },
    { scheme: 'http', host: '127.0.0.1', port: 8336 },
  ],
  allowedHttpDestinations: [
    { scheme: 'http', host: '127.0.0.1', port: 8334 },
    { scheme: 'http', host: '127.0.0.1', port: 8335 },
    { scheme: 'http', host: '127.0.0.1', port: 8336 },
  ],
});

let control: TestControlPlane;
let receiver: TestOtlpReceiver;
const running: AppProcess[] = [];

interface Scenario {
  readonly streamId: string;
  readonly app: AppProcess;
}

async function runScenario(
  port: number,
  behaviours: readonly ReceiverBehaviour[],
  fallback: ReceiverBehaviour,
): Promise<Scenario> {
  receiver.program(behaviours, fallback);
  const app = await startApp({
    appFile: 'node-http-app.cjs',
    withPreload: true,
    environment: {
      APP_PORT: String(port),
      [ENVIRONMENT_VARIABLES.bootstrapCredential]: BOOTSTRAP_CREDENTIAL,
      [ENVIRONMENT_VARIABLES.controlUrl]: `ws://127.0.0.1:${String(CONTROL_PORT)}`,
      [ENVIRONMENT_VARIABLES.ingestUrl]: `http://127.0.0.1:${String(INGEST_PORT)}`,
      [ENVIRONMENT_VARIABLES.serviceName]: SERVICE_KEY,
      [ENVIRONMENT_VARIABLES.manifestTrustAnchor]: IDENTITY.trustAnchorEnvValue,
    },
  });
  running.push(app);
  const activation = control.messagesOfType('activate').at(-1);
  if (activation === undefined) {
    throw new Error('the scenario process never activated');
  }
  const probe = browserTraceparent();
  await callApp(port, '/api/saved-views/5', {
    traceparent: probe.traceparent,
    [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
  });
  return { streamId: activation.server_stream_id, app };
}

function batchesForStream(streamId: string): ReceivedBatch[] {
  return receiver.batches.filter(
    (batch) => batch.headers['x-utrace-server-stream-id'] === streamId,
  );
}

function healthForStream(streamId: string) {
  return control
    .messagesOfType('health')
    .filter((message) => message.report.server_stream_id === streamId);
}

beforeAll(async () => {
  control = new TestControlPlane(BOOTSTRAP_CREDENTIAL);
  receiver = new TestOtlpReceiver();
  await control.listen(CONTROL_PORT);
  await receiver.listen(INGEST_PORT);
  control.useActivation({
    signedManifest: signManifest(MANIFEST, IDENTITY),
    previewEntryToken: PREVIEW_ENTRY_TOKEN,
    requiredHops: ['server'],
    probeRouteTemplate: '/api/saved-views/:view_id',
    probeMethod: 'GET',
    limits: SLOW_RETRY_LIMITS,
  });
});

afterAll(async () => {
  for (const app of running) {
    await app.stop();
  }
  await control.close();
  await receiver.close();
});

describe('retrying an immutable batch', () => {
  it('reuses the batch identity and honours Retry-After', async () => {
    const { streamId } = await runScenario(
      8334,
      [{ kind: 'retryable', status: 503, retryAfterSeconds: 1 }],
      { kind: 'accept' },
    );
    const attemptsOfSequence = (sequence: string): ReceivedBatch[] =>
      batchesForStream(streamId).filter(
        (batch) => batch.headers['x-utrace-batch-sequence'] === sequence,
      );
    await waitFor(() => attemptsOfSequence('1').length >= 2, 25_000, 'the retried batch');

    const attempts = attemptsOfSequence('1');
    const first = attempts[0];
    const second = attempts[1];
    expect(first?.headers['x-utrace-batch-id']).toBe(second?.headers['x-utrace-batch-id']);
    expect(first?.headers['x-utrace-content-sha256']).toBe(
      second?.headers['x-utrace-content-sha256'],
    );
    expect(first?.spans.map((span) => span.spanId)).toEqual(
      second?.spans.map((span) => span.spanId),
    );
    // Retry-After of one second beats the 200 ms computed backoff.
    expect((second?.receivedAt ?? 0) - (first?.receivedAt ?? 0)).toBeGreaterThanOrEqual(900);
  });
});

describe('acknowledgement integrity', () => {
  it('fails the stream when ingestion answers with a partial success', async () => {
    const { streamId } = await runScenario(8335, [{ kind: 'partial', rejectedSpans: 1 }], {
      kind: 'accept',
    });
    await waitFor(
      () =>
        healthForStream(streamId).some((message) =>
          message.report.failures.some((failure) => failure.code === 'partial_acceptance'),
        ),
      25_000,
      'the partial acceptance failure',
    );
    const failing = healthForStream(streamId).find((message) =>
      message.report.failures.some((failure) => failure.code === 'partial_acceptance'),
    );
    expect(failing?.report.lifecycle_state).toBe('failed');
    const failure = failing?.report.failures.find((entry) => entry.code === 'partial_acceptance');
    expect(failure?.retry).toBe('never');
    expect(failure?.detail['rejected_spans']).toBe(1);
    expect(failure?.batch_sequence).toBe(1);
  });

  it('never retries a permanent validation rejection', async () => {
    const { streamId, app } = await runScenario(8336, [], { kind: 'permanent', status: 422 });
    await waitFor(
      () =>
        healthForStream(streamId).some((message) => message.report.lifecycle_state === 'failed'),
      25_000,
      'the permanent rejection to fail the stream',
    );
    const attempts = batchesForStream(streamId);
    const firstSequence = attempts[0]?.headers['x-utrace-batch-sequence'];
    expect(
      attempts.filter((batch) => batch.headers['x-utrace-batch-sequence'] === firstSequence),
    ).toHaveLength(1);

    await app.stop();
    await waitFor(
      () =>
        control
          .messagesOfType('stream_report')
          .some((message) => message.server_stream_id === streamId),
      20_000,
      'the terminal stream report of the failed stream',
    );
    const report = control
      .messagesOfType('stream_report')
      .find((message) => message.server_stream_id === streamId);
    expect(report?.clean).toBe(false);
    expect(report?.failures.some((failure) => failure.code === 'unclean_stream_end')).toBe(true);
  });
});

describe('the bounded export queue', () => {
  const limits = { ...TEST_LIMITS, max_queue_spans: 4, readiness_high_water_ratio: 0.5 };

  function span(): ReturnType<typeof readableSpan> {
    return readableSpan({
      name: 'GET /api/saved-views/:view_id',
      kind: SpanKind.SERVER,
      scopeName: '@opentelemetry/instrumentation-http',
      attributes: {},
    });
  }

  it('withdraws readiness at the high-water mark before the hard bound', () => {
    const queue = new ExportQueue(limits);
    expect(queue.admit(span())).toEqual({ kind: 'admitted', highWaterReached: false });
    expect(queue.admit(span())).toEqual({ kind: 'admitted', highWaterReached: true });
    expect(queue.isAboveHighWater).toBe(true);
  });

  it('reports exhaustion rather than evicting a span', () => {
    const queue = new ExportQueue(limits);
    for (let index = 0; index < 4; index += 1) {
      expect(queue.admit(span()).kind).toBe('admitted');
    }
    expect(queue.admit(span())).toEqual({ kind: 'exhausted', queuedSpans: 4 });
    expect(queue.queuedSpans).toBe(4);
  });

  it('advances the acknowledged watermark only across a contiguous run', () => {
    const queue = new ExportQueue(TEST_LIMITS);
    const batches: ExportBatch[] = [1, 2, 3].map((sequence) => ({
      envelope: {
        activation_id: 'a',
        server_stream_id: 's',
        service_instance_id: 'i',
        batch_id: `batch-${String(sequence)}`,
        batch_sequence: queue.nextSequence(),
        span_count: 1,
        sdk_version: '0.1.0',
        contract_major: 1,
        manifest_version: 1,
        policy_version: '1.0.0',
        utrace_schema_version: '1.0.0',
        semantic_convention_version: '1.37.0',
        content_sha256: 'f'.repeat(64),
      },
      body: Buffer.alloc(1),
      spanCount: 1,
    }));
    for (const batch of batches) {
      queue.registerBatch(batch);
    }
    queue.acknowledge(2);
    expect(queue.lastAcknowledgedSequence).toBe(0);
    queue.acknowledge(1);
    expect(queue.lastAcknowledgedSequence).toBe(2);
    queue.acknowledge(3);
    expect(queue.lastAcknowledgedSequence).toBe(3);
  });
});
