# Evaluation and operational observability

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how durable evidence supports verification while operational telemetry monitors the platform:

![Automated tests and the demo harness verify the journey from durable evidence while structured logs and durable IDs support operational diagnosis.](../diagrams/svg/evaluation-and-observability.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Verification ownership

Automated tests and the demo harness verify the demo journey. Their results inform
releases; they never participate in live authority, change product state, or
supply evidence missing from the product loop. There is no training or
fine-tuning pipeline. Browser effects beyond observation, pointing, and
highlighting are out of scope.

## Verification lanes

- Deterministic tests exercise auth, Feature Request Flow intake, structured
  observation, interruption, live implementation, validation, cancellation,
  blocking, delivery, and Outreach Offers against pinned fixtures, using real
  provider sandboxes where practical.
- Opt-in live voice checks (`UTRACE_REALTIME_E2E=1`) run real calls against the
  local stack. The browser call test runs the API, which hosts each voice agent
  session in its own process, the worker, and a
  production build of the web app, and drives the real session tab in Chrome
  under its real autoplay policy: a synthetic microphone speaks recorded lines,
  and the test measures what the tab actually plays, including through a reload
  and an agent that joins late. The interview quality test holds the scenario's
  whole interview, greeting to recorded validation, with the scenario's
  originating user played by a model that hears the agent; it fails on a
  repeated or re-listed sentence, a second greeting, a stall, or a walk back
  through confirmed checks, and on a judge model finding repetition, a restart,
  a stall, or anything false.
- Each test empties the tables it owns, so two test processes cannot share one
  database. Live checks run side by side on separate local Supabase stacks: a copy
  of `supabase/` with its own `project_id` and ports, started with
  `supabase start --workdir`, gives each process its own database. Tests that bind
  a fixed local port, such as the browser call test and the telemetry ingestion
  tests, still run one process at a time.
- The demo harness (`tools/demo-harness`) brings a target control plane to the
  [scenario's](../SCENARIO.md) starting state with `demo prepare`, and follows the
  live journey with `demo watch`, which prints each milestone as it commits by
  reading staff read models. `demo rehearse-intake` posts the canonical request
  and checks what routing did, for rehearsals only; `demo settle-invoice`
  finalizes the service invoice and cross-checks the issued credit; and
  `demo verify` asserts the scenario's acceptance boundary. `demo reset-target`
  reports what a demo database holds, and with
  `--i-understand-this-deletes-demo-state` returns it to its pre-prepare state.
- Every failed, blocked, cancelled, and abandoned case remains observable in
  durable records.

### Running the demo

- `demo prepare` seeds the deployed database, so run
  `demo reset-target --i-understand-this-deletes-demo-state` first. A
  Product Signal, Job, or offer left over from a rehearsal is indistinguishable
  from live work, and the worker acts on it.
- The watched-channel consumer lives inside the deployed worker, so confirm the
  worker is up before the request is posted. If a message was posted while the
  consumer was down, re-ingest that exact message through the
  [operator route](./signal-and-outreach.md#discord-ingestion-and-identity) rather
  than reposting it, which would create a second message.
- A rehearsal (`demo rehearse-intake`) posts the canonical request through the
  bot instead of a person. Intake refuses a bot-authored message unless an author-attribution row maps
  that identity to a customer identity, so a rehearsal database carries exactly
  one such row. Reset the rehearsal's Product Signal before the recorded run, or
  the canonical request opens a second Product Signal beside it and the worker
  acts on both.

Verification uses no fixed demonstration duration. It assesses whether the agent
gathers sufficient evidence while maintaining a fluid conversation and whether
implementation progresses without repetitive status narration. Voice transcript
fragments are provisional: they cannot prove a final utterance or that speech was
heard.

## Provider integration checks

Voice integration checks exercise Daily room and meeting-token creation, the voice
agent joining the call, Flux turns, the model's tool calls and gesture marks,
playback, reconnection, and revocation. Reject cross-session or stale setup
results and verify that browser responses contain no provider credential.

Model checks run the voice agent's tools against real records, check GPT-6.1 Sol
gate proposals, intake interpretations, and Case File drafts against their
schemas, and run the
Codex harness in a Vercel Sandbox with the credential added at egress. Record provider configuration revisions, and measure
the voice agent's reply latency and control-plane-to-provider model latency separately.
[Benchmarks](./benchmarks.md) records the dated measurements behind the active
models, voice pipeline, and agent cursor.

## Outreach Offers verification

Stripe sandbox tests cover reservation before invitation, entitlement at the final
participation milestone, completed no-implementation cases, system-ended cases,
voluntary cancellation, and one credit adjustment per offer, including retries
that reuse the same operation. Verify each adjustment's customer, account,
environment, amount, and currency, and reject cross-client, cross-customer, or
sandbox/live mismatches.

## Operational observability

The control plane and the voice agent emit no OpenTelemetry spans or metrics. The control plane uses OpenTelemetry only to receive the Server SDK's product spans over OTLP; those spans are evidence under the [telemetry contract](./telemetry-and-evidence.md), not operational tracing. Durable events and evidence remain the authoritative audit path.

The web surfaces report to Vercel Observability. Always-on tracing samples every production request to `utrace-web`, `@vercel/otel` adds Next.js framework and outbound fetch spans, and Web Analytics and Speed Insights record page views and Core Web Vitals. A reported page URL carries route parameters in place of identifiers and no query string, because an invitation path holds a bearer token and query strings hold OAuth codes. The preview front door runs none of this instrumentation: it forwards the browser's `traceparent` untouched, and a second injector fails its propagation probe.

The control plane's operational record is its structured logs, which use fixed messages and fields for client, signal, session, attempt, candidate, command, job, and provider reference.

## Traceability

Every provider call records its business IDs and provider reference transactionally. From a Discord Intake Item, operators can traverse Product Signal, invitation, Interview Session, model/tool activity, attempt, workspace sandbox, Preview Version, validation evidence, Case File, and GitHub draft PR in either direction.
