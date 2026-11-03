# Security, privacy, and abuse boundaries

[Back to uTrace architecture](../ARCHITECTURE.md)

Authority stays out of untrusted input, model proposals, and isolated compute:

![Layered controls separate untrusted inputs, model proposals, authorization, data, and provider effects.](../diagrams/svg/security.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Prompt injection and model containment

Discord content, product text, documentation, repository files, telemetry labels,
transcripts, and provider responses are untrusted data: source-tagged, minimized,
and separated from system policy and tool schemas. The voice agent,
GPT-6.1 Sol, the coding agent, and Jev may propose speech, attention, retrieval,
scope, code, and actions; none may authorize itself, mutate lifecycle state
directly, or turn quoted text into a command.

The control plane validates every proposed effect against authenticated actor,
pinned configuration, current state revision, evidence sufficiency, Autonomy
Policy, target identity, budget, and idempotency key. Tool outputs are typed
data, secrets are excluded before model context assembly, and prompt injection
detected in one source cannot expand retrieval or network scope.

## Implementation security

The Implementation Gate performs a deterministic security screen after
sufficient interview and telemetry evidence. Forbidden targets include
authentication and authorization weakening, secret handling, billing or
destructive flows outside policy, production infrastructure, cross-tenant access,
SDK or redaction bypass, and unrelated repositories or paths. A passing proposal
creates a new attempt revision with frozen scope, not general repository
authority.

Only an attempt-scoped fenced Vercel Sandbox workspace lease permits code
changes. The workspace starts at the exact authorized revision, reads exact
files, has no copied customer data, production credentials, or stored model
credential, and receives bounded network access. Outputs are content-addressed
and scanned before becoming a Preview Version, and a sandbox report is evidence,
not authority.

## Browser and preview security

The embedded browser SDK registers structured targets and applies client
redaction. The extension accepts commands only for its authenticated session,
bound preview tab, current target revision, and
[permitted](./policy-and-permissions.md#browser-command-permission-scopes) visual
command: `scene`, `scroll_into_view`, or `clear_scene`.

Each session preview has its own origin `<opaque>.preview.utrace.dev`, sharing
the `utrace.dev` site in the demo under the
[front-door origin contract](./preview-front-door.md#one-origin-per-session).
Because sandbox port URLs are public, the Vercel front door requires an
origin-bound claim and strips caller-supplied provider headers, and runtime
middleware verifies the signed route claim and entry token on every request.
Responses are `no-store`, and door logs never hold request or response bodies.

## Compromised client and abuse containment

A compromised SDK, client repository, participant, or staff account is contained
to its tenant, session, and authorized scope. Anomaly signals can immediately
deny new effects, revoke credentials, freeze delivery, and preserve evidence, but
never permit runtime staff editing or silent recovery.

Public invitation claims, auth callbacks, telemetry ingestion, and preview claims
use rate limits, replay protection, signature or token verification, size limits,
schema validation, and abuse monitoring. Bare preview URLs and leaked invitation
identifiers grant nothing.

Revocation narrows authority immediately; no stale credential, worker result,
target, or model statement can widen it. Seeding and cleanup preserve
tenant, credential, and evidence boundaries.

## Outreach Offers billing isolation

Stripe credentials, verified customer mappings, and credit issuance stay in the
backend [Outreach Offers controller](./outreach-offers.md); every operation
binds to the client, Stripe account, environment, and customer organization.
Preview side-effect sinks cannot reach the billing
adapter, and sandbox and live Stripe resources remain explicitly separated.

## Data classification and secrets

Client data is encrypted in transit and at rest. Supabase RLS, explicit
memberships, private Storage buckets, and server checks enforce tenant
boundaries.

Long-lived credentials stay in the control plane's
[credential custody](./deployment.md#secrets-and-credentials), never in browsers
or model context. Short-lived lane credentials are audience-bound and revoked with the session.
Provider credentials sit here:

- **Backend adapters** hold the OpenAI, Daily, Deepgram, ElevenLabs, and Vercel
  AI Gateway API keys.
- **The voice agent's Pipecat Cloud secret set** holds the same keys. Because the
  voice agent runs control-plane code, it also holds the database URL, Supabase
  secret key, lane signing key, and front-door service token, so it needs the
  same custody as the Compute project secrets.
- **The workspace sandbox** receives the OpenAI credential only as a header its
  firewall adds at egress.
- **The participant's browser** gets none. The meeting token from its
  [call setup route](./interview-and-voice.md#call-setup) admits it to that
  connection's room only, not provider endpoints or other rooms.

Provider processing follows the
[data contract](./data-and-schema.md#model-data-and-cleanup).

## Audit

Audit records are typed, append-only facts for identity, membership,
configuration, policy, gate, command, provider effect, validation, delivery,
deletion, and security decisions, citing actor, tenant, inputs, before and after
revision, outcome, and correlated trace. Operational logs cannot replace
audit evidence.

## Supply chain and standing rule

Dependencies, container images and sandbox templates, extension bundles, SDKs,
and web artifacts are pinned, scanned, signed where supported, and tied to
release provenance; build credentials are isolated from runtime credentials. The
standing rule is deny by default: without explicit authenticated, state-valid,
policy-valid authority, no effect runs.
