# Evaluation and operational observability

[Back to uTrace architecture](../ARCHITECTURE.md)

Verification runs before release and against a deployed target:

![Automated tests and the demo harness verify the journey from durable evidence while structured logs and durable IDs support operational diagnosis.](../diagrams/svg/evaluation-and-observability.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Verification ownership

Automated tests and the demo harness verify the demo journey. Their results inform
releases but never participate in live authority, change product state, or supply
evidence missing from the product loop. Every failed, blocked, cancelled, and
abandoned case remains observable in durable records. There is no training or
fine-tuning pipeline, and browser effects beyond observation and
[visual guidance](../PRD.md#permission-boundary) are out of scope.

Verification uses no fixed demonstration duration: it assesses whether the agent
gathers sufficient evidence in a fluid conversation and whether implementation
progresses without repetitive status narration. Voice transcript fragments stay
provisional under the [transcript contract](./interview-and-voice.md#transcript-and-evidence-contract).

## Verification lanes

Deterministic tests exercise auth, Feature Request Flow intake, structured
observation, interruption, live implementation, validation, cancellation,
blocking, delivery, and Outreach Offers against pinned fixtures, using real
provider sandboxes where practical.

### Live voice checks

Opt-in live voice checks (`UTRACE_REALTIME_E2E=1`) run real calls against the
local stack:

- **Browser call test:** runs the API (which hosts each voice agent session in its
  own process), the worker, and a production build of the web app, and drives the
  real session tab in Chrome under its real autoplay policy. A synthetic
  microphone speaks recorded lines; the test measures what the tab actually plays,
  including through a reload and a late-joining agent.
- **Interview quality test:** holds the scenario's whole interview, greeting to
  recorded validation, with a model that hears the agent playing the originating
  user. It fails on a repeated or re-listed sentence, a second greeting, a stall,
  or a walk back through confirmed checks, or when a judge model finds
  repetition, a restart, a stall, or anything false.

### Test databases

Each test empties the tables it owns, so two test processes cannot share a
database. Live checks run side by side on separate local Supabase stacks: a copy of
`supabase/` with its own `project_id` and ports, started with
`supabase start --workdir`, gives each process its own database. Tests binding a
fixed local port, such as the browser call and telemetry ingestion tests, still
run one at a time.

### Demo harness

The demo harness (`tools/demo-harness`) drives a target control plane through the
[scenario](../SCENARIO.md):

| Command | Effect |
| --- | --- |
| `demo prepare` | Brings the target to the scenario's starting state |
| `demo watch` | Follows the live journey, printing each milestone as it commits, from staff read models |
| `demo rehearse-intake` | Rehearsals only: posts the canonical request and checks what routing did |
| `demo settle-invoice` | Finalizes the service invoice and cross-checks the issued credit |
| `demo verify` | Asserts the scenario's acceptance boundary |
| `demo reset-target` | Reports what a demo database holds; with `--i-understand-this-deletes-demo-state`, returns it to its pre-prepare state |

### Running the demo

`demo prepare` seeds the deployed database, where a Product Signal, Job, or offer
left from a rehearsal looks like live work and the worker acts on it. Before a
recorded run:

1. Run `demo reset-target --i-understand-this-deletes-demo-state`.
2. Clear what the last run left outside the database: delete the uTrace bot's
   messages in the watched channels and its DM with the participant, and zero the
   Stripe sandbox customer's credit, which `demo prepare` checks.
3. Run `demo prepare`.
4. Before the request is posted, confirm the deployed worker, which hosts the
   watched-channel consumer, is up.

If a message was posted while the consumer was down, re-ingest that exact message
through the [operator route](./signal-and-outreach.md#discord-ingestion-and-identity);
reposting it would create a second message.

A rehearsal (`demo rehearse-intake`) posts the canonical request as the bot, not a
person. Intake refuses a bot-authored message unless an
author-attribution row maps that identity to a customer identity, so a rehearsal
database carries exactly one such row. Reset the rehearsal's Product Signal
before the recorded run, or the canonical request opens a second one beside it and
the worker acts on both.

## Provider integration checks

Voice integration checks exercise Daily room and meeting-token creation, the voice
agent joining the call, Flux turns, the model's tool calls and gesture marks,
playback, reconnection, and revocation; they reject cross-session or stale setup
results and verify browser responses contain no provider credential.

Model checks run the voice agent's tools against real records, check GPT-6.1 Sol
gate proposals, intake interpretations, and Case File drafts against their
schemas, and run the Codex harness in a Vercel Sandbox with the credential added
at egress. They record provider configuration revisions and measure the voice
agent's reply latency separately from control-plane-to-provider model latency.
[Benchmarks](./benchmarks.md) records the dated measurements behind the active
models, voice pipeline, and agent cursor.

## Outreach Offers verification

Stripe sandbox tests cover reservation before invitation, entitlement at the final
participation milestone, completed no-implementation cases, system-ended cases,
voluntary cancellation, and one credit adjustment per offer, including retries
reusing the same operation. Verify each adjustment's customer, account,
environment, amount, and currency, and reject cross-client, cross-customer, or
sandbox/live mismatches.

## Operational observability

Operational telemetry stays apart from product evidence:

![The web surfaces report to Vercel Observability, the preview front door passes the browser's trace context through untouched, Server SDK spans reach the control plane as product evidence, and the control plane writes structured logs beside its authoritative durable records.](../diagrams/svg/evaluation-and-observability-telemetry.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

- **Control plane and voice agent:** emit no OpenTelemetry spans or metrics; the
  control plane uses OpenTelemetry only to receive the Server SDK's product spans,
  evidence under the [telemetry contract](./telemetry-and-evidence.md) rather than
  operational tracing. Its structured logs use fixed messages and fields for
  client, signal, session, attempt, candidate, command, job, and provider
  reference.
- **Web surfaces:** always-on tracing samples every production request to
  `utrace-web`; `@vercel/otel` adds Next.js framework and outbound fetch spans; Web
  Analytics and Speed Insights record page views and Core Web Vitals. Reported
  page URLs carry route parameters instead of identifiers and no query string,
  because invitation paths hold bearer tokens and query strings hold OAuth codes.
- **Preview front door:** runs none of this instrumentation and forwards the
  browser's `traceparent` untouched; a second injector fails its propagation
  probe.

## Traceability

Every provider call records its business IDs and provider reference transactionally. From a Discord Intake Item, operators can traverse Product Signal, invitation, Interview Session, model/tool activity, attempt, workspace sandbox, Preview Version, validation evidence, Case File, and GitHub draft PR in either direction.
