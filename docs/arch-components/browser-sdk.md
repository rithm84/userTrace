# Embedded browser SDK and shared browser observation runtime

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows the browser SDK's activation and single observation path:

![An attested document runtime collects structured browser events and exports one ordered, redacted stream.](../diagrams/svg/browser-sdk.svg)

## Package and bootstrap boundary

- The client installs one public uTrace browser SDK package. The shared browser
  observation runtime is a separately versioned internal dependency of that
  package, not a second client-facing installation.
- The embedded SDK is the only component allowed to create the shared runtime.
  The browser extension discovers and joins the existing authenticated runtime;
  it never creates a competing runtime, elects a leader, or captures product
  observations independently.
- Source integration follows [client configuration](./client-configuration.md#sdk-source-integration).
  The preview build includes the bootstrap and observation runtime; the production
  build excludes both. Annotation helpers retained in production are inert and
  cannot initialize collection or affect application behavior.
- Before activation, the package contains only a small inert bootstrap. It does
  not install observers or network hooks, register targets, buffer product data,
  export telemetry, or expose an active extension bridge.
- Static browser configuration contains only a public SDK installation ID,
  client-defined environment name, client release ID, and supported schema and
  protocol versions. It contains no secret, uTrace User identity, customer
  account identity, session identity, ingestion credential, or executable
  policy.
- Activation is a bounded pre-application gate. It completes before the preview
  hydrates or sends client-side product requests so the beginning of the
  workflow cannot escape observation. If activation or readiness fails, the
  prepared preview reports the failure and does not boot as an unobserved
  substitute.
- The core SDK and runtime are framework-neutral JavaScript and support the same
  Chrome 137-or-newer baseline as the required extension. Framework adapters may
  supply product semantics, but they cannot create a second observation path.

## Activation and session attestation

- The inert bootstrap requests activation through a reserved same-origin uTrace
  endpoint at the preview front door. The request uses the preview's existing
  authenticated session context and includes a new per-document nonce.
- The control plane returns a signed, short-lived document attestation bound to
  the client, client user, customer account, Interview Session, preview
  environment, runtime allocation, optional candidate Preview Version, origin,
  document nonce, SDK installation, protocol, annotation-manifest version,
  redaction-policy version, and expiry.
- The bootstrap verifies the attestation with a packaged uTrace public key and
  exchanges it once for a short-lived, write-only browser telemetry credential.
  The attestation and credential are not placed in a URL, browser storage, page
  markup, or application-visible global.
- Reload, full navigation, and a telemetry credential that has expired or that
  ingestion rejects require fresh attestation and a fresh telemetry credential.
  A prior document cannot activate or export for its replacement. A transport
  disconnect or a resume reuses the unexpired credential, as described in
  [Export, buffering, and acknowledgement](#export-buffering-and-acknowledgement).
- This document attestation is independent of the session tab's extension
  binding offer. The control plane binds the extension only when the offer,
  installation proof, and SDK document proof identify the same client, user,
  session, preview runtime allocation, and compatible protocol.
- Signed policy and manifest versions are immutable for one activation. A
  runtime cannot broaden them, accept unsigned replacements, or continue after
  the control plane withdraws their authority.

## Document and worker topology

- A runtime is the JavaScript observation engine executing inside one browser
  document. It is not a server process, virtual machine, tab, or whole browser.
- Every instrumented top-level document or iframe has one local runtime. The
  top-document runtime is the coordinator and the only browser telemetry
  exporter for that page.
- An instrumented child frame authenticates its own document and sends normalized
  records to the top-document runtime through an authenticated
  `MessageChannel`. An uninstrumented frame is opaque to observation and cannot
  supply structured targets.
- Dedicated workers receive a one-time child capability from the document that
  created them and may report only work causally assigned by that document.
  Shared Workers and Service Workers never receive a global interview session
  identity; relevant per-operation trace and session context must travel with
  each request or message.
- Every runtime and attachment has a distinct identity. Frame, worker, reload,
  and navigation boundaries are explicit rather than inferred from an origin or
  URL.

## Observation envelope and ordering

- Browser observation uses a baseline-plus-events model. The runtime emits a
  safe structured baseline at activation, resume, and full navigation, then
  reports semantic changes rather than repeatedly dumping the page.
- An additional baseline checkpoint occurs only after a settled SPA route
  transition, an unrecoverable delivery gap, detected registry or DOM drift, or
  an authorized diagnostic request. There is no periodic full-page timer.
- A baseline contains redacted structure, semantics, mounted targets, route
  state, and relevant product state. It is not a screenshot, raw HTML copy, or
  unrestricted DOM or accessibility-tree dump.
- Every observation logically carries this envelope, even when repeated values
  are encoded once in a transport batch header:

  ```text
  observation_id
  observation_type
  schema_version
  client_id
  client_user_id
  customer_account_id
  session_id
  preview_environment_id
  preview_runtime_allocation_id
  preview_version_id
  browser_stream_id
  runtime_id
  document_attachment_id
  frame_id
  source_sequence
  export_sequence
  occurred_at
  monotonic_offset
  route_revision
  visibility_state
  target_id + target_revision        when applicable
  trace_id + span_id                 when applicable
  policy_version + redaction_version
  typed payload
  ```

- There is no generic `correlation_id`. Joins use the exact typed identities
  that establish the relationship.
- `source_sequence` is monotonic within one local runtime.
  `export_sequence` is assigned by the top-document exporter across the browser
  stream. This gives a stable delivery order without claiming a false physical
  total order between simultaneous frames or workers.
- `occurred_at` is RFC 3339 UTC wall time. `monotonic_offset` comes from the
  document's monotonic performance clock. Runtime health records the measured
  wall-clock offset needed to interpret cross-system timing without rewriting
  the original occurrence time.

## Automatic observation boundaries

- The runtime records semantic product events, not an unbounded copy of native
  browser events. It emits one activation for a completed user or agent action,
  explicit focus changes, bounded input metadata, form submission, committed
  route changes, and coalesced DOM deltas.
- Input observation reports safe metadata such as target, edit class, length
  bucket, validity, and changed state. It does not emit every keystroke or an
  unapproved field value.
- Browser requests use paired `request.started` and `request.completed` records
  joined by a browser request ID and, when eligible, W3C trace context.
- Other typed records cover frontend errors, bounded performance measurements,
  product milestones, target-registry changes, user-versus-agent actor, and
  observation gaps.
- Runtime readiness and transport health travel on the control path. They are
  not disguised as product observations.
- Routes are normalized to registered route templates. Raw query strings,
  fragments, secrets, and unsafe literal path values are never exported. An
  unrecognized route is reported as unknown with safe structural metadata.
- Frontend errors are typed and sanitized before export. The browser does not
  stream console output. It may report a stable message fingerprint, error
  class, registered product area, and bounded code location. Trusted source-map
  resolution occurs after ingestion against the exact registered release
  artifact, not from arbitrary browser-provided source.
- Performance observations are bounded, aggregated, and lower priority than
  interactions, routes, product requests, errors, milestones, targets, and
  action results.

## Semantic projection and annotations

- Automatic structure capture is selective. The runtime projects page
  landmarks, relevant hierarchy, mounted interactive elements, registered
  product entities and regions, structured targets, and the minimum ancestor
  context needed to understand them.
- Accessibility semantics are derived from DOM, HTML, ARIA, and the W3C
  accessible-name computation available to page JavaScript. The runtime does not
  require the Chrome debugger permission or claim access to Chrome DevTools'
  privileged accessibility tree.
- Raw HTML, scripts, arbitrary attributes, class lists, computed styles, hidden
  content, and unrestricted page text are excluded. Labels and approved values
  are exported only after policy and redaction.
- A mounted registered target may remain represented while offscreen, but its
  visibility and geometry state must be accurate. The runtime cannot imply that
  an offscreen, disabled, hidden, or covered target is actionable.
- An authorized detail query may request additional redacted fields for an exact
  registered target or region. It cannot turn selective projection into a
  full-page dump.
- The client annotation surface combines a typed SDK API with a small set of
  declarative attributes. It defines product surfaces, entities, targets,
  visual targets, sensitive fields, approved safe values,
  workflow milestones, severity, and priority.
- Unknown annotations, fields, actions, or safe-value rules fail closed. They do
  not become generic attributes or unrestricted payloads.
- Annotation definitions are built into a signed manifest associated with the
  client release. The activation attestation pins its version and digest for the
  session. Runtime application code may instantiate registered definitions, but
  it cannot invent a definition or grant a new observation scope.
- Client configuration produces a sample instrumentation report showing which example
  structures and values would be retained, masked, pseudonymized, or dropped.
  The report never stores the excluded originals.

## Redaction and dynamically changing content

- Redaction occurs before a value enters an observation buffer. The evaluation
  order is:

  1. non-overridable platform exclusions
  2. authenticated session and document scope
  3. signed client annotation and redaction policy
  4. structural inheritance from sensitive surfaces and ancestors
  5. per-value evaluation at observation time
  6. defensive sensitive-data detection, which may only restrict further
  7. normalization, length and cardinality limits, and schema validation

- Platform exclusions cover credentials, authentication material, payment
  details, private keys, security answers, unrestricted request and response
  bodies, and browser-owned or cross-origin content outside the authenticated
  runtime. A client policy cannot re-enable them.
- The policy remains useful rather than blanket-denying product context. Safe
  product chrome, registered labels, roles, states, route templates, HTTP
  method and status, duration, sizes, and approved operational values may pass
  when their specific checks succeed.
- Values use five output classes: `never_collect`, `metadata_only`,
  `pseudonymized`, `bounded_safe_value`, and `structural_operational_safe`.
  User-authored text and form values default to metadata-only unless an exact
  signed rule approves a bounded safe representation.
- Classification is reevaluated whenever content changes. A node once contained
  by a sensitive surface remains tainted after being moved elsewhere until that
  node is destroyed; reparenting cannot launder data into an allowed region.
- Redaction audit data records rule IDs, versions, output classes, and counts,
  never the rejected original value.

## Structured target registry

- A target has two identities: a stable signed target definition from the
  annotation manifest and a document-scoped mounted target instance.
- A mounted instance receives an opaque `target_id` and monotonic
  `target_revision`. The revision changes when its element binding, supported
  visual capability, semantic state, or display-relevant state
  changes. Geometry-only changes update a separate `layout_revision`.
- A component registration handle may preserve the same target instance through
  an ordinary framework rerender. Unmount is terminal; a later mount receives a
  new ID. IDs are never reused in a document.
- Repeated product elements include a safe registered entity or instance
  reference. Tombstones allow commands against an unmounted or replaced target
  to fail specifically as `stale_target`.
- A target query is structured by definition, safe entity reference, effect,
  route, and visibility. The control plane and model never submit CSS, XPath,
  JavaScript, raw coordinates, or unrestricted visible text. Zero matches return
  `not_found`; multiple matches return `ambiguous`; the runtime never chooses a
  nearest guess.
- The runtime may automatically create visual-only targets for safe native
  interactive elements and meaningful semantic elements whose DOM and ARIA
  semantics are sufficient. These targets may be pointed to, circled, labeled,
  or scrolled into view.
- A client declares a visual target (`data-utrace-visual-target`) for anything
  worth showing that is not itself a product control: a region, or a part of a
  component such as one point of a chart, one axis label, one table row, or one
  review. It registers under its declared definition with the same identity,
  revision, and geometry rules, and its label leaves the runtime only through a
  signed safe-value rule. Declaring the parts of a component is how the agent
  shows one of them; no screenshot is interpreted to find a point inside a chart.
- Targets expose only observation and visual guidance. Clicking,
  typing, submission, and product navigation are not command capabilities.
  Every registered target may be pointed to or circled, a disabled one
  included: showing where a control is never implies it is actionable, and its
  disabled state is reported with it.
  Automatic target discovery preserves broad visual communication without
  granting product-action authority.
- Portal, frame, shadow-root, visibility, occlusion, and top-level geometry
  behavior follows the browser-extension decision record. The shared runtime is
  the sole source of the target identity and current geometry used there.

## Observation ownership and duplicate prevention

- The shared runtime is the sole producer of automatic browser observations.
  The embedded SDK contributes annotations, explicit product milestones, and
  safe product values through the runtime; it does not export a parallel copy.
- The extension owns binding, controls, character state, visual commands,
  visual-command dispatch, and command results. It does not export DOM, route,
  request, performance, error, or product-event observations.
- The extension may report visual command status on the control path, but only
  the shared runtime emits canonical product observation.
- Hook installation is single-owner and idempotent. Activation readiness proves
  that fetch, XHR, history, error, interaction, and mutation capture are each
  installed exactly once.
- Inside a uTrace-prepared preview, uTrace owns the browser and server
  OpenTelemetry pipelines. Client OpenTelemetry bootstrap, automatic
  instrumentation, and exporters are disabled there so they cannot create
  duplicate spans, conflicting `traceparent` headers, or a second export path.
  uTrace instrumentation does not run in the client's production environment.

## Trace creation and propagation

- A trace represents one causal product operation, not the entire interview
  session. A user or authorized agent interaction starts an interaction span;
  an eligible product request creates a browser `CLIENT` span beneath it, then
  the server extracts W3C context into its `SERVER` and downstream spans.
- A background request without a defensible parent starts its own trace. When a
  causal relationship is plausible but not a parent-child relation, uTrace uses
  a span link. It never fabricates a parent merely to make a waterfall look
  complete.
- The browser injects `traceparent` and compatible `tracestate` only on
  same-origin requests or on origins explicitly approved in the signed release
  manifest as server-SDK endpoints. It never injects uTrace context into an
  arbitrary third party.
- W3C baggage and custom correlation headers do not carry client, user, customer
  account, or session IDs. Telemetry ingestion joins trace IDs with the explicit
  authenticated browser and server observation envelopes.
- Active interview browser traces are not sampled in the browser. All eligible
  spans and observations pass redaction and enter the bounded delivery path;
  downstream retention or evaluation sampling may occur only after ingestion.
- uTrace follows stable OpenTelemetry semantic conventions where they apply.
  uTrace-specific attributes use a versioned `utrace.*` namespace and are never
  aliases for an existing standard attribute. The activation pins the semantic
  schema version used by that browser stream.

## Request and asynchronous transport coverage

- Native fetch and XHR receive full paired observation and W3C propagation.
- WebSocket, Server-Sent Events, worker messaging, and product-specific async
  protocols use typed client adapters when they are relevant to an interview
  workflow. The adapter supplies safe operation and message metadata and the
  corresponding server SDK extracts or links the protocol context.
- Static-resource loads, `sendBeacon`, and unapproved third-party traffic are
  observation-only. They never receive uTrace trace context.
- If a critical product workflow uses WebSocket, SSE, worker messaging, or a
  custom protocol, its compatible adapter is required readiness for that client
  release. uTrace does not silently observe only fetch/XHR as a lower-fidelity
  substitute.
- Request summaries exclude raw headers, query strings, and request or response
  bodies. They may include a safe method, registered operation and route,
  status, duration, size, approved annotated fields, error classification,
  browser request ID, and trace identity.

## Export, buffering, and acknowledgement

- The top-document runtime exports through a dedicated authenticated WebSocket
  to browser telemetry ingestion. It is separate from the extension's control
  WebSocket and accepts telemetry writes plus acknowledgements and flow-control
  messages, not browser commands.
- If this WebSocket is unavailable, the runtime enters bounded recovery. It does
  not fall back to HTTP export, extension relay, or an uncorrelated provider
  endpoint.
- The runtime keeps a bounded memory-only buffer containing only normalized,
  already-redacted records. It never stores session telemetry in
  `localStorage`, `sessionStorage`, IndexedDB, Cache Storage, or extension
  storage.
- Ingestion acknowledges the highest contiguous `export_sequence`. Retries
  preserve original observation IDs and sequence numbers, and ingestion
  deduplicates them rather than treating retry as new evidence.
- Interactions, committed routes, product requests, errors, milestones, targets,
  and visual-target observations receive low-latency priority. DOM changes are
  coalesced. Performance measurements are batched and are the first eligible
  records to drop under sustained pressure.
- A dropped low-priority range produces an explicit gap record with the range,
  count, class, and reason, followed by a fresh safe baseline. Critical records
  are never silently discarded. If they cannot be delivered within the bound,
  runtime readiness is withdrawn. Observation-dependent claims, probing,
  browser commands, implementation evidence, and validation pause; authenticated
  conversation about already established facts may continue.
- `pagehide` performs a best-effort bounded flush. Browser crash or process kill
  may lose an unacknowledged tail; the missing sequence range remains explicit
  rather than being represented as complete evidence.
- Each top-document runtime creates a new `browser_stream_id` and starts its
  export sequence at one. Reload or full navigation starts a new stream.
- A browser stream belongs to one observation activation and document
  attachment. While the telemetry credential is unexpired, a transport
  disconnect reconnects with that credential and runs no attestation: the
  stream keeps its activation, document attachment, `browser_stream_id`, and
  buffer, and retransmission resumes from ingestion's last contiguous
  acknowledgement. A pause is the same shape: the runtime flushes, waits for the
  acknowledgement, and closes the transport with a terminal sequence, and a
  resume reopens that stream at that watermark rather than starting a second
  one.
- When a reconnect or resume finds the credential expired, or ingestion rejects
  it as a credential failure, the runtime obtains a fresh attestation and
  credential. That is a new observation activation with a new document
  attachment, which can never resume the old stream, so the runtime starts a new
  stream as a new document runtime does: a new `browser_stream_id`, export
  sequence from one, and a fresh baseline. The old stream's unacknowledged and
  unsent records belong to the old activation, so the runtime discards them
  rather than exporting them under the new one. The old stream records no
  terminal sequence, which leaves its acknowledged watermark as the end of what
  it delivered; the missing tail stays explicit, and activity from the unready
  interval is not reconstructed.
- A graceful stream end records a terminal sequence. An acknowledgement beyond
  a produced sequence, regression in acknowledged sequence, or incompatible
  stream identity is an integrity failure that ends recovery, not a reconnect
  hint.

## Runtime readiness and failure reporting

- Runtime lifecycle states are `dormant`, `activating`, `ready`, `paused`,
  `recovering`, `failed`, and `terminated`.
- `ready` requires a current document attestation and telemetry credential,
  compatible protocol and schemas, exactly one top coordinator, every required
  frame and worker connected, each capture hook installed once, an accepted
  initial baseline, an acknowledging ingestion WebSocket, a successful trace
  propagation probe without duplicate injectors, and a consistent target
  registry.
- Runtime health reports only operational metadata: browser stream, latest
  produced and acknowledged sequences, buffer utilization, dropped ranges,
  required component status, manifest and policy versions, and specific failure
  codes. It never includes product content as a health diagnostic.
- Failure codes distinguish at least invalid or expired attestation, credential
  rejection, incompatible protocol or schema, duplicate runtime or hook,
  required frame or adapter missing, baseline rejection, target-registry drift,
  ingestion unavailable, acknowledgement integrity failure, buffer exhaustion,
  and trace-correlation failure.
- The control plane combines this runtime record with browser extension, server
  SDK, browser-to-server trace correlation, warm-environment, seeded-data, and
  preview readiness. Browser runtime readiness alone never authorizes
  product-specific probing.
- Pause stops session-correlated observation and export after acknowledged
  transition cleanup. Resume reconnects as described in
  [Export, buffering, and acknowledgement](#export-buffering-and-acknowledgement),
  emits a new baseline, and requires combined readiness again; activity during
  pause is not reconstructed.

## Authorized detail queries

- When the realtime interview needs more detail than the current evidence view,
  the voice agent's model requests a typed detail query through the control plane;
  Jev may recommend the target and fields as typed semantic attention. The
  control plane authorizes it, sends it through the extension control path to the
  exact bound runtime, and returns the redacted result to interview context.
- A query names the client, session, document attachment, expected registry
  revision, exact target or registered region, requested field set, depth bound,
  size bound, and deadline. It cannot contain CSS, XPath, JavaScript,
  unrestricted text search, or a full-page selector.
- Detail queries are read-only. They cannot click, focus, type, scroll, submit,
  or navigate as a side effect.
- A stale or ambiguous target, sensitive field, unsupported field, oversize
  result, inactive attachment, or expired deadline fails explicitly. The runtime
  does not broaden the query to find something similar.
- Detail-query traffic stays on the authenticated control path rather than the
  telemetry ingestion WebSocket. It is available only for the current bound tab
  while combined browser readiness is current.
- The control plane records the query, requesting interview turn, source
  attachment and target revisions, manifest and redaction versions, typed result
  reference, and failure status so later reasoning remains auditable.
