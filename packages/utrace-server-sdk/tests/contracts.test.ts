/**
 * Contract-level checks that need no sockets: environment resolution, manifest
 * signature verification, route resolution, backoff, readiness, and agreement
 * between the JSON Schemas and the runtime validators in this package.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

import { SpanKind } from '@opentelemetry/api';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { describe, expect, it } from 'vitest';

import { resolveActivation } from '../src/activation.js';
import { canonicalJson } from '../src/contracts/canonical-json.js';
import { serverActivationSchema } from '../src/contracts/activation.js';
import {
  serverInstrumentationManifestSchema,
  signedServerInstrumentationManifestSchema,
} from '../src/contracts/manifest.js';
import { verifySignedManifest } from '../src/contracts/signature.js';
import { controlSocketUrl } from '../src/control/control-socket.js';
import { ENVIRONMENT_VARIABLES, resolveEnvironment } from '../src/environment.js';
import { ServerSdkError } from '../src/errors.js';
import { backoffDelayMillis, parseRetryAfter } from '../src/export/backoff.js';
import { otlpTracesUrl } from '../src/export/exporter.js';
import { evaluateReadiness } from '../src/health/readiness.js';
import { resolveRoute, templateMatches } from '../src/provider/routes.js';
import { PropagationProbe } from '../src/probe/probe.js';
import { detectSensitiveValue } from '../src/redaction/sensitive.js';
import { safeFrames } from '../src/redaction/error-evidence.js';
import { TEST_LIMITS } from './helpers/control-server.js';
import { buildManifest, createSigningIdentity, signManifest } from './helpers/manifest.js';
import { fixedJitter } from './helpers/support.js';
import { readableSpan } from './helpers/spans.js';

const SCHEMA_DIRECTORY = join(
  import.meta.dirname,
  '..',
  '..',
  'utrace-contracts',
  'schemas',
  'server-activation',
);

const IDENTITY = createSigningIdentity('utrace-test-key');

const ROUTES = [
  {
    route_template: '/api/saved-views/:view_id',
    methods: ['GET' as const],
    operation_name: 'saved_view.read',
    required: true,
  },
  {
    route_template: '/api/saved-views',
    methods: ['POST' as const],
    operation_name: 'saved_view.create',
    required: true,
  },
];

const NOISE = [{ path_pattern: '/healthz', methods: ['GET' as const] }];

function baseManifest(): ReturnType<typeof buildManifest> {
  return buildManifest({
    services: [
      {
        serviceKey: 'shopalytics_api',
        requiredAdapters: ['node_http'],
        registeredRoutes: ROUTES,
        operationalNoise: NOISE,
      },
    ],
  });
}

function activationFor(
  signed: ReturnType<typeof signManifest>,
  streamId: string,
  instanceId: string,
): unknown {
  const service = signed.manifest.services[0];
  if (service === undefined) {
    throw new Error('missing service');
  }
  return {
    activation_id: randomUUID(),
    client_id: signed.manifest.client_id,
    client_user_id: randomUUID(),
    customer_account_id: randomUUID(),
    session_id: randomUUID(),
    preview_environment_id: randomUUID(),
    preview_runtime_allocation_id: randomUUID(),
    server_stream_id: streamId,
    service_instance_id: instanceId,
    service_key: service.service_key,
    resource: {
      service_name: service.service_name,
      service_namespace: service.service_namespace,
      service_version: service.service_version,
      deployment_environment_name: 'preview',
    },
    credential: { token: 'service-token', expires_at: new Date().toISOString() },
    ingest: {
      traces_path: '/v1/ingest/otlp/v1/traces',
      preview_entry_token: 'preview-entry-token-value',
    },
    limits: TEST_LIMITS,
    probe: {
      probe_id: randomUUID(),
      route_template: '/api/saved-views/:view_id',
      method: 'GET',
      required_hops: ['server'],
    },
    signed_manifest: signed,
  };
}

describe('bootstrap environment', () => {
  it('stays dormant when the runtime injects no bootstrap credential', () => {
    expect(resolveEnvironment({}, new Date())).toEqual({ kind: 'dormant' });
    expect(
      resolveEnvironment({ [ENVIRONMENT_VARIABLES.bootstrapCredential]: '   ' }, new Date()),
    ).toEqual({ kind: 'dormant' });
  });

  it('fails when a bootstrap credential arrives without its companions', () => {
    expect(() =>
      resolveEnvironment(
        { [ENVIRONMENT_VARIABLES.bootstrapCredential]: 'secret' },
        new Date(),
      ),
    ).toThrow(ServerSdkError);
  });

  it('rejects an endpoint that carries a query', () => {
    const environment = {
      [ENVIRONMENT_VARIABLES.bootstrapCredential]: 'secret',
      [ENVIRONMENT_VARIABLES.serviceName]: 'shopalytics_api',
      [ENVIRONMENT_VARIABLES.controlUrl]: 'wss://control.utrace.dev/compute/v1/api?region=us',
      [ENVIRONMENT_VARIABLES.ingestUrl]: 'https://ingest.utrace.dev',
      [ENVIRONMENT_VARIABLES.manifestTrustAnchor]: IDENTITY.trustAnchorEnvValue,
    };
    expect(() => resolveEnvironment(environment, new Date())).toThrow(/without a query or fragment/u);
  });

  it('keeps a base URL path prefix on the control and OTLP endpoints', () => {
    const resolution = resolveEnvironment(
      {
        [ENVIRONMENT_VARIABLES.bootstrapCredential]: 'secret',
        [ENVIRONMENT_VARIABLES.serviceName]: 'shopalytics_api',
        [ENVIRONMENT_VARIABLES.controlUrl]: 'wss://api.utrace.dev/compute/v1/api',
        [ENVIRONMENT_VARIABLES.ingestUrl]: 'https://api.utrace.dev/compute/v1/api/',
        [ENVIRONMENT_VARIABLES.manifestTrustAnchor]: IDENTITY.trustAnchorEnvValue,
      },
      new Date(),
    );
    if (resolution.kind !== 'prepared') {
      throw new Error(`expected a prepared environment, received ${resolution.kind}`);
    }
    expect(controlSocketUrl(resolution.environment.controlUrl).href).toBe(
      'wss://api.utrace.dev/compute/v1/api/v1/server-sdk/control',
    );
    expect(otlpTracesUrl(resolution.environment.ingestUrl).href).toBe(
      'https://api.utrace.dev/compute/v1/api/v1/ingest/otlp/v1/traces',
    );
  });

  it('parses a complete prepared-preview environment', () => {
    const resolution = resolveEnvironment(
      {
        [ENVIRONMENT_VARIABLES.bootstrapCredential]: 'secret',
        [ENVIRONMENT_VARIABLES.serviceName]: 'shopalytics_api',
        [ENVIRONMENT_VARIABLES.controlUrl]: 'wss://control.utrace.dev',
        [ENVIRONMENT_VARIABLES.ingestUrl]: 'https://ingest.utrace.dev',
        [ENVIRONMENT_VARIABLES.manifestTrustAnchor]: IDENTITY.trustAnchorEnvValue,
      },
      new Date(),
    );
    expect(resolution.kind).toBe('prepared');
    if (resolution.kind === 'prepared') {
      expect(resolution.environment.manifestTrustAnchor.keyId).toBe('utrace-test-key');
      expect(resolution.environment.serviceName).toBe('shopalytics_api');
    }
  });
});

describe('manifest signature', () => {
  it('verifies a manifest signed over its canonical form', () => {
    const signed = signManifest(baseManifest(), IDENTITY);
    const verified = verifySignedManifest(
      signed,
      { keyId: IDENTITY.keyId, algorithm: 'ed25519', publicKeySpkiBase64: IDENTITY.publicKeySpkiBase64 },
      new Date(),
    );
    expect(verified.manifest_version).toBe(1);
  });

  it('rejects a manifest whose content changed after signing', () => {
    const signed = signManifest(baseManifest(), IDENTITY);
    const tampered = {
      ...signed,
      manifest: { ...signed.manifest, client_release_id: 'tampered-release' },
    };
    expect(() =>
      verifySignedManifest(
        tampered,
        { keyId: IDENTITY.keyId, algorithm: 'ed25519', publicKeySpkiBase64: IDENTITY.publicKeySpkiBase64 },
        new Date(),
      ),
    ).toThrow(/signature does not verify/u);
  });

  it('rejects a manifest signed by an untrusted key', () => {
    const other = createSigningIdentity('other-key');
    const signed = signManifest(baseManifest(), other);
    expect(() =>
      verifySignedManifest(
        signed,
        { keyId: IDENTITY.keyId, algorithm: 'ed25519', publicKeySpkiBase64: IDENTITY.publicKeySpkiBase64 },
        new Date(),
      ),
    ).toThrow(/untrusted key/u);
  });

  it('orders object members and drops undefined in canonical JSON', () => {
    expect(canonicalJson({ b: 1, a: [2, { d: 4, c: 3 }], e: undefined }, new Date())).toBe(
      '{"a":[2,{"c":3,"d":4}],"b":1}',
    );
  });
});

describe('activation resolution', () => {
  const anchor = {
    keyId: IDENTITY.keyId,
    algorithm: 'ed25519' as const,
    publicKeySpkiBase64: IDENTITY.publicKeySpkiBase64,
  };

  it('binds every activation identity', () => {
    const streamId = randomUUID();
    const instanceId = randomUUID();
    const activation = serverActivationSchema.parse(
      activationFor(signManifest(baseManifest(), IDENTITY), streamId, instanceId),
    );
    const observation = resolveActivation(
      activation,
      anchor,
      { serverStreamId: streamId, serviceInstanceId: instanceId, serviceKey: 'shopalytics_api' },
      new Date(),
    );
    expect(observation.identities.server_stream_id).toBe(streamId);
    expect(observation.identities.service_instance_id).toBe(instanceId);
    expect(observation.service.service_key).toBe('shopalytics_api');
  });

  it('rejects an activation that echoes a different stream', () => {
    const activation = serverActivationSchema.parse(
      activationFor(signManifest(baseManifest(), IDENTITY), randomUUID(), randomUUID()),
    );
    expect(() =>
      resolveActivation(
        activation,
        anchor,
        {
          serverStreamId: randomUUID(),
          serviceInstanceId: activation.service_instance_id,
          serviceKey: 'shopalytics_api',
        },
        new Date(),
      ),
    ).toThrow(/different server stream/u);
  });

  it('rejects a handshake limit above its platform ceiling', () => {
    const streamId = randomUUID();
    const instanceId = randomUUID();
    const raw = activationFor(signManifest(baseManifest(), IDENTITY), streamId, instanceId) as Record<
      string,
      unknown
    >;
    raw['limits'] = { ...TEST_LIMITS, max_in_flight_batches: 9 };
    expect(() => serverActivationSchema.parse(raw)).toThrow();
  });

  it('rejects a resource identity that contradicts the manifest', () => {
    const streamId = randomUUID();
    const instanceId = randomUUID();
    const raw = activationFor(signManifest(baseManifest(), IDENTITY), streamId, instanceId) as Record<
      string,
      unknown
    >;
    raw['resource'] = {
      service_name: 'someone-elses-service',
      service_namespace: 'shopalytics',
      service_version: '1.4.2',
      deployment_environment_name: 'preview',
    };
    const activation = serverActivationSchema.parse(raw);
    expect(() =>
      resolveActivation(
        activation,
        anchor,
        { serverStreamId: streamId, serviceInstanceId: instanceId, serviceKey: 'shopalytics_api' },
        new Date(),
      ),
    ).toThrow(/contradicts the manifest/u);
  });
});

describe('registered route resolution', () => {
  it('matches parameter and wildcard templates', () => {
    expect(templateMatches('/api/saved-views/:view_id', ['api', 'saved-views', 'abc'])).toBe(true);
    expect(templateMatches('/api/saved-views/:view_id', ['api', 'saved-views'])).toBe(false);
    expect(templateMatches('/_next/*', ['_next', 'static', 'chunk.js'])).toBe(true);
  });

  it('prefers a registered product route over operational noise', () => {
    const overlapping = [{ path_pattern: '/api/*', methods: ['GET' as const] }];
    expect(resolveRoute('/api/saved-views/7?filter=x', 'GET', ROUTES, overlapping)).toEqual({
      kind: 'product',
      route: ROUTES[0],
    });
  });

  it('classifies registered noise and unregistered paths distinctly', () => {
    expect(resolveRoute('/healthz', 'GET', ROUTES, NOISE).kind).toBe('operational_noise');
    expect(resolveRoute('/admin/secret', 'GET', ROUTES, NOISE).kind).toBe('unregistered');
    expect(resolveRoute('/api/saved-views/7', 'DELETE', ROUTES, NOISE).kind).toBe('unregistered');
  });
});

describe('export backoff', () => {
  it('bounds exponential growth and applies jitter deterministically', () => {
    const retry = TEST_LIMITS.retry;
    expect(backoffDelayMillis(1, retry, undefined, fixedJitter)).toBe(10);
    expect(backoffDelayMillis(2, retry, undefined, fixedJitter)).toBe(20);
    expect(backoffDelayMillis(9, retry, undefined, fixedJitter)).toBe(retry.max_backoff_millis);
  });

  it('honours Retry-After but never beyond the maximum backoff', () => {
    expect(backoffDelayMillis(1, TEST_LIMITS.retry, 50, fixedJitter)).toBe(50);
    expect(backoffDelayMillis(1, TEST_LIMITS.retry, 10_000, fixedJitter)).toBe(200);
  });

  it('parses both Retry-After forms', () => {
    const now = new Date('2026-10-03T00:00:00.000Z');
    expect(parseRetryAfter('3', now)).toBe(3000);
    expect(parseRetryAfter('Sat, 03 Oct 2026 00:00:05 GMT', now)).toBe(5000);
    expect(parseRetryAfter('soon', now)).toBeUndefined();
    expect(parseRetryAfter(undefined, now)).toBeUndefined();
  });
});

describe('readiness', () => {
  const ready = {
    activationBound: true,
    credentialValid: true,
    providerInstalled: true,
    missingRequiredAdapters: 0,
    controlConnected: true,
    bufferAboveHighWater: false,
    probePassed: true,
    openFailures: 0,
    paused: false,
  };

  it('is ready only when every requirement holds', () => {
    expect(evaluateReadiness(ready)).toEqual({ ready: true, blockers: [] });
  });

  it('names each blocker exactly', () => {
    expect(evaluateReadiness({ ...ready, probePassed: false }).blockers).toEqual([
      'propagation_probe_incomplete',
    ]);
    expect(evaluateReadiness({ ...ready, bufferAboveHighWater: true }).blockers).toEqual([
      'export_queue_above_high_water',
    ]);
    expect(evaluateReadiness({ ...ready, missingRequiredAdapters: 1 }).blockers).toEqual([
      'required_adapter_missing',
    ]);
  });
});

describe('defensive redaction helpers', () => {
  it('detects credential-shaped values', () => {
    expect(detectSensitiveValue('Bearer abcdefghijklmnop')).toBe('bearer_credential');
    expect(detectSensitiveValue('maya@dudulemon.test')).toBe('email_address');
    expect(detectSensitiveValue('/api/saved-views/:view_id')).toBeUndefined();
  });

  it('keeps only bounded repository-relative frames', () => {
    const stack = [
      'Error: boom',
      '    at handler (/workspace/app/src/routes.ts:12:5)',
      '    at /workspace/app/src/server.ts:20:3',
      '    at Module._compile (node:internal/modules/cjs/loader:1234:14)',
      '    at /etc/secret/other.js:1:1',
    ].join('\n');
    expect(safeFrames(stack, '/workspace/app', 12)).toEqual([
      'src/routes.ts:12:5:handler',
      'src/server.ts:20:3:<anonymous>',
    ]);
    expect(safeFrames(stack, '/workspace/app', 1)).toHaveLength(1);
  });
});

describe('the propagation probe', () => {
  const spec = {
    probe_id: randomUUID(),
    route_template: '/api/saved-views/:view_id',
    method: 'GET' as const,
    required_hops: ['server' as const, 'database' as const],
  };

  const serverSpan = (traceId: string, remoteParent: boolean) =>
    readableSpan({
      name: 'GET /api/saved-views/:view_id',
      kind: SpanKind.SERVER,
      scopeName: '@opentelemetry/instrumentation-http',
      attributes: {},
      traceId,
      remoteParent,
    });

  const databaseSpan = (traceId: string) =>
    readableSpan({
      name: 'SELECT saved_views',
      kind: SpanKind.CLIENT,
      scopeName: '@opentelemetry/instrumentation-pg',
      attributes: {},
      traceId,
    });

  it('passes only after every hop is observed and its batch is acknowledged', () => {
    const traceId = '11111111111111111111111111111111';
    const probe = new PropagationProbe(spec);
    probe.observe(databaseSpan(traceId), 'database');
    probe.observe(serverSpan(traceId, true), 'server');
    expect(probe.result().state).toBe('awaiting_spans');
    probe.noteBatch([serverSpan(traceId, true)], 4);
    expect(probe.result().state).toBe('awaiting_acknowledgement');
    probe.acknowledge(3);
    expect(probe.hasPassed).toBe(false);
    probe.acknowledge(4);
    expect(probe.hasPassed).toBe(true);
    expect([...probe.result().observed_hops].sort()).toEqual(['database', 'server']);
  });

  it('ignores a probe request that did not inherit the browser span', () => {
    const traceId = '22222222222222222222222222222222';
    const probe = new PropagationProbe(spec);
    probe.observe(serverSpan(traceId, false), 'server');
    probe.noteBatch([serverSpan(traceId, false)], 1);
    probe.acknowledge(1);
    expect(probe.hasPassed).toBe(false);
    expect(probe.result().observed_hops).toEqual([]);
  });

  it('fails on a duplicate authoritative server span', () => {
    const traceId = '33333333333333333333333333333333';
    const probe = new PropagationProbe(spec);
    probe.observe(serverSpan(traceId, true), 'server');
    probe.observe(serverSpan(traceId, true), 'server');
    expect(probe.result()).toMatchObject({
      state: 'failed',
      failure_reason: 'duplicate_server_span',
    });
  });

  it('fails a missing hop once the acknowledgement lag is exceeded', () => {
    const traceId = '44444444444444444444444444444444';
    const probe = new PropagationProbe(spec);
    probe.observe(serverSpan(traceId, true), 'server');
    probe.noteBatch([serverSpan(traceId, true)], 1);
    probe.checkDeadline(Date.now() + 10_000, 5_000);
    expect(probe.result()).toMatchObject({ state: 'failed', failure_reason: 'missing_hop' });
  });
});

describe('JSON Schemas and runtime validators agree', () => {
  // `strictRequired` is an Ajv lint about `required` inside `if`/`then`; the
  // conditional annotation rules are valid 2020-12 and both validators enforce them.
  const ajv = new Ajv2020.default({ strict: true, strictRequired: false, allErrors: true });
  addFormats.default(ajv);
  for (const file of readdirSync(SCHEMA_DIRECTORY)) {
    ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIRECTORY, file), 'utf8')) as object);
  }

  it('publishes one schema document per contract record', () => {
    expect(readdirSync(SCHEMA_DIRECTORY).sort()).toEqual([
      'server-activation.v1.schema.json',
      'server-control-client-message.v1.schema.json',
      'server-control-server-message.v1.schema.json',
      'server-export-batch-envelope.v1.schema.json',
      'server-instrumentation-manifest.v1.schema.json',
      'server-sdk-failure.v1.schema.json',
      'server-sdk-health-report.v1.schema.json',
      'signed-server-instrumentation-manifest.v1.schema.json',
    ]);
  });

  it('accepts the same signed manifest in both implementations', () => {
    const signed = signManifest(baseManifest(), IDENTITY);
    const validate = ajv.getSchema(
      'https://utrace.dev/schemas/server-activation/signed-server-instrumentation-manifest/v1',
    );
    expect(validate).toBeDefined();
    expect(validate?.(signed)).toBe(true);
    expect(signedServerInstrumentationManifestSchema.safeParse(signed).success).toBe(true);
  });

  it('rejects the same invalid manifest in both implementations', () => {
    const invalid = {
      ...baseManifest(),
      annotation_definitions: [
        {
          definition_id: randomUUID(),
          definition_ref: 'saved_view_tier.v1',
          kind: 'safe_value',
          name: 'saved_view_tier',
          // bounded_enum without permitted_values violates the conditional rule.
          output_class: 'bounded_enum',
        },
      ],
    };
    const validate = ajv.getSchema(
      'https://utrace.dev/schemas/server-activation/server-instrumentation-manifest/v1',
    );
    expect(validate?.(invalid)).toBe(false);
    expect(serverInstrumentationManifestSchema.safeParse(invalid).success).toBe(false);
  });

  it('accepts the same activation record in both implementations', () => {
    const activation = activationFor(
      signManifest(baseManifest(), IDENTITY),
      randomUUID(),
      randomUUID(),
    );
    const validate = ajv.getSchema(
      'https://utrace.dev/schemas/server-activation/server-activation/v1',
    );
    expect(validate?.(activation)).toBe(true);
    expect(serverActivationSchema.safeParse(activation).success).toBe(true);
  });
});
