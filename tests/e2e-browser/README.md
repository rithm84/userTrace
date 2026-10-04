# The cross-package browser journey

One real Chrome, four real components, and the contract servers their
counterparties own. It is the only place the uTrace session tab, the packaged
browser extension, the preview front door, the embedded browser SDK and the
Server SDK meet, so it is where a disagreement between any two of them shows up.

| Component | What runs | Port |
| --- | --- | --- |
| uTrace session tab | `apps/web`, production build, fixture control plane | 3100 |
| Browser extension | `apps/browser-extension/dist-e2e-browser`, unpacked | — |
| Preview front door | `apps/preview-front-door`, production build | 3200 |
| Shopalytics preview | the client checkout, preview profile, both SDKs | 3300 |

Everything those four depend on and do not own is the test server its owning
workstream wrote, reached by relative import rather than reimplemented here:
browser telemetry ingestion from `packages/utrace-browser-sdk`, the Server SDK's
control socket and OTLP edge from `packages/utrace-server-sdk`, and the extension
shadow-root inspector from `apps/browser-extension`. Only the control plane's
five front-door routes and its extension control socket are implemented in
`support/control-plane.ts`, because no workstream owns a test double for them
yet. Nothing inside any component is mocked.

## What it proves

1. The participant compatibility preflight passes against the packaged
   extension, and its proof of possession is one the page cannot forge.
2. The session tab's binding offer reaches the service worker, which opens its
   control connection.
3. The front door claims the session origin and the Shopalytics preview signs
   the seeded growth lead in through the one-time handoff.
4. The browser SDK activates through the real door, verifies all three signed
   envelopes with WebCrypto, and exports a baseline.
5. The chart-state channel's three facts arrive as registered milestones:
   applied filter values, displayed series identity, rendering completion.
6. The browser's `traceparent` reaches the Server SDK as a remote parent and
   produces one `SERVER` span with its `pg` hop beneath it — which only happens
   when the door's `x-utrace-preview-entry` token matched.
7. Focusing the preview tab binds it, and the packaged Rive character paints.
8. When the session origin advances to a new runtime with its own product
   sign-in secret, a fresh offer makes the extension reload the bound tab, the
   door signs it in to the new runtime and returns it to the same page, and the
   reloaded document rebinds.
9. A point-and-circle scene resolves the channel filter through the SDK's own
   target registry and applies.
10. Pausing from the orb clears the scene, and Return to session focuses the
    session tab.

## Running it

```sh
npm ci                                  # in this directory
(cd ../../apps/web && npm ci)
(cd ../../apps/preview-front-door && npm ci)
(cd ../../apps/browser-extension && npm ci && npm run build:e2e-browser)
(cd ../../packages/utrace-browser-sdk && npm ci)
(cd ../../packages/utrace-server-sdk && npm ci)

SHOPALYTICS_ROOT=/path/to/shopalytics npm test
```

`SHOPALYTICS_ROOT` defaults to `../shopalytics` beside the uTrace checkout. The
client checkout must already have run `npm ci` and `npm run utrace:manifest`, so
that both signed manifests exist in its `build/utrace/`; the journey reads them
rather than writing its own, because the manifest is the client's declaration.

Every test server listens inside 8520-8599. The applications keep their own
ports, and nothing uses 3000.

## Why the journey needs TLS and a resolver rule

The front door issues a `__Host-` session cookie, which a browser stores only on
a secure origin. A session origin is a subdomain, so `http://localhost`'s
exemption does not reach it and the cookie would be dropped — the journey would
then prove nothing about routing, because every request after the claim would
arrive without a session. `support/tls.ts` therefore terminates TLS for
`*.preview.localtest` in front of the door with a certificate generated for the
run, and Chrome is launched with a resolver rule that points that name at the
loopback address and with the certificate accepted. Neither flag changes what
any component does.

## Why the trust anchor is replaced

The browser SDK ships only public keys and the control plane holds the private
half of the one it packages, so the journey cannot sign an attestation with it.
`support/trust-anchor.ts` generates a pair per run and installs the public half
into the client checkout's vendored SDK for the length of the run, restoring the
file afterwards. This is what the browser SDK's own Chrome suite does and for the
same reason: swapping the anchor keeps real WebCrypto verification in the path
instead of bypassing it.

Chrome must be 137 or newer. WebCrypto gained Ed25519 there, and every
activation envelope is verified with it; the journey refuses to run on an older
build rather than reporting an activation failure that is really a browser one.
Chrome stable from 153 no longer loads an unpacked extension from the command
line, so a Chrome for Testing build from the Playwright or Puppeteer cache is
used.

## The participant fixture

Participant sign-in is Discord OAuth, which needs a client secret uTrace does not
hold outside production, so a browser-driven test can never reach an
authenticated session tab. `apps/web` therefore honours
`UTRACE_PARTICIPANT_FIXTURE=1`, but only together with
`UTRACE_CONTROL_PLANE_FIXTURES=1`: it admits the fixture's own participant, grants
no staff access, and never applies to a deployment serving the real API.
