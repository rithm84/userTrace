/**
 * Redaction pipeline behaviour, class by class.
 *
 * These run the real pipeline over finished spans and assert both what
 * survives and what can never survive.
 */

import { randomUUID } from 'node:crypto';

import { SpanKind } from '@opentelemetry/api';
import { describe, expect, it } from 'vitest';

import { resolveActivation, type ActiveObservation } from '../src/activation.js';
import { UTRACE_ATTRIBUTES, UTRACE_ERROR_EVENT_NAME } from '../src/constants.js';
import { serverActivationSchema } from '../src/contracts/activation.js';
import type { AnnotationDefinition } from '../src/contracts/manifest.js';
import { compileRedactionPolicy } from '../src/redaction/policy.js';
import { redactSpan } from '../src/redaction/span.js';
import { TEST_LIMITS } from './helpers/control-server.js';
import { buildManifest, createSigningIdentity, signManifest } from './helpers/manifest.js';
import { readableSpan } from './helpers/spans.js';

const IDENTITY = createSigningIdentity('utrace-redaction-key');
const ANCHOR = {
  keyId: IDENTITY.keyId,
  algorithm: 'ed25519' as const,
  publicKeySpkiBase64: IDENTITY.publicKeySpkiBase64,
};

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
    definition_ref: 'saved_view_filters_applied.v1',
    kind: 'workflow_milestone',
    name: 'saved_view.filters_applied',
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
];

function observation(): ActiveObservation {
  const manifest = buildManifest({
    services: [
      {
        serviceKey: 'shopalytics_api',
        requiredAdapters: ['node_http', 'undici', 'pg', 'express', 'fastify'],
        registeredRoutes: [
          {
            route_template: '/api/saved-views/:view_id',
            methods: ['GET'],
            operation_name: 'saved_view.read',
            required: true,
          },
        ],
        registeredOperations: ['saved_view.create'],
        operationalNoise: [{ path_pattern: '/healthz', methods: ['GET'] }],
      },
    ],
    propagationTargets: [{ scheme: 'http', host: '127.0.0.1', port: 8399 }],
    allowedHttpDestinations: [{ scheme: 'http', host: '127.0.0.1', port: 8399 }],
    allowedDatabaseClasses: ['utrace_server_sdk_test.saved_views'],
    allowedCacheClasses: ['saved_view'],
    allowedQueueDestinations: ['saved-view-refresh'],
    allowedErrorTypes: ['SavedViewNotFoundError'],
    sanitizedErrorMessages: [
      { error_type: 'SavedViewNotFoundError', sanitized_message: 'saved view not found' },
    ],
    repositoryRoot: '/workspace/shopalytics',
    annotationDefinitions: DEFINITIONS,
  });
  const streamId = randomUUID();
  const instanceId = randomUUID();
  const service = manifest.services[0];
  if (service === undefined) {
    throw new Error('missing service');
  }
  const activation = serverActivationSchema.parse({
    activation_id: randomUUID(),
    client_id: manifest.client_id,
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
    credential: { token: 'token', expires_at: new Date().toISOString() },
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
    signed_manifest: signManifest(manifest, IDENTITY),
  });
  return resolveActivation(
    activation,
    ANCHOR,
    { serverStreamId: streamId, serviceInstanceId: instanceId, serviceKey: 'shopalytics_api' },
    new Date(),
  );
}

const POLICY = compileRedactionPolicy(observation());

describe('inbound HTTP spans', () => {
  it('keeps the registered template, method, status, protocol, and sizes only', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'GET',
        kind: SpanKind.SERVER,
        scopeName: '@opentelemetry/instrumentation-http',
        attributes: {
          'http.request.method': 'GET',
          'http.route': '/api/saved-views/:view_id',
          'http.response.status_code': 200,
          'url.scheme': 'http',
          'url.path': '/api/saved-views/7',
          'url.query': 'token=supersecret',
          'url.full': 'http://shop.test/api/saved-views/7?token=supersecret',
          'user_agent.original': 'Mozilla/5.0',
          'client.address': '203.0.113.7',
          'network.protocol.name': 'http',
          'network.protocol.version': '1.1',
          'http.response.body.size': 128,
          'http.request.header.cookie': 'session=abc',
          'server.address': 'shop.test',
        },
        statusMessage: 'Error: saved view 7 for maya@dudulemon.test not found',
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind !== 'export') {
      return;
    }
    expect(outcome.span.name).toBe('GET /api/saved-views/:view_id');
    expect(outcome.span.attributes).toEqual({
      'http.route': '/api/saved-views/:view_id',
      'http.request.method': 'GET',
      'http.response.status_code': 200,
      'url.scheme': 'http',
      'network.protocol.name': 'http',
      'network.protocol.version': '1.1',
      'http.response.body.size': 128,
    });
    expect(outcome.span.status.message).toBeUndefined();
    expect(outcome.hop).toBe('server');
  });

  it('fails with route_not_registered for an unregistered path', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'GET',
        kind: SpanKind.SERVER,
        scopeName: '@opentelemetry/instrumentation-http',
        attributes: { 'http.request.method': 'GET', 'http.route': '/admin/secret' },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
    if (outcome.kind === 'fail') {
      expect(outcome.failure.code).toBe('route_not_registered');
      expect(outcome.failure.readiness_consequence).toBe('withdraw_readiness');
    }
  });

  it('fails when a server span carries no route template at all', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'GET',
        kind: SpanKind.SERVER,
        scopeName: '@opentelemetry/instrumentation-http',
        attributes: { 'http.request.method': 'GET', 'url.path': '/api/saved-views/7' },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
  });
});

describe('outbound HTTP spans', () => {
  it('keeps an approved destination', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'GET',
        kind: SpanKind.CLIENT,
        scopeName: '@opentelemetry/instrumentation-http',
        attributes: {
          'http.request.method': 'GET',
          'server.address': '127.0.0.1',
          'server.port': 8399,
          'http.response.status_code': 204,
          'url.full': 'http://127.0.0.1:8399/internal?secret=1',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind === 'export') {
      expect(outcome.span.attributes['url.full']).toBeUndefined();
      expect(outcome.span.attributes['server.address']).toBe('127.0.0.1');
      expect(outcome.hop).toBe('downstream_http');
    }
  });

  it('fails an unapproved destination instead of exporting it', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'GET',
        kind: SpanKind.CLIENT,
        scopeName: '@opentelemetry/instrumentation-http',
        attributes: {
          'http.request.method': 'GET',
          'server.address': 'analytics.example.com',
          'server.port': 443,
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
    if (outcome.kind === 'fail') {
      expect(outcome.failure.code).toBe('unauthorized_destination');
    }
  });
});

describe('database spans', () => {
  it('keeps system, operation, and the approved class but never SQL text', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'pg.query:SELECT shopalytics',
        kind: SpanKind.CLIENT,
        scopeName: '@opentelemetry/instrumentation-pg',
        attributes: {
          'db.system.name': 'postgresql',
          'db.operation.name': 'SELECT',
          'db.collection.name': 'saved_views',
          'db.namespace': 'utrace_server_sdk_test',
          'db.query.text': "SELECT * FROM saved_views WHERE owner = 'maya@dudulemon.test'",
          'db.query.parameter.$1': 'maya@dudulemon.test',
          'server.address': '127.0.0.1',
          'server.port': 5432,
          'error.type': 'SavedViewNotFoundError',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind !== 'export') {
      return;
    }
    expect(outcome.span.attributes).toEqual({
      'db.system.name': 'postgresql',
      'db.operation.name': 'SELECT',
      'db.collection.name': 'utrace_server_sdk_test.saved_views',
      'error.type': 'SavedViewNotFoundError',
    });
    expect(outcome.span.name).toBe('SELECT utrace_server_sdk_test.saved_views');
    expect(outcome.hop).toBe('database');
  });

  it('drops an unapproved collection class rather than exporting it', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'pg.query:SELECT shopalytics',
        kind: SpanKind.CLIENT,
        scopeName: '@opentelemetry/instrumentation-pg',
        attributes: {
          'db.system.name': 'postgresql',
          'db.operation.name': 'SELECT',
          'db.collection.name': 'billing_secrets',
          'db.namespace': 'utrace_server_sdk_test',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind === 'export') {
      expect(outcome.span.attributes['db.collection.name']).toBeUndefined();
    }
  });

  it('normalizes an unregistered error type', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'pg.query:SELECT shopalytics',
        kind: SpanKind.CLIENT,
        scopeName: '@opentelemetry/instrumentation-pg',
        attributes: {
          'db.system.name': 'postgresql',
          'db.operation.name': 'SELECT',
          'error.type': 'PasswordAuthenticationFailedFor_maya',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind === 'export') {
      expect(outcome.span.attributes['error.type']).toBe('unregistered_error');
    }
  });
});

describe('cache and queue spans', () => {
  it('keeps a registered cache class and the hit flag', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'GET saved_view:7',
        kind: SpanKind.CLIENT,
        scopeName: '@opentelemetry/instrumentation-pg',
        attributes: {
          'db.system.name': 'redis',
          'db.operation.name': 'GET',
          [UTRACE_ATTRIBUTES.cacheClass]: 'saved_view',
          [UTRACE_ATTRIBUTES.cacheHit]: true,
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind === 'export') {
      expect(outcome.span.attributes[UTRACE_ATTRIBUTES.cacheClass]).toBe('saved_view');
      expect(outcome.span.attributes[UTRACE_ATTRIBUTES.cacheHit]).toBe(true);
      expect(outcome.hop).toBe('cache');
    }
  });

  it('keeps a registered queue destination and an opaque message id', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'publish',
        kind: SpanKind.PRODUCER,
        scopeName: '@opentelemetry/instrumentation-pg',
        attributes: {
          'messaging.system': 'bullmq',
          'messaging.operation.name': 'publish',
          'messaging.destination.name': 'saved-view-refresh',
          'messaging.message.id': 'job-8821',
          'messaging.message.body': '{"owner":"maya@dudulemon.test"}',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind === 'export') {
      expect(outcome.span.attributes['messaging.message.body']).toBeUndefined();
      expect(outcome.span.attributes['messaging.message.id']).toBe('job-8821');
      expect(outcome.hop).toBe('queue');
    }
  });

  it('fails an unregistered queue destination', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'publish',
        kind: SpanKind.PRODUCER,
        scopeName: '@opentelemetry/instrumentation-pg',
        attributes: {
          'messaging.system': 'bullmq',
          'messaging.operation.name': 'publish',
          'messaging.destination.name': 'billing-exports',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
    if (outcome.kind === 'fail') {
      expect(outcome.failure.code).toBe('unauthorized_destination');
    }
  });
});

describe('error evidence', () => {
  it('replaces the exception with a fingerprinted, bounded record', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'GET',
        kind: SpanKind.SERVER,
        scopeName: '@opentelemetry/instrumentation-http',
        attributes: {
          'http.request.method': 'GET',
          'http.route': '/api/saved-views/:view_id',
          'http.response.status_code': 500,
        },
        events: [
          {
            name: 'exception',
            time: [1_700_000_000, 1_000_000],
            attributes: {
              'exception.type': 'SavedViewNotFoundError',
              'exception.message': 'saved view 7 for maya@dudulemon.test is missing',
              'exception.stacktrace': [
                'SavedViewNotFoundError: saved view 7 for maya@dudulemon.test is missing',
                '    at loadView (/workspace/shopalytics/src/views.ts:44:11)',
                '    at /private/etc/other.js:1:1',
              ].join('\n'),
            },
          },
        ],
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind !== 'export') {
      return;
    }
    expect(outcome.span.events).toHaveLength(1);
    const event = outcome.span.events[0];
    expect(event?.name).toBe(UTRACE_ERROR_EVENT_NAME);
    expect(event?.attributes?.['error.type']).toBe('SavedViewNotFoundError');
    expect(event?.attributes?.[UTRACE_ATTRIBUTES.errorMessage]).toBe('saved view not found');
    expect(event?.attributes?.[UTRACE_ATTRIBUTES.errorFrames]).toEqual([
      'src/views.ts:44:11:loadView',
    ]);
    expect(String(event?.attributes?.[UTRACE_ATTRIBUTES.errorFingerprint])).toMatch(
      /^[0-9a-f]{32}$/u,
    );
    expect(JSON.stringify(outcome.span)).not.toContain('maya@dudulemon.test');
  });
});

describe('the utrace namespace', () => {
  it('keeps registered annotations and their definition references', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'saved_view.create',
        kind: SpanKind.INTERNAL,
        scopeName: '@utrace/server-sdk',
        attributes: {
          [UTRACE_ATTRIBUTES.operationName]: 'saved_view.create',
          [UTRACE_ATTRIBUTES.operationDefinitionRef]: 'saved_view_create.v1',
          [UTRACE_ATTRIBUTES.entityClass]: 'saved_view',
          [UTRACE_ATTRIBUTES.entityDefinitionRef]: 'saved_view_entity.v1',
          [`${UTRACE_ATTRIBUTES.valuePrefix}saved_view_tier`]: 'priority',
          [`${UTRACE_ATTRIBUTES.valuePrefix}saved_view_filter_count`]: 6,
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('export');
    if (outcome.kind === 'export') {
      expect(outcome.span.attributes[`${UTRACE_ATTRIBUTES.valuePrefix}saved_view_tier`]).toBe(
        'priority',
      );
      expect(outcome.span.name).toBe('saved_view.create');
    }
  });

  it('fails a safe value outside its bounded enum', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'saved_view.create',
        kind: SpanKind.INTERNAL,
        scopeName: '@utrace/server-sdk',
        attributes: {
          [UTRACE_ATTRIBUTES.operationName]: 'saved_view.create',
          [UTRACE_ATTRIBUTES.operationDefinitionRef]: 'saved_view_create.v1',
          [`${UTRACE_ATTRIBUTES.valuePrefix}saved_view_tier`]: 'maya@dudulemon.test',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
    if (outcome.kind === 'fail') {
      expect(outcome.failure.code).toBe('redaction_or_schema_failure');
    }
  });

  it('fails an unknown utrace attribute', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'saved_view.create',
        kind: SpanKind.INTERNAL,
        scopeName: '@utrace/server-sdk',
        attributes: {
          [UTRACE_ATTRIBUTES.operationName]: 'saved_view.create',
          [UTRACE_ATTRIBUTES.operationDefinitionRef]: 'saved_view_create.v1',
          'utrace.freeform.note': 'anything at all',
        },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
  });

  it('fails an unregistered product operation', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'billing.charge',
        kind: SpanKind.INTERNAL,
        scopeName: '@utrace/server-sdk',
        attributes: {},
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
  });
});

describe('instrumentation scope eligibility', () => {
  it('fails a span from a scope the manifest does not register', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'mystery',
        kind: SpanKind.SERVER,
        scopeName: 'some-other-vendor-agent',
        attributes: { 'http.request.method': 'GET', 'http.route': '/api/saved-views/:view_id' },
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('fail');
    if (outcome.kind === 'fail') {
      expect(outcome.failure.detail['reason']).toBe('ineligible_instrumentation_scope');
    }
  });

  it('excludes a framework handler span outside the registered routes', () => {
    const outcome = redactSpan(
      readableSpan({
        name: 'middleware - query',
        kind: SpanKind.INTERNAL,
        scopeName: '@opentelemetry/instrumentation-express',
        attributes: {},
      }),
      POLICY,
    );
    expect(outcome.kind).toBe('exclude');
  });
});
