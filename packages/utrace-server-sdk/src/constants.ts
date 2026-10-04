/** Fixed, packaged constants. None of these is configurable by a client. */

/** Published SDK version. Advertised during activation and in health reports. */
export const SDK_VERSION = '0.1.0';

/** Contract major this build speaks on the control and ingestion edges. */
export const CONTRACT_MAJOR = 1;

/** Capabilities advertised during the activation handshake. */
export const SDK_CAPABILITIES = [
  'otlp_http_protobuf_gzip',
  'adapter.node_http',
  'adapter.undici',
  'adapter.pg',
  'adapter.express',
  'adapter.fastify',
  'adapter.next_server',
  'propagation_probe',
  'typed_annotations',
] as const;

/**
 * Header carrying the activation-scoped preview entry token.
 *
 * The preview front door places it on requests that have entered the
 * authenticated, active prepared preview, and the SDK re-injects it toward
 * manifest-approved propagation targets. A remote `traceparent` is accepted as
 * a parent only when this header is present and matches the activation.
 */
export const PREVIEW_ENTRY_HEADER = 'x-utrace-preview-entry';

/** Prefix of the export batch envelope headers. */
export const EXPORT_ENVELOPE_HEADER_PREFIX = 'x-utrace-';

/** Instrumentation scope used by the SDK's own annotation tracer. */
export const ANNOTATION_SCOPE_NAME = '@utrace/server-sdk';

/**
 * Fixed platform ceilings. The activation handshake supplies operational limits
 * beneath these; a handshake value above a ceiling is an invalid activation, not
 * a value to clamp.
 */
export const PLATFORM_CEILINGS = {
  maxBatchSpans: 2048,
  maxBatchBytes: 4 * 1024 * 1024,
  maxQueueBatches: 1024,
  maxQueueSpans: 65_536,
  maxInFlightBatches: 8,
  maxExportIntervalMillis: 30_000,
  maxAcknowledgementLagMillis: 300_000,
  maxFlushTimeoutMillis: 30_000,
  maxBackoffMillis: 60_000,
  maxRetryAttempts: 12,
} as const;

/**
 * Attribute keys the SDK never exports, whatever produced them. This is the
 * non-overridable platform exclusion layer; no client policy can re-enable one.
 */
export const NEVER_COLLECT_ATTRIBUTE_KEYS: readonly string[] = [
  'http.request.header',
  'http.response.header',
  'http.request.body',
  'http.response.body',
  'url.query',
  'url.full',
  'url.path',
  'url.fragment',
  'user_agent.original',
  'user_agent.synthetic.type',
  'client.address',
  'client.port',
  'network.peer.address',
  'network.peer.port',
  'network.local.address',
  'network.local.port',
  'db.query.text',
  'db.query.parameter',
  'db.statement',
  'db.connection_string',
  'db.user',
  'db.query.summary',
  'messaging.message.body',
  'messaging.message.body.size',
  'enduser.id',
  'enduser.role',
  'enduser.scope',
  'code.stacktrace',
  'exception.stacktrace',
  'exception.message',
];

/** Prefixes that are never exported, matched against the whole attribute key. */
export const NEVER_COLLECT_ATTRIBUTE_PREFIXES: readonly string[] = [
  'http.request.header.',
  'http.response.header.',
  'db.query.parameter.',
  'messaging.message.header.',
  'aws.',
  'gcp.',
  'azure.',
];

/** Versioned uTrace attribute namespace for registered product concepts. */
export const UTRACE_ATTRIBUTE_PREFIX = 'utrace.';

/** Registered product concept attribute keys. */
export const UTRACE_ATTRIBUTES = {
  operationName: 'utrace.operation.name',
  operationDefinitionRef: 'utrace.operation.definition_ref',
  milestoneName: 'utrace.milestone.name',
  milestoneDefinitionRef: 'utrace.milestone.definition_ref',
  entityClass: 'utrace.entity.class',
  entityDefinitionRef: 'utrace.entity.definition_ref',
  outcomeName: 'utrace.outcome.name',
  outcomeDefinitionRef: 'utrace.outcome.definition_ref',
  errorFingerprint: 'utrace.error.fingerprint',
  errorProductArea: 'utrace.error.product_area',
  errorFrames: 'utrace.error.frames',
  errorMessage: 'utrace.error.message',
  cacheClass: 'utrace.cache.class',
  cacheHit: 'utrace.cache.hit',
  valuePrefix: 'utrace.value.',
} as const;

/** Event name carrying redacted backend exception evidence. */
export const UTRACE_ERROR_EVENT_NAME = 'utrace.error';

/**
 * Instrumentation scopes eligible for export, keyed by the manifest adapter
 * that installs them. A span from any other scope is not product evidence.
 */
export const ADAPTER_SCOPE_NAMES = {
  node_http: '@opentelemetry/instrumentation-http',
  undici: '@opentelemetry/instrumentation-undici',
  pg: '@opentelemetry/instrumentation-pg',
  express: '@opentelemetry/instrumentation-express',
  fastify: '@fastify/otel',
  next_server: '@opentelemetry/instrumentation-http',
} as const;

/**
 * Scopes a framework emits under its own name once a real provider exists, and
 * which duplicate work the adapter's own span already describes.
 *
 * Serverful Next.js instruments itself: with a global tracer provider
 * registered it starts spans under the `next.js` scope, and it offers no way
 * to turn that off. Those spans repeat the request the authoritative HTTP
 * `SERVER` span already covers and carry no registered route, so they are
 * framework churn rather than product evidence and rather than a manifest gap.
 * When the manifest requires the adapter, its framework scope is suppressed:
 * the framework's tracer never records, so the spans an adapter starts inside a
 * framework span parent to the authoritative one, and readiness is unaffected.
 */
export const ADAPTER_SUPPRESSED_SCOPE_NAMES: Readonly<Record<string, readonly string[]>> = {
  next_server: ['next.js'],
};
