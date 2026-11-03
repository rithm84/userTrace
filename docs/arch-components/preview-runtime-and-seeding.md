# Preview runtime allocation and data seeding

[Back to uTrace architecture](../ARCHITECTURE.md)

Every interview requires a Preview Runtime Allocation, the full running copy of
the client's product that the
[PRD](../PRD.md#preview-runtime-allocations-implementation-workspaces-and-session-data)
defines. Each runtime boots from the pinned template and installs the pinned [seed
manifest](#seed-manifest):

![The client configuration release pins the runtime template and seed manifest; the template snapshot boots every Vercel Sandbox runtime, the seed is installed and verified in each, and a base or candidate route carries traffic only after every gating readiness probe passes.](../diagrams/svg/preview-runtime-and-seeding.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Template and readiness

Each client configuration pins a Vercel Sandbox runtime template:

- a custom image in Vercel Container Registry, pinned by digest
- a template snapshot taken after the product is installed and built
- the product revision, SDK versions, database shape, explicit startup and health
  contract, seed manifest revision, and supported preview origin

### Readiness probes

The template recipe splits readiness probes into two sets:

- **Gating:** `service_health`, `data_identity`, `authenticated_handoff`,
  `route_resolution`, `stale_route_rejection`, `server_sdk_activation`,
  `redaction`, and `side_effect_neutering`.
- **Advisory:** `browser_observation` and `trace_correlation`, because they need
  the participant's own traffic.

A pooled runtime passes service health, data identity, redaction, and
side-effect neutering before it joins the pool. Once the participant opens the
route, the advisory probes are asked again with `server_sdk_activation`, and all
three gate observation-dependent work.

The probes reach the runtime through its port with a signed route context; a
session base's traffic probes go through the public front door only when
`UTRACE_PREVIEW_PUBLIC_PATH_ENABLED` is set. Failures identify the incompatible or
missing configuration and the repair it needs; uTrace does not infer arbitrary
stack behavior.

Runtime readiness feeds the
[session readiness boundary](./interview-and-voice.md#readiness-has-two-boundaries);
healthy voice alone cannot authorize observation-dependent work.

### Template identity

Template identity is derived rather than chosen. The template record stores a
recipe digest, a SHA-256 over the canonical recipe, its build arguments, the
derived container definition, the pinned product source revision, the seed
manifest digest, and the resolved SDK versions, beside the image digest and
snapshot ID. Templates differing in any of those cannot share a record, and an
allocation's recorded template digest identifies exactly what it booted. Template
snapshots have no expiry, because Vercel otherwise expires a snapshot 30 days
after its last use.

A template image must build the same way twice, so the client repository commits
its lockfile, without which the image build fails outright. Packages the image
cannot fetch publicly, such as the uTrace SDK packages, are vendored into the
image before installation.

### Sandbox behavior

Vercel Sandbox runs each runtime in its own Firecracker microVM and does not run
an image's `ENTRYPOINT` or `CMD`, so the control plane starts the product's
supervisor with a detached sandbox command. Preview runtimes are created
non-persistent, because Vercel sandboxes persist by default and snapshot their
filesystem when they stop, which would retain seeded data after the allocation
ends; see [Understanding Sandboxes](https://vercel.com/docs/sandbox/concepts).

## Side-effect neutering

Preview safety has two layers, both proven by tests: network and credential
policy removes production authority and restricts egress, and application
adapters replace email, payments, webhooks, queues, destructive integrations, and
other side effects with recorded preview sinks, so the preview keeps realistic
behavior without real external effects.

## Allocation lifecycle

A claimed base allocation is exclusive to one Interview Session; candidate
allocations are separate and immutable per Preview Version, and a base runtime
never becomes a candidate. Vercel sandbox names, session IDs, and port URLs are
provider references, not lifecycle identity.

Each allocation carries a pool role:

![A warm_pool runtime is claimed into session_base, where it is bound, configured, and routed; a candidate is inserted directly; a warm runtime whose template is retired or whose claim margin has passed, and any allocation no longer needed, is stopped and deleted.](../diagrams/svg/preview-runtime-and-seeding-allocation-lifecycle.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

A warm runtime's case columns start empty. The claim locks the runtime and sets
the `session_base` role and its Product Signal and Interview Session in one
transaction. A candidate is inserted
with the `candidate` role, its Product Signal, and its Preview Version. One base
allocation per Interview Session and one live candidate per Preview Version are
database constraints, not conventions.

### Claim-time configuration

Claim-time configuration is required, not an optimization: a pooled runtime has
no Interview Session or preview origin, so it cannot yet hold a Server SDK
bootstrap credential bound to either. The activation settings go into the
runtime's application directory, and a sandbox command restarts the product's
supervisor so the supervised process reads them before the claim-time health
probe.

The supervisor's shutdown signals only its own children, so the restart also
signals deeper processes, such as the server a package manager's `run` wrapper
spawned, once the supervisor exits; direct children keep a grace period to flush.
A restart first ends a live server stream as release does: uTrace terminates it
as `activation_superseded` and waits, bounded, for its terminal report, since the
framework may exit on the shutdown signal before the Server SDK finishes
flushing.

### Runtime lifetime

A runtime's planned lifetime is the execution limit, and a warm runtime spends
part of it waiting. The allocation records when it ends (`runtime_expires_at`).
Only a warm runtime with more than the claim margin left is claimed or counts
toward the pool target, and the claim restarts the lifetime so the session gets
the full limit.

If no claimable healthy base runtime is ready when the user arrives (the
[scenario](../SCENARIO.md#starting-state-and-fixture) starts with one), one is
created from the template while greeting and problem discussion continue under the
session readiness boundary; setup that misses the readiness window follows its
recovery contract and never authorizes reduced-observation work.

### Sandbox deadlines

The sandbox never holds the whole lifetime. A preview runtime, like a prepared
[implementation workspace](./implementation.md#prepared-workspace), is created
with a deadline of at most 20 minutes. Every minute,
`utrace.scan_sandbox_deadlines` queues one `sandboxes.extend_deadlines` Job per
client still tracking a live sandbox, and the Job extends each tracked sandbox
whose deadline is under 10 minutes away to 20 minutes from now, never past its
recorded end. A tracked sandbox therefore survives about ten missed scans.

A sandbox no row tracks, because its test or script died, a reset truncated its
row, or a worker crashed between creating and recording it, is never extended and
stops within 20 minutes.

## Seed manifest

The demo's dataset is a manually prepared synthetic manifest:

- seed records
- frozen-data hashes
- a fixture clock where the workflow depends on time
- the authenticated identity the product handoff signs in as

Installation writes the digest-pinned manifest into the runtime's isolated
database and verifies referential integrity and the pinned
hashes before readiness.

The fixture clock stays part of the identity, but the runtime presents the data
on today's date: at each start it moves every date in the dataset forward by the
whole days elapsed since the clock, and the product's "now" moves with them. The
clock's day reads as today, a relative range such as `Last 30 days` ends today
and selects the same rows as at the clock, and the recorded manifest and hashes
do not change.

Base and candidate runtimes install the same manifest independently; a candidate
never copies data from the base. As the
[PRD](../PRD.md#seed-data) requires, the manifest preserves what the workflow
depends on and never presents a synthetic value as production evidence. No
customer data source or production credential is involved, and the
implementation workspace never receives the dataset.

## Failure, cleanup, and reset

Seed or readiness failure is typed, preserves evidence, and blocks
observation-dependent work. Partial datasets are destroyed rather than repaired
in place.

A session preview whose preparation fails enters bounded recovery: until a
recovery deadline pinned at the first failure, uTrace retries readiness on the
session's committed route or finishes preparing the runtime the session holds,
and retries never extend the deadline. Past it, the session closes as blocked and
the case ends as
[terminal expiry](./failure-recovery.md#bounded-return-and-terminal-expiry)
describes.

When an allocation is no longer needed, required minimized evidence is extracted,
the route and credentials are revoked, and the sandbox is stopped and deleted. A
data-bearing runtime is never reset into a warm pool; fresh provisioning replaces
it. Pinning a new template retires the previous one: its unclaimed warm runtimes
can never serve a claim, so the pool replaces them, and any warm runtime past the
claim margin, from the current template.

Every allocation records the pinned configuration release, template digest, seed
manifest, clock, authenticated identity, SDK versions, and route revision.
Validation evidence is admissible only when it cites the exact candidate and
matching dataset identity.
