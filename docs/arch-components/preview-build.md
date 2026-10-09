# Preview build and publication

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how checked workspace output becomes an immutable Preview Version:

![A fenced workspace produces checked artifacts that run unchanged in a separate candidate runtime.](../diagrams/svg/preview-build.svg)

The [implementation controller](./implementation.md) owns authorization and
budgets.

## Build once, run the same bytes

An approved attempt begins at the pinned GitHub revision inside its fenced Vercel Sandbox workspace. The coding agent inspects exact files through filesystem search and reads and applies only the frozen scope; the control plane runs the required checks on the clean base and once on the candidate. The workspace emits a source manifest, patch, dependency lock identity, build recipe, check results, and content-addressed artifacts. Publication never rebuilds from an unpinned branch. The build recipe pins the
[SDK build/startup profile](./client-configuration.md#sdk-source-integration) already merged
by the client. Required checks verify preview instrumentation and production
exclusion; implementation cannot silently change those profiles. The Preview
Version identifies the preview artifact, not a claim that production bytes are
identical to that instrumented build.

The control plane verifies lease, scope, source revision, required checks, artifact digests, secret scan, and policy before accepting a candidate. A failed or ambiguous check cannot be relabeled as success by the coding model.

## Candidate runtime deployment

Each accepted candidate is installed in a fresh Vercel Sandbox runtime created from the pinned client template. Its files are installed and built before the runtime first starts, so it starts once, on the candidate build. That build runs while the workspace's required checks run, from the same staged files; nothing routes to it, and it is destroyed when the checks fail. The deployment adopts it only when every file digest matches the published artifact, and builds afresh otherwise. Its dataset is installed independently with the same approved seed identity used for the base demonstration. The base allocation is not mutated. Runtime configuration contains preview-only credentials and neutered side-effect adapters, never workspace or production credentials.

## Migrations

Database changes run only against the candidate's isolated database. Migration provenance, before/after schema digest, seed compatibility, reversibility assessment, and check results become candidate evidence. A destructive or production-coupled migration outside policy blocks the attempt. Preview migration success never grants production migration authority.

## Readiness and Preview Version lifecycle

A Preview Version becomes `ready` only after its installed files match the artifact's digests, its dataset verifies against the approved seed identity, and the candidate runtime passes the template's [gating readiness probes](./preview-runtime-and-seeding.md#template-and-readiness) against the route revision it is about to receive. The probes reach the candidate through its runtime port, never through the public front door. The durable record binds attempt revision, artifact manifest, data identity, template, route revision, and check evidence.

Preview Versions are immutable. The system never patches a candidate in place: a change the originating user asks for during validation produces a new Preview Version from a new gate-approved attempt, and an unsuccessful validation closes the case rather than producing another candidate.

## Candidate handoff

When a live candidate is ready, the control plane atomically points the stable
session origin to that exact version and tells orchestration which critical facts
changed. Validation waits for a fresh observation binding. Conversation and
implementation follow the [interruption contract](./implementation.md#conversation-pause-and-interruption);
a preview swap cannot grant new implementation authority.

## Publication failure and retention

Build, deploy, seed, route, or readiness failures remain typed blockers and preserve resumable evidence when infrastructure repair is possible. They never produce validation claims. Cancellation destroys non-required artifacts and produces no Case File. After successful same-user validation, the exact source/artifact identity represented by the draft PR is recorded in the Case File. Derived runtime bytes expire under retention policy; durable hashes and provenance remain while the Case File is retained.
