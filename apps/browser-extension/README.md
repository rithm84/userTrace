# uTrace browser extension

The Manifest V3 Chrome extension defined by the
[browser extension contract](../../docs/arch-components/browser-extension.md).
It renders the agent character, its controls, the agent cursor, and visual
scenes inside the bound preview tab, and it never owns durable workflow truth.

## Commands

```sh
npm install
npm run build        # dist/       from config/build-origins.json
npm run typecheck
npm run lint
npm test             # unit tests, then the real-Chrome test
```

`npm test` builds `dist-e2e/` from `config/build-origins.e2e.json` and drives it
in a real browser. The Chrome test needs a Chrome for Testing build; install one
with `npx playwright install chromium` if the harness reports none. Installed
Google Chrome 153 stable no longer loads unpacked extensions from the command
line, so the harness refuses to run against it rather than testing something
else.

## Shape

| Path | What it owns |
| --- | --- |
| `src/background/` | Installation identity, the control connection, binding, command lifecycle, surface injection |
| `src/content/` | The orb, its control pills, the agent cursor, and scene execution inside a closed shadow root |
| `src/mainworld/` | The narrow bridge to the embedded browser SDK; no interface, credential, or decision |
| `src/protocol/` | Runtime validation of every message, mirroring the shared JSON Schemas |
| `src/ui/` | Popup and settings pages |
| `src/build/` | Manifest, build origins, and generated icons (Node only) |

The wire contracts live in
[`packages/utrace-contracts/schemas/extension-protocol/`](../../packages/utrace-contracts/schemas/extension-protocol/README.md)
with fixtures beside them. The TypeScript bindings live in `src/protocol/`, and
`tests/unit/protocol.test.ts` checks them and the JSON Schemas against every
fixture so they cannot drift.

## What the build produces

Four Vite passes and no remote code: the module service worker, the two
extension pages, and two IIFE bundles Chrome injects into a page. The Rive
runtime is bundled and its WebAssembly, the coral character file, and the
generated icons are copied into the package.

Build-time configuration is `config/build-origins.json`: the API and control
socket base URLs (each may carry a path prefix; possession proofs and the CSP
use their origins), the account-link origin, the session-tab origins allowed
to reach the extension, and whether host permissions are optional (shipped) or
required (test builds, which cannot answer Chrome's permission prompt).

## Boundaries the code keeps

- No content script is declared. The service worker injects the surface only
  after the control plane binds the document, so a tab without a matching page
  proof is left untouched.
- The only messages that can start a binding arrive through
  `externally_connectable` from a uTrace session-tab origin.
- Commands address an exact registered target revision. Selectors, coordinates,
  and expressions are not representable in the protocol, and visibility,
  occlusion, and revision are rechecked immediately before each gesture.
- The device key never leaves the service worker, and no bearer or binding
  credential exists; the main-world bridge cannot reach the closed shadow root.
- Readiness, character state, binding, and control outcomes are revisioned
  control-plane records. The extension renders them and never infers them.

## Wire details

- The socket authenticates with a DPoP-style proof of possession of the
  installation's non-exportable device key, carried in the WebSocket
  subprotocol (`utrace.extension.v1`, `utrace.installation.<id>`,
  `utrace.pop.<challenge>.<signature>`) because a browser WebSocket cannot set
  headers. The control plane enforces freshness and single use.
- Every connection opens with `recovery_request` carrying the last acknowledged
  control sequence and the commands whose outcome is still unknown.
- A first binding consumes the session tab's single-use offer; focusing another
  valid tab of the same session sends `{kind: "rebind"}` with the current
  `extension_binding_id` instead of replaying the consumed offer.
- The SDK answers `page_proof` on a port handed to it by `window.postMessage`,
  then keeps that port for `target_query`, `detail_query`, and `pause_notice`.
  `tests/chrome/pages/sdk-stub.js` is the smallest implementation that satisfies
  the contract.
