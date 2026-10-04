/**
 * The redaction pipeline for one finished span.
 *
 * Layers run in the contract's order — non-overridable platform exclusions,
 * authenticated activation scope, signed client policy, registered annotation
 * definition, per-value evaluation, defensive sensitive-data detection,
 * normalization and cardinality bounds, then schema validation — and a later
 * layer may only restrict an earlier result. The input span is never mutated:
 * a new, redacted span is built and only that one can reach the export buffer.
 */

import type { AttributeValue, Attributes } from '@opentelemetry/api';
import type { ReadableSpan, TimedEvent } from '@opentelemetry/sdk-trace-base';
import { ATTR_EXCEPTION_TYPE, ATTR_EXCEPTION_STACKTRACE } from '@opentelemetry/semantic-conventions';

import { UTRACE_ATTRIBUTES, UTRACE_ERROR_EVENT_NAME } from '../constants.js';
import type { ProbeHop } from '../contracts/activation.js';
import type { ServerSdkFailureInput } from '../errors.js';
import { redactByClass } from './attributes.js';
import { buildErrorEvidence } from './error-evidence.js';
import { isPlatformExcluded } from './platform-exclusions.js';
import type { RedactionPolicy } from './policy.js';
import { detectSensitiveValue } from './sensitive.js';
import { classifySpan, type SpanClass } from './span-class.js';
import { redactUtraceAttributes } from './utrace-attributes.js';

/** Event name a typed workflow milestone annotation produces. */
export const MILESTONE_EVENT_NAME = 'utrace.milestone';

/** What the pipeline decided about one span. */
export type RedactionOutcome =
  | { readonly kind: 'export'; readonly span: ReadableSpan; readonly hop: ProbeHop | undefined }
  | { readonly kind: 'exclude'; readonly reason: string }
  | { readonly kind: 'fail'; readonly failure: ServerSdkFailureInput };

const HOP_BY_CLASS: Readonly<Record<SpanClass, ProbeHop | undefined>> = {
  server: 'server',
  client_http: 'downstream_http',
  database: 'database',
  cache: 'cache',
  queue_producer: 'queue',
  queue_consumer: 'queue',
  framework_handler: undefined,
  product_operation: undefined,
};

function normalizeValue(value: AttributeValue, maxLength: number): AttributeValue {
  if (typeof value === 'string') {
    return value.length > maxLength ? value.slice(0, maxLength) : value;
  }
  if (Array.isArray(value)) {
    return value.map((item) =>
      typeof item === 'string' && item.length > maxLength ? item.slice(0, maxLength) : item,
    ) as AttributeValue;
  }
  return value;
}

function scanForSensitiveValues(attributes: Attributes): string | undefined {
  for (const [key, value] of Object.entries(attributes)) {
    if (typeof value === 'string') {
      const detector = detectSensitiveValue(value);
      if (detector !== undefined) {
        return `${key}:${detector}`;
      }
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        if (typeof item === 'string') {
          const detector = detectSensitiveValue(item);
          if (detector !== undefined) {
            return `${key}:${detector}`;
          }
        }
      }
    }
  }
  return undefined;
}

function redactEvents(
  span: ReadableSpan,
  policy: RedactionPolicy,
): { readonly events: TimedEvent[] } | { readonly failure: ServerSdkFailureInput } {
  const events: TimedEvent[] = [];
  for (const event of span.events) {
    if (event.name === 'exception') {
      const attributes = event.attributes ?? {};
      const rawType = attributes[ATTR_EXCEPTION_TYPE];
      const rawStack = attributes[ATTR_EXCEPTION_STACKTRACE];
      const evidence = buildErrorEvidence(
        typeof rawType === 'string' ? rawType : undefined,
        typeof rawStack === 'string' ? rawStack : undefined,
        policy,
      );
      const eventAttributes: Attributes = {
        'error.type': evidence.errorType,
        [UTRACE_ATTRIBUTES.errorFingerprint]: evidence.fingerprint,
        [UTRACE_ATTRIBUTES.errorProductArea]: evidence.productArea,
        [UTRACE_ATTRIBUTES.errorFrames]: [...evidence.frames],
      };
      if (evidence.sanitizedMessage !== undefined) {
        eventAttributes[UTRACE_ATTRIBUTES.errorMessage] = evidence.sanitizedMessage;
      }
      events.push({ name: UTRACE_ERROR_EVENT_NAME, time: event.time, attributes: eventAttributes });
      continue;
    }
    if (event.name === MILESTONE_EVENT_NAME) {
      const outcome = redactUtraceAttributes(event.attributes ?? {}, policy);
      if (outcome.kind === 'fail') {
        return { failure: outcome.failure };
      }
      events.push({ name: MILESTONE_EVENT_NAME, time: event.time, attributes: outcome.attributes });
      continue;
    }
    // Every other event is free-form and is not part of the uTrace surface.
  }
  return { events };
}

function buildRedactedSpan(
  source: ReadableSpan,
  name: string,
  attributes: Attributes,
  events: TimedEvent[],
): ReadableSpan {
  return {
    name,
    kind: source.kind,
    spanContext: (): ReturnType<ReadableSpan['spanContext']> => source.spanContext(),
    ...(source.parentSpanContext === undefined
      ? {}
      : { parentSpanContext: source.parentSpanContext }),
    startTime: source.startTime,
    endTime: source.endTime,
    // A status message can carry a raw exception string, so only the code survives.
    status: { code: source.status.code },
    attributes,
    links: source.links.map((link) => ({ context: link.context })),
    events,
    duration: source.duration,
    ended: source.ended,
    resource: source.resource,
    instrumentationScope: source.instrumentationScope,
    droppedAttributesCount: source.droppedAttributesCount,
    droppedEventsCount: source.droppedEventsCount,
    droppedLinksCount: source.droppedLinksCount,
  };
}

/**
 * Runs the pipeline over a finished span and returns the span that may be
 * buffered, the reason it is excluded, or the contract failure it triggered.
 */
export function redactSpan(span: ReadableSpan, policy: RedactionPolicy): RedactionOutcome {
  const classification = classifySpan(span, policy);
  if (classification.kind === 'ineligible_scope') {
    return {
      kind: 'fail',
      failure: {
        code: 'redaction_or_schema_failure',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { reason: 'ineligible_instrumentation_scope', scope: classification.scope },
      },
    };
  }
  if (classification.kind === 'unclassified') {
    return { kind: 'exclude', reason: 'unclassified_span' };
  }

  const classOutcome = redactByClass(span, classification.spanClass, policy);
  if (classOutcome.kind !== 'retain') {
    return classOutcome;
  }

  const utraceOutcome = redactUtraceAttributes(span.attributes, policy);
  if (utraceOutcome.kind === 'fail') {
    return { kind: 'fail', failure: utraceOutcome.failure };
  }

  const merged: Attributes = { ...classOutcome.attributes, ...utraceOutcome.attributes };

  for (const key of Object.keys(merged)) {
    if (isPlatformExcluded(key)) {
      // A platform exclusion outranks every other layer, including a class rule.
      // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
      delete merged[key];
    }
  }

  const sensitiveHit = scanForSensitiveValues(merged);
  if (sensitiveHit !== undefined) {
    return {
      kind: 'fail',
      failure: {
        code: 'redaction_or_schema_failure',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { reason: 'sensitive_value_detected', detector: sensitiveHit },
      },
    };
  }

  const normalized: Attributes = {};
  for (const [key, value] of Object.entries(merged)) {
    if (value !== undefined) {
      normalized[key] = normalizeValue(value, policy.maxAttributeValueLength);
    }
  }

  if (Object.keys(normalized).length > policy.maxSpanAttributeCount) {
    return {
      kind: 'fail',
      failure: {
        code: 'redaction_or_schema_failure',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: {
          reason: 'attribute_count_above_bound',
          attribute_count: Object.keys(normalized).length,
          bound: policy.maxSpanAttributeCount,
        },
      },
    };
  }

  const eventOutcome = redactEvents(span, policy);
  if ('failure' in eventOutcome) {
    return { kind: 'fail', failure: eventOutcome.failure };
  }

  return {
    kind: 'export',
    span: buildRedactedSpan(span, classOutcome.spanName, normalized, eventOutcome.events),
    hop: HOP_BY_CLASS[classification.spanClass],
  };
}
