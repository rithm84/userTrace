/**
 * Builds `ReadableSpan` values for redaction tests.
 *
 * The redaction pipeline is a pure function over finished spans, so these
 * fixtures exercise every class rule without needing a framework that happens
 * to produce that shape.
 */

import { SpanStatusCode } from '@opentelemetry/api';
import type { Attributes, SpanContext, SpanKind } from '@opentelemetry/api';
import { resourceFromAttributes } from '@opentelemetry/resources';
import type { ReadableSpan, TimedEvent } from '@opentelemetry/sdk-trace-base';

/** Fields a test overrides on a synthesized span. */
export interface SpanFixture {
  readonly name: string;
  readonly kind: SpanKind;
  readonly scopeName: string;
  readonly attributes: Attributes;
  readonly events?: readonly TimedEvent[];
  readonly statusMessage?: string;
  readonly traceId?: string;
  readonly remoteParent?: boolean;
}

const RESOURCE = resourceFromAttributes({ 'service.name': 'shopalytics-api' });

/** Builds one finished span. */
export function readableSpan(fixture: SpanFixture): ReadableSpan {
  const spanContext: SpanContext = {
    traceId: fixture.traceId ?? '0123456789abcdef0123456789abcdef',
    spanId: 'abcdef0123456789',
    traceFlags: 1,
  };
  return {
    name: fixture.name,
    kind: fixture.kind,
    spanContext: (): SpanContext => spanContext,
    ...(fixture.remoteParent === true
      ? {
          parentSpanContext: {
            traceId: spanContext.traceId,
            spanId: '1111111111111111',
            traceFlags: 1,
            isRemote: true,
          },
        }
      : {}),
    startTime: [1_700_000_000, 0],
    endTime: [1_700_000_000, 5_000_000],
    status: {
      code: SpanStatusCode.UNSET,
      ...(fixture.statusMessage === undefined ? {} : { message: fixture.statusMessage }),
    },
    attributes: fixture.attributes,
    links: [],
    events: [...(fixture.events ?? [])],
    duration: [0, 5_000_000],
    ended: true,
    resource: RESOURCE,
    instrumentationScope: { name: fixture.scopeName, version: '1.0.0' },
    droppedAttributesCount: 0,
    droppedEventsCount: 0,
    droppedLinksCount: 0,
  };
}
