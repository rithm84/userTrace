# Preview build and publication

[Back to uTrace architecture](../ARCHITECTURE.md)

Checked workspace output becomes an immutable Preview Version:

![A fenced workspace produces checked artifacts that run unchanged in a separate candidate runtime.](../diagrams/svg/preview-build.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

The [implementation controller](./implementation.md) owns authorization, budgets,
and cycles.

## Build once, run the same bytes

An approved attempt starts at the pinned GitHub revision in its fenced Vercel
Sandbox workspace, applies only the frozen scope, and emits:

- a source manifest and patch
- the dependency lock identity
- the build recipe
- check results
- content-addressed artifacts

Publication never rebuilds from an unpinned branch. The build recipe pins the
client's already-merged [SDK build/startup
profile](./client-configuration.md#sdk-source-integration). Required checks verify
preview instrumentation and production exclusion; implementation cannot silently
change those profiles. The Preview Version identifies the preview artifact and
makes no claim that production bytes match that instrumented build.

To accept a candidate, the control plane verifies lease, scope, source
revision, required checks, artifact digests, secret scan, and policy; the coding
model cannot relabel a failed or ambiguous check as success.

## Candidate runtime deployment

Each accepted candidate runs in a fresh Vercel Sandbox runtime from the pinned
client template; its files are installed and built first, so it starts once, on
the candidate build. That build runs from the same staged files during the
required checks, unrouted, and failing checks destroy it. The deployment adopts it
only when every file digest matches the published artifact, and otherwise builds
afresh.

The candidate installs its dataset independently, with the base demonstration's
approved seed identity; the base allocation is not mutated. Runtime
configuration holds preview-only credentials and neutered side-effect adapters,
never workspace or production credentials.

## Migrations

Database changes run only against the candidate's isolated database. Migration
provenance, before/after schema digest, seed compatibility, reversibility
assessment, and check results become candidate evidence. A destructive or
production-coupled migration outside policy blocks the attempt, and preview
migration success never grants production migration authority.

## Readiness and Preview Version lifecycle

A Preview Version becomes `ready` only after its installed files match the
artifact's digests, its dataset verifies against the approved seed identity, and
the candidate runtime passes the template's
[gating readiness probes](./preview-runtime-and-seeding.md#template-and-readiness)
against the route revision it is about to receive. Probes reach the
candidate through its runtime port, never the public front door. The durable
record binds attempt revision, artifact manifest, data identity,
template, route revision, and check evidence.

Preview Versions are immutable and never patched in place: a change the
originating user asks for yields a new Preview Version from a new gate-approved
attempt, and unsuccessful validation closes the case instead of producing another
candidate.

## Candidate handoff

When a live candidate is ready, the control plane
[points the stable session origin](./preview-front-door.md#caching-and-routing)
to that exact version and tells orchestration which critical facts changed, and
validation waits for a fresh observation binding. A preview swap grants no new
implementation authority; conversation and implementation follow the
[interruption contract](./implementation.md#conversation-pause-and-interruption).

## Publication failure and retention

Build, deploy, seed, route, or readiness failures remain typed blockers,
preserving resumable evidence when infrastructure repair is possible, and never
produce validation claims. Cancellation destroys non-required artifacts and
produces no Case File.

After successful same-user validation, the Case File records the exact
source/artifact identity the draft PR represents. Derived runtime bytes expire
under retention policy; durable hashes and provenance last as long as the Case
File.
