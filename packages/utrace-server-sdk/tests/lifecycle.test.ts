/**
 * Lifecycle, ownership, and the typed annotation surface.
 *
 * An ESM application exercises the annotation API through the preload, and the
 * control plane drives pause, resume, and terminate. Separate processes prove
 * that a preview which cannot be observed does not boot.
 */

import { randomBytes, randomUUID } from 'node:crypto';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { PREVIEW_ENTRY_HEADER, UTRACE_ATTRIBUTES } from '../src/constants.js';
import { ENVIRONMENT_VARIABLES } from '../src/environment.js';
import type { AnnotationDefinition } from '../src/contracts/manifest.js';
import type { DecodedSpan } from './helpers/protobuf.js';
import { TestControlPlane } from './helpers/control-server.js';
import { TestOtlpReceiver } from './helpers/otlp-receiver.js';
import { buildManifest, createSigningIdentity, signManifest } from './helpers/manifest.js';
import { browserTraceparent, waitFor } from './helpers/support.js';
import { callApp, startApp, type AppProcess } from './helpers/app-process.js';

const CONTROL_PORT = 8314;
const INGEST_PORT = 8324;
const APP_PORT = 8337;
const BLOCKED_APP_PORT = 8338;

const SERVICE_KEY = 'shopalytics_api_annotated';
const IDENTITY = createSigningIdentity('utrace-lifecycle-key');
const BOOTSTRAP_CREDENTIAL = `bootstrap-${randomBytes(8).toString('hex')}`;
const PREVIEW_ENTRY_TOKEN = `preview-entry-${randomBytes(12).toString('hex')}`;

const DEFINITIONS: AnnotationDefinition[] = [
  {
    definition_id: randomUUID(),
    definition_ref: 'saved_view_create.v1',
    kind: 'product_operation',
    name: 'saved_view.create',
    output_class: 'registered_reference',
  },
  {
    definition_id: randomUUID(),
    definition_ref: 'saved_view_entity.v1',
    kind: 'entity_class',
    name: 'saved_view',
    output_class: 'registered_reference',
  },
  {
    definition_id: randomUUID(),
    definition_ref: 'saved_view_tier.v1',
    kind: 'safe_value',
    name: 'saved_view_tier',
    output_class: 'bounded_enum',
    permitted_values: ['standard', 'priority'],
  },
  {
    definition_id: randomUUID(),
    definition_ref: 'saved_view_filter_count.v1',
    kind: 'safe_value',
    name: 'saved_view_filter_count',
    output_class: 'bounded_integer',
    value_bounds: { minimum: 0, maximum: 24 },
  },
  {
    definition_id: randomUUID(),
    definition_ref: 'saved_view_filters_applied.v1',
    kind: 'workflow_milestone',
    name: 'saved_view.filters_applied',
    output_class: 'registered_reference',
  },
  {
    definition_id: randomUUID(),
    definition_ref: 'saved_view_created.v1',
    kind: 'expected_outcome',
    name: 'saved_view.created',
    output_class: 'registered_reference',
  },
];

const MANIFEST = buildManifest({
  services: [
    {
      serviceKey: SERVICE_KEY,
      requiredAdapters: ['node_http'],
      registeredRoutes: [
        {
          route_template: '/api/saved-views',
          methods: ['GET'],
          operation_name: 'saved_view.create',
          required: true,
        },
        {
          route_template: '/api/saved-views/:view_id',
          methods: ['GET'],
          operation_name: 'saved_view.create',
          required: true,
        },
      ],
      registeredOperations: ['saved_view.create'],
      operationalNoise: [{ path_pattern: '/healthz', methods: ['GET'] }],
    },
  ],
  annotationDefinitions: DEFINITIONS,
});

let control: TestControlPlane;
let receiver: TestOtlpReceiver;
let app: AppProcess;

function allSpans(): DecodedSpan[] {
  return receiver.batches.flatMap((batch) => [...batch.spans]);
}

function baseEnvironment(): Record<string, string> {
  return {
    [ENVIRONMENT_VARIABLES.bootstrapCredential]: BOOTSTRAP_CREDENTIAL,
    [ENVIRONMENT_VARIABLES.controlUrl]: `ws://127.0.0.1:${String(CONTROL_PORT)}`,
    [ENVIRONMENT_VARIABLES.ingestUrl]: `http://127.0.0.1:${String(INGEST_PORT)}`,
    [ENVIRONMENT_VARIABLES.serviceName]: SERVICE_KEY,
    [ENVIRONMENT_VARIABLES.manifestTrustAnchor]: IDENTITY.trustAnchorEnvValue,
  };
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
    // Short enough that the first maintenance tick rotates the credential.
    credentialLifetimeMillis: 30_000,
  });
  app = await startApp({
    appFile: 'annotated-app.mjs',
    withPreload: true,
    environment: { APP_PORT: String(APP_PORT), ...baseEnvironment() },
  });
});

afterAll(async () => {
  await app.stop();
  await control.close();
  await receiver.close();
});

describe('an ESM application', () => {
  it('is instrumented through the preload module hook', async () => {
    const browser = browserTraceparent();
    const response = await callApp(APP_PORT, '/api/saved-views/3', {
      traceparent: browser.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    expect(response.statusCode).toBe(200);
    await waitFor(
      () => allSpans().some((span) => span.traceId === browser.traceId),
      25_000,
      'the ESM application trace',
    );
    const server = allSpans().find(
      (span) => span.traceId === browser.traceId && span.kind === 2,
    );
    expect(server?.name).toBe('GET /api/saved-views/:view_id');
    expect(server?.parentSpanId).toBe(browser.spanId);
  });
});

describe('the typed annotation surface', () => {
  it('exports registered operations, entities, milestones, values, and outcomes', async () => {
    const browser = browserTraceparent();
    await callApp(APP_PORT, '/api/saved-views', {
      traceparent: browser.traceparent,
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    await waitFor(
      () =>
        allSpans().some(
          (span) => span.traceId === browser.traceId && span.name === 'saved_view.create',
        ),
      25_000,
      'the annotated product operation span',
    );
    const operation = allSpans().find(
      (span) => span.traceId === browser.traceId && span.name === 'saved_view.create',
    );
    expect(operation?.attributes).toEqual({
      [UTRACE_ATTRIBUTES.operationName]: 'saved_view.create',
      [UTRACE_ATTRIBUTES.operationDefinitionRef]: 'saved_view_create.v1',
      [UTRACE_ATTRIBUTES.entityClass]: 'saved_view',
      [UTRACE_ATTRIBUTES.entityDefinitionRef]: 'saved_view_entity.v1',
      [UTRACE_ATTRIBUTES.outcomeName]: 'saved_view.created',
      [UTRACE_ATTRIBUTES.outcomeDefinitionRef]: 'saved_view_created.v1',
      [`${UTRACE_ATTRIBUTES.valuePrefix}saved_view_tier`]: 'priority',
      [`${UTRACE_ATTRIBUTES.valuePrefix}saved_view_filter_count`]: 6,
    });
    const milestone = operation?.events.find((event) => event.name === 'utrace.milestone');
    expect(milestone?.attributes[UTRACE_ATTRIBUTES.milestoneName]).toBe(
      'saved_view.filters_applied',
    );
  });

  it('withdraws readiness for an unregistered annotation without failing the request', async () => {
    const response = await callApp(APP_PORT, '/api/saved-views/unregistered', {
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    expect(response.statusCode).toBe(200);
    expect(response.body).toBe('{"ok":true}');
    await waitFor(
      () =>
        control
          .messagesOfType('health')
          .some((message) =>
            message.report.failures.some(
              (failure) => failure.detail['reason'] === 'unregistered_annotation',
            ),
          ),
      25_000,
      'the unregistered annotation failure',
    );
    expect(allSpans().some((span) => span.name === 'billing.charge_customer')).toBe(false);
  });
});

describe('credential rotation', () => {
  it('takes fresh service-instance credentials before expiry and uses them', async () => {
    await waitFor(
      () => control.messagesOfType('rotate').length >= 1,
      25_000,
      'the credential rotation request',
    );
    const before = receiver.batches.length;
    await callApp(APP_PORT, '/api/saved-views/44', {
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    await waitFor(
      () => receiver.batches.length > before,
      20_000,
      'a batch exported after rotation',
    );
    expect(receiver.batches.at(-1)?.authorization).toMatch(/^Bearer rotated-/u);
  });
});

describe('control commands', () => {
  it('suspends export while paused and resumes afterwards', async () => {
    const pauseId = control.pause('session_paused');
    await waitFor(
      () =>
        control
          .messagesOfType('command_ack')
          .some((message) => message.command_id === pauseId),
      15_000,
      'the pause acknowledgement',
    );
    expect(
      control.messagesOfType('command_ack').find((message) => message.command_id === pauseId)
        ?.lifecycle_state,
    ).toBe('paused');

    const pausedCount = receiver.batches.length;
    await callApp(APP_PORT, '/api/saved-views/55', {
      [PREVIEW_ENTRY_HEADER]: PREVIEW_ENTRY_TOKEN,
    });
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 1_000);
    });
    // Paused means no new batch leaves the process; the spans stay queued.
    expect(receiver.batches.length).toBe(pausedCount);

    const resumeId = control.resume();
    await waitFor(
      () =>
        control
          .messagesOfType('command_ack')
          .some((message) => message.command_id === resumeId),
      15_000,
      'the resume acknowledgement',
    );
    const resumed = control
      .messagesOfType('command_ack')
      .find((message) => message.command_id === resumeId);
    expect(resumed?.lifecycle_state).not.toBe('paused');
    await waitFor(
      () => receiver.batches.length > pausedCount,
      20_000,
      'the queued spans to export after resume',
    );
  });
});

describe('a preview that cannot be observed', () => {
  it('refuses to boot when the bootstrap credential is rejected', async () => {
    const blocked = await startApp({
      appFile: 'annotated-app.mjs',
      withPreload: true,
      expectListening: false,
      environment: {
        APP_PORT: String(BLOCKED_APP_PORT),
        ...baseEnvironment(),
        [ENVIRONMENT_VARIABLES.bootstrapCredential]: 'not-the-issued-credential',
      },
    });
    const code = await blocked.exited;
    expect(code).not.toBe(0);
    expect(blocked.stdout.join('')).not.toContain('APP_LISTENING');
  });

  it('refuses to boot when another tracing distribution is configured', async () => {
    const blocked = await startApp({
      appFile: 'annotated-app.mjs',
      withPreload: true,
      expectListening: false,
      environment: {
        APP_PORT: String(BLOCKED_APP_PORT),
        ...baseEnvironment(),
        OTEL_TRACES_EXPORTER: 'otlp',
      },
    });
    const code = await blocked.exited;
    expect(code).not.toBe(0);
    expect(blocked.stderr.join('')).toContain('duplicate_provider_or_hook');
    expect(blocked.stdout.join('')).not.toContain('APP_LISTENING');
  });
});

describe('a manifest-required adapter with no tested library', () => {
  it('refuses to boot', async () => {
    const missing = buildManifest({
      services: [
        {
          serviceKey: SERVICE_KEY,
          requiredAdapters: ['node_http', 'next_server'],
          registeredRoutes: [
            {
              route_template: '/api/saved-views/:view_id',
              methods: ['GET'],
              operation_name: 'saved_view.create',
              required: true,
            },
          ],
          registeredOperations: ['saved_view.create'],
        },
      ],
      annotationDefinitions: DEFINITIONS,
    });
    control.useActivation({
      signedManifest: signManifest(missing, IDENTITY),
      previewEntryToken: PREVIEW_ENTRY_TOKEN,
      requiredHops: ['server'],
      probeRouteTemplate: '/api/saved-views/:view_id',
      probeMethod: 'GET',
    });
    const blocked = await startApp({
      appFile: 'annotated-app.mjs',
      withPreload: true,
      expectListening: false,
      environment: { APP_PORT: String(BLOCKED_APP_PORT), ...baseEnvironment() },
    });
    const code = await blocked.exited;
    expect(code).not.toBe(0);
    expect(blocked.stderr.join('')).toContain('required_adapter_missing');
    expect(blocked.stdout.join('')).not.toContain('APP_LISTENING');
  });
});

describe('termination', () => {
  it('flushes and reports a clean terminal stream on a terminate command', async () => {
    control.terminate('session_ended');
    await waitFor(
      () => control.messagesOfType('stream_report').length >= 1,
      20_000,
      'the terminal stream report',
    );
    const report = control.messagesOfType('stream_report')[0];
    expect(report?.clean).toBe(true);
    expect(report?.last_acknowledged_sequence).toBe(report?.last_produced_sequence);
  });
});
