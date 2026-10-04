/**
 * End-to-end behaviour of a prepared preview running a plain Node HTTP service.
 *
 * The application starts through the supported bootstrap path
 * (`node --import @utrace/server-sdk/preload`) in its own process. The test
 * process runs the control plane and the ingestion edge, so every assertion is
 * made on what uTrace actually received.
 */

import { randomBytes } from 'node:crypto';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { PREVIEW_ENTRY_HEADER } from '../src/constants.js';
import { ENVIRONMENT_VARIABLES } from '../src/environment.js';
import type { DecodedSpan } from './helpers/protobuf.js';
import { TestControlPlane } from './helpers/control-server.js';
import { TestOtlpReceiver } from './helpers/otlp-receiver.js';
import { buildManifest, createSigningIdentity, signManifest } from './helpers/manifest.js';
import { browserTraceparent, waitFor } from './helpers/support.js';
import { callApp, startApp, type AppProcess } from './helpers/app-process.js';

const CONTROL_PORT = 8310;
const INGEST_PORT = 8320;
const APP_PORT = 8330;
const DORMANT_APP_PORT = 8339;

const SERVICE_KEY = 'shopalytics_api';
const IDENTITY = createSigningIdentity('utrace-preview-key');
const BOOTSTRAP_CREDENTIAL = `bootstrap-${randomBytes(8).toString('hex')}`;
const PREVIEW_ENTRY_TOKEN = `preview-entry-${randomBytes(12).toString('hex')}`;

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
  propagationTargets: [{ scheme: 'http', host: '127.0.0.1', port: APP_PORT }],
  allowedHttpDestinations: [{ scheme: 'http', host: '127.0.0.1', port: APP_PORT }],
});

let control: TestControlPlane;
let receiver: TestOtlpReceiver;
let app: AppProcess;

function allSpans(): DecodedSpan[] {
  return receiver.batches.flatMap((batch) => [...batch.spans]);
}

function spansNamed(name: string): DecodedSpan[] {
  return allSpans().filter((span) => span.name === name);
}

beforeAll(async () => {
  control = new TestControlPlane(BOOTSTRAP_CREDENTIAL);
  receiver = new TestOtlpReceiver();
  const controlUrl = await control.listen(CONTROL_PORT);
  const ingestUrl = await receiver.listen(INGEST_PORT);
  control.useActivation({
    signedManifest: signManifest(MANIFEST, IDENTITY),
    previewEntryToken: PREVIEW_ENTRY_TOKEN,
    requiredHops: ['server', 'downstream_http'],
    probeRouteTemplate: '/api/saved-views/:view_id',
    probeMethod: 'GET',
  });

  app = await startApp({
    appFile: 'node-http-app.cjs',
    withPreload: true,
    environment: {
      APP_PORT: String(APP_PORT),
      [ENVIRONMENT_VARIABLES.bootstrapCredential]: BOOTSTRAP_CREDENTIAL,
      [ENVIRONMENT_VARIABLES.controlUrl]: controlUrl.origin,
      [ENVIRONMENT_VARIABLES.ingestUrl]: ingestUrl.origin,
      [ENVIRONMENT_VARIABLES.serviceName]: SERVICE_KEY,
      [ENVIRONMENT_VARIABLES.manifestTrustAnchor]: IDENTITY.trustAnchorEnvValue,
    },
  });
});

afterAll(async () => {
  await app.stop();
  await control.close();
  await receiver.close();
});

describe('a production startup profile', () => {
  it('stays dormant with no control connection, provider, or export', async () => {
    const production = await startApp({
      appFile: 'node-http-app.cjs',
      withPreload: true,
      environment: { APP_PORT: String(DORMANT_APP_PORT) },
    });
    try {
      const response = await callApp(DORMANT_APP_PORT, '/api/saved-views/7', {});
      expect(response.statusCode).toBe(200);
      const controlConnections = control
        .messagesOfType('activate')
        .filter((message) => message.service_name === SERVICE_KEY).length;
      // Only the prepared preview activated; the production profile added none.
      expect(controlConnections).toBe(1);
      expect(production.stderr.join('')).not.toContain('failure_recorded');
    } finally {
      await production.stop();
    }
  });
});

describe('activation and readiness', () => {
  it('activates exactly once and binds the stream to the service', () => {
    const activations = control.messagesOfType('activate');
    expect(activations).toHaveLength(1);
    expect(activations[0]?.service_name).toBe(SERVICE_KEY);
    expect(activations[0]?.sdk.contract_major).toBe(1);
  });

  it('reaches ready once the propagation probe is acknowledged', async () => {
    const probe = browserTraceparent();
    const response = await callApp(APP_PORT, '/api/saved-views/7?token=supersecret', {
      traceparent: probe.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
      'user-agent': 'Mozilla/5.0 (probe)',
      cookie: 'session=abc123',
    });
    expect(response.statusCode).toBe(200);

    await waitFor(
      () =>
        control
          .messagesOfType('health')
          .some(
            (message) =>
              message.report.lifecycle_state === 'ready'
              && message.report.probe_result.state === 'passed',
          ),
      20_000,
      'the SDK to report a ready lifecycle with a passed probe',
    );

    const ready = control
      .messagesOfType('health')
      .find((message) => message.report.lifecycle_state === 'ready');
    expect(ready?.report.failures).toEqual([]);
    expect(ready?.report.adapters).toHaveLength(1);
    const adapter = ready?.report.adapters[0];
    expect(adapter?.adapter).toBe('node_http');
    expect(adapter?.installed).toBe(true);
    expect(typeof adapter?.module_version).toBe('string');
    expect(ready?.report.buffer.last_acknowledged_sequence).toBeGreaterThan(0);
  });
});

describe('trace creation and propagation', () => {
  it('accepts the browser CLIENT span as the remote parent inside the preview', async () => {
    const browser = browserTraceparent();
    await callApp(APP_PORT, '/api/saved-views/11', {
      traceparent: browser.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    await waitFor(
      () => allSpans().some((span) => span.traceId === browser.traceId && span.kind === 2),
      15_000,
      'the server span of the browser-parented trace',
    );
    const server = allSpans().find(
      (span) => span.traceId === browser.traceId && span.name === 'GET /api/saved-views/:view_id',
    );
    expect(server).toBeDefined();
    expect(server?.parentSpanId).toBe(browser.spanId);

    const downstream = allSpans().filter((span) => span.traceId === browser.traceId);
    expect(downstream.map((span) => span.name).sort()).toEqual([
      'GET',
      'GET /api/internal/echo',
      'GET /api/saved-views/:view_id',
    ]);
    // Exactly one authoritative SERVER span per inbound request.
    expect(downstream.filter((span) => span.kind === 2)).toHaveLength(2);
  });

  it('refuses a traceparent that did not enter through the authenticated preview', async () => {
    const outsider = browserTraceparent();
    await callApp(APP_PORT, '/api/saved-views/12', { traceparent: outsider.traceparent });
    await waitFor(
      () => spansNamed('GET /api/saved-views/:view_id').length >= 3,
      15_000,
      'the unauthenticated request to be exported',
    );
    expect(allSpans().some((span) => span.traceId === outsider.traceId)).toBe(false);
  });

  it('never injects uTrace context toward an unapproved destination', () => {
    // The control and ingest edges are not manifest propagation targets, so the
    // SDK's own traffic produced no client spans at all.
    expect(
      allSpans().some(
        (span) => span.attributes['server.port'] === CONTROL_PORT
          || span.attributes['server.port'] === INGEST_PORT,
      ),
    ).toBe(false);
  });
});

describe('redaction on the wire', () => {
  it('exports route templates and never the raw request', () => {
    const server = spansNamed('GET /api/saved-views/:view_id')[0];
    expect(server).toBeDefined();
    expect(Object.keys(server?.attributes ?? {}).sort()).toEqual([
      'http.request.method',
      'http.response.status_code',
      'http.route',
      'network.protocol.version',
      'url.scheme',
    ]);
    const serialized = JSON.stringify(receiver.batches, (_key, value: unknown) =>
      typeof value === 'bigint' ? value.toString() : value,
    );
    expect(serialized).not.toContain('supersecret');
    expect(serialized).not.toContain('Mozilla/5.0');
    expect(serialized).not.toContain('session=abc123');
    expect(serialized).not.toContain('127.0.0.1:8330/api/saved-views');
  });

  it('carries the standard resource identity and no dynamic identity', () => {
    const resource = receiver.batches[0]?.resourceAttributes ?? {};
    expect(resource['service.name']).toBe('shopalytics-api');
    expect(resource['service.namespace']).toBe('shopalytics');
    expect(resource['service.version']).toBe('1.4.2');
    expect(resource['deployment.environment.name']).toBe('preview');
    expect(String(resource['service.instance.id'])).toMatch(/^[0-9a-f-]{36}$/u);
    expect(Object.keys(resource)).not.toContain('session.id');
  });

  it('excludes registered operational noise', async () => {
    const before = allSpans().length;
    await callApp(APP_PORT, '/healthz', { [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN });
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 300);
    });
    expect(allSpans().filter((span) => span.name.includes('healthz'))).toHaveLength(0);
    expect(allSpans().length).toBeGreaterThanOrEqual(before);
  });
});

describe('the authenticated export contract', () => {
  it('sends gzipped binary protobuf with the batch envelope outside the payload', () => {
    const batch = receiver.batches[0];
    expect(batch).toBeDefined();
    expect(batch?.contentType).toBe('application/x-protobuf');
    expect(batch?.contentEncoding).toBe('gzip');
    expect(batch?.authorization).toMatch(/^Bearer service-/u);
    expect(batch?.headers['x-utrace-batch-id']).toMatch(/^[0-9a-f-]{36}$/u);
    expect(batch?.headers['x-utrace-content-sha256']).toMatch(/^[0-9a-f]{64}$/u);
    expect(batch?.headers['x-utrace-contract-major']).toBe('1');
    expect(batch?.headers['x-utrace-policy-version']).toBe('1.0.0');
  });

  it('numbers batches gaplessly within the stream', () => {
    const sequences = receiver.batches.map((batch) =>
      Number(batch.headers['x-utrace-batch-sequence']),
    );
    expect(sequences).toEqual(sequences.map((_, index) => index + 1));
    const streamIds = new Set(receiver.batches.map((batch) => batch.headers['x-utrace-server-stream-id']));
    expect(streamIds.size).toBe(1);
  });
});

describe('unregistered routes', () => {
  it('reports a manifest gap and withdraws readiness instead of exporting a raw URL', async () => {
    await callApp(APP_PORT, '/admin/secret', { [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN });
    await waitFor(
      () =>
        control
          .messagesOfType('health')
          .some((message) =>
            message.report.failures.some((failure) => failure.code === 'route_not_registered'),
          ),
      20_000,
      'the unregistered route failure to reach the control plane',
    );
    const withFailure = control
      .messagesOfType('health')
      .find((message) =>
        message.report.failures.some((failure) => failure.code === 'route_not_registered'),
      );
    expect(withFailure?.report.lifecycle_state).toBe('recovering');
    expect(allSpans().some((span) => span.name.includes('admin'))).toBe(false);
  });
});

describe('graceful shutdown', () => {
  it('flushes and sends a terminal stream report', async () => {
    await app.stop();
    await waitFor(
      () => control.messagesOfType('stream_report').length === 1,
      15_000,
      'the terminal stream report',
    );
    const report = control.messagesOfType('stream_report')[0];
    expect(report?.clean).toBe(true);
    expect(report?.last_acknowledged_sequence).toBe(report?.last_produced_sequence);
  });
});
