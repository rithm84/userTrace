# uTrace preview front door

The Vercel deployment that serves `<opaque>.preview.utrace.dev`: it verifies the session
claim, resolves one committed route from the control plane, and forwards the request to
the Interview Session's Vercel Sandbox preview runtime. It performs no client product build and
holds no product state. The contract it implements is
[preview front door](../../docs/arch-components/preview-front-door.md).

## Request paths

| Path | What it does |
| --- | --- |
| `/__utrace/claim?code=` | Exchanges a one-time front-door handoff for the `__Host-` session cookie, then redirects to `/`. |
| `/__utrace/activate` | Same-origin relay for the embedded browser SDK's document activation; returns the signed document attestation. |
| `/__utrace/telemetry-credential` | Same-origin relay that exchanges a verified attestation for the write-only browser telemetry credential. |
| everything else | Verified, routed, and streamed from the preview runtime. |

Requests reach the control plane at `POST /v1/preview/handoffs/{code}/exchange`,
`GET /v1/preview/routes/{host}`, `POST /v1/preview/activations`, and
`POST /v1/preview/telemetry-credentials`. Every call carries the door's bearer service
token; the session credential travels in an `x-utrace-session-credential` header, never in
a URL or a body. The control plane authorises; the door enforces the answer and adds its
own fail-closed guards (expiry, a superseded route revision, and an upstream that is not
the configured application port).

Four surfaces replace a routed response: the neutral interstitial when the door has no
authority, the ended page when the session or mapping is over, the reconnecting page while
the route's durable readiness is withdrawn, and an unavailable page when uTrace itself
fails. All four are `no-store`, carry `Vary: Cookie`, and are written in the register of
[DESIGN.md](../../docs/DESIGN.md) without comic devices. The component doc names the first
three; the fourth exists because each of those three asserts something that is false when
the fault is uTrace's own.

## Origin and cookie

One opaque single-level subdomain per session, read from the `Host` header. The cookie
carries the `__Host-` prefix, so a browser accepts it only over HTTPS, only with `Path=/`,
and only without a `Domain` attribute: it cannot be read by a sibling session origin or by
`preview.utrace.dev`. It is `HttpOnly` and `SameSite=Lax`, its lifetime is the smaller of
the configured bound and the session credential's expiry, and it is stripped from the
`Cookie` header before the request reaches the client product, which keeps its own cookies.

`preview.utrace.dev` must be listed in the Public Suffix List private section before pilot
activation, so that each session origin is a separate site from `utrace.dev`. That listing
is a pilot readiness requirement, not a runtime check; the
[Shopalytics demo](../../docs/SCENARIO.md#preview-origin-shortcut) runs before it takes
effect.

## Route context

The door adds a short-lived `x-utrace-route-context` header the preview runtime verifies
independently of the provider hop, and the route's `x-utrace-preview-entry` token, which
the Server SDK requires before it will treat a remote `traceparent` as a parent:

```
v1.<base64url(claims)>.<base64url(HMAC-SHA256(claims))>
```

The signature covers the encoded payload bytes exactly as transmitted. The claim set is
`utrace-preview-route-v1`, defined in
[`schemas/preview/route-context-token.v1.schema.json`](../../packages/utrace-contracts/schemas/preview/route-context-token.v1.schema.json).
A field change requires a new `schema_version` and a new token version, so an old front
door cannot be mistaken for a current one. Caller-supplied `x-utrace-*`, `x-forwarded-*`,
and `x-vercel-*` headers are stripped before the door sets its own. The claim's
`public_preview_host` is how the runtime learns its session origin: the Vercel Sandbox
port proxy sets `Host` and `x-forwarded-host` to its own host, so the door sets neither.

## Contracts

JSON Schemas live in
[`packages/utrace-contracts/schemas/preview/`](../../packages/utrace-contracts/schemas/preview)
with canonical and rule-violating fixtures in `packages/utrace-contracts/fixtures/preview/`.
The generated `@utrace/contracts` bindings do not cover the `preview` area yet, so this app
carries hand-written zod mirrors in `lib/contracts.ts`. `tests/contracts.test.ts` replays
every fixture against both representations so they cannot drift; moving the mirrors into
the shared package is a later change for the contracts workstream.

## Local development

Node.js 24 and npm 11:

```sh
npm ci
npm run dev        # http://localhost:3200
```

Copy `.env.example` and fill it in. `__Host-` cookies require a secure context; browsers
treat `http://localhost` as one, so the claim works locally without TLS, while any other
plain-HTTP host silently drops the cookie.

`apps/api/tests/preview/test_live_preview.py` runs this app for real against a local
control plane and a live Vercel Sandbox preview: `tests/preview/live_harness.py` builds it,
starts it on port 3170, and drives Chrome at a session origin with
`--host-resolver-rules` pointing that name at the door and
`--unsafely-treat-insecure-origin-as-secure` standing in for the TLS a deployment
terminates. Both flags exist so the cookie is stored and WebCrypto is available; neither
changes what this app does.

## Verification

```sh
npm test           # vitest, ports 8600-8699
npx tsc --noEmit
npx eslint .
npm run build
```

The suites run real HTTP servers: one stands in for the control plane and implements the
four documented routes, one stands in for a Vercel Sandbox preview runtime and verifies the
door's route-context header with the runtime-side verifier logic. The control plane and
the runtime are built in other workstreams; nothing here is mocked, and no test reaches a
live provider.

## Deployment

The Vercel project `utrace-preview-front-door` (team `rithm84s-projects`) already exists
with root directory `apps/preview-front-door`, Node 24, and the domains
`preview.utrace.dev` and `*.preview.utrace.dev` attached.

```sh
vercel project inspect utrace-preview-front-door --scope rithm84s-projects
vercel deploy --scope rithm84s-projects        # preview deployment only
```

Production promotion, DNS, and domain changes are human-owned and outside this workstream.

### Wildcard domain: what Vercel requires

Serving `*.preview.utrace.dev` does **not** require moving `utrace.dev` to Vercel
nameservers, but it does require giving Vercel control of the ACME DNS challenge, because
a wildcard certificate can only be issued through DNS-01 validation. Vercel documents two
supported ways ([Adding and configuring a custom
domain](https://vercel.com/docs/domains/working-with-domains/add-a-domain)):

1. Use Vercel nameservers, which are enabled automatically when a wildcard domain is
   saved.
2. Keep the third-party nameservers and delegate only the challenge name: `NS` records for
   `_acme-challenge.preview` pointing at `ns1.vercel-dns.com.` and `ns2.vercel-dns.com.`,
   plus a `CNAME` for `*.preview` pointing at `cname.vercel-dns-0.com.` for the traffic
   itself. Vercel labels this a workaround and warns that delegating the challenge name
   can stop other providers from issuing certificates that use it.

Verified on 2026-10-03: `utrace.dev` uses `ns1.vercel-dns.com` and
`ns2.vercel-dns.com`. A session origin under `*.preview.utrace.dev` resolves to
Vercel and completes a verified TLS handshake with a wildcard certificate.
DNS routing and wildcard TLS are in place.

## Not handled here

Vercel functions cannot proxy a WebSocket upgrade, so this door serves HTTP only. The
Shopalytics demo product needs none; a client release whose critical workflow depends on
WebSocket, SSE over a long-lived upgrade, or another upgraded protocol cannot be served by
this deployment as built; see the
[front door contract](../../docs/arch-components/preview-front-door.md#vercel-to-sandbox-request-path).
