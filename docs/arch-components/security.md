# Security, privacy, and abuse boundaries

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows the layers that constrain untrusted input, model proposals, data, and provider effects:

![Layered controls separate untrusted inputs, model proposals, authorization, data, and provider effects.](../diagrams/svg/security.svg)

## Prompt injection and model containment

Discord content, product text, documentation, repository files, telemetry labels, transcripts, and provider responses are untrusted data. They are source-tagged, minimized, and separated from system policy and tool schemas. GPT-Live, GPT-6.1 Sol, the coding agent, and Jev may propose speech, attention, retrieval, scope, code, and actions; none may authorize itself, mutate lifecycle state directly, or turn quoted text into a command.

The control plane validates every proposed effect against authenticated actor, pinned configuration, current state revision, evidence sufficiency, Autonomy Policy, target identity, budget, and idempotency key. Tool outputs return typed data. Secrets are excluded before model context assembly. Prompt injection detected in one source cannot expand retrieval or network scope.

## Implementation security

The Implementation Gate performs a deterministic security screen after sufficient interview and telemetry evidence. Forbidden targets include authentication and authorization weakening, secret handling, billing or destructive flows outside policy, production infrastructure, cross-tenant access, SDK/redaction bypass, and unrelated repositories or paths. A passing proposal creates a new attempt revision with frozen scope; it does not grant general repository authority.

Only an attempt-scoped fenced Vercel Sandbox workspace lease permits code changes. The workspace starts at the exact authorized revision, reads exact files, has no copied customer data, production credentials, or stored model credential, and receives bounded network access. Outputs are content-addressed and scanned before they can become a Preview Version. A sandbox report is evidence, not authority.

## Browser and preview security

The embedded browser SDK registers structured targets and applies client redaction. The extension accepts commands only for its authenticated session, bound preview tab, current target revision, and permitted visual class: observation, pointing, or highlighting. Screenshot or vision interpretation cannot replace structured product evidence. Clicking, typing, product navigation, and other browser effects are outside the supported authority.

Each session preview has its own origin `<opaque>.preview.utrace.dev`, which shares the `utrace.dev` site in the demo; the [front-door origin contract](./preview-front-door.md#one-origin-per-session) owns that boundary. The Vercel front door requires an origin-bound claim, resolves the current control-plane route, strips caller-supplied provider headers, and attaches short-lived route context. Because sandbox port URLs are public, runtime middleware verifies the signed route claim and the runtime's entry token on every request. Responses are `no-store`; request and response bodies are never door logs.

## Compromised client and abuse containment

A compromised SDK, client repository, participant, or staff account is contained to its tenant, session, and authorized scope. Anomaly signals can immediately deny new effects, revoke credentials, freeze delivery, and preserve evidence. They do not permit runtime staff editing or silent recovery.

Public invitation claims, auth callbacks, telemetry ingestion, and preview claims use rate limits, replay protection, signature or token verification, size limits, schema validation, and abuse monitoring. Bare preview URLs and leaked invitation identifiers are insufficient authority.

Revocation can narrow authority immediately, and nothing stale can widen it: a
stale credential, worker result, target, or model statement cannot expand
authority. Seeding and cleanup preserve tenant, credential, and evidence
boundaries.

## Outreach Offers billing isolation

Stripe credentials, verified customer mappings, and credit issuance stay in the
backend [Outreach Offers controller](./outreach-offers.md). Bind every
operation to the client, Stripe account, environment, and customer organization.
Neither participant-supplied billing IDs nor model-generated completion claims
can authorize a credit. Preview side-effect sinks cannot reach the billing
adapter; sandbox and live Stripe resources remain explicitly separated.

## Data classification and secrets

Client data is encrypted in transit and at rest. Supabase RLS, explicit memberships, private Storage buckets, and server checks enforce tenant boundaries.

Long-lived credentials remain in the control plane's [credential custody](./deployment.md#secrets-and-credentials). Supabase service-role and secret keys, GitHub private keys, Discord secrets, model credentials, and Vercel tokens never enter browsers or model context. Short-lived lane credentials are audience-bound and revoked with the session.

The OpenAI API key remains in backend adapters, and the workspace
sandbox receives the OpenAI credential only as a header its firewall adds at
egress. Participant authentication authorizes the
[voice setup route](./interview-and-voice.md#webrtc-session-setup); it does not
grant access to provider endpoints or other provider sessions. Provider processing
follows the [data contract](./data-and-schema.md#model-data-and-cleanup).

## Audit

Audit records are typed, append-only facts for identity, membership, configuration, policy, gate, command, provider effect, validation, delivery, deletion, and security decisions. They cite actor, tenant, inputs, before/after revision, outcome, and correlated trace. Operational logs cannot replace audit evidence.

## Supply chain and standing rule

Dependencies, container images and sandbox templates, extension bundles, SDKs, and web artifacts are pinned, scanned, signed where supported, and tied to release provenance. Build credentials are isolated from runtime credentials. The standing rule is deny by default: absent explicit authenticated, state-valid, policy-valid authority, an effect does not run.
