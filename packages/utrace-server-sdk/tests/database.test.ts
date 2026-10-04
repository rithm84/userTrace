/**
 * The `pg` adapter against a real Postgres database.
 *
 * The product path runs one parameterized query; the test proves the exported
 * span carries the system, operation, and approved class and never the SQL
 * text, bind values, connection string, or credentials.
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
import { TEST_SCHEMA, TEST_TABLE, prepareSchema, scopedDatabaseUrl } from './helpers/database.js';

const CONTROL_PORT = 8312;
const INGEST_PORT = 8322;
const APP_PORT = 8333;

const SERVICE_KEY = 'shopalytics_api_pg';
const IDENTITY = createSigningIdentity('utrace-database-key');
const BOOTSTRAP_CREDENTIAL = `bootstrap-${randomBytes(8).toString('hex')}`;
const PREVIEW_ENTRY_TOKEN = `preview-entry-${randomBytes(12).toString('hex')}`;
const APPROVED_CLASS = `${TEST_SCHEMA}.${TEST_TABLE}`;

const MANIFEST = buildManifest({
  services: [
    {
      serviceKey: SERVICE_KEY,
      requiredAdapters: ['node_http', 'undici', 'pg'],
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
  allowedDatabaseClasses: [APPROVED_CLASS],
});

let control: TestControlPlane;
let receiver: TestOtlpReceiver;
let app: AppProcess;

function allSpans(): DecodedSpan[] {
  return receiver.batches.flatMap((batch) => [...batch.spans]);
}

beforeAll(async () => {
  await prepareSchema();
  control = new TestControlPlane(BOOTSTRAP_CREDENTIAL);
  receiver = new TestOtlpReceiver();
  const controlUrl = await control.listen(CONTROL_PORT);
  const ingestUrl = await receiver.listen(INGEST_PORT);
  control.useActivation({
    signedManifest: signManifest(MANIFEST, IDENTITY),
    previewEntryToken: PREVIEW_ENTRY_TOKEN,
    requiredHops: ['server', 'downstream_http', 'database'],
    probeRouteTemplate: '/api/saved-views/:view_id',
    probeMethod: 'GET',
  });

  app = await startApp({
    appFile: 'node-http-app.cjs',
    withPreload: true,
    environment: {
      APP_PORT: String(APP_PORT),
      APP_DATABASE_URL: scopedDatabaseUrl(),
      APP_FETCH_ECHO: '1',
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

describe('the pg adapter', () => {
  it('continues the trace into the database and keeps only approved fields', async () => {
    const browser = browserTraceparent();
    const response = await callApp(APP_PORT, '/api/saved-views/1', {
      traceparent: browser.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    expect(response.statusCode).toBe(200);

    await waitFor(
      () =>
        allSpans().some(
          (span) =>
            span.traceId === browser.traceId
            && span.attributes['db.system.name'] === 'postgresql',
        ),
      25_000,
      'the database span',
    );

    const trace = allSpans().filter((span) => span.traceId === browser.traceId);
    const database = trace.find((span) => span.attributes['db.system.name'] === 'postgresql');
    expect(database).toBeDefined();
    expect(database?.name).toBe(`SELECT ${APPROVED_CLASS}`);
    expect(database?.attributes).toEqual({
      'db.system.name': 'postgresql',
      'db.operation.name': 'SELECT',
      'db.collection.name': APPROVED_CLASS,
    });

    const server = trace.find((span) => span.name === 'GET /api/saved-views/:view_id');
    expect(server).toBeDefined();
    expect(database?.parentSpanId).toBe(server?.spanId);
  });

  it('traces the undici downstream hop toward an approved target only', () => {
    const undici = receiver.batches
      .flatMap((batch) => [...batch.spans])
      .filter((span) => span.scopeName === '@opentelemetry/instrumentation-undici');
    expect(undici.length).toBeGreaterThan(0);
    for (const span of undici) {
      expect(span.attributes['server.address']).toBe('127.0.0.1');
      expect(span.attributes['server.port']).toBe(APP_PORT);
      expect(span.attributes['url.full']).toBeUndefined();
    }
  });

  it('never exports SQL text, bind values, or connection details', () => {
    const serialized = JSON.stringify(receiver.batches, (_key, value: unknown) =>
      typeof value === 'bigint' ? value.toString() : value,
    );
    expect(serialized).not.toContain('SELECT view_id FROM');
    expect(serialized).not.toContain('db.query.text');
    expect(serialized).not.toContain('postgres://');
    expect(serialized).not.toContain('search_path');
    expect(serialized).not.toContain('5432');
  });

  it('requires the database hop before the propagation probe passes', async () => {
    await waitFor(
      () =>
        control
          .messagesOfType('health')
          .some(
            (message) =>
              message.report.probe_result.state === 'passed'
              && message.report.lifecycle_state === 'ready',
          ),
      25_000,
      'the probe to pass with the database hop',
    );
    const passed = control
      .messagesOfType('health')
      .find((message) => message.report.probe_result.state === 'passed');
    expect(passed?.report.probe_result.required_hops.sort()).toEqual([
      'database',
      'downstream_http',
      'server',
    ]);
    expect([...(passed?.report.probe_result.observed_hops ?? [])].sort()).toEqual([
      'database',
      'downstream_http',
      'server',
    ]);
  });
});
