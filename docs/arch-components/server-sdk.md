# Server SDK

[Back to uTrace architecture](../ARCHITECTURE.md)

The mandatory server SDK supplies the backend half of a uTrace product trace: a
uTrace-owned OpenTelemetry distribution, not a bridge to the client's
production observability system.

The following diagram shows how the preview-only Server SDK produces correlated, redacted spans:

![One uTrace provider redacts, queues, and exports the preview's spans to ingestion and keeps a control channel to the control plane; the client's own OpenTelemetry setup is disabled.](../diagrams/svg/server-sdk.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Support and compatibility

- The first implementation supports Node.js, and TypeScript compiled to it, on
  the current Active LTS and immediately previous Maintenance LTS releases.
  CommonJS and ESM work only through bootstrap paths that initialize
  instrumentation before application modules.
- Supported deployments are long-running Node.js processes and containers,
  including serverful Next.js. Edge runtimes, ephemeral serverless functions,
  Bun, Deno, and non-Node services are initially unsupported.
- The framework-neutral core has a versioned compatibility matrix of exact
  tested Node, framework, HTTP client, database, cache, queue, and RPC library
  ranges. Initial adapters cover Node HTTP/HTTPS, Undici, serverful Next.js,
  Express, Fastify, and PostgreSQL. Cache and queue adapters are not shipped; a
  client release needing one has it built and conformance-tested while that
  client is configured.
- Every library on a client release's required product path needs a tested
  adapter or conformance fixtures proving the complete span, propagation,
  redaction, and readiness contract; a framework abstraction alone is not
  support.
- An unsupported required runtime or library blocks configuration of that
  release; uTrace never silently reduces the server trace to whatever works.
- Another language or deployment model needs the same contract and conformance
  suite, not a best-effort port of the Node SDK.

## Preview-only ownership and startup

- Of the [client configuration](./client-configuration.md#sdk-source-integration)
  startup profiles, only preview includes and loads the server SDK runtime;
  production gets no uTrace preload, activation credential, instrumentation
  hooks, control connection, or exporter.
- The typed annotation package may stay linked in production code but,
  without a prepared-preview activation, installs no hooks, creates no
  provider, opens no connection, and exports nothing; product logic cannot
  depend on an annotation call's result.
- In a prepared preview, uTrace starts before application code and owns the
  sole OpenTelemetry provider, context manager, propagator, sampler,
  auto-instrumentation set, processors, and exporter; the client's own
  OpenTelemetry bootstrap, auto-instrumentation, and exporters are disabled.
- A second provider, duplicate instrumentation hook, conflicting preload, or
  non-uTrace exporter is an activation failure; the SDK never chooses one or
  merges their telemetry.
- Approved manual spans from the standard OpenTelemetry API may flow through
  the uTrace provider, but only instrumentation scopes registered in the signed
  release manifest are exportable, and their attributes still pass the uTrace
  schema and redaction pipeline.
- Initialization or configuration failure keeps the preview from becoming
  ready. A later telemetry failure never throws into, delays, or fails the
  client's business request; it withdraws uTrace readiness, reports the exact
  failure, and makes the control plane pause product-specific probing.

## Configuration, identity, and credentials

### Signed release manifest

- Client configuration produces a signed server instrumentation manifest per
  client release, declaring required services and adapters, service
  identities, registered routes and operations, domain annotations, permitted
  downstream propagation targets, redaction rules, policy versions, and schema
  versions. Application code cannot broaden it at runtime.
- The SDK takes the manifest only from the signed activation record, verified against a trust anchor supplied with
  its environment, and never from the repository or a local file, so the
  instrumented preview cannot disagree with the release the Product Signal
  pinned.
- Configuration is fixed during client configuration and release preparation:
  the SDK never asks a client or customer for a runtime decision, and an
  interview cannot weaken instrumentation or redaction to recover readiness.

### Activation and the control channel

The following diagram shows one service process's activation and control
channel:

![The SDK trades a bootstrap credential for an activation record, then exports, rotates credentials, reports health, and obeys control commands.](../diagrams/svg/server-sdk-activation.svg)

- The bootstrap credential, injected at
  [claim time](./preview-runtime-and-seeding.md#allocation-lifecycle), is never
  stored in the repository or client configuration.
- Activation, not the credential, is the one-time step: one activation per
  SDK-minted stream prevents replay.
- The control plane, not the client, sets every activation-record value per
  activation, and the runtime adopts a remote trace parent only after seeing
  the record's front-door entry token.

### Activation identity

- One observation activation binds one service process's server stream to the
  exact `client_id`,
  `client_user_id`, `customer_account_id`, `session_id`,
  `preview_environment_id`, `preview_runtime_allocation_id`, and optional
  `preview_version_id`. A new session, validation encounter, Preview Version,
  or runtime allocation needs a new activation.
- Standard OpenTelemetry resource attributes describe the process and
  deployment, at minimum `service.name`, `service.namespace`,
  `service.version`, `service.instance.id`, and `deployment.environment.name`;
  dynamic user and session identity never appears as resource identity.
- Repeated activation identities may be encoded once in an authenticated export
  batch header but stay logically part of every exported span, and the
  credential and ingestion-side activation record must agree with them.
  Knowing an environment, session, trace, or span ID never authenticates a
  sender.

## Automatic instrumentation and annotation surface

### Automatic coverage

- The SDK installs only the explicit instrumentation set the signed release
  manifest requires, never an unrestricted auto-instrumentation bundle
  exporting every span it creates.
- Automatic coverage includes inbound HTTP route, method, status, and duration;
  one authoritative server span per request; meaningful framework handler or
  controller work; outbound HTTP and RPC calls; database operations; and
  backend exception classifications, plus cache and queue operations given a
  client-specific adapter.
- Layered libraries must not create duplicate spans for one operation:
  framework spans stay only when they describe distinct work beneath the
  authoritative server span, and middleware churn and per-function tracing are
  not product evidence.
- Health checks, static assets, SDK control traffic, exporter traffic, and
  other registered operational noise stay out of the product trace, but
  exclusion cannot remove a required product route or its causal descendants.
- The SDK exports traces and typed exception events, not the client's general
  logs, metrics, stdout, or stderr. An exception belongs to its active span;
  operational SDK health belongs to the authenticated control path.

### Annotation API

- The typed, registration-based annotation API may create a registered product
  operation, record a registered workflow milestone, attach an approved
  product entity class or bounded safe value, classify an expected product
  outcome, or start a registered background root during an active observation
  window.
- It has no arbitrary `setAttributes` or free-form event API. Unknown
  operations, fields, values, and instrumentation scopes fail the SDK contract
  and withdraw readiness instead of becoming generic telemetry.
- Annotation definitions and their permitted output classes are compiled into
  the signed release manifest: runtime code may instantiate a definition with
  valid values but cannot create one or declare a value safe.

## Span model and semantic schema

- One trace represents one causal product operation, never the whole
  interview. An inbound browser request produces one `SERVER` span beneath the
  browser `CLIENT` span, with internal, database, cache, queue, RPC, and
  downstream client spans beneath it as the work requires.
- Spans follow the stable OpenTelemetry semantic conventions of their pinned
  schema version, and the activation records the semantic-convention and
  uTrace schema versions.
- Span names are low-cardinality operations such as registered route
  templates, database operations, or registered product operations, never raw
  URLs, entity IDs, user input, or error messages.
- Standard concepts use standard attributes and placement: service and
  deployment identity on the resource; request-specific HTTP, database,
  messaging, RPC, and error fields on spans or exception events.
- The versioned `utrace.*` namespace covers only product concepts without a
  stable OpenTelemetry equivalent, including registered product operations,
  entity classes, workflow milestones, safe outcomes, and their manifest
  definition references; it never aliases a standard attribute.
- The uTrace contract package owns span names, permitted attributes, types,
  cardinality limits, placement, output classes, and valid custom values; SDK
  constants and runtime validators are generated from that schema, not
  hand-copied into adapters.
- Trace and span IDs keep their W3C/OpenTelemetry representation. Durable
  product identities stay in the authenticated telemetry envelope and the
  [ingestion correlation model](./telemetry-and-evidence.md#correlation-model),
  with no generic `correlation_id` attribute.

## Trace extraction and propagation

- The provider uses W3C Trace Context and a parent-based always-on sampler:
  the browser creates sampled traces during an active interview, and the
  server does no head sampling of eligible roots or descendants.
- A syntactically valid `traceparent` is not authorization; the SDK accepts it
  as a remote parent only on a request already inside the authenticated,
  active prepared preview. Client, customer, user, session, preview
  environment, and runtime-allocation identity comes from the server
  activation, never from `traceparent`, `tracestate`, baggage, or an
  application-supplied correlation header.
- The uTrace provider neither extracts nor injects W3C baggage; it propagates
  only `traceparent` and compatible `tracestate`.
- Trace context follows the operation through Node asynchronous execution.
  Outbound HTTP, RPC, and message producers inject it only toward server-SDK
  services or transports the signed release manifest approves.
- Queue producers attach context to approved message metadata, and consumers
  create the matching consumer span to continue the causal trace. Fan-out,
  batch, retry, and delayed work use span links where parent-child semantics
  would be false; message bodies are never trace carriers.
- A background operation inherits the originating context when one exists;
  otherwise only a root the [annotation API](#annotation-api) starts is
  eligible. Unregistered cron jobs, maintenance work, and unrelated preview
  traffic are not exported.
- WebSocket, SSE, custom RPC, and custom queue protocols on a required client
  workflow need a typed adapter; missing propagation there is a readiness
  failure, not permission to return an incomplete trace.

## Redaction boundaries

- Redaction happens before a finished span enters the uTrace export buffer:
  capture is disabled at the instrumentation library when possible, a uTrace
  processor reapplies the policy before serialization, and ingestion adds a
  separate defensive validation layer.
- Layers apply in this order, and a later layer may only restrict an earlier
  result:
  1. Non-overridable platform exclusions
  2. Authenticated activation scope
  3. Signed client policy
  4. Registered annotation definition
  5. Per-value evaluation
  6. Defensive sensitive-data detection
  7. Normalization and cardinality bounds
  8. Schema validation
- Authentication headers, cookies, tokens, credentials, secrets, payment data,
  private keys, unrestricted request or response bodies, and unapproved
  customer content are never collected, and client policy cannot re-enable
  them.
- Product-domain spans keep only registered fields in their declared output
  class. User-authored text defaults to metadata-only; safe categorical or
  bounded values need an exact signed rule. Rejected originals never enter a
  redaction audit record.

Other span kinds keep only approved fields:

| Span kind | May retain | Never exported |
| --- | --- | --- |
| HTTP | Registered route template, method, status, duration, protocol, safe sizes, and approved service destination | Raw query strings, literal path parameters, IP addresses, user-agent strings, headers, and bodies |
| Database | System, operation, approved database or collection class, status, duration, and error classification | SQL text, bind values, result rows, connection strings, and credentials |
| Cache | System, operation, hit or miss, duration, and a registered cache class | Cache keys and values |
| Queue | System, registered destination, operation, delivery state, duration, and an opaque protocol message ID when trace semantics require it | Message bodies and application headers; only the approved trace carrier is injected |
| Error | Stable error type, normalized status, fingerprint, registered product area, and bounded repository-relative code frames | Raw exception messages, unless an exact signed rule approves a stable sanitized form; stack-frame arguments, local values, environment values, and absolute host paths |

## Export, buffering, retry, and acknowledgement

- The uTrace exporter sends only to telemetry ingestion, never to a client
  collector, production observability backend, alternate vendor, or fallback
  endpoint.
- Each immutable export batch carries a stream-scoped sequence and batch ID in
  `x-utrace-` request headers, outside the OTLP span payload. Retries keep the
  batch ID and span identities, so ingestion
  [deduplicates](./telemetry-and-evidence.md#duplicates-ordering-lateness-and-time)
  them.
- The uTrace ingestion contract forbids OTLP partial success, which would make
  the trace ambiguously incomplete;
  receiving one fails the stream and records the rejected count and reason.
  Permanent validation or authentication errors are not retried.
- Retryable transport failures and throttling use bounded exponential backoff
  with jitter and honor `Retry-After`, retrying the same immutable batch
  without switching transport or destination.
- During an active observation window, every eligible span passes through
  redaction and the bounded export path with no SDK-side sampling; any later
  retention or evaluation sampling occurs after ingestion and cannot rewrite
  the evidence as complete.
- The SDK never writes telemetry to the application filesystem, database,
  cache, or queue. An export-queue high-water mark withdraws readiness before
  the hard bound is reached;
  authenticated conversation about already established facts may continue.
  Reaching the hard bound anyway makes the span loss an explicit stream
  integrity failure; the SDK never silently evicts older or supposedly
  lower-value spans and continues.
- Graceful shutdown performs a bounded flush and terminal stream report. A
  crash, OOM kill, or forced termination may lose an unacknowledged tail; the
  missing terminal acknowledgement is recorded as a possible evidence gap, not
  a complete trace.

## Readiness, health, and failure

- Server SDK lifecycle states are `dormant`, `activating`, `ready`, `paused`,
  `recovering`, `failed`, and `terminated`. An installed package without a
  prepared-preview activation stays `dormant` and does no work.
- `ready` requires:
  - valid environment attestation and credentials
  - compatible SDK, Node, manifest, policy, and schema versions
  - one provider and one hook per library
  - valid resource identity
  - every required service and adapter present
  - an active observation grant
  - a healthy control connection and export queue
  - an acknowledged end-to-end propagation probe
- The propagation probe enters through the authenticated preview path and
  proves browser `CLIENT` to server `SERVER` parentage plus every
  client-declared downstream, database, cache, queue, or custom-protocol hop
  the smoke workflow requires. Duplicate spans, missing hops, rejected fields,
  or unexpected export destinations fail it.
- Health reports hold operational metadata only: SDK and capability versions,
  service and stream identity, manifest and policy versions, lifecycle state,
  last produced and acknowledged batch sequences, buffer utilization, required
  adapter status, credential expiry, probe result, and typed failure codes,
  never product content or rejected raw values.
- Failure codes distinguish at least invalid activation, credential rejection,
  incompatible runtime or contract, duplicate provider or hook, required
  adapter missing, resource mismatch, redaction or schema failure, propagation
  failure, unauthorized destination, ingestion unavailable, partial acceptance,
  acknowledgement integrity failure, buffer exhaustion, and unclean stream end.
- A control or export disconnect enters bounded recovery and immediately
  removes the affected service from combined readiness, pausing
  observation-dependent claims, probing, implementation evidence, and
  validation until fresh credentials, an acknowledged exporter, and the
  required probe restore it.
- Readiness never reads what a health report claims: the control plane marks
  the server path ready only when ingestion, from accepted telemetry, finds the
  signed release manifest's entire required service graph ready and
  browser-to-server correlation complete, as
  [completeness and readiness](./telemetry-and-evidence.md#completeness-and-readiness)
  defines.
- Client-installed component compatibility follows the common
  [versioning rule](./cross-component-contracts.md#versioning-and-compatibility):
  the control plane supports the current and immediately previous major during
  an explicit deprecation window. An older SDK, unsupported capability, or
  semantic-schema mismatch fails before the interview, and no version changes
  during an active stream.
