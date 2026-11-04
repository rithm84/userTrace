# Telemetry ingestion, correlation, and evidence

[Back to uTrace architecture](../ARCHITECTURE.md)

Telemetry ingestion is the trust boundary between client-installed producers and
uTrace evidence.

The following diagram shows how authenticated telemetry becomes frozen, source-linked evidence:

![Browser observations and server spans pass through durable ingestion, correlation, evidence windows, and promotion.](../diagrams/svg/telemetry-and-evidence.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Ingress topology and ownership

Browser observations and server spans enter through separate public edges; two
control sockets beside them carry no telemetry:

| Channel | Endpoint | Carries | Credential presented as |
| --- | --- | --- | --- |
| Browser ingestion edge | `WS /v1/ingest/browser` | [Browser runtime](./browser-sdk.md#export-buffering-and-acknowledgement) observations | Second WebSocket subprotocol value beside the versioned protocol name, since a browser cannot set handshake headers; the accept echoes only the protocol name |
| Server ingestion edge | `POST /v1/ingest/otlp/v1/traces` | OTLP/HTTP binary Protobuf from the uTrace server SDK | Bearer `Authorization` header; the rest of the activation envelope in `x-utrace-` headers |
| Server SDK control channel | `WS /v1/server-sdk/control` | Activation, credential rotation, health, and pause or resume | Bearer header, re-presented in the activation frame and compared in constant time |
| Extension control channel | `WS /v1/extension/control` | Binding, commands, and results | Installation identity and a single-use possession proof as subprotocol values; no bearer token |

- Ingestion edges accept no product queries, browser commands, domain messages,
  media, general logs, or metrics; typed
  [queries](#query-and-subscription-contracts) use the authenticated
  client-scoped telemetry path.
- The server edge is the control plane's own OTLP/HTTP receiver: it reads the
  server telemetry credential before building anything else, parses binary
  Protobuf directly, enforces payload limits, validates each span against its
  activation, and writes accepted batches to the versioned durable ingress
  journal both edges share. No OpenTelemetry Collector, the client's or
  another, sits in the path; nothing forwards to an observability vendor.
- A validation or policy evaluation error rejects the affected batch and
  withdraws readiness, never logged and ignored while the span continues as
  evidence.
- Gateways scale horizontally and hold no durable product truth. A connected
  browser WebSocket is instance-affine, but a reconnect may resume from the
  durable stream cursor on another instance; server OTLP requests need no
  affinity.
- Correlation workers partition by client and activation, then by trace where
  required; a worker change never alters record identity, ordering, or
  correlation outcome.
- Gateway and receiver self-telemetry is uTrace operational observability,
  never client product telemetry or customer evidence.

## Authentication, quotas, and payload limits

### Credentials

- Each ingress connection or request uses TLS and a short-lived, write-only
  credential minted for one authenticated observation activation, bound to the
  exact browser stream and document attachment or server stream and service
  instance.
- Payload claims for client, customer, user, session, preview environment,
  runtime allocation, candidate Preview Version, policy, and schema must match
  the control-plane activation record and never override authenticated
  identity.
- Ingress credentials cannot read telemetry, resolve artifact references, call
  control-plane APIs, or authorize product actions. Revocation or expiration
  stops new writes immediately.

### Page proof

Browser activation also issues the page proof: the transaction recording the
observation activation mints the document attachment identity and signs a proof
of the client, session, preview environment, runtime allocation, origin,
document attachment, document nonce, extension protocol version, and validity
window. The SDK forwards it verbatim; the control plane re-verifies the
signature over the exact bytes before issuing an extension binding. The SDK
runtime is not a claim, so no runtime identity can act as authority.

### Limits

These are operational limits, not client- or interview-level preferences:

| Limit | Browser edge | Server edge |
| --- | --- | --- |
| Per record | 256 KiB per observation | Field-level bounds, below |
| Per batch | 1 MiB uncompressed | 4 MiB after decompression and 2,048 spans |
| Rate | 2 MiB and 2,000 records per second, metered per connection | 16 MiB and 8,192 spans per second per API process across all server streams: the batch ceilings times four in-flight batches |
| In-flight batches | Four | Per server activation |
| Acknowledgement lag | 15 seconds | Per server activation |
| Handed out by | The browser activation | The server activation, beneath the edge's ceilings: batch, queue, in-flight, acknowledgement-lag, and retry limits, but no rate limit |

- A larger browser baseline splits into ordered, individually hashed chunks,
  available only once its terminal chunk and aggregate hash validate.
- The server edge checks compression ratio, nesting depth, attribute count,
  array length, and field-specific string limits before allocation or
  normalization. Oversized code frames or other approved structured content use
  an artifact reference, not a larger span.
- Ingestion never samples, truncates, or silently drops eligible telemetry to
  stay under quota: transient limits get the explicit backpressure the
  [receipt sequence](#durable-receipt-and-acknowledgement) shows, and sustained
  pressure removes readiness before producer buffers exhaust.

## Durable receipt and acknowledgement

The following diagram shows how each edge answers a batch:

![Each edge checks a batch in memory, then refuses it, applies backpressure, withholds acknowledgement, or commits it in one transaction and acknowledges it.](../diagrams/svg/telemetry-and-evidence-receipt.svg)

- Each received batch gets an immutable receipt: `ingestion_receipt_id` (a
  UUIDv7 that never replaces a source record ID), authenticated producer and
  activation identity, source stream, batch identity and sequence, received
  time, byte and record counts, schema versions, and validation result.
- Forbidden material is never written to the journal, object storage, logs,
  quarantine, or a content hash.
- An acknowledgement, per batch rather than per record, means the
  authenticated batch passed the ingress checks and committed to the replicated
  durable journal; receipt by a load balancer, WebSocket process, OTLP
  receiver, or in-memory queue is not enough. A missing or rejected
  `export_sequence` leaves later records durable without implying a continuous
  stream.
- A browser batch's transaction may wait at most 30 seconds between
  statements. Postgres ends a stalled commit and releases its locks; the
  unacknowledged producer reconnects, and its resend commits instead of waiting
  behind it.
- uTrace never emits OTLP partial success: one invalid span fails the whole
  request, so the server SDK can fail the stream explicitly.

### Delivery gaps

A delivery gap is a range, not a set of missing records. The producer coalesces
adjacent drops and reports the first and last `source_sequence`, record count,
observation class, reason, and whether a fresh baseline follows; ingestion
keeps the range open against the browser stream until resolved. Dropped records
never got an `export_sequence`, so the export stream stays contiguous and the
acknowledgement watermark advances past the gap: completeness comes from open
ranges, never the watermark alone.

### Pause, resume, and new streams

Ingestion holds the stream side of the
[browser export contract](./browser-sdk.md#export-buffering-and-acknowledgement):

- A pause records a terminal sequence and marks the stream paused without
  ending it. A resume, like a reconnect after a transport disconnect, reopens
  the same stream at its recorded acknowledgement watermark under a new stream
  revision and the same observation activation.
- A stream never changes activation: a reopened stream presenting another
  observation activation is an integrity failure, not a resume.
- A stream abandoned without a terminal sequence ends at its accepted
  watermark.

## Validation and normalization

- Accepted journal records move through explicit stages: `received` →
  `authenticated` → `source_validated` → `normalized` → `indexed` →
  `correlated` → `available`. A terminal rejection or quarantine records the
  exact stage and reason; an earlier stage never implies a later one.
- Browser validation enforces the observation envelope, stream and source
  sequences, document and frame attachment, route and target revisions, actor
  classification, trace fields, policy versions, and typed payload schema.
- Server validation enforces OTLP structure, resource and instrumentation-scope
  identity, allowed span names and kinds, parent and link encoding, pinned
  semantic conventions, uTrace attributes, manifest capabilities, and the
  complete server activation envelope.
- Source-side redaction stays mandatory. Ingestion re-applies the
  non-overridable platform exclusions and never cleans up a violation: one
  excluded value refuses the whole batch as `unsafe_data_detected`
  ([receipt sequence](#durable-receipt-and-acknowledgement)), so downstream
  cleanup never stands in for proof of a healthy source SDK.

### Decoders and reprocessing

- Explicit decoders map the current and immediately previous negotiated source
  majors into one canonical internal schema. A decoder may make documented
  representation changes but preserves the immutable original receipt and
  never guesses missing identity, actor, target, route, parentage, or business
  meaning.
- Unknown record types, versions, attributes, enum values, and required fields
  fail validation. A deprecated semantic-convention name passes only when the
  negotiated decoder explicitly maps it; there is no open-ended best-effort
  semantic upgrade.
- Canonical records preserve source IDs, trace and span IDs, original
  timestamps, source sequences, source schema versions, policy versions, and
  provenance. Ingestion adds trusted receipt time, canonical schema version,
  normalization run, storage references, and validation outcome without
  overwriting source facts.
- Reprocessing a retained receipt creates a new `normalization_run_id` with new
  derived records or supersession links, never editing the prior canonical
  record or causing product side effects.

## Correlation model

The following diagram shows ingestion's records, identity keys, and joins:

![An activation binds one stream, whose batch receipts commit observations or spans; correlation projections join them.](../diagrams/svg/telemetry-and-evidence-correlation.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

- Correlation starts from authenticated tenant and activation equality: records
  from different clients, customer accounts, users, sessions, preview
  environments, runtime allocations, or Preview Versions never join because
  another field matches.
- Within one activation, browser continuity uses browser stream, runtime,
  document attachment, frame, source sequence, route revision, target identity,
  browser request ID, command identity, and actor, where each schema applies.
- Browser-to-server causality uses the W3C trace ID and exact span
  relationship: the `SERVER` span must name the browser `CLIENT` span as its
  remote parent, and downstream services, databases, caches, and queues extend
  the trace by parentage or explicit span links. One interaction
  may issue several requests under one trace, each from its own `CLIENT` span;
  the trace is `ambiguous` only when two `SERVER` spans name the same browser
  parent.
- A trace ID is scoped by the authenticated activation; the same ID under
  another client or activation is an integrity violation, not a correlation
  opportunity.
- Workflow, Product Signal, Validation Attempt, Implementation Attempt, target,
  and command relationships use their exact typed IDs; there is no generic
  `correlation_id`. Ingestion never infers a durable product relationship from
  matching text, route, entity value, or temporal proximity.
- Correlation status is explicit: `pending`, `complete`, `partial`,
  `ambiguous`, `invalid`, or `expired`. The projection lists required and
  observed components, gaps, conflicts, first and last relevant watermarks, and
  its revision.
- Arrival time may rank an engineering diagnostic but never establishes
  causality. Ingestion never synthesizes a span or parent to complete a partial
  trace, and a missing observation never becomes successful validation.

## Duplicates, ordering, lateness, and time

### Duplicates

- A browser record, server span, or batch deduplicates by its identity key in
  the [records diagram](#correlation-model); an exact duplicate with the same
  content hash is acknowledged without new evidence.
- Reusing an identity with different content is an integrity violation: both
  versions stay non-authoritative diagnostic facts, the source stream fails,
  and ingestion never chooses the later arrival.

### Ordering

- Browser source and export sequences keep their defined ordering but differ
  under loss:
  - A missing `export_sequence` is a record produced but not yet accepted:
    later sequences are held, and neither the contiguous watermark nor any
    completeness claim advances across it.
  - A `source_sequence` range reported as dropped never reached the exporter:
    the watermark advances, and the open range carries the completeness
    consequence.
- Server batches are strictly gapless: the accepted batch sequence advances by
  one, so a missing batch stops the watermark rather than producing a range.
- Server spans may finish and arrive in any order; incremental correlation
  follows parentage and links, not receive order. A child arriving before its
  parent is ordinary; one whose parent never arrives by the evidence cutoff
  leaves the trace partial.
- Ordering authority is, in order: source sequence where defined, explicit span
  parentage or link, paired request identity, then server-anchored time. uTrace
  claims no physical total order across browser frames, services, workers, and
  networks.

### Lateness

- Every frozen evidence use records exact browser stream watermarks, server
  batch watermarks, trace completeness revisions, and an `accepted_through`
  time, so later data never appears to have informed it.
- Late telemetry may update an open live projection. After a gate decision,
  validation outcome, Case File core version, or other immutable output, it
  becomes later evidence or a discrepancy, never a rewrite of that artifact. The
  control plane decides whether it warrants a new lifecycle; ingestion never
  reopens terminal state.

### Time

- Source times stay unmodified: browser records keep wall time and monotonic
  offset, server spans keep OpenTelemetry nanosecond start and end times, and
  ingestion adds trusted received and accepted times.
- A browser record's server-clock time, `observed_at`, is derived, never read
  from the browser's wall clock. Each page (one browser stream and frame) has
  its own monotonic clock, anchored to the database's by the earliest
  acceptance of anything the page sent minus that record's monotonic offset;
  `observed_at` adds the record's own offset to that origin. It is never
  earlier than the event, later only by the page's fastest delivery, and
  unmoved by records the browser held back.
- Every comparison of browser activity with a server time uses `observed_at`
  (a query bound, a frozen evidence cutoff, or whether evidence followed a
  Validation Attempt's opening), so a wrong participant clock can neither
  hide activity nor admit an earlier action.
- Ingestion bounds the browser's wall clock instead of estimating its offset:
  a record whose `occurred_at` is more than 900 seconds from its receipt time
  refuses its batch as `clock_quality_failure`.
- Impossible durations, child intervals materially outside their parent without
  an allowed async relationship, excessive clock uncertainty, and time
  regression are typed quality failures, never repaired by silently changing
  time.

## Completeness and readiness

Ingestion owns source validation, stream continuity, trace completeness, and
correlation readiness evidence. The control plane, as the authority, combines
them with voice, preview, extension, SDK, environment, policy, and session
readiness in the durable
[session readiness record](./interview-and-voice.md#readiness-has-two-boundaries).

### Readiness requirements

| Readiness | Requires |
| --- | --- |
| Browser | Current baseline and required chunks, continuous accepted sequence, current document and frame set, valid target registry, bounded acknowledgement lag, and no unresolved critical delivery gap |
| Server | Every manifest-required service stream, compatible resources and adapters, and bounded export lag |
| Correlation | Browser `CLIENT` span, server `SERVER` span, and every required downstream hop of the probe in one trace under the current document's activation |

- An ambiguous trace withdraws correlation readiness.
- The Server SDK's own propagation-probe verdict appears in its health reports
  but does not gate readiness: the SDK cannot re-arm a failed probe, and
  correlation readiness proves the same path from accepted records.

### Projections and evaluation

The following diagram shows per-session readiness evaluation:

![Three triggers run one readiness Job per Interview Session, which appends per-activation projections that feed the session readiness record.](../diagrams/svg/telemetry-and-evidence-readiness.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

- A projection carries a monotonically increasing revision, contributing
  resource IDs, accepted watermarks, trace-probe identity, checked-at time,
  typed failures, and a short expiry so a stale one never reads as current.
  Publishing appends a revision rather than editing the last; replacing a
  stream or resource invalidates the previous projection.
- An activation owns the evidence and a session asks the question, so a
  session's answer is only as current as the projections behind it.
- A projection counts only for the participant's open page: its browser
  stream, the server streams of its preview (the session's own preview before a
  page opens) from each service's newest activation, and a trace correlated under
  it. A service that restarts, as a spare candidate's does when a draft is staged
  in it, activates again, so the stream its earlier process ended is history,
  not a failure. Until a new page's first
  evaluation, readiness reads as not ready, because the newest projections
  still describe the page before.

### Withdrawal and recovery

- Backpressure, an expired credential, an acknowledgement gap, a failed
  normalizer, correlation ambiguity, a stale required stream, unavailable
  durable storage, or an unhealthy evidence subscription removes readiness
  immediately.
- Recovery creates fresh readiness evidence and, as required, a current
  baseline or probe. Persisted evidence stays valid, but activity from an
  unready interval is never backfilled as if continuously observed.

## Evidence windows and promotion

- Normalized telemetry is an observation source, not automatically a confirmed
  Product Signal claim: the voice agent's tools or Jev may only propose
  evidence within their typed roles, and the control plane validates and
  promotes it.
- Each promoted item preserves its canonical record or artifact
  reference, exact source record IDs, client and activation scope, trace and
  target context, policy and schema versions, correlation and completeness
  revision, source watermarks, and the claim it supports or contradicts.

### Rolling and frozen windows

- `evidence_window_id` identifies one bounded selection of observations for a
  live interview view, gate decision, implementation input, validation outcome,
  Case File core version, or evaluation run. It records purpose, authorized
  caller, domain identities, query contract, source watermarks, completeness
  state, creation time, and whether it is rolling or frozen.
- A rolling live window advances only within the active session and current
  readiness. A decision or output freezes a new immutable window; later data
  needs a new window.
- Freezing writes a separate frozen window instead of sealing the rolling
  one, and the control plane's evidence-window row is immutable from insertion.
  Promotion's one later write links the telemetry window to the control-plane
  window citing it, recording that a decision consumed it; the link changes no
  bound, watermark, completeness state, or content.

### Citations

- Gate decisions, validation outcomes, Case File claims, and evaluation results
  cite frozen evidence windows. Implementation work may query the frozen source
  evidence plus explicitly newer authorized context, identified separately.
- Durable citations never depend solely on a record scheduled for deletion;
  [retention](#retention-deletion-and-tenant-isolation) materializes each
  promoted item first.
- An evidence artifact never turns a user statement into telemetry or
  telemetry into user intent; source class and provenance stay explicit.

## Query and subscription contracts

- Ingestion exposes versioned, typed, read-only queries for:
  - current product-state projection
  - bounded session timeline
  - the person's latest session activity: product milestones, control
    activations, and page loads, newest first so a bound keeps the latest
  - one trace and its completeness
  - one browser request and its server chain
  - exact target history
  - registered-operation errors
  - before-and-after validation comparison
  - delivery-gap report
- A query must name the authenticated client, purpose, exact session or frozen
  evidence window, allowed source classes, bounded time or watermarks, field
  projection, result limit, and cursor, plus trace, target, request, signal,
  validation, or implementation IDs only where its schema requires them.
- There is no arbitrary SQL, OTTL, object-store path, full-text product search,
  raw DOM selector, or cross-session telemetry API. Queries cannot mutate
  state, promote evidence, start work, or broaden their own authorization.
- Results contain canonical IDs, safe typed summaries, freshness, sensitivity,
  completeness, provenance, and artifact references, never a storage credential
  or permanent object URL.
- The live reasoning subscription is a bounded projection over the active
  rolling window: the baseline and selected ordered updates the
  [voice architecture](./interview-and-voice.md) defines, not the full
  ingestion stream. High-volume coalescing cannot hide a gap or readiness
  failure.
- The client dashboard may render authorized session and Product Signal
  summaries and evidence cited by Case Files and validation outputs, but no
  general session replay or unrestricted raw-telemetry explorer.
- Evaluation queries receive an immutable frozen window or explicitly versioned
  replay input and cannot change correlation, promote evidence, or cause
  historical side effects.

## Persistence and integrity

- Telemetry uses four logical storage layers:
  - the durable ingress journal, for accepted transport records
  - a hot normalized telemetry store, for active and recently terminal loops
  - Supabase Postgres, for identities, receipts, watermarks, correlation
    indexes, evidence windows, lifecycle references, and retention state
  - object storage, for immutable raw batches, baselines, and large evidence
    artifacts
- Storage providers do not define product truth; canonical record IDs, hashes,
  versions, scope, and relational references stay portable uTrace contracts.
- A consumer persists the canonical record or artifact before marking its
  receipt `available`, and correlation and evidence indexes update
  idempotently from that durable record. Cross-store work uses explicit stages
  and reconciliation, not a false distributed transaction.
- Every accepted batch is kept whole in object storage as its raw artifact,
  every canonical record (payload included) is a Postgres row, and a baseline
  payload over 64 KiB is also its own object-storage artifact. Relational rows
  never store bucket names, filesystem paths, permanent URLs, or object
  credentials.
- Large payloads use the
  [common artifact reference](./cross-component-contracts.md#artifact-references)
  with a UUIDv7 `artifact_id`; baseline chunk manifests also record ordered
  chunk hashes and an aggregate hash.
- Artifact content is immutable. Resolution verifies authenticated client
  scope, expected type, size, and hash before returning a bounded result or
  temporary internal stream; a mismatch is an integrity failure.
- Content-addressed optimization never deduplicates across clients, so
  identical bytes in different client scopes stay independently authorized and
  deletable and hashes cannot become a cross-tenant side channel.

## Retention, deletion, and tenant isolation

- Ingestion applies the centralized production retention, deletion, export,
  legal-hold, reconciliation, and recovery policy in
  [Durable data and schema evolution](./data-and-schema.md) to telemetry and
  evidence, never a second schedule.
- Before eligible source telemetry expires, each promoted citation is
  materialized as the minimum scoped artifact preserving its meaning and
  provenance; unrelated surrounding telemetry is not kept merely because one
  item was promoted.
- Deletion removes content and all resolvable indexes from every governed
  storage layer. A retained non-sensitive tombstone records only identity,
  scope, deletion time, reason, and that a citation is unavailable.
- Unsafe rejected payloads are never retained. Safe quarantined diagnostics
  follow the applicable centralized policy.
- Every write, partition, index, query, cache key, artifact resolution,
  retention job, and deletion job carries and verifies `client_id`, plus user,
  customer account, session, and activation scope where the contract requires
  them.
- A cross-client reference mismatch fails before lookup; services never fetch
  an object and then decide whether the caller should have seen it.
- Legal hold, regional placement, client offboarding, and complete user-data
  request mechanics stay governed by
  [Durable data and schema evolution](./data-and-schema.md) and
  [Security, privacy, and abuse boundaries](./security.md), which preserve or
  delete only through explicit audited state, never an ingestion implementation
  shortcut.

## Failure reporting and recovery

- Ingestion reports typed control facts for authentication rejection, quota or
  payload violation, unsafe-data detection, schema incompatibility, duplicate
  conflict, source gap, normalization failure, correlation partiality or
  ambiguity, clock-quality failure, storage or journal outage,
  artifact-integrity failure, retention failure, and unauthorized query.
- A failure record holds the safe receipt and stream identities, activation,
  stage, stable code, retry classification, affected watermarks or trace,
  readiness consequence, and redacted diagnostic details, never the rejected
  product value.
- Raw telemetry stays outside the domain-message system; only derived
  readiness, gap, correlation, evidence-availability, and failure facts reach
  the control plane, as versioned
  [domain messages](./cross-component-contracts.md#domain-messages).
- Consumers checkpoint durable journal offsets only after their output stage is
  durable. Restart replays from the checkpoint with the same source IDs and
  idempotent writes and by itself cannot resend browser commands, change
  interview state, or promote evidence.
- Safe quarantined records are invisible to normal queries and never count as
  product evidence. A corrected decoder
  or policy may reprocess them within their retention window into new derived
  records, never rewriting an immutable decision that used an earlier window.
- Partial processing never reports a complete trace or ready observation path.
  Recovery clears a failure only after the missing stage succeeds and a fresh
  readiness projection proves the current path.
