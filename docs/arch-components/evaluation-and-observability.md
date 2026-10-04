# Evaluation and operational observability

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how durable evidence supports verification while operational telemetry monitors the platform:

![Automated tests and the demo harness verify the journey from durable evidence while OpenTelemetry monitors platform operation.](../diagrams/svg/evaluation-and-observability.svg)

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
- The demo harness (`tools/demo-harness`) brings a target control plane to the
  [scenario's](../SCENARIO.md) starting state with `demo prepare`, returns a demo
  database to its pre-prepare state with `demo reset-target`, and follows the live
  journey with `demo watch`, which prints each milestone as it commits by reading
  staff read models.
- Every failed, blocked, cancelled, and abandoned case remains observable in
  durable records.

### Running the demo

- `demo prepare` seeds the deployed database, so run `demo reset-target` first. A
  Product Signal, Job, or offer left over from a rehearsal is indistinguishable
  from live work, and the worker acts on it.
- The watched-channel consumer lives inside the deployed worker, so confirm the
  worker is up before the request is posted. If a message was posted while the
  consumer was down, re-ingest that exact message through the
  [operator route](./signal-and-outreach.md#discord-ingestion-and-identity) rather
  than reposting it, which would create a second message.
- A rehearsal posts the canonical request through the bot instead of a person.
  Intake refuses a bot-authored message unless an author-attribution row maps
  that identity to a customer identity, so a rehearsal database carries exactly
  one such row. Reset the rehearsal's Product Signal before the recorded run, or
  the canonical request opens a second Product Signal beside it and the worker
  acts on both.

Verification uses no fixed demonstration duration. It assesses whether the agent
gathers sufficient evidence while maintaining a fluid conversation and whether
implementation progresses without repetitive status narration. GPT-Live transcript
fragments are provisional: they cannot prove a final utterance or that speech was
heard.

## Provider integration checks

Voice integration checks exercise the GPT-Live session handshake, direct WebRTC
media, sideband attachment, client delegation, reconnection, and revocation.
Reject cross-session or stale setup results and verify that browser responses
contain no OpenAI credential.

Model checks run a GPT-6.1 Sol delegation tool run with the real strict function
tools, check GPT-6.1 Sol gate proposals, intake interpretations, and Case File
drafts against their schemas, and run the
Codex harness in a Vercel Sandbox with the credential added at egress. Record provider configuration revisions, and measure
browser-to-OpenAI voice latency and control-plane-to-provider model latency separately.

## Outreach Offers verification

Stripe sandbox tests cover reservation before invitation, entitlement at the final
participation milestone, completed no-implementation cases, system-ended cases,
voluntary cancellation, and one credit adjustment per offer, including retries
that reuse the same operation. Verify each adjustment's customer, account,
environment, amount, and currency, and reject cross-client, cross-customer, or
sandbox/live mismatches.

## Operational observability

OpenTelemetry traces cover Vercel-to-control-plane requests, Supabase operations, GPT-Live session delegation, GPT-6.1 Sol and Jev calls, Discord, GitHub, and Stripe adapters, Vercel Sandbox workspaces and harness runs, and preview routing. Trace context joins operational spans to typed business IDs without placing sensitive content in span attributes. Durable events and evidence remain the authoritative audit path.

The web surfaces report to Vercel Observability. Always-on tracing samples every production request to `utrace-web`, `@vercel/otel` adds Next.js framework and outbound fetch spans, and Web Analytics and Speed Insights record page views and Core Web Vitals. A reported page URL carries route parameters in place of identifiers and no query string, because an invitation path holds a bearer token and query strings hold OAuth codes. The preview front door runs none of this instrumentation: it forwards the browser's `traceparent` untouched, and a second injector fails its propagation probe.

Structured logs use fixed messages and fields for client, signal, session, attempt, candidate, command, job, and provider reference. Metrics cover request rate, errors, duration, queue age, lease health, active sessions, voice reconnects, observation correlation, implementation/check duration, route health, validation return, and delivery. Exemplars connect metrics to traces.

## Traceability

Every provider call records its business IDs and provider reference transactionally. From a Discord Intake Item, operators can traverse Product Signal, invitation, Interview Session, model/tool activity, attempt, workspace sandbox, Preview Version, validation evidence, Case File, and GitHub draft PR in either direction.
