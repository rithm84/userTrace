/**
 * Express and Fastify adapters inside one activation.
 *
 * Both services are required by the same signed manifest, start in their own
 * processes through the preload, and report readiness independently.
 */

import { randomBytes } from 'node:crypto';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { PREVIEW_ENTRY_HEADER, UTRACE_ATTRIBUTES, UTRACE_ERROR_EVENT_NAME } from '../src/constants.js';
import { ENVIRONMENT_VARIABLES } from '../src/environment.js';
import type { DecodedSpan } from './helpers/protobuf.js';
import { TestControlPlane } from './helpers/control-server.js';
import { TestOtlpReceiver } from './helpers/otlp-receiver.js';
import { buildManifest, createSigningIdentity, signManifest } from './helpers/manifest.js';
import { browserTraceparent, waitFor } from './helpers/support.js';
import { callApp, startApp, type AppProcess } from './helpers/app-process.js';

const CONTROL_PORT = 8311;
const INGEST_PORT = 8321;
const EXPRESS_PORT = 8331;
const FASTIFY_PORT = 8332;

const EXPRESS_SERVICE = 'shopalytics_express';
const FASTIFY_SERVICE = 'shopalytics_fastify';
const IDENTITY = createSigningIdentity('utrace-frameworks-key');
const BOOTSTRAP_CREDENTIAL = `bootstrap-${randomBytes(8).toString('hex')}`;
const PREVIEW_ENTRY_TOKEN = `preview-entry-${randomBytes(12).toString('hex')}`;

const ROUTES = [
  {
    route_template: '/api/saved-views/:view_id',
    methods: ['GET' as const],
    operation_name: 'saved_view.read',
    required: true,
  },
  {
    route_template: '/api/saved-views/:view_id/boom',
    methods: ['GET' as const],
    operation_name: 'saved_view.boom',
    required: true,
  },
];

const MANIFEST = buildManifest({
  services: [
    {
      serviceKey: EXPRESS_SERVICE,
      serviceName: 'shopalytics-express',
      requiredAdapters: ['node_http', 'express'],
      registeredRoutes: ROUTES,
      operationalNoise: [{ path_pattern: '/healthz', methods: ['GET'] }],
    },
    {
      serviceKey: FASTIFY_SERVICE,
      serviceName: 'shopalytics-fastify',
      requiredAdapters: ['node_http', 'fastify'],
      registeredRoutes: ROUTES,
      operationalNoise: [{ path_pattern: '/healthz', methods: ['GET'] }],
    },
  ],
  allowedErrorTypes: ['SavedViewNotFoundError'],
  sanitizedErrorMessages: [
    { error_type: 'SavedViewNotFoundError', sanitized_message: 'saved view unavailable' },
  ],
});

let control: TestControlPlane;
let receiver: TestOtlpReceiver;
let expressApp: AppProcess;
let fastifyApp: AppProcess;

function spansFor(serviceName: string): DecodedSpan[] {
  return receiver.batches
    .filter((batch) => batch.resourceAttributes['service.name'] === serviceName)
    .flatMap((batch) => [...batch.spans]);
}

beforeAll(async () => {
  control = new TestControlPlane(BOOTSTRAP_CREDENTIAL);
  receiver = new TestOtlpReceiver();
  const controlUrl = await control.listen(CONTROL_PORT);
  const ingestUrl = await receiver.listen(INGEST_PORT);
  control.useActivation({
    signedManifest: signManifest(MANIFEST, IDENTITY),
    previewEntryToken: PREVIEW_ENTRY_TOKEN,
    requiredHops: ['server'],
    probeRouteTemplate: '/api/saved-views/:view_id',
    probeMethod: 'GET',
  });

  const shared = {
    [ENVIRONMENT_VARIABLES.bootstrapCredential]: BOOTSTRAP_CREDENTIAL,
    [ENVIRONMENT_VARIABLES.controlUrl]: controlUrl.origin,
    [ENVIRONMENT_VARIABLES.ingestUrl]: ingestUrl.origin,
    [ENVIRONMENT_VARIABLES.manifestTrustAnchor]: IDENTITY.trustAnchorEnvValue,
  };

  expressApp = await startApp({
    appFile: 'express-app.cjs',
    withPreload: true,
    environment: {
      ...shared,
      APP_PORT: String(EXPRESS_PORT),
      [ENVIRONMENT_VARIABLES.serviceName]: EXPRESS_SERVICE,
    },
  });
  fastifyApp = await startApp({
    appFile: 'fastify-app.cjs',
    withPreload: true,
    environment: {
      ...shared,
      APP_PORT: String(FASTIFY_PORT),
      [ENVIRONMENT_VARIABLES.serviceName]: FASTIFY_SERVICE,
    },
  });
});

afterAll(async () => {
  await expressApp.stop();
  await fastifyApp.stop();
  await control.close();
  await receiver.close();
});

describe('independent service readiness', () => {
  it('activates each required service on its own stream', () => {
    const activations = control.messagesOfType('activate');
    expect(activations).toHaveLength(2);
    expect(activations.map((message) => message.service_name).sort()).toEqual([
      EXPRESS_SERVICE,
      FASTIFY_SERVICE,
    ]);
    expect(new Set(activations.map((message) => message.server_stream_id)).size).toBe(2);
  });

  it('reports readiness per service after each probe', async () => {
    for (const port of [EXPRESS_PORT, FASTIFY_PORT]) {
      const probe = browserTraceparent();
      const response = await callApp(port, '/api/saved-views/7', {
        traceparent: probe.traceparent,
        [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
      });
      expect(response.statusCode).toBe(200);
    }
    await waitFor(
      () =>
        new Set(
          control
            .messagesOfType('health')
            .filter((message) => message.report.lifecycle_state === 'ready')
            .map((message) => message.report.service_key),
        ).size === 2,
      25_000,
      'both services to report ready independently',
    );
    const ready = control
      .messagesOfType('health')
      .filter((message) => message.report.lifecycle_state === 'ready');
    expect(new Set(ready.map((message) => message.report.server_stream_id)).size).toBe(2);
  });
});

describe('Express adapter', () => {
  it('produces one authoritative server span and a route-scoped handler span', async () => {
    const browser = browserTraceparent();
    await callApp(EXPRESS_PORT, '/api/saved-views/21', {
      traceparent: browser.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    await waitFor(
      () => spansFor('shopalytics-express').some((span) => span.traceId === browser.traceId),
      20_000,
      'the Express trace',
    );
    const trace = spansFor('shopalytics-express').filter(
      (span) => span.traceId === browser.traceId,
    );
    expect(trace.filter((span) => span.kind === 2)).toHaveLength(1);
    expect(trace.map((span) => span.name).sort()).toEqual([
      'GET /api/saved-views/:view_id',
      'handler /api/saved-views/:view_id',
    ]);
    // Middleware churn is not product evidence.
    expect(trace.some((span) => span.name.includes('middleware'))).toBe(false);
    const handler = trace.find((span) => span.name.startsWith('handler '));
    expect(handler?.attributes).toEqual({ 'http.route': '/api/saved-views/:view_id' });
  });
});

describe('Fastify adapter', () => {
  it('produces one authoritative server span with the registered route template', async () => {
    const browser = browserTraceparent();
    await callApp(FASTIFY_PORT, '/api/saved-views/31', {
      traceparent: browser.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    await waitFor(
      () => spansFor('shopalytics-fastify').some((span) => span.traceId === browser.traceId),
      20_000,
      'the Fastify trace',
    );
    const trace = spansFor('shopalytics-fastify').filter(
      (span) => span.traceId === browser.traceId,
    );
    expect(trace.filter((span) => span.kind === 2)).toHaveLength(1);
    const server = trace.find((span) => span.kind === 2);
    expect(server?.name).toBe('GET /api/saved-views/:view_id');
    expect(server?.parentSpanId).toBe(browser.spanId);
  });

  it('turns a backend exception into fingerprinted evidence without its message', async () => {
    const browser = browserTraceparent();
    const response = await callApp(FASTIFY_PORT, '/api/saved-views/7/boom', {
      traceparent: browser.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    expect(response.statusCode).toBe(500);
    await waitFor(
      () =>
        spansFor('shopalytics-fastify').some(
          (span) =>
            span.traceId === browser.traceId
            && span.events.some((event) => event.name === UTRACE_ERROR_EVENT_NAME),
        ),
      20_000,
      'the redacted error evidence',
    );
    const withError = spansFor('shopalytics-fastify').find(
      (span) =>
        span.traceId === browser.traceId
        && span.events.some((event) => event.name === UTRACE_ERROR_EVENT_NAME),
    );
    const event = withError?.events.find((entry) => entry.name === UTRACE_ERROR_EVENT_NAME);
    expect(event?.attributes['error.type']).toBe('SavedViewNotFoundError');
    expect(event?.attributes[UTRACE_ATTRIBUTES.errorMessage]).toBe('saved view unavailable');
    expect(String(event?.attributes[UTRACE_ATTRIBUTES.errorFingerprint])).toMatch(
      /^[0-9a-f]{32}$/u,
    );
    const frames = event?.attributes[UTRACE_ATTRIBUTES.errorFrames];
    expect(Array.isArray(frames)).toBe(true);
    for (const frame of frames as readonly string[]) {
      expect(frame.startsWith('/')).toBe(false);
    }
    const serialized = JSON.stringify(receiver.batches, (_key, value: unknown) =>
      typeof value === 'bigint' ? value.toString() : value,
    );
    expect(serialized).not.toContain('maya@dudulemon.test');
    expect(serialized).not.toContain('exploded');
  });
});
