/**
 * The manifest-driven instrumentation set.
 *
 * The SDK installs exactly the adapters the signed release manifest requires.
 * It never enables an unrestricted auto-instrumentation bundle and then exports
 * every span it happens to create.
 */

import { createRequire } from 'node:module';
import { join } from 'node:path';
import type { IncomingMessage, RequestOptions } from 'node:http';

import type { Attributes } from '@opentelemetry/api';
import type { Instrumentation } from '@opentelemetry/instrumentation';
import { ExpressInstrumentation, ExpressLayerType } from '@opentelemetry/instrumentation-express';
import { HttpInstrumentation } from '@opentelemetry/instrumentation-http';
import { PgInstrumentation } from '@opentelemetry/instrumentation-pg';
import { UndiciInstrumentation } from '@opentelemetry/instrumentation-undici';
import fastifyOtel from '@fastify/otel';
import {
  ATTR_DB_COLLECTION_NAME,
  ATTR_DB_OPERATION_NAME,
  ATTR_HTTP_ROUTE,
} from '@opentelemetry/semantic-conventions';

import type { Adapter } from '../contracts/manifest.js';
import type { AdapterStatus } from '../contracts/health.js';
import type { RedactionPolicy } from '../redaction/policy.js';
import { defaultPortForScheme, isApprovedTarget } from './propagation.js';
import { resolveRoute } from './routes.js';

const MODULE_BY_ADAPTER: Readonly<Record<Adapter, string | undefined>> = {
  node_http: undefined,
  undici: undefined,
  pg: 'pg',
  express: 'express',
  fastify: 'fastify',
  next_server: 'next',
};

const SQL_OPERATION = /^\s*([A-Za-z]+)/u;

function resolveModuleVersion(moduleName: string): string | null {
  try {
    const appRequire = createRequire(join(process.cwd(), 'noop.js'));
    const manifestPath = appRequire.resolve(`${moduleName}/package.json`);
    const loaded: unknown = appRequire(manifestPath);
    if (typeof loaded === 'object' && loaded !== null) {
      const version = (loaded as Record<string, unknown>)['version'];
      return typeof version === 'string' ? version : null;
    }
    return null;
  } catch {
    return null;
  }
}

function outgoingTargetApproved(policy: RedactionPolicy, options: RequestOptions): boolean {
  const host = options.hostname ?? options.host ?? 'localhost';
  const scheme = typeof options.protocol === 'string' ? options.protocol : 'http:';
  const port =
    typeof options.port === 'number'
      ? options.port
      : typeof options.port === 'string' && options.port !== ''
        ? Number.parseInt(options.port, 10)
        : defaultPortForScheme(scheme);
  return isApprovedTarget(policy.propagationTargets, scheme, host.split(':')[0] ?? host, port);
}

function incomingRouteAttributes(policy: RedactionPolicy, request: IncomingMessage): Attributes {
  const resolution = resolveRoute(
    request.url ?? '/',
    request.method ?? 'GET',
    policy.routes,
    policy.operationalNoise,
  );
  return resolution.kind === 'product'
    ? { [ATTR_HTTP_ROUTE]: resolution.route.route_template }
    : {};
}

function buildHttpInstrumentation(policy: RedactionPolicy): Instrumentation {
  return new HttpInstrumentation({
    // Registered operational noise never becomes a product span. An
    // unregistered path still produces a span so redaction can report the
    // manifest gap instead of silently dropping the request.
    ignoreIncomingRequestHook: (request) =>
      resolveRoute(
        request.url ?? '/',
        request.method ?? 'GET',
        policy.routes,
        policy.operationalNoise,
      ).kind === 'operational_noise',
    // Only manifest-approved propagation targets are traced, so uTrace context
    // never reaches an arbitrary third party.
    ignoreOutgoingRequestHook: (options) => !outgoingTargetApproved(policy, options),
    startIncomingSpanHook: (request) => incomingRouteAttributes(policy, request),
    requireParentforOutgoingSpans: false,
    requireParentforIncomingSpans: false,
    enableSyntheticSourceDetection: false,
  });
}

function buildUndiciInstrumentation(policy: RedactionPolicy): Instrumentation {
  return new UndiciInstrumentation({
    ignoreRequestHook: (request): boolean => {
      let origin: URL;
      try {
        origin = new URL(request.origin);
      } catch {
        return true;
      }
      const port = origin.port === '' ? defaultPortForScheme(origin.protocol) : Number.parseInt(origin.port, 10);
      return !isApprovedTarget(policy.propagationTargets, origin.protocol, origin.hostname, port);
    },
    requireParentforSpans: false,
  });
}

function buildPgInstrumentation(policy: RedactionPolicy): Instrumentation {
  return new PgInstrumentation({
    // SQL text and bind values never leave the process; the hook reads the
    // query in memory only to select a manifest-registered operation and class.
    enhancedDatabaseReporting: false,
    addSqlCommenterCommentToQueries: false,
    requireParentSpan: true,
    requestHook: (span, queryInfo): void => {
      const text = queryInfo.query.text;
      const operation = SQL_OPERATION.exec(text)?.[1]?.toUpperCase();
      if (operation !== undefined) {
        span.setAttribute(ATTR_DB_OPERATION_NAME, operation);
      }
      // The exported class is always the manifest's own spelling: the query
      // text only selects which approved class it refers to.
      const lowered = text.toLowerCase();
      for (const approved of policy.allowedDatabaseClasses) {
        const bare = approved.includes('.') ? (approved.split('.').at(-1) ?? approved) : approved;
        if (new RegExp(`(^|[^a-z0-9_])${bare.toLowerCase()}([^a-z0-9_]|$)`, 'u').test(lowered)) {
          span.setAttribute(ATTR_DB_COLLECTION_NAME, approved);
          break;
        }
      }
    },
  });
}

function buildExpressInstrumentation(): Instrumentation {
  return new ExpressInstrumentation({
    // Middleware churn and router internals are not product evidence.
    ignoreLayersType: [ExpressLayerType.MIDDLEWARE, ExpressLayerType.ROUTER],
  });
}

function buildFastifyInstrumentation(policy: RedactionPolicy): Instrumentation {
  const FastifyOtelInstrumentation = fastifyOtel.FastifyOtelInstrumentation;
  return new FastifyOtelInstrumentation({
    registerOnInitialization: true,
    instrumentHooks: false,
    instrumentHandler: true,
    recordExceptions: true,
    ignorePaths: (routeOptions) =>
      resolveRoute(routeOptions.url, routeOptions.method, policy.routes, policy.operationalNoise)
        .kind === 'operational_noise',
  });
}

/** The instrumentation set plus the adapter status the health report carries. */
export interface InstrumentationSet {
  readonly instrumentations: readonly Instrumentation[];
  readonly statuses: readonly AdapterStatus[];
  readonly missingRequired: readonly Adapter[];
}

/** Builds exactly the adapters the manifest declares for this service. */
export function buildInstrumentationSet(
  requiredAdapters: readonly Adapter[],
  policy: RedactionPolicy,
): InstrumentationSet {
  const instrumentations: Instrumentation[] = [];
  const statuses: AdapterStatus[] = [];
  const missingRequired: Adapter[] = [];

  for (const adapter of requiredAdapters) {
    const moduleName = MODULE_BY_ADAPTER[adapter];
    const moduleVersion = moduleName === undefined ? process.versions.node : resolveModuleVersion(moduleName);
    const present = moduleName === undefined || moduleVersion !== null;
    if (!present) {
      missingRequired.push(adapter);
      statuses.push({ adapter, required: true, installed: false });
      continue;
    }
    switch (adapter) {
      case 'node_http':
      case 'next_server':
        // Serverful Next.js rides on the Node HTTP adapter; the registered
        // route template comes from the manifest, not from the framework.
        if (!instrumentations.some((entry) => entry.instrumentationName.endsWith('instrumentation-http'))) {
          instrumentations.push(buildHttpInstrumentation(policy));
        }
        break;
      case 'undici':
        instrumentations.push(buildUndiciInstrumentation(policy));
        break;
      case 'pg':
        instrumentations.push(buildPgInstrumentation(policy));
        break;
      case 'express':
        instrumentations.push(buildExpressInstrumentation());
        break;
      case 'fastify':
        instrumentations.push(buildFastifyInstrumentation(policy));
        break;
      default:
        break;
    }
    statuses.push({
      adapter,
      required: true,
      installed: true,
      ...(moduleVersion === null ? {} : { module_version: moduleVersion }),
    });
  }

  return { instrumentations, statuses, missingRequired };
}
