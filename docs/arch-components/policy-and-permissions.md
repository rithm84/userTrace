# Policy and permission evaluation

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how independent grants intersect before a Command is authorized:

![Client policy, user permission, runtime facts, and typed intent intersect in deny-wins checks.](../diagrams/svg/policy-and-permissions.svg)

## Authority boundary

The demo supports live implementation for Feature Request Flow in one authorized repository. Browser authority is limited to observation and safe visual commands.

## Client policy model

Policy is part of the [client configuration](./client-configuration.md) and becomes active only through a verified immutable client configuration release.

The release's policy configures:

- allowed implementation modes (`live` or `none`)
- one authorized repository and its blocked or allowed code surfaces
- required checks, the per-case `implementation_limit`, and the
  [resource budgets](./implementation.md#enforcement-model), every one of them
  required
- dependency policy (`none | allowlist | open`) and permitted license classes
- visual-command classes and registered-target constraints
- private Discord outreach and reminder budgets
- evidence and cleanup rules within platform invariants

Every Product Signal pins the active configuration release. Each Implementation Gate decision then freezes a narrower approved scope for one attempt. Later policy or gate revisions never rewrite prior decisions. Client offboarding, credential revocation, legal deletion, and platform security prohibitions may remove authority immediately, but cannot grant authority withheld by the pinned policy.

## Precedence and runtime authority

The precedence order is platform invariants, pinned client policy, current user permission, readiness and target freshness, then typed model intent. Any denial wins. Voice, dashboard activity, provider acknowledgements, and model output cannot broaden authority.

There is no runtime human state editor, manual handoff, or per-action client approval. Owners and admins configure policy before a Product Signal begins. They may use the audited, deny-only **Stop this case** control described below; it cannot grant authority or edit an outcome. Engineering may repair infrastructure outside the product lifecycle, but cannot fabricate evidence or mark work successful.

## Implementation Gate contract

The gate receives the Product Signal, interview-established flow classification and its revision, acceptance criteria, confirmed user evidence, correlated telemetry, exact-revision code findings, prepared-environment readiness, pinned policy, and budget consumption. When the interview has not recorded a flow classification, Jev proposes one first; Jev has no other role at the gate. GPT-6.1 Sol produces a typed, cited proposal at a low pinned effort on the Ultrafast tier. The proposal cites evidence and findings by short handles the bundle assigns (`E1`, `F1`), because a participant waits on it and its time is mostly the tokens it writes; the controller maps each handle back to its record before validating, so a handle the bundle never offered fails citation like any unknown identifier. The deterministic controller validates all inputs and owns the durable decision.

The gate returns `approved_live` or `no_implementation` with a typed blocker. It
applies the rules for the interview-established flow type from the Product
Signal's pinned configuration release, rejects stale classification revisions,
and checks the proposed work against remaining budgets. A decision whose case
moved while its proposal was produced (state, classification, agreed scope, or
concern) is refused, and the gate decides again on a fresh bundle. The
[attempt protocol](./implementation.md#attempt-protocol) owns the atomic
pre-dispatch check.

An approval freezes the classification revision, intended user-visible outcome, acceptance criteria, authorized repository, in-scope and excluded changes, permitted code areas, required checks, dependencies, resource budgets, and cited evidence revision. Missing evidence, unresolved disagreement, policy denial, excessive live scope, or safety risk cannot be hidden by a model confidence score.

A task that would exceed approved scope stops mutation, and denied scope or exhausted budgets close with a blocker Case File after participation. A change the originating user asks for during validation returns the case to `gate_pending` for a new attempt, up to the pinned `implementation_limit`; a refused change, or one beyond the limit, leaves them validating the version they have.

## Client case stop

The client owner or an admin may withdraw authority from one nonterminal case through an audited **Stop this case** action. This denial takes precedence immediately: it blocks new dispatch, revokes pending invitations and credential refresh, fences leases, reconciles already-dispatched effects to a safe checkpoint, and schedules cleanup. It cannot mark evidence or validation successful, alter a Case File, expand permissions, resume stopped work, or revoke an output that was already published.

The control records the staff identity, client, Product Signal, lifecycle revision, reason, affected authority, and reconciliation result. The originating user receives a private, user-safe notice. A stop before participation produces a distinct `client_stopped_before_participation` terminal record and no Case File; a stop after participation produces a blocker Case File with the client-initiated reason and no new PR.

An already published output remains intact. Outreach Offers evaluates the stop as system-initiated: the reward follows the existing participation-based earning rules and remains owed if already earned.

After participation, the stop does not itself close the case. It withdraws
authority at once and schedules the blocker Case File; the Product Signal stays
in its current state until that Case File is published, and publication and
closure commit together. This is deliberate: the client's obligation to the
participant is the explanation, so a case cannot reach `closed_blocked` through a
stop that produced nothing. A stop before participation closes immediately,
because there is nothing to publish.

## Browser command permission scopes

The system permits observation after complete readiness and visual commands such as cursor movement, pointing, highlighting, circling, labeling, scrolling a registered target into view, and clearing overlays. Scrolling is permitted only as presentation of an already registered target; it must not become general navigation or an effectful product action. Clicking, typing, navigation, submission, and other effectful browser actions are outside the supported authority.

Sensitive or destructive actions remain prohibited, including payment, credentials, permissions, account ownership or membership changes, external sends, refunds, and deletion. The extension and embedded SDK classify registered actions by product effect rather than mouse gesture. Every intent, decision, target revision, command, and result is logged.

Installing the extension or granting visual access does not broaden browser authority. User preferences may narrow authority immediately. There is no per-client, per-session, or per-command voice prompt that can broaden it.

## Pause and interruption permissions

The active session’s Pause control withdraws authority for voice, observation, and new live implementation steps. Accepted in-flight work may only reach a safe checkpoint before stopping. A spoken conversational pause does not invoke this control and may allow already authorized implementation to continue. Temporary transport loss permits current authorized live implementation/checks only within the bounded reconnection policy; it never authorizes validation without the same user.

Live departure and recovery run against a pinned expiry. Participant cancellation
or a client stop revokes authority outright.

## Outreach Offers billing authority

Client-approved [credit allowance and offer policy](./outreach-offers.md)
grants the backend adapter bounded authority to issue invoice credits. Models,
the extension, SDKs, and implementation workspaces receive no billing tools.
The prohibition on product-browser payment actions remains unchanged. Product
cancellation revokes product work; an already-earned credit remains owed, with
issuance subject to the integration's authorization and security checks.

## Decision records

Every gate, outreach, browser-command, session-control, retention, and security evaluation writes an append-only record containing the exact subject, intent, revisions, policy and permission inputs, readiness and target state, outcome and reason codes, evidence references, and timestamp. Historical reconstruction uses those immutable versions; replay cannot dispatch effects.
