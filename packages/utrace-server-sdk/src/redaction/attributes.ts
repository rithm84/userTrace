/**
 * Per-class attribute rules.
 *
 * Each class states the complete set of fields it may retain. Anything not
 * named here is absent from the exported span, whatever the instrumentation
 * library produced.
 */

import type { AttributeValue, Attributes } from '@opentelemetry/api';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import {
  ATTR_DB_COLLECTION_NAME,
  ATTR_DB_NAMESPACE,
  ATTR_DB_OPERATION_NAME,
  ATTR_DB_RESPONSE_STATUS_CODE,
  ATTR_DB_SYSTEM_NAME,
  ATTR_ERROR_TYPE,
  ATTR_HTTP_REQUEST_METHOD,
  ATTR_HTTP_RESPONSE_STATUS_CODE,
  ATTR_HTTP_ROUTE,
  ATTR_NETWORK_PROTOCOL_NAME,
  ATTR_NETWORK_PROTOCOL_VERSION,
  ATTR_SERVER_ADDRESS,
  ATTR_SERVER_PORT,
  ATTR_URL_SCHEME,
} from '@opentelemetry/semantic-conventions';
import {
  ATTR_HTTP_REQUEST_BODY_SIZE,
  ATTR_HTTP_RESPONSE_BODY_SIZE,
  ATTR_MESSAGING_DESTINATION_NAME,
  ATTR_MESSAGING_MESSAGE_ID,
  ATTR_MESSAGING_OPERATION_NAME,
  ATTR_MESSAGING_SYSTEM,
} from '@opentelemetry/semantic-conventions/incubating';

import { UTRACE_ATTRIBUTES } from '../constants.js';
import type { ServerSdkFailureInput } from '../errors.js';
import type { RedactionPolicy } from './policy.js';
import type { SpanClass } from './span-class.js';

/** Outcome of applying one class's rules. */
export type ClassRedaction =
  | { readonly kind: 'retain'; readonly attributes: Attributes; readonly spanName: string }
  | { readonly kind: 'exclude'; readonly reason: string }
  | { readonly kind: 'fail'; readonly failure: ServerSdkFailureInput };

const OPERATION_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,31}$/u;
const OPAQUE_MESSAGE_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/u;

function readString(span: ReadableSpan, key: string): string | undefined {
  const value = span.attributes[key];
  return typeof value === 'string' ? value : undefined;
}

function readInteger(span: ReadableSpan, key: string): number | undefined {
  const value = span.attributes[key];
  return typeof value === 'number' && Number.isInteger(value) ? value : undefined;
}

function copy(
  target: Attributes,
  key: string,
  value: AttributeValue | undefined,
): void {
  if (value !== undefined) {
    target[key] = value;
  }
}

function copyApprovedErrorType(
  span: ReadableSpan,
  policy: RedactionPolicy,
  target: Attributes,
): void {
  const errorType = readString(span, ATTR_ERROR_TYPE);
  if (errorType === undefined) {
    return;
  }
  target[ATTR_ERROR_TYPE] = policy.allowedErrorTypes.has(errorType)
    ? errorType
    : 'unregistered_error';
}

function copyHttpShared(span: ReadableSpan, policy: RedactionPolicy, target: Attributes): void {
  copy(target, ATTR_HTTP_REQUEST_METHOD, readString(span, ATTR_HTTP_REQUEST_METHOD));
  copy(target, ATTR_HTTP_RESPONSE_STATUS_CODE, readInteger(span, ATTR_HTTP_RESPONSE_STATUS_CODE));
  copy(target, ATTR_URL_SCHEME, readString(span, ATTR_URL_SCHEME));
  copy(target, ATTR_NETWORK_PROTOCOL_NAME, readString(span, ATTR_NETWORK_PROTOCOL_NAME));
  copy(target, ATTR_NETWORK_PROTOCOL_VERSION, readString(span, ATTR_NETWORK_PROTOCOL_VERSION));
  copy(target, ATTR_HTTP_REQUEST_BODY_SIZE, readInteger(span, ATTR_HTTP_REQUEST_BODY_SIZE));
  copy(target, ATTR_HTTP_RESPONSE_BODY_SIZE, readInteger(span, ATTR_HTTP_RESPONSE_BODY_SIZE));
  copyApprovedErrorType(span, policy, target);
}

function redactServer(span: ReadableSpan, policy: RedactionPolicy): ClassRedaction {
  const route = readString(span, ATTR_HTTP_ROUTE);
  const method = readString(span, ATTR_HTTP_REQUEST_METHOD);
  if (route === undefined || method === undefined) {
    return {
      kind: 'fail',
      failure: {
        code: 'route_not_registered',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { reason: 'missing_route_template', span_kind: 'server' },
      },
    };
  }
  const registered = policy.routes.find(
    (candidate) =>
      candidate.route_template === route
      && candidate.methods.some((allowed) => allowed === method),
  );
  if (registered === undefined) {
    return {
      kind: 'fail',
      failure: {
        code: 'route_not_registered',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { reason: 'unregistered_route_template', http_request_method: method },
      },
    };
  }
  const attributes: Attributes = { [ATTR_HTTP_ROUTE]: registered.route_template };
  copyHttpShared(span, policy, attributes);
  return { kind: 'retain', attributes, spanName: `${method} ${registered.route_template}` };
}

function redactClientHttp(span: ReadableSpan, policy: RedactionPolicy): ClassRedaction {
  const address = readString(span, ATTR_SERVER_ADDRESS);
  const port = readInteger(span, ATTR_SERVER_PORT);
  const method = readString(span, ATTR_HTTP_REQUEST_METHOD);
  if (address === undefined || port === undefined || method === undefined) {
    return {
      kind: 'fail',
      failure: {
        code: 'unauthorized_destination',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { reason: 'incomplete_destination' },
      },
    };
  }
  const approved = policy.allowedHttpDestinations.some(
    (target) => target.host.toLowerCase() === address.toLowerCase() && target.port === port,
  );
  if (!approved) {
    return {
      kind: 'fail',
      failure: {
        code: 'unauthorized_destination',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { server_address: address, server_port: port },
      },
    };
  }
  const attributes: Attributes = {
    [ATTR_SERVER_ADDRESS]: address,
    [ATTR_SERVER_PORT]: port,
  };
  copyHttpShared(span, policy, attributes);
  return { kind: 'retain', attributes, spanName: method };
}

function approvedDatabaseClass(
  namespace: string | undefined,
  collection: string | undefined,
  policy: RedactionPolicy,
): string | undefined {
  if (collection === undefined) {
    return undefined;
  }
  const qualified = namespace === undefined ? undefined : `${namespace}.${collection}`;
  if (qualified !== undefined && policy.allowedDatabaseClasses.has(qualified)) {
    return qualified;
  }
  return policy.allowedDatabaseClasses.has(collection) ? collection : undefined;
}

function redactDatabase(span: ReadableSpan, policy: RedactionPolicy): ClassRedaction {
  const system = readString(span, ATTR_DB_SYSTEM_NAME);
  const operation = readString(span, ATTR_DB_OPERATION_NAME);
  if (system === undefined || operation === undefined || !OPERATION_NAME_PATTERN.test(operation)) {
    return { kind: 'exclude', reason: 'database_span_without_registered_operation' };
  }
  const namespace = readString(span, ATTR_DB_NAMESPACE);
  const approvedClass = approvedDatabaseClass(
    namespace,
    readString(span, ATTR_DB_COLLECTION_NAME),
    policy,
  );
  const attributes: Attributes = {
    [ATTR_DB_SYSTEM_NAME]: system,
    [ATTR_DB_OPERATION_NAME]: operation,
  };
  if (approvedClass !== undefined) {
    attributes[ATTR_DB_COLLECTION_NAME] = approvedClass;
  }
  if (namespace !== undefined && policy.allowedDatabaseClasses.has(namespace)) {
    attributes[ATTR_DB_NAMESPACE] = namespace;
  }
  copy(attributes, ATTR_DB_RESPONSE_STATUS_CODE, readString(span, ATTR_DB_RESPONSE_STATUS_CODE));
  copyApprovedErrorType(span, policy, attributes);
  return {
    kind: 'retain',
    attributes,
    spanName: approvedClass === undefined ? operation : `${operation} ${approvedClass}`,
  };
}

function redactCache(span: ReadableSpan, policy: RedactionPolicy): ClassRedaction {
  const system = readString(span, ATTR_DB_SYSTEM_NAME);
  const operation = readString(span, ATTR_DB_OPERATION_NAME);
  if (system === undefined || operation === undefined || !OPERATION_NAME_PATTERN.test(operation)) {
    return { kind: 'exclude', reason: 'cache_span_without_registered_operation' };
  }
  const attributes: Attributes = {
    [ATTR_DB_SYSTEM_NAME]: system,
    [ATTR_DB_OPERATION_NAME]: operation,
  };
  const cacheClass = readString(span, UTRACE_ATTRIBUTES.cacheClass);
  if (cacheClass !== undefined && policy.allowedCacheClasses.has(cacheClass)) {
    attributes[UTRACE_ATTRIBUTES.cacheClass] = cacheClass;
  }
  const hit = span.attributes[UTRACE_ATTRIBUTES.cacheHit];
  if (typeof hit === 'boolean') {
    attributes[UTRACE_ATTRIBUTES.cacheHit] = hit;
  }
  copyApprovedErrorType(span, policy, attributes);
  return {
    kind: 'retain',
    attributes,
    spanName: cacheClass === undefined ? operation : `${operation} ${cacheClass}`,
  };
}

function redactQueue(span: ReadableSpan, policy: RedactionPolicy): ClassRedaction {
  const system = readString(span, ATTR_MESSAGING_SYSTEM);
  const operation = readString(span, ATTR_MESSAGING_OPERATION_NAME);
  const destination = readString(span, ATTR_MESSAGING_DESTINATION_NAME);
  if (system === undefined || operation === undefined || destination === undefined) {
    return { kind: 'exclude', reason: 'queue_span_without_registered_destination' };
  }
  if (!policy.allowedQueueDestinations.has(destination)) {
    return {
      kind: 'fail',
      failure: {
        code: 'unauthorized_destination',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { messaging_destination: 'unregistered', messaging_system: system },
      },
    };
  }
  const attributes: Attributes = {
    [ATTR_MESSAGING_SYSTEM]: system,
    [ATTR_MESSAGING_OPERATION_NAME]: operation,
    [ATTR_MESSAGING_DESTINATION_NAME]: destination,
  };
  const messageId = readString(span, ATTR_MESSAGING_MESSAGE_ID);
  if (messageId !== undefined && OPAQUE_MESSAGE_ID_PATTERN.test(messageId)) {
    attributes[ATTR_MESSAGING_MESSAGE_ID] = messageId;
  }
  copyApprovedErrorType(span, policy, attributes);
  return { kind: 'retain', attributes, spanName: `${operation} ${destination}` };
}

function redactFrameworkHandler(span: ReadableSpan, policy: RedactionPolicy): ClassRedaction {
  const route = readString(span, ATTR_HTTP_ROUTE);
  if (route === undefined) {
    return { kind: 'exclude', reason: 'framework_span_without_route' };
  }
  const registered = policy.routes.some((candidate) => candidate.route_template === route);
  if (!registered) {
    return { kind: 'exclude', reason: 'framework_span_outside_registered_routes' };
  }
  return {
    kind: 'retain',
    attributes: { [ATTR_HTTP_ROUTE]: route },
    spanName: `handler ${route}`,
  };
}

function redactProductOperation(span: ReadableSpan, policy: RedactionPolicy): ClassRedaction {
  const operation = readString(span, UTRACE_ATTRIBUTES.operationName);
  if (operation === undefined || !policy.registeredOperations.has(operation)) {
    return {
      kind: 'fail',
      failure: {
        code: 'redaction_or_schema_failure',
        retry: 'never',
        readiness_consequence: 'withdraw_readiness',
        detail: { reason: 'unregistered_product_operation' },
      },
    };
  }
  const attributes: Attributes = {};
  copyApprovedErrorType(span, policy, attributes);
  return { kind: 'retain', attributes, spanName: operation };
}

/** Applies the rules for `spanClass` and returns the retained fields. */
export function redactByClass(
  span: ReadableSpan,
  spanClass: SpanClass,
  policy: RedactionPolicy,
): ClassRedaction {
  switch (spanClass) {
    case 'server':
      return redactServer(span, policy);
    case 'client_http':
      return redactClientHttp(span, policy);
    case 'database':
      return redactDatabase(span, policy);
    case 'cache':
      return redactCache(span, policy);
    case 'queue_producer':
    case 'queue_consumer':
      return redactQueue(span, policy);
    case 'framework_handler':
      return redactFrameworkHandler(span, policy);
    case 'product_operation':
      return redactProductOperation(span, policy);
    default:
      return { kind: 'exclude', reason: 'unclassified_span' };
  }
}
