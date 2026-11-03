# Implementation controller and isolated workspace

[Back to uTrace architecture](../ARCHITECTURE.md)

One Implementation Gate approval yields one Implementation Attempt and, if its
checks pass, a candidate that [Preview Build](./preview-build.md) publishes as an
immutable Preview Version:

![After the Implementation Gate approves live work, the controller commits the attempt, claims the prepared workspace under the lease, and runs Codex cycles whose output it formats, checks, and builds in parallel until it accepts a candidate.](../diagrams/svg/implementation.svg)

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
The voice agent's tools (which read observed activity and record agreements) and
Jev (typed semantic-attention results) cannot write code or authorize an effect.
Only the control plane approves the Implementation Attempt, grants its fenced
Environment Lease, accepts artifacts, and advances durable state.

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
  an Implementation Attempt and the Fast tier for code investigation. On the
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
revision; an investigation's carries the repository's tracked files instead. It
tells the agent that the participant is waiting, and to:

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

The case's first attempt starts from the clean base; a later attempt's fresh
workspace is checked out at the same base revision, so its candidate holds both
changes.

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
coding agent can start the moment the participant agrees:

![A prepared workspace is built when the participant claims the invitation, records its baseline, and is either claimed by an approved attempt with matching pins or released when its case ends or its one-hour window passes.](../diagrams/svg/implementation-prepared-workspace.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

It is built from the case's pinned release alone and never reaches the OpenAI API
before a claim. The claim binds the sandbox to the attempt and its fencing token
in the same row update that takes it, and the first cycle starts with the
recorded baseline.

An attempt claims only a workspace prepared from its own specification's pins:
template snapshot, clone URL, base revision, install command, required checks,
and vCPUs. It waits for a preparation still running rather than starting fresh;
with none to claim, it prepares its own and authors the first cycle while the
dependencies install.

Until release, the sandbox runs on a short
[sandbox deadline](./preview-runtime-and-seeding.md#sandbox-deadlines) extended
toward the end of the one-hour window, so a workspace uTrace loses track of stops
within minutes.

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

An attempt that no longer fits the live session, needs a changed scope, or
exhausts its budgets closes the case with a blocked Case File explaining the
evidence, work attempted, and blocker, and opens no PR.

## Artifact extraction and cleanup

Before workspace destruction, uTrace extracts:

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
