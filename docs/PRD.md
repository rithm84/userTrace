# uTrace PRD

## Product thesis

uTrace is a fleet of forward-deployed agents for product teams.

By integrating with a product's user community and autonomously closing long-running feedback loops, it identifies [Product Signals](#product-signal) and turns them into validated implementation work, scaling linearly with company growth without adding headcount.

It is not a mere tool but the following end-to-end software factory:

![A Product Signal reaches gated live or async implementation and exact-candidate validation, or an explanatory Case File without a PR.](./diagrams/svg/prd-product-loop.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](./diagrams/svg/_key.svg)

</details>
<p></p>

The key insight is that product teams at growing or mid-size organizations already find a steady stream of feedback in their Discord servers, Reddit posts, X threads, customer calls, and support channels. The bottleneck is not "finding feedback," but synthesizing that scattered customer truth into structured product improvement by uprooting shared pain points and unmet needs, primarily done through probing user interviews. While this works on a small scale, increased growth creates a knee in the graph: human bandwidth cannot linearly scale to fulfill the demands of maintaining such feedback loops.

uTrace erases that bottleneck. It plugs into where customers already talk, identifies Product Signals, invites the right users into agent-led interviews, observes their product workflows, asks context-aware probing questions, connects the answers to code, implements changes, and actually validates the modified workflows with the same users before handing the client product team any final output.

## Positioning

uTrace is best understood as a team of forward-deployed agents.

Human forward-deployed engineers sit close to customers, product behavior, and technical implementation details. uTrace automates their function for high-volume customer feedback and product improvement loops.

uTrace is not purely:

- a customer support automation, because the output is product work rather than ticket deflection
- a session replay, because it actively asks questions based on user telemetry
- an observability platform, because traces are inputs for broader understanding of the customer's situation
- a coding agent, because code changes are gated on customer evidence and telemetry
- a voice agent, because voice is just the live interview interface for a broader product-engineering loop

It is made up of parts of all of those.

## Account model

uTrace has two account classes:

![A uTrace User keeps one global memory and belongs to a customer account, a business or a person, at each uTrace Client whose product they use; each client keeps that user's history in its own memory, never shared with another client.](./diagrams/svg/prd-account-model.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](./diagrams/svg/_key.svg)

</details>
<p></p>

### uTrace Client

A uTrace Client is a company that uses uTrace to improve its product. One client organization represents one product. A client has [Customer Surfaces](#customer-surfaces) (e.g. Discord server, Zendesk helpdesk), integrates both components of the [uTrace SDK](#utrace-sdk) into its normal application branch (usually `main`) for preview-only instrumentation, and receives [Case Files](#outputs) with potential PRs. One client must not see another client's interview history, product telemetry, or customer context.

### uTrace User

A uTrace User is a person who uses one or more uTrace Clients' products and
participates in agent-led interviews.

Global user memory may include communication preferences, participation count, and contact preferences. Client-scoped memory may include prior interviews, product-specific workflow context, frustrations, validation outcomes, and account metadata for that client only.

## Customer Surfaces

A Customer Surface is any place where a client's users give feedback or show
behavior that may warrant a [Product Signal](#product-signal): a community server
such as Discord, a helpdesk such as Zendesk, forums, social posts, or support
calls. uTrace watches the channels the client chooses and reaches each user
privately on the surface they used, so an interview invitation or return link goes
only to the person who raised the concern and is bound to their identity there.
The [outreach contract](./arch-components/signal-and-outreach.md) defines delivery,
identity, and contact-budget enforcement.

## Product Signal

A Product Signal is not raw customer feedback. It is uTrace's recorded finding
that feedback plausibly points to a product failure, a feature gap, workflow
friction, or another opportunity worth investigating.

A *case* is all the work that follows from one Product Signal: the interview,
implementation, validation, and closure. The [output rules](#outputs) decide
whether it produces a Case File.

A Product Signal should identify:

- affected user goal or workflow
- expected behavior or requested capability
- current behavior/suspected failure or product gap
- customer evidence
- impact, frequency, or urgency
- potential gain from acting on it

The user whose report creates a Product Signal becomes its originating user: the
one person uTrace interviews and asks to validate the result. Several reports can
point at the same need, and relating later reports to an existing Product Signal
adds to its evidence.

## Intake router

The Intake Router reads incoming feedback, decides whether it warrants a Product
Signal, and, when it does, invites the reporter to an interview.

Intake keeps the source of every decision; a generated narrative is optional. The
[routing contract](./arch-components/signal-and-outreach.md#routing-context-and-cognition)
defines classification, conditional reasoning, and policy enforcement.

### Flow type

The router first separates real signals from noise, such as unrelated content or
a question that a factual pointer answers. An existing capability alone does not
make feedback noise: the user may be describing real friction with it. Noise ends
the flow: uTrace may send the user a short pointer, but no interview,
implementation, or client output follows.

Otherwise, the router assigns an initial flow type:

- **Feature Request Flow:** the user asks for, or implies a need for, a new or
  changed capability.
- **Bugfix Flow:** the user reports something that does not work as it should.

The interview confirms the flow type from the user's account and their observed
product activity before the Implementation Gate.

### Actionability

Every Product Signal leads to an interview invitation, within the client's
contact budgets. Whether anything gets built is decided later, at the
[Implementation Gate](#implementation-gate), once the interview, the observed
workflow, telemetry, and the code have been weighed.

## Outreach Offers

Clients can reward users for taking part. The client authorizes a budget for fixed
rewards, such as an invoice credit that reduces the user's customer account's
future bills for the client's product.

The private invitation states the reward, who receives it, how it is applied, and
what completing participation means, including that it may take more than one
session. uTrace reserves the reward against the client's budget before sending the
offer, and the agent cannot negotiate or increase it. The budget is an allowance
to issue rewards, not money deposited with uTrace.

One offered reward covers the case's required participation, and how the case
ends decides whether it is earned:

![A reward is earned after a completed interview with no implementation, after participation through validation, or when uTrace or the client ends a case the participant had fulfilled; cancellation, declining, abandonment, and pre-participation stops earn nothing.](./diagrams/svg/prd-outreach-earning.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](./diagrams/svg/_key.svg)

</details>
<p></p>

A client stop follows the same earning rules as a technical blocker, a scope limit,
or a spent execution budget. A temporary interruption is not abandonment. Honest
negative feedback never costs the user their reward: earning it never depends on a
favorable verdict, a PR, or a merge.

uTrace issues an earned reward to the customer account through the client's
billing system, which applies an invoice credit to future invoices on its own. The
[Outreach Offers](./arch-components/outreach-offers.md) contract owns customer
mapping, reservations, and issuance recovery.

## Core flows

The interview follows the flow type. Both flows separate what the user asks for
from what they actually need, and both end at the Implementation Gate.

### Feature Request Flow

The agent should separate the requested solution from the underlying job, pain, workflow, and impact. It should probe why the user wants the feature, what they are trying to accomplish, how often the need appears, and whether a workaround exists today.

If the requested solution would actually alleviate the underlying pain, the agent proceeds to the [Implementation Gate](#implementation-gate). If not, it proposes more fitting solutions and proceeds once the user agrees on one. If the user never agrees, the session closes, the Implementation Gate records no implementation, and the Case File records the underlying pain, the user's ask, and the disagreement.

### Bugfix Flow

The agent has the user reproduce the failure in their preview, and captures what
they expected, what happened instead, how often it happens, and what it blocks.
The observed browser and server evidence of the failure, not the description
alone, is what reaches the Implementation Gate.

### Shared stopping rules

If the originating user takes part but the interview's time and evidence budget
runs out before uTrace has enough evidence, the Implementation Gate records no
implementation, and the Case File records the evidence gathered, the remaining
uncertainty, and insufficient evidence as the blocker.

A user who declines the invitation, cannot be reached, or opts out before taking
part follows the cancellation path and produces no Case File. A technical
readiness or infrastructure failure follows its own recovery or blocker path.

## Implementation Gate

The Implementation Gate decides whether uTrace builds anything. It weighs the flow
type the interview confirmed, the evidence, the agreed scope, and the remaining
budgets, and chooses:

- live implementation, when the agreed scope can be finished within the live
  session's limits
- async implementation, for agreed work too large for the live session; uTrace
  builds it after the session and reaches back out to the user to validate it
- no implementation, when nothing passes; uTrace then produces an explanatory
  Case File without a PR

A participant's explicit cancellation is a separate path with no output.

uTrace starts drafting the requested change as soon as the user claims their
invitation and adjusts the draft while the agent proposes it, and the gate prepares
its evaluation of the proposal at the same time. Only the gate's decision on what
the user actually agreed to authorizes work: if what they agree to differs from
what was prepared, the gate evaluates it again, and a draft the gate never approves
is discarded.

The Implementation Gate considers:

- reproduction clarity
- user confirmation
- telemetry evidence
- boundedness
- likely code area
- sensitive surfaces
- risk
- [Autonomy Policy](#autonomy-policy)

## Autonomy Policy

The client sets its Autonomy Policy in advance, as part of its configuration.
Each case keeps the policy it started with; later changes apply to new cases only.

The policy covers:

- which implementation paths uTrace may take
- which code areas it may and may not change
- which checks and tests must pass
- the iteration budget: how many Implementation Attempts one Product Signal may
  use

Client owners and admins can stop an active case, with an audited reason, which
withdraws authority for any further work. Offboarding, revoked credentials, and
platform security rules can also remove authority at once. uTrace never asks the client to approve individual actions.

## Client configuration

Before uTrace works with a client, the client's configuration connects its
Customer Surfaces, code repository, preview environment, data, and policy, and its
application meets the supported application contract: repository access,
environment configuration, startup commands, health checks, preview sign-in, SDK
installation, and data seeding. The
[client configuration contract](./arch-components/client-configuration.md) owns
the release, how it is verified, and the SDK build profiles.

## uTrace SDK

The uTrace SDK is how uTrace sees what happens in the client's product without
reading screenshots. It has two required parts: a browser component in the
product's pages and a server component in its backend. The client commits both,
with their configuration and product annotations, to its normal branch, usually
`main`. They run only in previews: production builds leave the runtime out, and
any annotation helpers left in production do nothing.

The browser component captures the structure, accessibility, routes, API calls,
interactions, and product meaning of what the user does. The server component
records what the backend does in response: API handling, database and downstream
calls, product-domain work, and errors, plus cache and queue operations where the
product's workflow depends on them.

Existing production observability does not replace the server component. Its
[preview instrumentation contract](./arch-components/server-sdk.md) owns
OpenTelemetry setup, correlation, redaction, and export.

A browser extension in the user's preview tab works alongside the SDK: the
extension shows the agent and its cursor while the SDK observes, and one shared
runtime in the page keeps them from collecting anything twice. All three are
required; without them, uTrace cannot run interviews or validation that depend on
observing the product.

![The extension shows the agent in the preview; the browser SDK observes the user and links each action to the server SDK's view of the backend; ingestion joins both into evidence for uTrace, which authorizes what the extension shows.](./diagrams/svg/prd-sdk-architecture.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](./diagrams/svg/_key.svg)

</details>
<p></p>

### Automatic browser signals

Captured automatically:

- the user's uTrace session, and the trace context that links their actions to
  backend work
- route changes
- clicks, focus, and input activity, without raw sensitive values
- form submissions
- network and API summaries
- frontend errors
- performance timings
- safe snapshots of the page and its accessibility tree
- visible labels and roles, where safe
- the positions of the controls the agent can point at

### Automatic server signals

Captured by the server component:

- each API request's route, method, status, and duration
- framework handler and controller work
- calls to downstream services
- database operation summaries
- cache and queue operation summaries, where the client's release includes an
  adapter for that library
- backend exceptions and error classes
- product-domain spans the client declares safe for uTrace
- the link from each browser action to the server work it caused

The server component never exports secrets, credentials, unrestricted request or
response bodies, unrelated production traces, or fields the client's redaction
policy excludes.

### Annotated SDK signals

Declared by the client:

- sensitive fields and screens
- business entities such as workspace, policy, incident, billing plan, or user
- important workflow milestones
- safe field values that may be discussed in interviews
- destructive or side-effecting actions
- domain-specific severity and priority tags

uTrace never interprets screenshots to understand the product.

## Session experience

A session is one live encounter for interviewing or validation. A case can span
several sessions when the originating user leaves and comes back.

An interview starts from a private uTrace Session Link. The originating user signs
in with the identity the invitation was sent to, which binds the link to them and
to the client. Claiming the link starts setup and prepares
the user's preview. Before interviewing, uTrace checks for a supported browser, the
extension and its permissions, account linking, and voice. If setup is impossible,
or does not finish in time, uTrace explains why and closes without an interview,
Case File, or reward.

The interview itself is a voice conversation. The voice agent talks naturally and
can be interrupted, while other agents investigate and implement in the
background. It asks useful questions and avoids filler and running commentary.
Text shows setup and connection status; it never replaces the conversation.

Once setup passes, the greeting and the first discussion of the problem can begin
while the preview finishes preparing. Questions and claims that depend on what the
user is doing in the product, and all validation, wait until uTrace can observe
the product end to end. If that observation drops out, those activities pause,
but an otherwise healthy conversation can continue.

uTrace keeps the conversation's context and gives the agent the observations and
implementation findings it needs. The agreed scope, acceptance criteria, open
questions, and validation evidence are recorded outside the model's memory.
Transcripts can be incomplete or corrected, so a fragment, a pause, or an isolated
"yes" never counts as consent, agreed scope, or successful validation.

The agent introduces itself as:

> [Name], a product agent helping the [Client] team [analyze the feature request | investigate the issue].

It explains that it will ask short questions while the user walks through the
workflow, that it can see structured product activity from the session, and that
it may point out fields or buttons.

### Agent character

The session tab shows the voice agent as a friendly animated character. The
character stays visible for the whole call, reflects the interview's recorded
state, and comes with a short status line, such as "Thinking...". Its animations
include idle, listening, thinking, speaking, acknowledgement, concern, and
completion.

### Separate preview tab

The interview uses two tabs:

- **Session tab:** the voice call, live transcript, connection state, progress,
  and session status.
- **Preview tab:** the user's private copy of the product, where they do the
  workflow, with the browser extension showing the agent character, controls,
  cursor, and overlays.

The session tab anchors the live session, and the user can switch to the preview
tab while leaving it open. Departure, cancellation, pause, and temporary
disconnection each lead somewhere different, and closing a tab never means the
user rejected the work; [implementation progress](#user-facing-implementation-progress)
covers continuing and resuming.

Two tabs keep the call and the product from crowding one page, and avoid the
iframe, CSP, and cookie problems many real products would have inside an embedded
frame.

### Telemetry language boundary

The agent may use telemetry and code/repo context internally. It must not expose internal implementation details to the interviewee.

Good user-facing language:

- "I noticed you reopened the same screen after saving. Were you checking whether your changes were preserved?"
- "It looks like the page changed after you applied that filter. Is this the state you expected to see?"
- "I noticed you went back to the list view after opening the details page. Were you checking whether the change carried over?"

Bad user-facing language:

- "The backend span returned a 422 from the save endpoint."
- "I inspected the repository and suspect the validation schema is stripping a nested field."
- "The click event emitted an internal target id, so the issue is probably in the frontend handler."

Technical details belong in the Case File, not in the user's live interview.

## Browser extension

The browser extension brings the agent into the preview tab. It activates only
after the user chooses the preview tab and the extension proves to the product's
browser SDK that it belongs to the user's current session.

### Agent character and controls

The extension shows a small, draggable version of the agent character inside the
preview tab, kept in sync with the interview's state but without the session tab's
status text. Hovering over it reveals controls to return to the session tab, mute
or unmute, pause or resume, and end the interview; dragging it moves it out of the
way.

### Agent cursor and product overlays

The extension draws an agent cursor and overlays in the preview tab, so the agent
can point at, circle, highlight, label, and mark the product elements it is
talking about, and the user knows exactly what it means. It finds those elements
through the browser SDK's registry of known controls, never by reading screenshots
or guessing screen coordinates.

Visual guidance only uses a current, authorized target in the user's preview tab,
and every attempt is recorded, including rejected or stale targets. The
[browser extension contract](./arch-components/browser-extension.md) defines
command authorization, execution, and result reporting.

### Permission boundary

The agent can move its cursor, point, highlight, circle, label, scroll something
into view, and clear its overlays. It observes and guides, but it can never click,
type, submit, or navigate for the user. Users can turn visual guidance off.

## Preview Runtime Allocations, implementation workspaces, and session data

uTrace keeps where the user works apart from where code changes:

![The user's preview link opens a private copy of the product and later switches, all at once, to a separate copy running the new version; a coding workspace the gate authorizes builds that version and holds no customer data; every copy is deleted when it is no longer needed, never reused.](./diagrams/svg/prd-preview-resources.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](./diagrams/svg/_key.svg)

</details>
<p></p>

- **Previews:** each Interview Session gets its own private, running copy of the
  client's product, with its frontend, backend, database, realistic data, the
  user's sign-in, and both SDKs. Each new Preview Version gets a separate copy of
  its own, and the user's preview link switches between them. A Preview Runtime
  Allocation records which copy belongs to which session or version.
- **Implementation workspaces:** the coding agent works in a separate workspace
  that holds the repository but never customer data or production credentials.
  Before the gate approves anything, a workspace may be used to investigate the
  code and to [draft a change](#live-implementation); only an authorized
  Implementation Attempt can turn code into a Preview Version. A workspace never
  becomes the product the user sees.

Each Product Signal has its own previews. A copy that held data is deleted once
the case no longer needs it, never reset and reused.

### Seed data

A preview is only useful if its data looks like the user's: the same kinds of
records, with the relationships and properties their workflow depends on. uTrace
installs a prepared dataset into every preview, and every version in a case uses
the same dataset, so before and after are compared fairly. uTrace prepares that
dataset from the client's own data, with sensitive values replaced by synthetic
ones, and a synthetic value is never presented as real production evidence. The
[seeding contract](./arch-components/preview-runtime-and-seeding.md#seed-manifest)
owns installation and verification.

## Implementation loop

Live implementation uses the [isolated resources](#preview-runtime-allocations-implementation-workspaces-and-session-data)
above. The [implementation contract](./arch-components/implementation.md) owns
workspace authority, checks, artifacts, and cleanup.

### Live implementation

The [product loop](#product-thesis) shows the path from the gate to validation and
delivery. The originating user tries the agreed change within seconds of agreeing
to it, because uTrace works ahead:

- **Speculative draft:** as soon as the user claims their interview invitation,
  the coding agent drafts the requested change from the original request, then
  adjusts it to
  exactly the change the agent proposes while the user decides. A change asked for
  while trying a version is drafted the same way, from that version. Neither the
  user nor the voice agent hears about a draft, and it reaches the user, a Preview
  Version, or a PR only if the Implementation Gate approves the change the user
  agreed to; otherwise uTrace discards it. uTrace records what every draft costs,
  including drafts in cases that end with no implementation.
- **Safe swap:** the drafted change runs in a separate copy of the product, with
  its own copy of the data and any new database tables it needs, while the user's
  current preview stays untouched. Fast checks confirm that it type-checks, builds
  for production, shows the workflow's page with its new controls, and reports
  telemetry correctly. Only then does the user's preview link switch to the new
  version, all at once, so they see either the version they had or the complete
  new one, never a half-built or broken page.
- **Slow checks during validation:** the remaining required checks, such as
  linters and tests, run while the user tries the change. If one fails, the
  coding agent fixes it within the same Implementation Attempt, and the user
  briefly re-checks the fixed version. Validation can succeed, and a PR open, only
  once every check has passed.

If authorized work outgrows the live session, it continues as async
implementation: uTrace saves its progress, finishes the work after the call, and
reaches back out to the user to validate it. An outage or departure follows its own
stopping rule instead.

### Changes during validation

While trying a new Preview Version, the originating user may find it does not fully
do what they need: they are dissatisfied, unsure, or want something different. That
makes the result partially validated, not validated, or unclear, and while the
case's iteration budget allows another attempt, the agent asks whether they would
like it changed. Once they agree out loud on the change and the checks that now
apply, the change goes back through the Implementation Gate, and an approved change
becomes a new Implementation Attempt that starts from the previous version's files
while the conversation continues.

The result stands, and the case closes with an explanatory Case File and no PR, when
the user wants no change or the iteration budget is spent. If the gate refuses the
change, the case ends with no implementation: a Case File that explains why, and no
PR.

Each gate approval authorizes one Implementation Attempt, and the client's Autonomy
Policy sets the iteration budget: how many attempts one Product Signal may use.
Retries and check fixes stay inside an attempt and its time and cost budgets, and a
fix made after the user started trying a version is a new Preview Version, which
the user briefly re-checks.

## User-facing implementation progress

While the change is built, the user stays on the session tab, which shows where
things stand: reproduction captured, making changes, preparing the preview, or
ready to try. The agent talks about progress only when it helps the user
understand a meaningful change or answers their question; it never narrates its
tools or repeats acknowledgements.

What happens when work stops depends on why it stopped:

![Pause, temporary loss, departure, recoverable blockers, and live work that outgrows the session can continue the same case; expiry, unrecoverable blockers, client stops after participation, a spent time or cost budget, and an unsuccessful validation with no change to make produce a blocker Case File; cancellation and pre-participation client stops produce no Case File or PR.](./diagrams/svg/prd-stopping-reasons.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](./diagrams/svg/_key.svg)

</details>
<p></p>

Each reason also decides what happens to the work in flight:

- **Pause (session tab or extension):** voice and observation stop, and no new
  implementation step starts. Work already under way finishes or is saved, with
  its outcome recorded.
- **Temporary connection or voice loss:** authorized implementation and checks
  can continue within a short reconnection window and the case's budget.
  Validation waits for the same user.
- **"Finish later" or leaving during live work:** uTrace saves progress and starts
  no new work. The originating user can come back through their dashboard or a
  private return link, with a few reminders.
- **Cancellation:** the attempt stops, its authority is revoked, and previews no
  longer needed are cleaned up.
- **Technical blocker:** uTrace keeps the evidence while recovery is still
  possible. Closing on a blocker never counts as the user rejecting the work.
- **Client stop:** owners or admins stop further work, and uTrace tells the user.
  The Outreach Offers earning rules still apply.

Pausing, leaving, and recovery never allow resuming indefinitely or leaving
resources running. The [failure-recovery contract](./arch-components/failure-recovery.md)
owns deadlines, saved progress, final cleanup, and notifications.

Once validation succeeds, publishing the Case File, delivering the PR, and cleanup
can finish after the user leaves, unless authority is withdrawn.

## Structured user validation

Only the user who asked can validate the change, on the same dataset they used. The agent asks them to repeat the workflow that motivated the request, or to
try the new feature, and records a structured result:

- validated
- partially validated
- not validated
- unclear

The transcript keeps the user's own words, but Case Files and evaluations rely on
the structured result. A partial, negative, or unclear result records the unmet
criteria and leads to a [change](#changes-during-validation) while the iteration
budget allows and the user wants one; otherwise the case closes with an unvalidated
Case File.

A successful result needs both the user's confirmation and observed evidence for
each acceptance criterion, on the exact Preview Version and the agreed dataset.
The criteria are agreed during the interview as things the user can observe. One
natural pass through the workflow can cover several criteria, and a criterion does
not need its own browser and server events. Missing telemetry that doesn't matter
never blocks success, but silence or an absence of errors cannot prove a behavior
that is required. If the user corrects themselves, the earlier claim stops counting
until the correction is resolved.

Validation does not repeat discovery questions or demonstrations when one pass
already supplies the evidence. A dropped connection interrupts validation; it is
never a negative verdict or an assumed success.

## Outputs

### Case File

The Case File is what the client's product team receives for every case. It is a
structured record shown as an authenticated page, and once published, its
sections never change.

The Case File includes:

- **Summary:** the Product Signal, the user and their customer account, and the
  validation result
- **Customer evidence:** the original message, interview excerpts, expected and
  actual behavior, impact, and any workaround
- **Product trace:** observed evidence for each acceptance criterion and its
  limits, with the routes, page elements, API and backend activity, and errors
  involved, before and after the change
- **Implementation Gate decision:** live implementation, async implementation, or
  no implementation
- **Implementation:** the repository, branch, PR, a summary of the changes and
  files touched, the checks run, cleanup state, and exactly which version the user
  validated
- **Risk:** blast radius, assumptions, and follow-up checks
- **Next action:** review and merge the PR, look into the blocker, or take no
  action

### PR

Validated work produces one draft PR in the product's repository, containing
exactly the files the user validated. It opens only after every required check,
including the production build, has passed on those files. The PR links to the
Case File and names the validated commit and the base it was tested against.
Validation does not carry over to later edits of the PR or to a different merge
result. uTrace never merges, and never opens a PR for work the user has not
validated.

### No implementation Case File

When nothing is built, the Case File says why: for example, a security risk, too
much scope, unclear evidence, an Autonomy Policy denial, or a poor strategic fit.

### Client dashboard

The client dashboard shows cases in progress and Case Files with their PRs. Owners
and admins can stop an active case, with a reason. The Outreach Offers view shows
the authorized budget, what is reserved and remaining, the rewards earned, and
their issuance status; only the owner can authorize or raise the budget.

## State and system of record

uTrace's state and evidence survive disconnections, worker failures, and provider
retries. Every record stays scoped to its client, its originating user, and the
exact work it describes. Dashboards and session views show what was recorded,
never unverified model claims. [Data and Schema](./arch-components/data-and-schema.md)
defines authoritative stores, artifact provenance, and cleanup.

## Demo scope

The demo uses one B2B client with a manually configured uTrace account, Discord
server, and code repository for its product. It supports Feature
Request Flow, live implementation with
[changes asked for during validation](#changes-during-validation), a manually
prepared synthetic dataset, [Outreach Offers](#outreach-offers) with invoice
credits in a Stripe sandbox, and the client dashboard views listed under
[Client dashboard](#client-dashboard).

Within that, the demo narrows the product described above:

- **Customer Surface:** the client's one Discord server, with its watched channels
  set in the client configuration. Invitations and return links are Discord DMs,
  and the originating user signs in with Discord.
- **Intake:** every report that warrants investigation opens its own Product
  Signal, and later reports are not related to existing ones. Bug reports are held
  at intake without a Product Signal.
- **Implementation:** the gate chooses live implementation or no implementation.
  Async implementation is not included, so live work that outgrows the session
  closes the case with a blocker Case File. The client's iteration budget is three
  Implementation Attempts per Product Signal.
- **Setup:** uTrace configures the client with scripts instead of a self-service
  flow, and setup, integration, membership, and data lifecycle management happen
  outside the dashboard.
- **Data and billing:** the dataset is prepared by hand rather than from the
  client's own data, and the Stripe sandbox is uTrace-controlled, standing in for
  the client's billing account.
