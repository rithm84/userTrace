# @utrace/browser-sdk

The embedded browser SDK and the shared browser observation runtime defined in
[browser SDK and shared runtime](../../docs/arch-components/browser-sdk.md).

The package is framework-neutral TypeScript ESM for Chrome 116 or newer. It runs
only inside a uTrace-prepared preview: the preview profile loads the bootstrap
before application hydration, and the production profile excludes the runtime and
keeps only inert annotation helpers.

## What lives where

| Area | Module |
| --- | --- |
| Inert bootstrap and public API | `src/index.ts` |
| Static configuration | `src/core/config.ts` |
| Activation gate, attestation, credential | `src/activation/` |
| Observation core shared by every document | `src/runtime/core.ts` |
| Top-document runtime, readiness, health, topology | `src/runtime/` |
| Single-owner capture hooks | `src/hooks/` |
| Redaction pipeline, policy, taint, detectors, audit | `src/redaction/` |
| Structured target registry and geometry | `src/registry/` |
| Annotation surface and signed manifest | `src/annotations/` |
| Export buffer, sequences, chunking, ingestion transport | `src/export/` |
| W3C trace creation and propagation scope | `src/trace/` |
| Extension bridge and authorized detail queries | `src/bridge/` |
| Next.js App Router helper | `src/next.ts` |
| Build-time manifest toolchain | `scripts/` |

JSON Schemas for this area live in
`packages/utrace-contracts/schemas/browser-observation/`, with canonical and
invalid fixtures beside them. The TypeScript bindings for the area stay inside
this package for now (`src/observation/types.ts`, `src/export/protocol.ts`,
`src/bridge/messages.ts`); they have not been promoted into
`@utrace/contracts`.

## Integration

```ts
import { createUTraceBootstrap } from "@utrace/browser-sdk";

// Importing installs nothing. The bootstrap is inert until it activates.
const bootstrap = createUTraceBootstrap(window, {
  sdkInstallationId: "shopalytics-preview",
  environmentName: "preview",
  clientReleaseId: "main-8f21",
  schemaVersion: "1",
  protocolVersion: "1",
});

const session = await bootstrap.activate();
```

Static configuration carries exactly those five public fields. Every other
identity — client, client user, customer account, Interview Session, preview
environment, runtime allocation, candidate Preview Version, policy and manifest
versions — arrives inside the signed document attestation.

`activate()` resolves only when the runtime is `ready`. If it rejects, the
prepared preview must report the failure; it must not boot as an unobserved
substitute.

### Next.js App Router

```tsx
import { UTraceGate } from "@utrace/browser-sdk/next";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <UTraceGate
          config={utraceConfig}
          profile={process.env.NEXT_PUBLIC_UTRACE_PROFILE === "preview" ? "preview" : "production"}
          pending={<PreviewPreparing />}
          renderFailure={(error) => <PreviewBlocked error={error} />}
        >
          {children}
        </UTraceGate>
      </body>
    </html>
  );
}
```

A browser cannot suspend hydration for an asynchronous gate. The helper does the
strictest thing that is possible instead: it starts activation during the first
client module evaluation, before any application client component renders, and
withholds the application subtree until the runtime is ready. In the production
profile it never imports the runtime — the import is dynamic and lives inside
the preview branch — and the annotation surface it supplies is inert.

## Activation

1. The bootstrap mints a fresh per-document nonce.
2. It posts to the same-origin reserved path `/__utrace/activate`, using the
   preview's existing authenticated session context.
3. It verifies the returned document attestation, the signed annotation
   manifest, and the extension page proof with a packaged Ed25519 public key
   through WebCrypto, and checks that the attestation binds this exact origin,
   nonce, SDK installation, protocol, and schema, and that the manifest matches
   the pinned version and digest.
4. It exchanges the attestation once at `/__utrace/telemetry-credential` for a
   short-lived, write-only telemetry credential.

Nothing from that exchange reaches a URL, browser storage, page markup, or an
application-visible global. The credential is held in a private field, travels to
ingestion as a WebSocket subprotocol value (the only request header a browser
`WebSocket` can set), and its `toJSON` throws so an accidental serialization of
runtime state cannot leak it.

Reload, full navigation, and a telemetry credential that has expired or that
ingestion rejects each run the gate again with a new nonce. A transport
disconnect or a resume reuses the unexpired credential; see
[Export, buffering, and acknowledgement](../../docs/arch-components/browser-sdk.md#export-buffering-and-acknowledgement).

## Annotations

Declarative attributes: `data-utrace-surface`, `data-utrace-entity`,
`data-utrace-target`, `data-utrace-visual-target`, `data-utrace-sensitive`,
`data-utrace-safe-value`, `data-utrace-milestone`, `data-utrace-severity`,
`data-utrace-priority`. Any other `data-utrace-*` attribute, an unknown
definition id, or a malformed value is rejected and counted; it never becomes a
generic attribute.

The typed API (`session.annotations`) registers component targets, marks
sensitive subtrees, produces safe values under a signed rule, records workflow
milestones, and sets user-versus-agent actor attribution. It returns a typed
rejection instead of throwing, so an annotation mistake cannot break the client
application inside a live preview.

### Manifest toolchain

```bash
npm run manifest:compile -- --declarations utrace.annotations.json --source src \
  --version 2026.09.20-1 --release main-8f21 --out build/utrace-manifest.json
npm run manifest:keygen  -- --key-id dev-local --out scripts/keys/dev-signing-key.json
npm run manifest:sign    -- --manifest build/utrace-manifest.json \
  --key scripts/keys/dev-signing-key.json --out build/utrace-manifest.signed.json
npm run manifest:verify  -- --signed build/utrace-manifest.signed.json \
  --key scripts/keys/dev-signing-key.json
```

The compiler fails the build closed on an unknown annotation attribute, an
attribute that names an undeclared definition, or a safe-value rule that tries
to grant a class the platform reserves. uTrace holds the production signing key;
`scripts/keys/` is git-ignored and a manifest signed with a development key
cannot verify against a release build, because its key id is not in
`src/activation/signing-keys.ts`.

## Documents, frames, and workers

Every instrumented document has one local runtime. The top-level document's
runtime is the coordinator and the page's only exporter. Both share
`ObservationCore`, which owns the manifest, redaction, the target registry,
route normalization, trace creation, the capture hooks, `source_sequence`, and
baseline projection; only the sink differs.

An instrumented iframe calls `activateUTraceFrame(window, config)` from inside
the frame. It attests its own document, joins the coordinator over an
authenticated `MessageChannel`, and forwards normalized records. It takes no
telemetry credential, opens no WebSocket, and assigns no `export_sequence`. The
coordinator re-envelopes each record under the child's runtime, document
attachment, and frame identity, and accepts a child only when its attestation
binds the same client, client user, customer account, Interview Session,
preview environment, and runtime allocation. An uninstrumented frame stays
opaque.

`session.grantWorkerCapability(worker, expiresAt)` hands one dedicated worker a
single capability and a private port. A second grant to the same worker is a
duplicate-capability failure, and a Shared Worker or Service Worker is refused:
per-operation trace and session context must travel with each request or
message instead.

## Extension bridge

The extension opens the channel. It creates the `MessageChannel` and posts a
`page_proof` request on `window` with one port transferred; the SDK takes the
port, answers the proof on it, and then serves the rest. That one handshake
covers both the service worker's lightweight page-proof probe and the content
script's long-lived bridge, so the SDK implements exactly one.

Every frame is a structured-cloneable object
`{schema_version, kind, phase, exchange_id, payload}`, and a request and its
answer share the same `exchange_id`. The wire format is owned by
`packages/utrace-contracts/schemas/extension-protocol/bridge-frame.v1.schema.json`
and its five per-kind schemas; `browser-observation/extension-bridge-message.v1`
states the envelope this runtime speaks and deliberately does not restate those
payloads.

| Kind | Request | Answer |
| --- | --- | --- |
| `page_proof` | extension | SDK: the signed page proof, the document attachment, and the registry revision |
| `target_query` | extension, by target ids or by definition | SDK, as `target_geometry`: clipped top-level viewport rects, visible fraction, occlusion, and revisions. Zero matches are `not_found` and several are `ambiguous` — never a nearest guess |
| `detail_query` | extension | SDK: an authorized, read-only result, or a typed refusal |
| `pause_notice` | extension, one-way | none; the runtime follows the authoritative transition |

No credential crosses this port, and product telemetry never travels over it.

## Export

The top-document runtime exports over a dedicated authenticated WebSocket to
`WS /v1/ingest/browser` under the ingestion base URL the credential names.
Frames are `stream.open`, `observation.batch`, and `stream.end` outbound;
`stream.accepted`, `ack`, `flow_control`, and `error` inbound.

`source_sequence` is monotonic within one runtime and is assigned when a record
is produced. `export_sequence` is assigned by the exporter when it drains a
record, in priority order: interactions, committed routes, product requests,
errors, milestones, targets, and gaps first; coalesced DOM changes next;
batched performance measurements last. Because a dropped record never received
an `export_sequence`, the export stream stays contiguous and ingestion's
contiguous acknowledgement watermark can always advance; a dropped range is
reported as an explicit `observation.gap` record naming its `source_sequence`
range, class, and reason, followed by a fresh baseline.

An acknowledgement beyond the produced sequence, a regression, or an
incompatible stream identity is an integrity failure that withdraws readiness —
never a reconnect hint.

## Commands

```bash
npm install            # inside packages/utrace-browser-sdk only
npm run typecheck      # tsc --noEmit
npm run lint           # eslint
npm run build          # tsc --build, emits dist/ with declarations
npm test               # vitest: both the unit and chrome projects
npm run test:unit      # jsdom units only
npm run test:chrome    # real Google Chrome only
```

`npm run test:chrome` bundles the fixture in `tests/chrome/fixture/` with
esbuild, replaces the packaged signing trust anchor with a key generated for
that run, starts the test preview front door on port 8210, a third-party origin
on 8212, and the test ingestion WebSocket on 8211, then drives the locally
installed Google Chrome through `playwright-core`. The servers under
`tests/chrome/support/` implement the documented contracts of the preview front
door, the control plane, and telemetry ingestion, which other workstreams own.

The Chrome suites cover activation and readiness, duplicate-runtime refusal,
the initial baseline, contiguous sequences and acknowledgement, interaction,
input and redaction, trace-injection scope, flow-control shedding with an
explicit gap, the extension bridge and detail queries, pause and resume, a new
stream after reload, acknowledgement-integrity failure, attestation
verification failure, and an instrumented child frame forwarding through the
coordinator.
