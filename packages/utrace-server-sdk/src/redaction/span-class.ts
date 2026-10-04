/**
 * Span classification.
 *
 * Every finished span is placed in exactly one uTrace class before the
 * attribute rules for that class run. A span whose instrumentation scope is not
 * registered for this activation is never exported.
 */

import { SpanKind } from '@opentelemetry/api';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { ATTR_DB_SYSTEM_NAME, ATTR_HTTP_REQUEST_METHOD } from '@opentelemetry/semantic-conventions';
import { ATTR_MESSAGING_SYSTEM } from '@opentelemetry/semantic-conventions/incubating';

import { ANNOTATION_SCOPE_NAME, UTRACE_ATTRIBUTES } from '../constants.js';
import type { RedactionPolicy } from './policy.js';

/** Redaction classes, each with its own permitted field set. */
export type SpanClass =
  | 'server'
  | 'client_http'
  | 'database'
  | 'cache'
  | 'queue_producer'
  | 'queue_consumer'
  | 'framework_handler'
  | 'product_operation';

/** Classification outcome. */
export type SpanClassification =
  | { readonly kind: 'classified'; readonly spanClass: SpanClass }
  | { readonly kind: 'ineligible_scope'; readonly scope: string }
  | { readonly kind: 'unclassified' };

const CACHE_SYSTEMS = new Set(['redis', 'valkey', 'memcached']);

function hasAttribute(span: ReadableSpan, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(span.attributes, key);
}

/** Classifies one finished span against the activation's policy. */
export function classifySpan(span: ReadableSpan, policy: RedactionPolicy): SpanClassification {
  const scope = span.instrumentationScope.name;
  if (policy.suppressedScopes.has(scope)) {
    // The framework's own span duplicates the authoritative one the adapter
    // produced, so it is dropped rather than reported as a manifest gap.
    return { kind: 'unclassified' };
  }
  if (!policy.eligibleScopes.has(scope)) {
    return { kind: 'ineligible_scope', scope };
  }

  switch (span.kind) {
    case SpanKind.SERVER:
      return { kind: 'classified', spanClass: 'server' };
    case SpanKind.PRODUCER:
      return { kind: 'classified', spanClass: 'queue_producer' };
    case SpanKind.CONSUMER:
      return { kind: 'classified', spanClass: 'queue_consumer' };
    case SpanKind.CLIENT: {
      const system = span.attributes[ATTR_DB_SYSTEM_NAME];
      if (typeof system === 'string') {
        return {
          kind: 'classified',
          spanClass: CACHE_SYSTEMS.has(system) ? 'cache' : 'database',
        };
      }
      if (hasAttribute(span, UTRACE_ATTRIBUTES.cacheClass)) {
        return { kind: 'classified', spanClass: 'cache' };
      }
      if (hasAttribute(span, ATTR_MESSAGING_SYSTEM)) {
        return { kind: 'classified', spanClass: 'queue_producer' };
      }
      if (hasAttribute(span, ATTR_HTTP_REQUEST_METHOD)) {
        return { kind: 'classified', spanClass: 'client_http' };
      }
      return { kind: 'unclassified' };
    }
    case SpanKind.INTERNAL:
      return {
        kind: 'classified',
        spanClass: scope === ANNOTATION_SCOPE_NAME ? 'product_operation' : 'framework_handler',
      };
    default:
      return { kind: 'unclassified' };
  }
}
