# Policy and permission evaluation

[Back to uTrace architecture](../ARCHITECTURE.md)

Independent grants intersect before a Command is authorized, and any denial
wins:

![Autonomy Policy, user permission, runtime facts, and typed intent intersect in deny-wins checks.](../diagrams/svg/policy-and-permissions.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Authority boundary

The demo supports live implementation for Feature Request Flow in one authorized
repository. Browser authority is limited to observation and safe visual commands.

## Autonomy Policy model

The Autonomy Policy, part of the [client configuration](./client-configuration.md),
takes effect only through a verified immutable configuration release. It
configures:

- allowed implementation modes (`live` or `none`)
- one authorized repository and its blocked or allowed code surfaces
- required checks, the per-case `implementation_limit`, and the
  [resource budgets](./implementation.md#enforcement-model), every one of them
  required
- dependency policy (`none | allowlist | open`) and permitted license classes
- visual-command classes and registered-target constraints
- evidence and cleanup rules within platform invariants

The release's private-contact budget, not the Autonomy Policy, limits private
Discord outreach and reminders.

### Pinning

Every Product Signal pins the active release:

![A Product Signal pins one release; each approving gate decision freezes a scope for one Implementation Attempt; deny-only overrides never grant authority.](../diagrams/svg/policy-and-permissions-pinning.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

Later policy or gate revisions never rewrite prior decisions.

## Precedence and runtime authority

Precedence runs from platform invariants to the pinned Autonomy Policy, current
user permission, readiness and target freshness, then typed model intent.
Any denial wins. Voice, dashboard activity, provider acknowledgements, and model
output cannot broaden authority.

There is no runtime human state editor, manual handoff, or per-action client
approval. Owners and admins configure policy before a Product Signal begins; at
runtime they have only the audited, deny-only [**Stop this case**](#client-case-stop)
control. Engineering may repair infrastructure outside the product lifecycle but
cannot fabricate evidence or mark work successful.

## Implementation Gate contract

### Inputs and proposal

The gate receives the Product Signal, interview-established flow classification
and its revision, acceptance criteria, confirmed user evidence, correlated
telemetry, exact-revision code findings, prepared-environment readiness, pinned
policy, and budget consumption. When the interview has not recorded a flow
classification, Jev proposes one first; Jev has no other role at the gate.

GPT-6.1 Sol writes a typed, cited proposal at a low pinned effort on the
Ultrafast tier. A participant waits and output tokens dominate its time, so it
cites evidence and findings by bundle-assigned short handles (`E1`, `F1`). The
controller maps each handle to its record before validating; a handle the bundle
never offered fails citation like any unknown identifier. The deterministic
controller validates all inputs and owns the durable decision.

### Decision

The gate returns `approved_live` or `no_implementation` with a typed blocker. It
applies the pinned release's rules for the interview-established flow type,
rejects stale classification revisions, and checks proposed work against
remaining budgets. A decision whose case moved while its
proposal was produced (state, classification, agreed scope, or concern) is
refused, and the gate decides again on a fresh bundle. The
[attempt protocol](./implementation.md#attempt-protocol) owns the atomic
pre-dispatch check.

A model confidence score cannot hide missing evidence, unresolved disagreement,
policy denial, excessive live scope, or safety risk.

### Approved scope

An approval freezes the classification revision, intended user-visible outcome,
acceptance criteria, authorized repository, in-scope and excluded changes,
permitted code areas, required checks, dependencies, resource budgets, and cited
evidence revision.

A task that would exceed approved scope stops mutation. After participation,
denied scope or exhausted budgets close with a blocker Case File. A
[change asked during validation](../PRD.md#changes-during-validation) returns
the case to `gate_pending` for a new attempt, up to the pinned
`implementation_limit`.

## Client case stop

The owner or an admin may withdraw authority from one nonterminal case
through an audited **Stop this case** action, a denial that takes precedence
immediately: it blocks new dispatch, revokes pending invitations and credential
refresh, fences leases, reconciles already-dispatched effects to a safe
checkpoint, and schedules cleanup. It cannot mark evidence or validation
successful, alter a Case File, expand permissions, resume stopped work, or revoke
an already published output.

The control records the staff identity, client, Product Signal, lifecycle
revision, reason, affected authority, and reconciliation result. The originating
user receives a private, user-safe notice.

### Closure after a stop

- **Before participation:** the case closes immediately with a distinct
  `client_stopped_before_participation` terminal record and no Case File, because
  there is nothing to publish.
- **After participation:** the stop schedules a blocker Case File with the
  client-initiated reason and no new PR. The Product Signal keeps its current
  state until publication and closure commit together. The explanation is the
  client's obligation to the participant, so a stop that produced nothing cannot
  reach `closed_blocked`.

Outreach Offers treats the stop as system-initiated under the
[PRD's earning rules](../PRD.md#outreach-offers); an already-earned reward remains
owed.

## Browser command permission scopes

After complete readiness, the system permits observation and visual commands:
cursor movement, pointing, highlighting, circling, labeling, scrolling a
registered target into view, and clearing overlays. Scrolling only presents an
already registered target, never general navigation or an effectful product
action. Clicking, typing, navigation, submission, and other effectful browser
actions are outside supported authority.

Sensitive or destructive actions remain prohibited, including payment,
credentials, permissions, account ownership or membership changes, external
sends, refunds, and deletion. The extension and embedded SDK classify registered
actions by product effect rather than mouse gesture. Every intent, decision,
target revision, command, and result is logged.

Neither installing the extension, granting visual access, nor any per-client,
per-session, or per-command voice prompt broadens browser authority; user
preferences may narrow it immediately.

## Pause and interruption permissions

The Pause control, a spoken conversational pause, and temporary transport loss
narrow authority as the
[interruption contract](./implementation.md#conversation-pause-and-interruption)
states; none authorizes validation without the same user. Live departure and
recovery run against the pinned
[return deadline](./failure-recovery.md#bounded-return-and-terminal-expiry), and
participant cancellation or a client stop revokes authority outright.

## Outreach Offers billing authority

Client-approved [credit allowance and offer policy](./outreach-offers.md) grants
the backend adapter bounded authority to issue invoice credits. Models, the
extension, SDKs, and implementation workspaces receive no billing tools, and
product-browser payment actions stay prohibited. Product
cancellation revokes product work; an already-earned credit remains owed, with
issuance subject to the integration's authorization and security checks.

## Decision records

Every gate, outreach, browser-command, session-control, retention, and security
evaluation writes an append-only record containing the exact subject, intent,
revisions, policy and permission inputs, readiness and target state, outcome and
reason codes, evidence references, and timestamp. Historical reconstruction uses
those immutable versions; replay cannot dispatch effects.
