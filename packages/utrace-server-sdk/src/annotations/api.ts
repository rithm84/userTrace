/**
 * The typed, registration-based annotation surface.
 *
 * There is no arbitrary `setAttributes` and no free-form event API. Every call
 * instantiates a definition compiled into the signed release manifest; runtime
 * code cannot create a definition or declare a value safe.
 *
 * Without a prepared-preview activation every call is inert. Product logic must
 * not depend on an annotation call's result, and an annotation never throws
 * into the client's business request path.
 */

import { ROOT_CONTEXT, SpanKind, context, trace } from '@opentelemetry/api';
import type { AttributeValue, Span, Tracer } from '@opentelemetry/api';

import { ANNOTATION_SCOPE_NAME, SDK_VERSION, UTRACE_ATTRIBUTES } from '../constants.js';
import type { AnnotationDefinition } from '../contracts/manifest.js';
import type { ServerSdkFailureInput } from '../errors.js';
import { MILESTONE_EVENT_NAME } from '../redaction/span.js';

/** What an active runtime supplies to the annotation surface. */
export interface AnnotationRuntime {
  readonly tracer: Tracer;
  readonly definitionsByName: ReadonlyMap<string, AnnotationDefinition>;
  readonly registeredOperations: ReadonlySet<string>;
  reportFailure(failure: ServerSdkFailureInput): void;
  isObservationWindowActive(): boolean;
}

let runtime: AnnotationRuntime | undefined;

/** Binds the annotation surface to an active observation. */
export function bindAnnotationRuntime(active: AnnotationRuntime): void {
  runtime = active;
}

/** Returns the annotation surface to its dormant, inert state. */
export function clearAnnotationRuntime(): void {
  runtime = undefined;
}

/** The instrumentation scope name and version uTrace annotations use. */
export const ANNOTATION_SCOPE = { name: ANNOTATION_SCOPE_NAME, version: SDK_VERSION } as const;

function activeRuntime(): AnnotationRuntime | undefined {
  if (runtime === undefined) {
    return undefined;
  }
  return runtime.isObservationWindowActive() ? runtime : undefined;
}

function definition(
  active: AnnotationRuntime,
  name: string,
  kind: AnnotationDefinition['kind'],
): AnnotationDefinition | undefined {
  const found = active.definitionsByName.get(name);
  if (found === undefined || found.kind !== kind) {
    active.reportFailure({
      code: 'redaction_or_schema_failure',
      retry: 'never',
      readiness_consequence: 'withdraw_readiness',
      detail: { reason: 'unregistered_annotation', annotation_kind: kind },
    });
    return undefined;
  }
  return found;
}

function valueWithinOutputClass(
  found: AnnotationDefinition,
  value: AttributeValue,
): boolean {
  switch (found.output_class) {
    case 'metadata_only':
      return value === true;
    case 'registered_reference':
      return value === found.name;
    case 'bounded_enum':
      return typeof value === 'string' && (found.permitted_values ?? []).includes(value);
    case 'bounded_integer':
      return (
        typeof value === 'number'
        && Number.isInteger(value)
        && found.value_bounds !== undefined
        && value >= found.value_bounds.minimum
        && value <= found.value_bounds.maximum
      );
    default:
      return false;
  }
}

function currentSpan(): Span | undefined {
  return trace.getSpan(context.active());
}

/** Runs `body` inside a registered product operation span. */
export function withProductOperation<T>(name: string, body: () => T): T {
  const active = activeRuntime();
  if (active === undefined) {
    return body();
  }
  const found = definition(active, name, 'product_operation');
  if (found === undefined || !active.registeredOperations.has(name)) {
    return body();
  }
  const span = active.tracer.startSpan(name, {
    kind: SpanKind.INTERNAL,
    attributes: {
      [UTRACE_ATTRIBUTES.operationName]: found.name,
      [UTRACE_ATTRIBUTES.operationDefinitionRef]: found.definition_ref,
    },
  });
  try {
    return context.with(trace.setSpan(context.active(), span), body);
  } finally {
    span.end();
  }
}

/**
 * Runs `body` inside a registered background root span.
 *
 * A root background operation is eligible only while the observation window is
 * active; unregistered cron and maintenance work produces nothing.
 */
export function withBackgroundRoot<T>(name: string, body: () => T): T {
  const active = activeRuntime();
  if (active === undefined) {
    return body();
  }
  const found = definition(active, name, 'background_root');
  if (found === undefined || !active.registeredOperations.has(name)) {
    return body();
  }
  const span = active.tracer.startSpan(
    name,
    {
      kind: SpanKind.INTERNAL,
      attributes: {
        [UTRACE_ATTRIBUTES.operationName]: found.name,
        [UTRACE_ATTRIBUTES.operationDefinitionRef]: found.definition_ref,
      },
    },
    ROOT_CONTEXT,
  );
  try {
    return context.with(trace.setSpan(ROOT_CONTEXT, span), body);
  } finally {
    span.end();
  }
}

/** Records a registered workflow milestone on the active span. */
export function recordWorkflowMilestone(name: string): void {
  const active = activeRuntime();
  if (active === undefined) {
    return;
  }
  const found = definition(active, name, 'workflow_milestone');
  if (found === undefined) {
    return;
  }
  currentSpan()?.addEvent(MILESTONE_EVENT_NAME, {
    [UTRACE_ATTRIBUTES.milestoneName]: found.name,
    [UTRACE_ATTRIBUTES.milestoneDefinitionRef]: found.definition_ref,
  });
}

/** Attaches an approved product entity class to the active span. */
export function attachEntityClass(name: string): void {
  const active = activeRuntime();
  if (active === undefined) {
    return;
  }
  const found = definition(active, name, 'entity_class');
  if (found === undefined) {
    return;
  }
  const span = currentSpan();
  span?.setAttribute(UTRACE_ATTRIBUTES.entityClass, found.name);
  span?.setAttribute(UTRACE_ATTRIBUTES.entityDefinitionRef, found.definition_ref);
}

/** Attaches a bounded safe value declared by a registered definition. */
export function attachSafeValue(name: string, value: AttributeValue): void {
  const active = activeRuntime();
  if (active === undefined) {
    return;
  }
  const found = definition(active, name, 'safe_value');
  if (found === undefined) {
    return;
  }
  if (!valueWithinOutputClass(found, value)) {
    active.reportFailure({
      code: 'redaction_or_schema_failure',
      retry: 'never',
      readiness_consequence: 'withdraw_readiness',
      detail: { reason: 'safe_value_outside_output_class', definition_ref: found.definition_ref },
    });
    return;
  }
  currentSpan()?.setAttribute(`${UTRACE_ATTRIBUTES.valuePrefix}${found.name}`, value);
}

/** Classifies an expected product outcome on the active span. */
export function classifyExpectedOutcome(name: string): void {
  const active = activeRuntime();
  if (active === undefined) {
    return;
  }
  const found = definition(active, name, 'expected_outcome');
  if (found === undefined) {
    return;
  }
  const span = currentSpan();
  span?.setAttribute(UTRACE_ATTRIBUTES.outcomeName, found.name);
  span?.setAttribute(UTRACE_ATTRIBUTES.outcomeDefinitionRef, found.definition_ref);
}
