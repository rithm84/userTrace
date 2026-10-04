/**
 * Sole ownership of the OpenTelemetry provider.
 *
 * uTrace registers exactly one `NodeTracerProvider`, one context manager, one
 * propagator, and one sampler, and installs only the manifest's adapters. Each
 * global registration is checked, so an existing owner surfaces as a typed
 * duplicate failure instead of a silent merge.
 */

import {
  INVALID_SPAN_CONTEXT,
  context,
  propagation,
  trace,
  type Context,
  type Span,
  type SpanOptions,
  type Tracer,
  type TracerOptions,
  type TracerProvider,
} from '@opentelemetry/api';
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks';
import { registerInstrumentations } from '@opentelemetry/instrumentation';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { AlwaysOnSampler, ParentBasedSampler } from '@opentelemetry/sdk-trace-base';
import type { SpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import {
  ATTR_DEPLOYMENT_ENVIRONMENT_NAME,
  ATTR_SERVICE_INSTANCE_ID,
  ATTR_SERVICE_NAME,
  ATTR_SERVICE_NAMESPACE,
  ATTR_SERVICE_VERSION,
} from '@opentelemetry/semantic-conventions';

import type { ActiveObservation } from '../activation.js';
import type { AdapterStatus } from '../contracts/health.js';
import { ServerSdkError, createFailure } from '../errors.js';
import type { RedactionPolicy } from '../redaction/policy.js';
import { detectStartupConflicts, globalRegistrationConflict } from './conflicts.js';
import { buildInstrumentationSet } from './instrumentations.js';
import {
  PreviewGatedTraceContextPropagator,
  type PreviewEntryTokenSource,
} from './propagation.js';

/**
 * The tracer a suppressed framework scope receives.
 *
 * Its spans never record, and each one carries the context of the span that was
 * active when it started, so a span an adapter starts inside it (a database
 * query inside a framework's route span) parents to the nearest recorded span
 * instead of to one that is never exported. Nothing synthetic is created: the
 * framework span simply does not exist, and what the framework does to its own
 * span (renaming it, setting attributes) cannot reach the recorded one.
 */
class PassThroughTracer implements Tracer {
  public startSpan(_name: string, _options?: SpanOptions, parent?: Context): Span {
    return trace.wrapSpanContext(
      trace.getSpanContext(parent ?? context.active()) ?? INVALID_SPAN_CONTEXT,
    );
  }

  public startActiveSpan<F extends (span: Span) => unknown>(name: string, fn: F): ReturnType<F>;
  public startActiveSpan<F extends (span: Span) => unknown>(
    name: string,
    options: SpanOptions,
    fn: F,
  ): ReturnType<F>;
  public startActiveSpan<F extends (span: Span) => unknown>(
    name: string,
    options: SpanOptions,
    parent: Context,
    fn: F,
  ): ReturnType<F>;
  public startActiveSpan<F extends (span: Span) => unknown>(
    name: string,
    ...rest: [F] | [SpanOptions, F] | [SpanOptions, Context, F]
  ): ReturnType<F> {
    const parent = rest.length === 3 ? rest[1] : context.active();
    const fn = rest[rest.length - 1] as F;
    const span = this.startSpan(name, undefined, parent);
    return context.with(
      trace.setSpan(parent, span),
      fn as (span: Span) => ReturnType<F>,
      undefined,
      span,
    );
  }
}

/** Hands suppressed framework scopes the pass-through tracer, and every other scope the real one. */
export class ScopeSuppressingTracerProvider implements TracerProvider {
  private readonly inner: TracerProvider;
  private readonly suppressedScopes: ReadonlySet<string>;
  private readonly passThrough = new PassThroughTracer();

  public constructor(inner: TracerProvider, suppressedScopes: ReadonlySet<string>) {
    this.inner = inner;
    this.suppressedScopes = suppressedScopes;
  }

  public getTracer(name: string, version?: string, options?: TracerOptions): Tracer {
    return this.suppressedScopes.has(name)
      ? this.passThrough
      : this.inner.getTracer(name, version, options);
  }
}

/** What the SDK installed and what it must report about the adapters. */
export interface ProviderInstallation {
  readonly provider: NodeTracerProvider;
  readonly adapterStatuses: readonly AdapterStatus[];
  readonly missingRequiredAdapters: readonly string[];
  readonly uninstall: () => void;
}

/**
 * Installs the uTrace provider for one activation.
 *
 * Throws a typed failure when another owner already holds a global, when a
 * duplicate hook is present, or when a conflicting preload or non-uTrace
 * exporter is configured.
 */
export function installProvider(
  observation: ActiveObservation,
  policy: RedactionPolicy,
  processor: SpanProcessor,
  tokens: PreviewEntryTokenSource,
  environment: Readonly<Record<string, string | undefined>>,
  now: Date,
): ProviderInstallation {
  const conflicts = detectStartupConflicts(environment);
  const firstConflict = conflicts[0];
  if (firstConflict !== undefined) {
    throw new ServerSdkError(
      'Another OpenTelemetry owner is already present in this process.',
      createFailure(firstConflict, now),
    );
  }

  const resource = resourceFromAttributes({
    [ATTR_SERVICE_NAME]: observation.activation.resource.service_name,
    [ATTR_SERVICE_NAMESPACE]: observation.activation.resource.service_namespace,
    [ATTR_SERVICE_VERSION]: observation.activation.resource.service_version,
    [ATTR_SERVICE_INSTANCE_ID]: observation.identities.service_instance_id,
    [ATTR_DEPLOYMENT_ENVIRONMENT_NAME]:
      observation.activation.resource.deployment_environment_name,
  });

  const provider = new NodeTracerProvider({
    resource,
    // Parent-based always-on: the browser decides sampling for an eligible
    // trace and the server does no head sampling of roots or descendants.
    sampler: new ParentBasedSampler({ root: new AlwaysOnSampler() }),
    spanProcessors: [processor],
  });

  if (
    !trace.setGlobalTracerProvider(
      new ScopeSuppressingTracerProvider(provider, policy.suppressedScopes),
    )
  ) {
    throw new ServerSdkError(
      'A global tracer provider is already registered.',
      createFailure(globalRegistrationConflict('tracer_provider'), now),
    );
  }

  const contextManager = new AsyncLocalStorageContextManager();
  contextManager.enable();
  if (!context.setGlobalContextManager(contextManager)) {
    throw new ServerSdkError(
      'A global context manager is already registered.',
      createFailure(globalRegistrationConflict('context_manager'), now),
    );
  }

  // W3C Trace Context only. Baggage is neither extracted nor injected.
  if (!propagation.setGlobalPropagator(new PreviewGatedTraceContextPropagator(tokens))) {
    throw new ServerSdkError(
      'A global propagator is already registered.',
      createFailure(globalRegistrationConflict('propagator'), now),
    );
  }

  const set = buildInstrumentationSet(observation.service.required_adapters, policy);
  const disable = registerInstrumentations({
    instrumentations: [...set.instrumentations],
    tracerProvider: provider,
  });

  return {
    provider,
    adapterStatuses: set.statuses,
    missingRequiredAdapters: set.missingRequired,
    uninstall: (): void => {
      disable();
      contextManager.disable();
      trace.disable();
      context.disable();
      propagation.disable();
    },
  };
}
