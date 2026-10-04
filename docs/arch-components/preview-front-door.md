# Preview front door

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how the preview front door authenticates and routes each session request:

![Vercel verifies a session claim and routes it to one authorized Vercel Sandbox preview runtime.](../diagrams/svg/preview-front-door.svg)

## Routing ownership

The front door routes one Interview Session between its separate base and
candidate runtimes. It never combines Product Signals or candidate identities.

## One origin per session

Each session receives an opaque single-level subdomain of the uTrace-owned preview
host `preview.utrace.dev`, so its origin is `<opaque>.preview.utrace.dev`. Browser
origin isolation prevents one session from reading another's cookies,
localStorage, or service workers. Because `preview.utrace.dev` shares the
registrable domain `utrace.dev`, browsers treat the previews and the uTrace
application as one site and may share cookies and saved passwords between them.
The demo accepts that only because its client code is the uTrace-owned fixture;
see the [scenario's starting state](../SCENARIO.md#starting-state-and-fixture).
Wildcard DNS and TLS cover
`*.preview.utrace.dev`, and the `.dev` TLD is HSTS-preloaded, so every preview
origin is HTTPS-only.

## Authenticated claim

After participant sign-in and invitation claim, the session tab requests a one-time front-door handoff and opens the preview. The front door exchanges it for a Secure, HttpOnly, host-bound cookie limited to that session origin and lifecycle. A bare or leaked preview URL has no authority and returns a neutral interstitial. The extension and SDK authenticate on their own paths, joined server-side on the same session ID.

Two different handoffs carry the participant in, and neither substitutes for the
other:

- **The front-door claim** admits the browser to the session origin. The session
  tab requests it at `POST /v1/sessions/{session_id}/preview-handoff`, which
  refuses unless a committed route for that session is ready, and receives a
  claim URL on its own session origin. The door exchanges the code at
  `POST /v1/preview/handoffs/{code}/exchange` for the origin-bound session
  credential it stores in the cookie. The exchange consumes the code in one
  conditional update that also checks the presenting origin, so a replay and a
  code presented on another origin both lose, across processes rather than within
  one.
- **The product handoff** signs the participant into the client application
  itself. The claim redirects to the door's `/__utrace/sign-in`, which asks
  `POST /v1/preview/routes/{host}/product-sign-in` for a short-lived signed token
  and posts it server-side to the authenticated sign-in endpoint the pinned
  template declares. The door returns the product's own session cookie and
  redirects to where the product lands the user, so the user arrives as the
  prepared identity rather than facing a login form, and the token never reaches
  the browser. The template owns that path and the runtime enforces single use;
  the token's secret belongs to the routed allocation and is injected when the
  runtime is created. Because the product's session is signed with that secret,
  the door records in a second host-bound cookie which allocation signed the
  browser in, and sends a page load that now resolves to another allocation, as
  after the origin advances to a candidate, back through `/__utrace/sign-in`. It
  returns to the page it asked for.

## Vercel-to-sandbox request path

One Vercel front-door deployment terminates public TLS and performs no client
product build. For each request it verifies the session cookie, fetches the
authoritative session-origin to Preview Version and runtime mapping, and resolves
one exact route revision. It strips caller-supplied provider and route headers,
adds short-lived signed uTrace route context, and forwards the HTTP request to the
runtime's exposed port URL held in the route record.

The door serves HTTP only. A Vercel function cannot proxy a WebSocket upgrade, so
the door strips the upgrade hop and never fronts a long-lived socket for the
session origin. The browser telemetry stream therefore connects directly to the
uTrace ingestion edge at the `wss://` base URL its activation returns, and the
extension's control socket connects to the control plane. What the door does
relay, on the session origin so the preview keeps one origin for its own
requests, is the same-origin activation and telemetry-credential exchange; it
passes the returned ingestion URL through untouched.

The control plane owns route mappings, their revisions, and their expiry. The
door resolves one route per request through `GET /v1/preview/routes/{host}`,
presenting its lane session credential, and holds no sandbox credential of its
own: it receives the runtime's port URL inside the route record and can do
nothing with the provider beyond following it.

A Vercel Sandbox exposed port is a public URL with no provider-level
authorization, so runtime middleware is the ingress check, and it guards every
path the product serves. It verifies client, session, allocation, Preview Version,
port, expiry, and route revision from the signed route claim, and it requires the
runtime's entry token. Port URLs are never distributed to the browser. A leaked
port URL without the entry token and a valid claim, a stale route, or a hostname
alone cannot reach product content or select another runtime, and at most one
route per session origin is active at a time.

### Authenticating the front-door hop

The door drops every caller-supplied forwarding, uTrace, platform, and provider
header before setting its own. It then presents `https` as the forwarded
protocol, the signed route claim as `x-utrace-route-context`, and the runtime's
entry token as `x-utrace-preview-entry`. The route claim carries the public session
origin.

The entry token is issued with the runtime's server activation and belongs to the
runtime rather than to a route revision, which is what makes it evidence that a
request arrived through the door. Runtime middleware verifies the route claim's
signature and expiry and takes the public preview host from the claim; a missing
or unverifiable claim returns an unavailable response rather than product content.
The Server SDK applies the same test to trace parentage: it adopts an inbound
`traceparent` only when the entry token is present and matches its activation, so
a request that reached the runtime by another path starts its own trace instead of
joining the browser's.

The runtime never reads the public host from `x-forwarded-host`. Vercel Sandbox's
port proxy replaces that header with its own `vercel.run` host, while custom
headers such as the route claim pass through unchanged.

## Caching and routing

The front door overrides preview responses with `Cache-Control: no-store` and defensive cookie variance. CDN response caching is prohibited. The stable hostname points initially to the base allocation and atomically advances only after a candidate passes full readiness. Each request resolves one committed revision; old mappings and credentials fail closed.

A runtime restart or a new sandbox session withdraws readiness. Recovery re-proves
the complete public path and commits a new route revision before traffic resumes.
Route teardown precedes runtime deletion. A route switch revokes the old document's
authority and establishes a fresh SDK and extension binding before validation
resumes. The origin moves to the candidate's allocation in the same transaction
that commits its route, so the next document activates against the candidate,
and the extension reloads the preview tab onto it
([browser extension](./browser-extension.md#session-anchor-binding-and-handshake)).

## Honest failure surfaces

Ended or revoked sessions return a clean ended-session page. A temporarily unhealthy route returns a reconnecting page driven by the same durable readiness state as the session tab. Bounded reconnect permits authorized implementation/checks to continue but never validation. An outage does not change the kind of work or claim success.

## Origin compatibility

Client applications receive their preview base URL, allowed origin, cookie
domain, and callback configuration through the pinned template, and they take
their public origin from that configuration or the route claim, never from the
forwarded host. The front door never rewrites response bodies. The client
configuration tests CORS, cookies, absolute links, SDK export, and unsupported
third-party OAuth redirects against the real preview origin.

Because the door serves HTTP only, a product feature whose observed workflow
needs its own WebSocket upgrade on the session origin is outside the supported
application contract.

## Observability and probes

Door logs contain method, safe path template, status, latency, client/session identifiers, allocation, and route revision. They never contain request or response bodies, cookies, handoff codes, provider credentials, or customer field values. Synthetic probes cover DNS, TLS, claim, route resolution, provider hop, middleware, runtime health, cross-origin isolation, cache isolation, stale-route rejection, and direct port access without a claim.
