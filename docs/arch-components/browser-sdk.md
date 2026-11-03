# Embedded browser SDK and shared browser observation runtime

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows the runtimes in one preview page and the single path
their observations take:

![Frames and workers report to the top-document runtime, the sole exporter of one redacted stream to ingestion.](../diagrams/svg/browser-sdk.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Package and bootstrap boundary

- The client installs one public uTrace browser SDK package, integrated as
  [client configuration](./client-configuration.md#sdk-source-integration)
  describes. Its preview build includes the bootstrap and shared observation
  runtime; its production build excludes both, and any annotation helpers it
  retains are inert and cannot initialize collection or affect application
  behavior.
- Only the embedded SDK creates the shared runtime; the browser extension joins
  it and never creates a competing one or elects a leader.
- Before activation, the package is only a small inert bootstrap: no observers,
  network hooks, registered targets, buffered product data, exported telemetry,
  or active extension bridge.
- Static browser configuration holds only a public SDK installation ID,
  client-defined environment name, client release ID, and supported schema and
  protocol versions; never a secret, uTrace User, customer account, or session
  identity, ingestion credential, or executable policy.
- Activation is a bounded pre-application gate that completes before the
  preview hydrates or sends client-side product requests, so the start of the
  workflow cannot escape observation; on failure the preview never boots
  unobserved.
- The core SDK and runtime are framework-neutral JavaScript with the required
  extension's Chrome 137-or-newer baseline. Framework adapters may supply
  product semantics but never a second observation path.

## Activation and session attestation

The following diagram shows how one document activates before the preview boots:

![The bootstrap trades a verified attestation for a write-only credential and starts the runtime, which must reach ready.](../diagrams/svg/browser-sdk-activation.svg)

- The signed, short-lived document attestation binds the client, client user,
  customer account, Interview Session, preview environment, runtime allocation,
  optional candidate Preview Version, origin, document nonce, SDK installation,
  protocol, annotation-manifest version, redaction-policy version, and expiry.
- The attestation and telemetry credential never appear in a URL, browser
  storage, page markup, or application-visible global.
- Reload, full navigation, or a telemetry credential that expired or that
  ingestion rejects requires a fresh attestation and credential, so a prior
  document cannot activate or export for its replacement. A transport
  disconnect or resume reuses the unexpired credential
  ([Export, buffering, and acknowledgement](#export-buffering-and-acknowledgement)).
- The attestation is independent of the extension binding offer. The control
  plane binds the extension only when the offer, installation proof, and SDK
  document proof name the same client, user, session, preview runtime
  allocation, and compatible protocol ([binding handshake](./browser-extension.md#binding-handshake)).
- Signed policy and manifest versions are immutable for one activation: a
  runtime cannot broaden them, accept unsigned replacements, or continue after
  the control plane withdraws their authority.

## Document and worker topology

- A runtime is the JavaScript observation engine inside one browser document,
  not a server process, virtual machine, tab, or whole browser. Every
  instrumented top-level document or iframe has one.
- An uninstrumented frame is opaque to observation and supplies no structured
  targets.
- A dedicated worker gets its one-time child capability from the document that
  created it. Shared Workers and Service Workers never receive a global
  interview session identity; per-operation trace and session context travels
  with each request or message.
- Every runtime and attachment has a distinct identity. Frame, worker, reload,
  and navigation boundaries are explicit, never inferred from an origin or URL.

## Observation envelope and ordering

### Baselines and events

- The runtime emits a safe structured baseline at activation, resume, and full
  navigation, then reports semantic changes rather than redumping the page.
- An extra baseline follows only a settled SPA route transition, an
  unrecoverable delivery gap, detected registry or DOM drift, or an authorized
  diagnostic request; there is no periodic full-page timer.
- A baseline holds redacted structure, semantics, mounted targets, route state,
  and relevant product state. It is never a screenshot, raw HTML copy, or
  unrestricted DOM or accessibility-tree dump.

### Envelope

Every observation logically carries this envelope, even when a batch header
encodes repeated values once:

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

There is no generic `correlation_id`; joins use the exact typed identities that
establish the relationship.

### Ordering and time

- `source_sequence` is monotonic within one runtime; the top-document exporter
  assigns `export_sequence` across the browser stream, giving a stable delivery
  order without claiming a physical total order across simultaneous frames or
  workers.
- `occurred_at` is unmodified RFC 3339 UTC wall time, and `monotonic_offset`
  comes from the document's monotonic performance clock. Comparisons with a
  server time use the server-anchored `observed_at` from
  [telemetry and evidence](./telemetry-and-evidence.md#duplicates-ordering-lateness-and-time).

## Automatic observation boundaries

- The runtime records semantic product events, not every native browser event:
  one activation per completed user or agent action, explicit focus changes,
  bounded input metadata, form submission, committed route changes, and
  coalesced DOM deltas.
- Input observation reports safe metadata such as target, edit class, length
  bucket, validity, and changed state, never every keystroke or an unapproved
  field value.
- Browser requests emit paired `request.started` and `request.completed` records
  joined by a browser request ID and, when eligible, W3C trace context.
- Other typed records cover frontend errors, bounded performance measurements,
  product milestones, target-registry changes, user-versus-agent actor, and
  observation gaps, but never runtime readiness or transport health
  ([Runtime readiness and failure reporting](#runtime-readiness-and-failure-reporting)).
- Routes normalize to registered route templates. Raw query strings, fragments,
  secrets, and unsafe literal path values are never exported; an unrecognized
  route is reported as unknown with safe structural metadata.
- Frontend errors are typed and sanitized, and console output is never
  streamed. An error may carry a stable message fingerprint, error class,
  registered product area, and bounded code location. Trusted source maps
  resolve after ingestion against the exact registered release artifact, never
  from arbitrary browser-provided source.
- Performance observations are bounded, aggregated, and lowest in export
  priority ([Buffer, priority, and gaps](#buffer-priority-and-gaps)).

## Semantic projection and annotations

### Semantic projection

- Structure capture is selective: page landmarks, relevant hierarchy, mounted
  interactive elements, registered product entities and regions, structured
  targets, and the minimum ancestor context to understand them.
- Accessibility semantics come from DOM, HTML, ARIA, and the W3C accessible-name
  computation available to page JavaScript, without the Chrome debugger
  permission or any claim to Chrome DevTools' privileged accessibility tree.
- Raw HTML, scripts, arbitrary attributes, class lists, computed styles, hidden
  content, and unrestricted page text are excluded; labels and approved values
  leave only after policy and redaction.
- A mounted registered target may stay represented while offscreen, with
  accurate visibility and geometry. The runtime never implies that an
  offscreen, disabled, hidden, or covered target is actionable.

### Client annotations

- The annotation surface, a typed SDK API plus a few declarative attributes,
  defines product surfaces, entities, targets, visual targets, sensitive fields,
  approved safe values, workflow milestones, severity, and priority.
- Unknown annotations, fields, actions, or safe-value rules fail closed rather
  than becoming generic attributes or unrestricted payloads.
- Annotation definitions build into a signed manifest tied to the client
  release; the activation attestation pins its version and digest for the
  session. Application code may instantiate registered definitions but never
  invent one or grant a new observation scope.
- Client configuration produces a sample instrumentation report of which
  example structures and values would be retained, masked, pseudonymized, or
  dropped, without storing the excluded originals.

## Redaction and dynamically changing content

Redaction runs before a value enters an observation buffer, in this order:

1. non-overridable platform exclusions
2. authenticated session and document scope
3. signed client annotation and redaction policy
4. structural inheritance from sensitive surfaces and ancestors
5. per-value evaluation at observation time
6. defensive sensitive-data detection, which may only restrict further
7. normalization, length and cardinality limits, and schema validation

- Platform exclusions, which no client policy can re-enable, cover credentials,
  authentication material, payment details, private keys, security answers,
  unrestricted request and response bodies, and browser-owned or cross-origin
  content outside the authenticated runtime.
- Safe product chrome, registered labels, roles, states, route templates, HTTP
  method and status, duration, sizes, and approved operational values pass when
  their specific checks succeed, so redaction never blanket-denies product
  context.
- Values take one of five output classes: `never_collect`, `metadata_only`,
  `pseudonymized`, `bounded_safe_value`, and `structural_operational_safe`.
  User-authored text and form values default to metadata-only unless an exact
  signed rule approves a bounded safe representation.
- Classification reruns whenever content changes. A node once inside a
  sensitive surface stays tainted after moving until it is destroyed, so
  reparenting cannot launder data into an allowed region.
- Redaction audit data records rule IDs, versions, output classes, and counts,
  never the rejected original value.

## Structured target registry

### Target identity and revisions

- A target has two identities: a stable signed definition from the annotation
  manifest and a document-scoped mounted instance.
- A mounted instance gets an opaque `target_id` and a monotonic
  `target_revision`, which changes with its element binding, semantic state, or
  display-relevant state; geometry-only changes update a separate
  `layout_revision`.
- A component registration handle may keep the same instance through an
  ordinary framework rerender. Unmount is terminal: a later mount gets a new ID,
  and IDs are never reused in a document.
- Repeated product elements carry a safe registered entity or instance
  reference. Tombstones make commands against an unmounted or replaced target
  fail specifically as `stale_target`.

### Target queries

A target query is structured by definition, safe entity reference, effect,
route, and visibility; the control plane and model never submit CSS, XPath,
JavaScript, raw coordinates, or unrestricted visible text. Zero matches return
`not_found`, several return `ambiguous`, and the runtime never picks a nearest
guess.

### Automatic and declared visual targets

- The runtime may automatically create visual-only targets for safe native
  interactive elements and semantic elements whose DOM and ARIA semantics
  suffice. These may be pointed to, circled, labeled, or scrolled into view.
- A client declares a visual target (`data-utrace-visual-target`) for anything
  worth showing that is not itself a product control: a region, or a component
  part such as one chart point, axis label, table row, or review. It registers
  under its declared definition with the same identity, revision, and geometry
  rules, and its label leaves the runtime only through a signed safe-value
  rule. No screenshot is ever interpreted to find a point inside a chart.

### Target capabilities

- Targets, automatically discovered ones included, expose only observation and
  visual guidance and grant no product-action authority
  ([command boundary](./browser-extension.md#command-boundary)).
- Every registered target may be pointed to or circled, a disabled one
  included. Showing a control never implies it is actionable, and its disabled
  state is reported with it.
- Portal, frame, shadow-root, visibility, occlusion, and top-level geometry
  behavior follows the extension's [rendering and target boundaries](./browser-extension.md#rendering-and-target-boundaries),
  with target identity and current geometry from the shared runtime alone.

## Observation ownership and duplicate prevention

- The shared runtime is the sole producer of automatic browser observations.
  The embedded SDK feeds annotations, explicit product milestones, and safe
  product values through it, never a parallel copy.
- The extension reports only visual-command status, on the control path, and
  exports no DOM, route, request, performance, error, or product-event
  observation.
- Fetch, XHR, history, error, interaction, and mutation capture hooks are
  single-owner and idempotent; `ready` proves each is installed exactly once.
- Inside a uTrace-prepared preview, uTrace owns the browser and server
  OpenTelemetry pipelines; client OpenTelemetry bootstrap, automatic
  instrumentation, and exporters are disabled, preventing duplicate spans,
  conflicting `traceparent` headers, or a second export path. uTrace
  instrumentation never runs in the client's production environment.

## Trace creation and propagation

- A trace is one causal product operation, not the whole interview session. A
  user or authorized agent interaction starts an interaction span; an eligible
  product request creates a browser `CLIENT` span beneath it, and the server
  extracts W3C context into its `SERVER` and downstream spans.
- A background request without a defensible parent starts its own trace. A
  plausible causal relationship that is not parent-child becomes a span link;
  uTrace never fabricates a parent to complete a waterfall.
- The browser injects `traceparent` and compatible `tracestate` only on
  same-origin requests or origins the signed release manifest approves as
  server-SDK endpoints, never into an arbitrary third party.
- W3C baggage and custom correlation headers never carry client, user, customer
  account, or session IDs; telemetry ingestion joins trace IDs with the
  authenticated browser and server observation envelopes.
- The browser never samples active interview traces; downstream retention or
  evaluation sampling may occur only after ingestion.
- uTrace follows stable OpenTelemetry semantic conventions where they apply.
  Its own attributes use a versioned `utrace.*` namespace and never alias a
  standard attribute. The activation pins the browser stream's semantic schema
  version.

## Request and asynchronous transport coverage

- Native fetch and XHR get full paired observation and W3C propagation.
- WebSocket, Server-Sent Events, worker messaging, and product-specific async
  protocols use typed client adapters when relevant to an interview workflow.
  The adapter supplies safe operation and message metadata, and the matching
  server SDK extracts or links the protocol context.
- Static-resource loads, `sendBeacon`, and unapproved third-party traffic are
  observation-only and never receive uTrace trace context.
- A critical product workflow on such a protocol makes its compatible adapter
  required readiness for that client release, never silently downgraded to
  fetch and XHR observation.
- Request summaries exclude raw headers, query strings, and request or response
  bodies. They may include a safe method, registered operation and route,
  status, duration, size, approved annotated fields, error classification,
  browser request ID, and trace identity.

## Export, buffering, and acknowledgement

### Export transport

- The export WebSocket is authenticated and separate from the extension's
  control WebSocket. It carries telemetry writes, acknowledgements, and flow
  control, never browser commands.
- If that WebSocket is unavailable, the runtime enters bounded recovery and
  never falls back to HTTP export, extension relay, or an uncorrelated provider
  endpoint.

### Buffer, priority, and gaps

- A bounded memory-only buffer holds only normalized, already-redacted records.
  Session telemetry never enters `localStorage`, `sessionStorage`, IndexedDB,
  Cache Storage, or extension storage.
- Retries keep original observation IDs and sequence numbers, and ingestion
  deduplicates them rather than treating a retry as new evidence.
- Interactions, committed routes, product requests, errors, milestones,
  targets, action results, and visual-target observations get low-latency
  priority. DOM changes are coalesced; performance measurements are batched and
  drop first under sustained pressure.
- A dropped low-priority range yields an explicit
  [gap record](./telemetry-and-evidence.md#delivery-gaps) and then a fresh safe
  baseline. Critical records are
  never silently discarded: if they cannot be delivered within the bound,
  runtime readiness is withdrawn, pausing observation-dependent claims, probing,
  browser commands, implementation evidence, and validation while authenticated
  conversation about established facts may continue.
- `pagehide` performs a best-effort bounded flush. A browser crash or process
  kill may lose an unacknowledged tail, whose missing sequence range stays
  explicit rather than passing as complete evidence.

### Stream continuity

A browser stream belongs to one observation activation and document attachment.
The following diagram shows what a disconnect, a pause, and an expired credential
each do to it:

![A disconnect or pause reopens the same stream; an expired or rejected credential starts a new activation and stream.](../diagrams/svg/browser-sdk-export.svg)

- Each top-document runtime opens a new stream, so reload or full navigation
  starts one.
- A new activation can never resume or export into an old stream. The old
  stream's missing tail stays explicit, and activity from the unready interval
  is not reconstructed.
- A graceful stream end records a terminal sequence. An acknowledgement beyond a
  produced sequence, an acknowledged-sequence regression, or an incompatible
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
- Runtime health stays in the page: the SDK exposes it through
  `session.health()` and never sends it. It holds only operational metadata
  (browser stream, latest produced and acknowledged sequences, buffer
  utilization, dropped ranges, required component status, manifest and policy
  versions, and specific failure codes), never product content.
- Failure codes distinguish at least invalid or expired attestation, credential
  rejection, incompatible protocol or schema, duplicate runtime or hook,
  required frame or adapter missing, baseline rejection, target-registry drift,
  ingestion unavailable, acknowledgement integrity failure, buffer exhaustion,
  and trace-correlation failure.
- Ingestion derives browser readiness from the accepted stream, as
  [completeness and readiness](./telemetry-and-evidence.md#completeness-and-readiness)
  defines. The control plane combines it with browser extension, server SDK,
  browser-to-server trace correlation, warm-environment, seeded-data, and
  preview readiness; browser readiness alone never authorizes product-specific
  probing.
- Pause stops session-correlated observation and export after acknowledged
  transition cleanup. Resume reconnects as
  [Export, buffering, and acknowledgement](#export-buffering-and-acknowledgement)
  describes and requires combined readiness again; activity during pause is not
  reconstructed.

## Authorized detail queries

When the live interview needs more detail than the current evidence view, the
voice agent's model requests a typed detail query. The following diagram shows
its path:

![A typed detail query goes from the voice agent's model through the control plane and extension to the bound runtime and back.](../diagrams/svg/browser-sdk-detail-query.svg)

- A query names the client, session, document attachment, expected registry
  revision, exact target or registered region, requested field set, depth and
  size bounds, and deadline. It cannot contain CSS, XPath, JavaScript,
  unrestricted text search, or a full-page selector.
- Detail queries are read-only: they never click, focus, type, scroll, submit,
  or navigate as a side effect.
- A stale or ambiguous target, sensitive or unsupported field, oversize result,
  inactive attachment, or expired deadline fails explicitly; the runtime never
  broadens the query to find something similar.
- Detail queries never use the telemetry WebSocket and serve only the current
  bound tab while combined browser readiness is current.
- The control plane records the query, requesting interview turn, source
  attachment and target revisions, manifest and redaction versions, typed
  result reference, and failure status, so later reasoning stays auditable.
