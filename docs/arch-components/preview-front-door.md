# Preview front door

[Back to uTrace architecture](../ARCHITECTURE.md)

The preview front door authenticates and routes each session request and relays
the embedded SDK's activation:

![For each request the Vercel front door verifies the session cookie, resolves one route revision from the control plane, and forwards a signed route claim and entry token through the sandbox port proxy to runtime middleware, or returns a neutral interstitial; it relays SDK activation, while telemetry streams directly to the control plane.](../diagrams/svg/preview-front-door.svg)

## Routing ownership

The front door routes one Interview Session between its separate base and
candidate runtimes. It never combines Product Signals or candidate identities.

## One origin per session

Each session's origin is an opaque single-level subdomain of the uTrace-owned
preview host: `<opaque>.preview.utrace.dev`. Browser origin isolation keeps one
session from reading another's cookies, localStorage, or service workers.
Wildcard DNS and TLS cover `*.preview.utrace.dev`, and the HSTS-preloaded `.dev`
TLD makes every preview origin HTTPS-only.

Because `preview.utrace.dev` shares the registrable domain `utrace.dev`, browsers
treat the previews and the uTrace application as one site and may share cookies
and saved passwords between them. The demo accepts that only because its client
code is the uTrace-owned fixture
([scenario's starting state](../SCENARIO.md#starting-state-and-fixture)).

## Authenticated claim

After participant sign-in and invitation claim, two handoffs, neither a
substitute for the other, carry the participant into the preview:

![The session tab obtains a claim URL, the door exchanges its one-time code for an origin-bound credential stored in a cookie, and then the door obtains a signed token, posts it to the product's sign-in endpoint, and returns the product's session cookie.](../diagrams/svg/preview-front-door-handoffs.svg)

- **Front-door claim:** the door stores the origin-bound session credential in a
  Secure, HttpOnly, host-bound cookie limited to that session origin and
  lifecycle. The exchange consumes the code in one conditional update that also
  checks the presenting origin, so a replay and a code presented on another
  origin both lose, across processes rather than within one. A bare or leaked
  preview URL has no authority.
- **Product handoff:** the user arrives as the prepared identity instead of
  facing a login form, and the token never reaches the browser. The template
  owns the sign-in path and the runtime enforces single use; the token's secret
  belongs to the routed allocation and is injected at runtime creation. Because
  the product's session is signed with that secret, the door's second host-bound
  cookie records which allocation signed the browser in. A page load that now
  resolves to another allocation, as after the origin advances to a candidate,
  repeats `/__utrace/sign-in` and returns to the requested page.

The extension and SDK authenticate on their own paths, joined server-side on the
same session ID.

## Vercel-to-sandbox request path

One Vercel front-door deployment terminates public TLS and performs no client
product build. The control plane owns route mappings, their revisions, and their
expiry. The door holds no sandbox credential: it can only follow the port URL in
the route record.

### HTTP only

The door serves HTTP only: a Vercel function cannot proxy a WebSocket upgrade,
so the door strips the upgrade hop and never fronts a long-lived socket for the
session origin. The browser telemetry stream connects directly to the uTrace
ingestion edge at the `wss://` base URL its activation returns, and the
extension's control socket to the control plane. The door relays only the
same-origin activation and telemetry-credential exchange, keeping one origin for
the preview's own requests.

### Runtime ingress check

A Vercel Sandbox exposed port is a public URL with no provider-level
authorization, so runtime middleware is the ingress check for every path the
product serves. It verifies client, session, allocation, Preview Version, port,
expiry, and route revision from the signed route claim, and requires the runtime's
entry token. Port URLs never reach the browser. A leaked port URL without the
entry token and a valid claim, a stale route, or a hostname alone cannot reach
product content or select another runtime, and at most one route per session
origin is active at a time.

### Authenticating the front-door hop

The door drops every caller-supplied forwarding, uTrace, platform, and provider
header before setting its own:

- `https` as the forwarded protocol
- the signed route claim, carrying the public session origin, as
  `x-utrace-route-context`
- the runtime's entry token as `x-utrace-preview-entry`

The entry token is issued with the runtime's server activation and belongs to the
runtime, not a route revision, which makes it evidence that a request arrived
through the door. Runtime middleware verifies the route claim's signature and
expiry and takes the public preview host from the claim; a missing or
unverifiable claim gets an unavailable response, not product content. The Server
SDK applies the same test to trace parentage: it adopts an inbound `traceparent`
only when the entry token is present and matches its activation, so a request
that arrived another way starts its own trace instead of joining the browser's.

The runtime never reads the public host from `x-forwarded-host`, because Vercel
Sandbox's port proxy replaces that header with its own `vercel.run` host.

## Caching and routing

The front door overrides preview responses with `Cache-Control: no-store` and
defensive cookie variance; CDN response caching is prohibited. The stable
hostname points first to the base allocation and atomically advances only after a
candidate passes full readiness. Each request resolves one committed
revision; old mappings and credentials fail closed.

A runtime restart or a new sandbox session withdraws readiness; recovery re-proves
the complete public path and commits a new route revision before traffic resumes.
Route teardown precedes runtime deletion. A route switch revokes the old
document's authority and establishes a fresh SDK and extension binding before
validation resumes. The origin moves to the candidate's allocation in the same
transaction that commits its route, so the next document activates against the
candidate, and the extension reloads the preview tab onto it ([browser
extension](./browser-extension.md#session-anchor-binding-and-handshake)).

## Honest failure surfaces

Ended or revoked sessions return a clean ended-session page; a temporarily
unhealthy route returns a reconnecting page driven by the same durable readiness
state as the session tab. Bounded reconnect permits authorized
implementation/checks to continue but never validation. An outage does not change
the kind of work or claim success.

The door retries a control-plane call that fails in transport or with a retryable
status, with growing pauses, for at least its configured attempts and until a
retry window of a few seconds ends, so a brief interruption of the control
plane's ingress, such as one around a route switch, reaches the participant as a
slower answer rather than an error.

## Origin compatibility

Client applications receive their preview base URL, allowed origin, cookie
domain, and callback configuration through the pinned template, and they take
their public origin from that configuration or the route claim, never the
forwarded host. The front door never rewrites response bodies. The client
configuration tests CORS, cookies, absolute links, SDK export, and unsupported
third-party OAuth redirects against the real preview origin.

Because the door serves HTTP only, a product feature whose observed workflow
needs its own WebSocket upgrade on the session origin is outside the supported
application contract.

## Observability and probes

Door logs contain method, safe path template, status, latency, client/session
identifiers, allocation, and route revision. They never contain request or
response bodies, cookies, handoff codes, provider credentials, or customer field
values.

Synthetic probes cover DNS, TLS, claim, route resolution, provider hop,
middleware, runtime health, cross-origin isolation, cache isolation, stale-route
rejection, and direct port access without a claim.
