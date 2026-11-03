# Preview build and publication

[Back to uTrace architecture](../ARCHITECTURE.md)

Workspace output that passes its fast checks, its production build among them,
becomes an immutable Preview Version in an already-running candidate runtime, and
its full checks finish before anything is delivered:

![Workspace files install into an already-running spare candidate runtime, which builds them for production while its database follows their schema; they pass fast checks and a readiness proof, usually before the participant answers, and the route switches once the change is approved; the lint and tests then pass while the participant validates; a failed check starts a correction cycle and a failed readiness proof is a typed blocker.](../diagrams/svg/preview-build.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

The [implementation controller](./implementation.md) owns authorization, budgets,
and cycles.

## Run the files that were checked

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
change those profiles. The Preview Version identifies the files the participant
runs and validates, and the production build they are served from is built from
those same files with the instrumented preview profile; it makes no claim that
production bytes match it.

To accept a candidate, the control plane verifies lease, scope, source revision,
fast checks, artifact digests, secret scan, and policy; to deliver it, the full
checks on the same file digests as well. The coding model cannot relabel a failed
or ambiguous check as success.

## Spare candidate runtimes

Each candidate runs in a spare candidate runtime that the control plane starts
before the candidate exists, from the pinned client template: once the session's
base route is ready, and again after each swap that no standby spare covers while
the case can still produce a version. A spare:

- installs its own copy of the dataset with the base demonstration's approved
  seed identity, so the base allocation is never mutated
- activates its Server SDK for its own allocation before its first start, so it
  never restarts to activate
- serves the image's production build until it is given files, and renders its
  landing page once, so a spare that cannot serve is known ahead of time
- when the session is already served a Preview Version, as after a swap, is given
  that version's files and builds them before it is ready, so a change asked for
  while the participant tries the version rebuilds only what the change touches
- stays unrouted, holding preview-only credentials and neutered side-effect
  adapters, never workspace or production credentials

A session also keeps a standby spare, which builds the version the session is
about to be served before it is. It starts when the session's first draft builds
and is restaged and rebuilt, incrementally, for each newer draft that builds, so it
always holds the newest one. Nothing stages a candidate into it. When a version is
swapped in:

- A standby that already serves a build of exactly that version's files becomes
  the session's next spare at once, ready for a change asked for while the
  participant tries the version.
- One that holds other files is torn down, and a new spare builds the served
  version, so no spare ever holds files the session is not served.
- One still rebuilding is settled the same way when its build finishes.

A new draft after the swap, such as a change asked for during validation, starts
the next standby. A spare or standby spare is torn down when its session closes,
as when the participant leaves, and with every other allocation when the case
ends.

A candidate's files are written into a spare that has served no version, which
then serves them as production does. It builds them with the recipe's
`rebuild_command`, in the environment the image's build ran in and with the build
cache that build left, so the rebuild compiles only what the files changed; the
spare's database follows their [schema](#schema-changes) beside the build. A
passing build is served by restarting the application alone with the recipe's
`restart_application_command`, after the spare's server stream is ended, and the
spare waits for its Server SDK to activate again. A spare that already serves a
build of exactly these files is not built or restarted again. A draft's files may
be written there before the gate decides, because nothing routes to a spare; a
runtime that has served a version is never given other files.

Drafts finish their candidates there before the participant answers. The intake
draft's files are built as soon as it is written, so a proposal that revises it
rebuilds only the files it changes, and none when it changes none. A proposal's
draft is built, rendered, and proven ready for the Preview Version identifier it
minted and the session's next route revision. Once the change is approved, the
swap needs no staging, building, or probing of its own.

## Fast checks before the swap

Before the session's route can point at a candidate, its fast checks pass on the
files' digest:

- **Type check:** the required type check, run incrementally in the workspace.
- **Production build:** the spare's build of the files, which is the required
  check the policy marks `runs_in_candidate_runtime`: the participant is served
  that build, and the full checks never build again.
- **Render check:** the spare serves the page of each declared target the change
  adds, through its runtime port with a signed route context and the authenticated
  handoff, with HTTP 200, and serves every client module that page loads, which
  together must declare every target the change adds. A change that adds no
  declared target renders the page the authenticated handoff lands on.

The render check reads what the server sends, because the browser SDK activates
only through the preview front door on the session's own origin, and the product
renders its controls in the browser only after that activation. A failure that
happens only in the browser after the page loads is therefore not caught before
the swap.

## Schema changes

Database changes run only against the spare's own database; the base allocation
is never touched. A spare installs its dataset from the base's schema file when it
starts, before any candidate exists, so the candidate's schema file, which the
recipe names as `schema_path`, is compared with the base's statement by statement:

- **In place:** when every statement the candidate adds only adds (a table, an
  index, a column, or a comment, or drops only a table the change itself creates)
  and none of the base's is gone or changed, the added statements run in one
  transaction through the recipe's `schema_command`, beside the build. A later
  staging that adds more applies only the rest.
- **Restart:** any other difference is destructive. Once the build passes, the
  whole spare restarts instead of its application alone, which installs the
  dataset afresh from the candidate's schema file and serves the new build, once
  per schema.

The spare records the change it followed, its mode and digest, and the deployment
follows the artifact's schema again before anything is proven, which changes
nothing when the staging already did. The dataset a version binds is therefore
the approved seed identity, which dataset verification checks, with exactly that
schema change, which its candidate binding records. Preview schema success never
grants production migration authority.

## Readiness and Preview Version lifecycle

A Preview Version becomes `ready` only after its installed files match the
artifact's digests, its fast checks pass, its dataset verifies against the
approved seed identity, and the candidate runtime passes the template's
[gating readiness probes](./preview-runtime-and-seeding.md#template-and-readiness)
against the route revision it is about to receive. Probes reach the
candidate through its runtime port, never the public front door. The durable
record binds attempt revision, artifact manifest, data identity,
template, route revision, and check evidence.

A proof recorded before the gate decided stands for the swap only for the same
version identifier and route revision, and only for 90 seconds
(`PREPARED_READINESS_SECONDS`); staging other files into the spare or restarting
it withdraws the proof. Otherwise the swap verifies the dataset and runs the
probes itself.

Each ready version records its full checks, which move `running` → `passed` or
`failed` on the version's file digest. Only a version whose full checks passed can
be [validated](./validation.md#outcome-criteria) and delivered.

Preview Versions are immutable and never patched in place: a change the
originating user asks for yields a new Preview Version from a new gate-approved
attempt, a correction after a failed full check yields a new version from the same
attempt, and unsuccessful validation closes the case instead of producing another
candidate.

## Candidate handoff

When a live candidate is ready, the control plane
[points the stable session origin](./preview-front-door.md#caching-and-routing)
to that exact version in one committed route revision and tells orchestration
which critical facts changed, and validation waits for a fresh observation
binding. The participant's previous version keeps serving until that commit, so
they see either it or the complete new one. A preview swap grants no new
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
