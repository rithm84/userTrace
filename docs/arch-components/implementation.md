# Implementation controller and isolated workspace

[Back to uTrace architecture](../ARCHITECTURE.md)

The coding agent drafts the requested feature as soon as the case's workspace is
ready, and adjusts that draft to the change the voice agent proposes, before the
participant agrees to it. One Implementation Gate approval then yields one
Implementation Attempt, which adopts that draft when it is the change the gate
approved. The draft has already passed its fast checks, so
[Preview Build](./preview-build.md) publishes it as an immutable Preview Version at
once, and its full checks run while the participant validates it:

![The coding agent drafts the original request, then adjusts the draft to the voice agent's proposal while the gate prepares its evaluation; the intake draft and then the proposal's revision are built for production in the spare candidate runtime, whose schema, render check, and readiness finish before the answer; after the participant agrees and the gate approves, the controller adopts the recorded draft, publishes it from its record and switches the route, confirms the workspace holds the published tree and runs the lint and tests, with a failure starting a correction cycle, and hands the workspace over for the next change.](../diagrams/svg/implementation.svg)

## Supported implementation mode

The demo supports Feature Request Flow in one authorized repository; the
Implementation Gate chooses `approved_live` or `no_implementation`. A Product
Signal may use up to the `implementation_limit` its configuration release pins
(three for the demo client): the first Implementation Attempt plus one per change
the originating user asks for while trying a new version. Each attempt has its
own approving gate decision, and at most one per case is in flight. Async
implementation and iteration after unsuccessful validation are outside the demo.

## Enforcement model

The coding agent, GPT-6.1 Sol in Codex, works inside a Vercel Sandbox workspace.
The voice agent's tools (which read observed activity, propose changes, and record
agreements) and Jev (typed semantic-attention results) cannot write code or
authorize an effect. Only the control plane approves the Implementation Attempt,
grants its fenced Environment Lease, accepts artifacts, and advances durable state.
A [prepared draft](#prepared-draft) holds no lease and no authority: only an
attempt that adopts it can turn its files into a Preview Version.

The workspace, separate from preview runtimes as the
[PRD](../PRD.md#preview-runtime-allocations-implementation-workspaces-and-session-data)
requires, starts from a uTrace-owned, digest-pinned image or snapshot. It holds
the authorized repository checkout and scratch space, but no copied customer
dataset, production credentials, client data, or workflow authority. Its network
policy bounds what it reaches:

![The controller creates the workspace sandbox for the current lease and collects its artifacts; all sandbox egress passes the firewall, which adds the OpenAI credential and permits only registries and uTrace endpoints.](../diagrams/svg/implementation-workspace-boundary.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

Every attempt has four bounds:

- **Network:** the sandbox reports no per-connection block events, so after every
  cycle the controller scans the run's commands, the agent's included, for
  unpermitted hosts. Repeated or exfiltration-shaped violations end the attempt
  as `unsafe`.
- **Code surfaces:** blocked paths stay readable when their interfaces are
  necessary but cannot appear in the accepted diff, which
  [scope verification](#scope-verification) checks every cycle.
- **Compute:** elapsed-time, agent-turn, cost, process, artifact-byte,
  correction-cycle, and changed-file budgets are all required, pinned by the
  Product Signal's policy, and frozen by the gate decision. Each agent run is
  bounded by its command timeout and charged afterwards; once a budget is spent,
  the control plane refuses the next step.
- **Lease:** the lease fences the sandbox itself. The control plane stops a
  superseded or revoked attempt's sandbox and accepts artifacts only under the
  current lease, so a stale run may leave logs but cannot publish candidates.

## Agent harness

The coding agent is the Codex CLI (`@openai/codex`), pinned in the workspace
template with that release's model catalog. The control plane runs one
`codex exec` per investigation or authoring cycle as the sandbox's non-root user;
see [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive).

### Run configuration

A Codex home written for each run pins:

- Model `gpt-6.1-sol` at low reasoning effort, on the Ultrafast service tier for
  a prepared draft and an Implementation Attempt and the Fast tier for code
  investigation. On the
  demo's change, Fast-tier Sol matched Fast-tier GPT-6 Astra at a sixth of the
  cost and ran about 30 seconds behind Ultrafast-tier Astra at a seventeenth.
  Codex requests only the tiers a model's catalog entry lists, so the template's
  catalog adds the `ultrafast` tier to Astra and Sol.
- The pinned checkout as working directory and a JSON Schema for the run's final
  report.
- Codex's own sandbox off: its read-only and write modes need bubblewrap, which a
  Vercel Sandbox cannot start, so the workspace sandbox is the boundary. The
  control plane keeps pre-gate investigation read-only by refusing the whole
  investigation if the checkout changed.
- Repository instruction files, web search, plugins, analytics, update checks,
  sub-agents, goals, skills, image viewing, sleeping, and questions to a user
  off, so only the task reaches the agent and requests stay short.
- The HTTP transport, because the sandbox firewall that adds the credential
  refuses WebSocket upgrades.

### Task

An attempt's task carries the frozen scope, acceptance criteria, required checks,
budgets, the gate's cited code findings, and the cited files in full at the base
revision. A draft's carries the proposed change and its checks in place of the
frozen scope, with the policy's paths, checks, and budgets and the findings of the
investigation that ran during the interview. An investigation's carries the
repository's tracked files instead. It tells the agent that the participant is
waiting, and to:

- write no progress commentary
- batch its reads
- make the smallest complete change
- build any new interface from the repository's own design system components
- wire the client's declared uTrace targets and milestones for the workflow it
  changes
- run none of the required checks itself

### Results and provenance

Codex writes its event stream to a file outside the checkout; after the run,
every command and file change in it becomes provenance in the attempt's journal.
The agent's report of success is evidence, not a result: the control plane's own
required checks and scope check decide acceptance.

Before the checks, the formatter the client's build recipe declares runs over the
cycle's added or changed files, so a formatting check judges the change, not its
layout; a failing formatter, as on code that does not parse, leaves them as they
were. Each required check runs once per cycle.

### Credential and provider failures

The OpenAI credential never enters the sandbox: `CODEX_API_KEY` holds a
placeholder, and the firewall supplies the real `Authorization` header. A failed
Codex turn, a refusal included, is a `provider_rejected` failure the attempt
cannot continue past; its Job does not retry it.

## Attempt protocol

The control plane dispatches a typed attempt specification containing:

- the Product Signal, attempt, and gate decision IDs
- the pinned configuration release
- the repository reference and pinned base revision
- the approved-scope object with its required checks and budgets
- the workspace snapshot
- context-bundle references
- the code findings the gate cited
- the permitted callback hosts
- the lease's fencing token

### Creation and dispatch

Before the atomic commit, attempt creation rechecks lifecycle, classification,
gate decision, configuration release, and the case's attempts against the pinned
`implementation_limit`. Replayed requests return the existing attempt, and no
worker dispatches before commit. Workers recheck current authority and execution
limits before each new step and enforce limits on in-flight work. Resume preserves
all consumption and never recreates an attempt to bypass a limit.

### Workspace and starting files

The harness runs in the case's [prepared workspace](#prepared-workspace) or one
the attempt creates, and writes logs and artifacts through uTrace-controlled
storage.

The case's first attempt starts from the clean base; a later attempt's workspace
is checked out at the same base revision and starts from the previous version's
files, so its candidate holds both changes.

A later attempt that claims the workspace the previous version's attempt
[handed over](#prepared-workspace) finds those files already in the checkout.

An attempt adopts the workspace's [prepared draft](#prepared-draft) as its first
cycle's authored change when the agreement its gate decision approved records that
draft and the draft started from the files the attempt starts from. It waits for a
draft still being written, charges the draft's agent turns, cost, processes, and
bytes to its own budgets, adds the draft's commands to its journal, so the network
scan covers them, and checks the draft's patch against the frozen scope like any
other cycle. The draft was written before the gate's exclusions existed, so the
scope check is what holds it to them. A draft the agreement does not record is
removed from the checkout, and the attempt authors its first cycle itself.

An adopted draft's record holds everything publication needs: its files and their
git tree, its patch, its dependency identity, its fast-check and render results on
exactly those files, the artifacts it stored, and a spare candidate runtime proven
ready for the Preview Version identifier it minted. So the attempt publishes the
draft from its record while it is still attaching the claimed sandbox, and the
participant's preview switches before the workspace is open. A record that does
not hold, such as fast checks that are not the scope's or a refused publication,
leaves the first cycle to check the draft in the workspace as it would its own
change.

### Retries and scope changes

Technical retries and check-fix cycles stay inside one attempt while its scope
and budget remain valid. An attempt that finds it needs changed acceptance
criteria, product outcome, risk, or implementation scope ends as
`scope_change_required` and closes the case as
[budget exhaustion](#outcomes-and-budget-exhaustion) does. Only a change the
originating user asks for while trying a ready version returns the case to the
gate.

## Prepared workspace

The control plane prepares the case's workspace beside the base preview, so the
coding agent can draft a change while the voice agent proposes it:

![A prepared workspace is built when the participant claims the invitation and records its baseline; a proposal drafts in it and a newer proposal starts the draft over; an approved attempt with matching pins claims it, or it is released when its case ends or its one-hour window passes.](../diagrams/svg/implementation-prepared-workspace.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

It is built from the case's pinned release alone and reaches the OpenAI API only
for a prepared draft. Preparation also runs the type check in the checkout itself,
so a draft's type check is incremental. The claim binds the sandbox to the attempt
and its fencing token in the same row update that takes it, and the first cycle
starts with the recorded baseline.

Each workspace is one generation of the case's, and a case holds at most one open
generation at a time. An attempt whose version passed its full checks hands its
sandbox over as the case's next generation while the pinned `implementation_limit`
allows another attempt. That generation holds the version's files, the installed
dependencies, and the warm caches, records the version and its git tree as the
files a draft starts from, and holds no lease or authority. A change asked for
while the participant tries the version is drafted in it at once; a draft proposed
while the attempt still holds the workspace waits for the hand-over. An attempt
that destroys its workspace instead marks the claim `released`.

An attempt claims only a workspace prepared from its own specification's pins:
template snapshot, clone URL, base revision, install command, required checks,
and vCPUs. It waits for a preparation still running rather than starting fresh;
with none to claim, it prepares its own and authors the first cycle while the
dependencies install.

Until release, the sandbox runs on a short
[sandbox deadline](./preview-runtime-and-seeding.md#sandbox-deadlines) extended
toward the end of the one-hour window, so a workspace uTrace loses track of stops
within minutes.

### Prepared draft

A draft is one of two kinds, `intake` or `proposal`, with one record and one Job:

- **Intake draft:** once the case's first workspace is ready, the requested
  feature is drafted from the original request and the concern intake recorded,
  so code exists before the call does. It is queued once, before any proposal.
- **Proposal draft:** when the voice agent proposes a change, its `propose_change`
  tool records the proposal and queues a draft. It revises a finished intake
  draft in the workspace into exactly the proposed change, keeping what fits,
  and starts over from the workspace's starting files otherwise.

The Job:

1. Takes the case's newest prepared workspace, waiting while it is still being
   prepared or an attempt still holds it, and resets the checkout to the
   workspace's starting files when an earlier draft it does not revise wrote in
   it.
2. Opens the workspace's egress to the model, waits a bounded time for an early
   code investigation still running, and runs one Codex cycle on the
   [draft task](#task), which carries that investigation's findings and the files
   they cite.
3. Formats, stages, and diffs the change, as an attempt's cycle does.
4. Runs the workspace's [fast checks](#fast-and-full-checks) on the draft's files,
   and at the same time stages them in the session's spare candidate runtime,
   which builds them for production and serves the build, as
   [Preview Build](./preview-build.md#spare-candidate-runtimes) describes. The
   intake draft is built there too, so a proposal that revises it rebuilds only
   the files it changes, and the session's standby spare follows each draft that
   builds, so it already serves the next version when that version swaps in.
5. For a proposal draft, at the same time:
   - stores the draft's large artifacts, the patch and the candidate files, which
     the adopting attempt then references by digest;
   - mints the Preview Version identifier, and the spare is rendered and proven
     ready for it.
6. Records the draft: its files and their digest, git tree, and patch, its
   dependency identity, its fast-check results, the artifacts, and what it
   consumed.

Beside a proposal draft's Codex run, the Implementation Gate
[prepares its evaluation](#gate-preparation) of the proposed change.

A draft moves `proposed` → `drafting` → `drafted`, or to `failed`; `adopted` and
`discarded` end it. A newer proposal discards the older draft, and so does an
agreement that records a different change. The agreement records the draft it
agreed to when the voice agent records the change as proposed, or the same change
up to case and spacing, so adoption never judges free text; it never records an
intake draft. A draft that is discarded while its Codex run is still writing
finishes that run, so the next holder of the workspace waits until the draft
settles.

Every draft records what it spent, whatever became of it, so a case's drafting
cost is what its draft records add up to, including the drafts of a case that ends
with no implementation.

### Gate preparation

While a proposal is drafted, the gate confirms the interview-established flow
classification and asks its proposal model for a proposal over the bundle the
agreement would produce: today's records with the proposed criteria in place of
the agreed ones. It records that proposal on the draft with a digest of every
input the decision depends on: the pinned policy and release, the flow types, the
criteria's text, the confirmed evidence, the telemetry citations, the code
findings, readiness, and the case's attempt count.

Once the participant agrees, the gate assembles the real bundle as always. Only
when the agreement records that draft and the digests are equal does it use the
prepared proposal instead of asking the model again; the deterministic controller
then validates and commits it exactly as it would a fresh one. Any difference,
and any failure of the preparation, leaves the gate to ask afresh.

A draft is prepared, not authorized. Neither the participant nor the voice agent
hears of it. It runs without a lease, and its files run only in an unrouted spare
candidate runtime, never on the participant's preview. It reaches a Preview
Version only through an adopting attempt, which holds the lease and the gate's
frozen scope.

## Conversation, pause, and interruption

Live implementation and checks run concurrently with a fluid interview whose
voice agent does not narrate every command or progress change;
[context assembly](./context-assembly.md#working-context-during-an-interview)
keeps every model role on the same revisioned context.

Implementation authority follows the Interview Session state:

- **Temporary transport or voice loss** starts a bounded reconnection window:
  authorized implementation and checks may continue, but validation waits for
  the same user.
- **An explicit leave** stops new implementation after safe checkpointing. The
  same originating user may return, after fresh readiness checks, while the
  case-level return deadline is open; budgets do not reset, and expiry follows
  [Failure Recovery](./failure-recovery.md#bounded-return-and-terminal-expiry).
- **The session Pause control** stops voice, observation, and new live
  implementation steps; in-flight operations reach a safe checkpoint, then stop.
  A spoken request such as “give me a moment” silences conversation without
  pausing authorized implementation.
- **Cancellation** revokes product-work authority, reconciles dispatched effects,
  discards the work from product delivery, and produces no Case File or PR.
- **An infrastructure blocker** checkpoints the attempt, resumable after
  engineering repair only before the fixed case-level deadline; it never
  fabricates evidence or becomes a product rejection.

Delivery after successful validation continues as
[Failure Recovery](./failure-recovery.md#user-intent-during-interruption)
describes.

## Repository binding and exact-file inspection

The gate approves against a base revision pinned before code mutation. The
workspace never commits: after successful validation,
[delivery](./client-outputs.md#github-delivery) builds the branch and commit from
the validated files through the GitHub Git Data API.

The coding agent investigates with filesystem search and exact file reads at the
pinned revision inside Vercel Sandbox; the Product Overview is not a code index.
Every inspected file and revision is recorded as provenance. The gate's cited
pre-gate findings are hints until the workspace reads the exact source.

## Required checks and scope control

Required commands come from the Product Signal's pinned Autonomy Policy and gate
decision.

### Baseline and candidate runs

Required checks run on the clean base for a comparable baseline, and again on
the candidate. The baseline uses the base revision exported from git beside the
checkout, with the installed dependencies linked in. It runs in a prepared
workspace before the attempt begins, or else while the agent authors the first
cycle, and the first comparison then waits for it.

A run's checks start together, except that a check waits for the earlier checks
its policy entry names in `after`, such as a build that writes files the type
check reads. Pre-existing failures are reported as integration health; the
candidate must not introduce new ones.

### Fast and full checks

A candidate's checks run in two stages, both on the same files, identified by
their digest:

- **Fast checks** decide whether the participant sees the candidate. The required
  checks the policy marks `fast` run before the swap. The Shopalytics type check
  runs incrementally in the workspace, whose preparation ran it once in the
  checkout to warm its cache. The production build is marked
  `runs_in_candidate_runtime` as well: the spare candidate runtime builds the
  staged files with the build cache its image's own build left, serves that build,
  and passes the [render check](./preview-build.md#fast-checks-before-the-swap) on
  it, and that build is the check's run. A draft's fast-check results carry over
  to the attempt that adopts it when the files are unchanged.
- **Full checks** decide whether the version can be delivered. The remaining
  required checks, the Shopalytics lint and tests, run after the swap, while the
  participant validates; nothing builds again. They start only once the
  workspace's own git tree is shown to be the published version's, and each
  result is recorded on the Preview Version with the tree it ran on.

A full check that introduces a new failure starts a correction cycle within the
same attempt, under the same lease and budgets. The corrected change passes its
fast checks in another spare candidate runtime and becomes a new Preview Version,
which the participant validates afresh; the version they were trying is never
changed in place.

### Scope verification

After every correction cycle, the controller verifies files, repositories,
dependencies, and other approved bounds, but not the diff's line count: a complete
change is never sent back to shrink. Mechanical violations return for correction
while budget remains. A persistent violation, or a semantic discovery that the
solution needs a different outcome or scope, stops mutation and closes the case
with a blocker Case File; scope never grows inside the workspace.

### Dependencies

Dependency additions follow the approved scope's `none | allowlist | open`
policy. Permitted additions use exact versions, update lockfiles, pass license
policy, and appear explicitly in the Case File and PR. A newly necessary
dependency outside the approved scope stops mutation the same way.

## Outcomes and budget exhaustion

Attempt outcomes are `candidate_ready`, `scope_change_required`, `checks_failed`,
`unsafe`, `infrastructure_blocked`, `paused`, `canceled`, and
`budget_exhausted`. Each result identifies completed side effects, retained
artifacts, and whether resumption is legal.

An attempt reaches `candidate_ready` once its first Preview Version serves the
participant. Its Job keeps the workspace and lease until that version's full
checks settle, and a correction cycle after a failed full check publishes the next
version from the same state. Full checks that still fail when the correction-cycle
budget ends leave the participant validating a version that cannot be delivered,
so the case closes with a blocker Case File.

An attempt that no longer fits the live session, needs a changed scope, or
exhausts its budgets closes the case with a blocked Case File explaining the
evidence, work attempted, and blocker, and opens no PR.

## Artifact extraction and cleanup

Once the newest version's full checks settle, and before workspace destruction,
uTrace extracts:

- the final diff
- file and process logs
- the agent's command and file-change journal
- check results and approved-scope checks
- inspection evidence
- the dependency manifest
- source and build provenance

Each artifact is content-addressed and bound to the attempt and lease. Extraction
failures retry and block cleanup; the sandbox is never the sole recovery record.
Once required artifacts are durable and no incident hold applies, uTrace deletes
the workspace sandbox and releases the attempt's environment lease, which durably
records the cleanup.
