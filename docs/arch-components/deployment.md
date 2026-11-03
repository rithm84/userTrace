# Deployment and network topology

[Back to uTrace architecture](../ARCHITECTURE.md)

Each uTrace workload runs in the region shown, with persistent coordination
outside the sandboxes:

![Web surfaces run on Vercel in pdx1 and the preview front door with Vercel Sandbox in iad1; the api and worker computes and Supabase run in one Supabase project in AWS us-west-2, and the voice agent runs on Pipecat Cloud in us-west.](../diagrams/svg/deployment.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Deployment scope

The demo deploys the B2B Discord loop with live implementation in one primary
application region.

One Supabase project holds the demo database, and the deployed control plane
(API and worker on Supabase Compute, voice agent on Pipecat Cloud) is its only
writer. Development and test runs use throwaway projects, so a deployed worker
and a local suite never share a database: a worker that sees a test's Jobs claims
them, and a suite that can truncate the demo's tables will. Both resolve the
database from one connection setting, so the separation is an operational rule
the code cannot infer.

### Database connections

The computes and the voice agent reach Postgres by different routes, each with
its own connection caps:

![The api and worker computes connect directly to Postgres inside Supabase's network, while the voice agent's instances and workstation tools connect through the session pooler.](../diagrams/svg/deployment-database-connections.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

Together they must stay within the pooler's client limit, which only the voice
agent counts against, and the database's own connection limit: a listener that
cannot connect stalls that session's cues.

## Provider and runtime placement

The control plane's API and worker run as two computes in the same Supabase
project on Supabase Compute, which is in private alpha. Together they cover
Discord ingestion, telemetry ingestion, session coordination, Jobs, and provider
adapters. The voice agent is a Pipecat pipeline deployed as a Pipecat Cloud agent.
Supabase supplies Postgres, Auth, Storage, Realtime, Queues, and Cron. Vercel
hosts the web surfaces and the preview front door; Vercel Sandbox supplies every
isolated implementation workspace and every full base or candidate preview
runtime, kept separate as the
[PRD](../PRD.md#preview-runtime-allocations-implementation-workspaces-and-session-data)
defines.

### Regions

The application region is AWS `us-west-2`, where Supabase Compute's private alpha
runs computes, so the Supabase project, with its Postgres, Auth, Storage, and
Realtime, is there too and each control-plane query is a same-region round trip of
a few milliseconds. The web surfaces' Vercel functions run beside the API they
call on every request, the front door's beside the Vercel Sandbox preview runtimes
it proxies every preview request to, and the voice agent beside the Supabase
project.

### Supporting services

- **Voice and models:** Daily carries each voice call in a private room. The
  voice agent calls Deepgram Flux, GPT-6.1 Sol through the OpenAI API, and
  ElevenLabs. GPT-6.1 Sol also serves Implementation Gate proposals, intake
  assistance, Case File drafting, and, through Codex, code investigation and
  implementation. The worker and the voice agent reach Jev through Vercel AI
  Gateway. The [system overview](../ARCHITECTURE.md#system) assigns these roles,
  [Model access](#model-access) pins them, and the
  [intake contract](./signal-and-outreach.md#routing-context-and-cognition) limits
  additional model calls to work that needs them.
- **Product and code knowledge:** no search or code index is deployed. The
  client's Product Overview, a versioned, source-linked record in Supabase,
  travels whole with the contexts that need it; code investigation reads exact
  files at the pinned repository revision inside Vercel Sandbox.
- **Tracing:** structured logs and uTrace's durable events and evidence trace the
  control plane; Vercel Observability, through `@vercel/otel`, traces the web
  surfaces.
- **GitHub:** holds branches, diffs, checks, and validated draft PRs; humans own
  production merge.
- **Stripe:** stores customer invoice credits in the uTrace-controlled Stripe
  sandbox standing in for the client's billing account; the control plane's
  computes issue and verify credits under the
  [Outreach Offers contract](./outreach-offers.md).

## Model access

The API, worker, and voice agent keep orchestration, tool execution, and
lifecycle authority over every model call; Jobs and voice runs pin the
configuration revision they ran under.

The workers request Implementation Gate proposals from GPT-6.1 Sol through the
OpenAI Responses API with the official OpenAI Python SDK, as one
structured-output request that stores nothing at OpenAI. Each OpenAI role pins
the model ID (`gpt-6.1-sol`), reasoning effort, service tier, and credential
reference. The gate proposal and implementation run on the Ultrafast tier, every
other role on the Fast tier, all at low effort except intake assistance and Case
File synthesis, which interpret or write for a reader and run at medium.

No model is ever substituted for another. A rate-limited request fails its Job
run, which retries on the Job's own exponential backoff within its run budget.

The voice agent pins its providers in its voice settings: Deepgram
`flux-general-en`, `gpt-6.1-sol` at the `ultrafast` service tier with reasoning
effort `low`, and ElevenLabs `eleven_v4_turbo` in the premade Jessica voice. The
backend creates each Daily room and the voice agent joins it, as the
[voice contract](./interview-and-voice.md#call-setup) defines.

Codex in each workspace sandbox calls the OpenAI API with the attempt's pinned
model, its credential added at egress per the
[harness contract](./implementation.md#agent-harness). The worker and the voice
agent call Jev through Vercel AI Gateway's evaluation endpoint as
`typesafe-ai/jev`, each with the gateway API key from its own secrets, and
restrict every request to the `typesafe-ai` provider. Neither an outage nor a
configuration omission redirects requests to another provider.

## Network and public boundaries

Public endpoints are limited to the Vercel web/session surfaces, preview front
door, authenticated telemetry ingestion, and the authenticated voice connection
route; Daily hosts the call's media. Because Vercel Sandbox port URLs are public,
runtime middleware verifies uTrace route context and the runtime's entry token on
every request, as the
[front-door contract](./preview-front-door.md#vercel-to-sandbox-request-path)
defines. Supabase client access uses publishable credentials plus RLS; secret and
service-role credentials stay server-side.

### API base URL

The `api` compute's public base URL is `https://api.utrace.dev/compute/v1/api`,
and its WebSockets use `wss://api.utrace.dev/compute/v1/api`. `api.utrace.dev` is
the Supabase project's custom domain, so it also serves the project's Auth,
Storage, and REST APIs. Compute strips the `/compute/v1/api` prefix, so the API's
own routes carry none and every client of the control plane (the extension, the
browser and server SDKs, the preview front door, and the web surfaces) takes a
base URL that may include a path. Signed extension proofs keep the bare origins
`https://api.utrace.dev` and `wss://api.utrace.dev` as their audiences.

### Voice connections

The control plane's model adapters and the voice agent call OpenAI, Daily,
Deepgram, ElevenLabs, and Vercel AI Gateway over authenticated TLS. The voice
connection route takes no body and returns a room URL and meeting token; the API
creates the room and starts the connection's voice agent session through Pipecat
Cloud's public API. The session tab reaches Daily over WebRTC, its
Content-Security-Policy admitting Daily's domains for scripts and connections,
and the voice agent joins the same room and streams to Deepgram, OpenAI, and
ElevenLabs. Browser and agent connectivity to Daily are independent, and voice
health checks cover both.

### Preview origins and egress

Each session origin `<opaque>.preview.utrace.dev` shares the `utrace.dev` site in
the demo, as the
[front-door origin contract](./preview-front-door.md#one-origin-per-session)
defines. All service egress is allowlisted by role where supported. The sandbox
network policy gives implementation workspaces only the repository, package,
test, and OpenAI API access the pinned policy requires, and preview runtimes only
the dependencies needed to run the prepared product; outbound side effects are
neutered.

## Secrets and credentials

Supabase Compute project secrets are the custody boundary for Discord, GitHub,
Stripe, OpenAI, Daily, Pipecat Cloud, Deepgram, ElevenLabs, Vercel, and other
server-side grants. The voice agent reads its own copies from its Pipecat Cloud
secret set, which `deploy/pipecat-cloud/deploy.py secrets` writes from the same
local environment; the
[security contract](./security.md#data-classification-and-secrets) lists its
contents.

![The operator's local environment syncs the Compute project secrets, injected into every compute, and the voice agent's Pipecat Cloud secret set; the workspace sandbox holds no key, and the sandbox firewall adds the OpenAI credential at egress.](../diagrams/svg/deployment-credential-custody.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

Secrets never enter prompts, telemetry, logs, Storage artifacts, build outputs,
browser bundles, or Case Files. uTrace-issued session and provider-hop
credentials are short-lived and audience-bound, and authentication failure never
implicitly selects another credential.

Custody is scoped to the project: no per-compute identity narrows which
credentials a compute can read. Compute reserves the `SUPABASE_` prefix for values
it injects itself, so the control plane reads its Supabase keys as
`UTRACE_SUPABASE_SECRET_KEY` and `UTRACE_SUPABASE_PUBLISHABLE_KEY`. A deployment
also removes any secret the control plane no longer names, as the
[deployment runbook](../../deploy/README.md) describes.

Model and voice provider keys stay server-side; the browser receives only a
meeting token for its own connection's room, under the voice contract's call
authorization.

## Scaling and availability

The persistent API scales horizontally with request volume, active sessions, and
telemetry load by adding `api` instances. The `worker` compute runs exactly one
instance: it hosts the Customer Surface consumers beside its Job loop, so it holds
the client's single Discord gateway session, and a second instance would deliver
every watched-channel message twice. That correctness constraint on the intake
path, not a cost decision, caps worker throughput while the gateway lives in the
worker process.

The voice agent scales on Pipecat Cloud: one warm instance stays ready so no call
waits for a cold start, and Pipecat Cloud adds instances up to ten. Each serves
one voice session at a time, so ten concurrent calls is the ceiling.

Ordering that matters is serialized per client or aggregate in Postgres;
unrelated clients remain concurrent. Supabase Realtime may disconnect without
losing truth because clients resynchronize from committed rows.

Temporary voice or network loss enters
[bounded reconnect](./failure-recovery.md#voice-cognition-and-observation-failure).
Provider outages create typed blocked work; an outage never changes the kind of
work, weakens evidence requirements, or hands workflow authority to a provider.

## Client application profiles

Client production and preview builds use the maintained SDK integration and
separate [build/startup profiles](./client-configuration.md#sdk-source-integration);
Vercel Sandbox base and candidate runtimes use the preview profile. Production
builds exclude active uTrace instrumentation, runtime credentials reach only
authorized previews, and no long-lived uTrace branch supplies a different product.

## Deployment and rollback

Platform releases are immutable and identify web, API, worker, voice agent,
database, prompt, tool-schema, and policy versions. Database changes use
expand-and-contract sequencing. Deploy between Interview Sessions; an active
session keeps its pinned compatible package. Rollback cannot reinterpret
committed evidence or revert an irreversible provider effect.

The [deployment runbook](../../deploy/README.md) gives the release order:

1. Database migrations.
2. The Compute project secrets and the voice agent's secret set.
3. The computes and the voice agent.
