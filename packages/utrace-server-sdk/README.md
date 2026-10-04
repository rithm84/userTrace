# @utrace/server-sdk

The uTrace-owned OpenTelemetry distribution for prepared-preview Node.js
services, as specified in [Server SDK](../../docs/arch-components/server-sdk.md).
It is not a bridge to a client's production observability system: in a prepared
preview it owns the sole provider, context manager, propagator, sampler,
instrumentation set, processors, and exporter, and in a production startup
profile it does nothing at all.

## Bootstrap

Preview startup profiles load the SDK before application modules:

```bash
node --import @utrace/server-sdk/preload ./server.js
# or
NODE_OPTIONS="--import @utrace/server-sdk/preload" node ./server.js
```

Node awaits the preload module before it loads the application entry point, so
activation completes first. A CommonJS or custom bootstrap awaits `register()`
and only then loads the application:

```js
const { register } = require('@utrace/server-sdk/register');
register().then(() => require('./server.js'));
```

`register()` also installs the ESM module hook, so an application whose graph is
`import`-based is patched as well. A dormant process installs neither hook.

Activation failure rejects on purpose: a prepared preview that cannot be
observed must not boot as an unobserved substitute. A later instrumentation or
exporter failure never throws into the client's business request path; it
withdraws readiness and reports the exact failure instead.

## Environment

The prepared-environment runtime injects these; nothing is read from the
repository or from client configuration.

| Variable | Meaning |
| --- | --- |
| `UTRACE_SERVER_BOOTSTRAP_CREDENTIAL` | One-time bootstrap credential. Its absence is the single signal that keeps the process dormant. |
| `UTRACE_SERVER_CONTROL_URL` | `wss://` base URL of the control plane; it may carry a path prefix, which every control connection keeps. |
| `UTRACE_SERVER_INGEST_URL` | `https://` base URL of the telemetry ingestion edge; it may carry a path prefix, which every export keeps. |
| `UTRACE_SERVER_SERVICE_NAME` | The manifest `service_key` this process claims. |
| `UTRACE_SERVER_MANIFEST_TRUST_ANCHOR` | Base64url JSON `{key_id, algorithm, public_key_spki_base64}` used to verify the signed manifest. |

Without the bootstrap credential the package is fully dormant: no hooks, no
provider, no control connection, no exporter, and every annotation call is
inert. With it, all four companions are required; a missing or malformed one is
an activation failure, never a lower-fidelity fallback.

Everything else — buffer bounds, batch sizes, backoff, flush timeouts, the
propagation probe, the resource identity — arrives in the activation handshake
beneath the packaged platform ceilings in `src/constants.ts`. A handshake value
above a ceiling is an invalid activation rather than a clamped one.

## Contract surfaces

JSON Schemas live in
[`packages/utrace-contracts/schemas/server-activation/`](../utrace-contracts/schemas/server-activation):
the instrumentation manifest and its signed envelope, the activation record,
both directions of the control protocol, the export batch envelope, the health
report, and the typed failure. The matching runtime validators are hand-written
Zod schemas under `src/contracts/`; they stay in this package until the
contracts workstream publishes generated bindings, and `tests/contracts.test.ts`
validates the same fixtures with Ajv and with Zod so the two cannot drift.

- **Control:** `WS /v1/server-sdk/control` carries `activate`, `rotate`,
  `health`, `command_ack`, and `stream_report` from the SDK, and `activated`,
  `rotated`, `health_ack`, `pause`, `resume`, `terminate`, and `error` from the
  control plane. The handshake presents the bootstrap credential in
  `Authorization`.
- **Export:** `POST /v1/ingest/otlp/v1/traces` with binary Protobuf and gzip.
  The batch envelope travels in `x-utrace-*` headers, outside the OTLP payload,
  so a retry preserves the batch ID, sequence, span identities, and content
  hash. Only a full success acknowledges a batch; a partial success fails the
  stream.
- **Preview entry:** the preview front door places the activation-scoped token
  in `x-utrace-preview-entry`. A remote `traceparent` becomes a parent only when
  that token matches, and the SDK re-injects it toward manifest-approved
  propagation targets so the next service applies the same gate.

## What the SDK exports

One trace is one causal product operation. An inbound request produces one
authoritative `SERVER` span named from the manifest's registered route
template, with framework handler, database, cache, queue, and downstream client
spans beneath it as the work requires.

Redaction runs before a finished span can reach the export buffer, in the
contract's order: platform exclusions, activation scope, signed client policy,
registered annotation definition, per-value evaluation, defensive sensitive-data
detection, normalization and cardinality bounds, then schema validation. The
input span is never mutated; a new redacted span is built and only that one is
buffered. `src/redaction/attributes.ts` states the complete retained field set
per class.

The `utrace.*` namespace carries only registered product concepts, and the
annotation API is typed and registration-based: a registered product operation,
a workflow milestone, an approved entity class, a bounded safe value, an
expected outcome, or a registered background root. There is no `setAttributes`
and no free-form event API.

## Known deviations and gaps

- **Unregistered inbound routes fail.** A request path that matches neither a
  registered route nor registered operational noise produces a
  `route_not_registered` failure and withdraws readiness rather than exporting a
  raw URL. The doc states exclusion is registration-based and that unknown
  operations fail the contract; this is that rule applied to inbound HTTP.
- **Outbound tracing follows `propagation_targets`.** A destination outside the
  manifest's propagation targets is not traced at all, so it can never receive
  uTrace context. `redaction_rules.allowed_http_destinations` is the second,
  defensive layer and must therefore be a superset.
- **Redis and BullMQ adapters are not installed.** The manifest adapter set is
  `node_http`, `undici`, `pg`, `express`, `fastify`, and `next_server`. The cache
  and queue redaction rules are implemented and tested, but no cache or queue
  auto-instrumentation ships yet.
- **Serverful Next.js uses the Node HTTP adapter.** Route templates come from
  the manifest rather than from Next.js internals. `tests/e2e-browser` in the
  uTrace repository exercises it against a real serverful Next.js application.
  That found one thing worth stating: Next.js instruments itself as soon as a
  global tracer provider exists, starting spans under its own `next.js` scope
  with no way to turn it off. Those spans repeat the request the authoritative
  HTTP `SERVER` span already covers and carry no registered route, so
  `next_server` suppresses that scope — the spans are dropped as framework
  churn rather than failed as a manifest gap, and readiness is unaffected.
  `ADAPTER_SUPPRESSED_SCOPE_NAMES` in `src/constants.ts` is the list.
- **Fastify uses `@fastify/otel`.** `@opentelemetry/instrumentation-fastify` is
  deprecated in favour of it.

## Commands

`scripts/` ships with the package, so a client can compile and sign a manifest
with the same toolchain uTrace uses: `npm run manifest:keygen` creates a
development Ed25519 identity and prints the
`UTRACE_SERVER_MANIFEST_TRUST_ANCHOR` value for it, and `npm run manifest:sign`
signs a manifest document over its RFC 8785 canonical form. uTrace holds the
release keys, so a manifest signed that way is usable only by a preview whose
environment names that development key as its trust anchor.

```bash
npm install          # inside this package
npm run build        # tsc -p tsconfig.build.json
npm run typecheck
npm run lint
npm test             # builds first: the suite drives the built preload
```

The suite starts real applications (Node HTTP, Express, Fastify, and an ESM
service) in their own processes through `node --import`, and runs a test control
plane and a test OTLP ingestion edge that implement the documented contracts in
`tests/helpers/`. The `pg` suite uses the real database at
`UTRACE_DATABASE_URL` and only the `utrace_server_sdk_test` schema. Every port
is inside 8300-8399.
