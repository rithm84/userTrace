# Failure recovery and operations

[Back to uTrace architecture](../ARCHITECTURE.md)

The [PRD's stopping rules](../PRD.md#user-facing-implementation-progress) decide
what each stopping reason produces; this contract owns the mechanics.

## Failure boundary

Failures never substitute another participant, combine lifecycle authority,
expand repository scope, or change the kind of work.

## Typed failure and retry contract

Command disposition, side-effect certainty, technical category, and retry advice
remain separate. Unknown or partially applied external effects enter
reconciliation, never blind repetition. Provider adapters map raw errors to
closed typed categories; unmapped errors fail closed with an engineering-visible
finding.

Retries use durable budgets, jittered backoff, idempotency keys, and provider
circuit breakers. Supabase Queues and Cron may wake and schedule work, but
authoritative Job, Command, lifecycle, and projection state stays in Postgres,
and Realtime notifications are hints, never proof of completion.

## Voice, cognition, and observation failure

### Voice and transport

Temporary transport or voice loss starts the bounded reconnect window of the
[interruption contract](./implementation.md#conversation-pause-and-interruption).
Room creation, rejoin, and voice agent failures follow the
[call setup contract](./interview-and-voice.md#call-setup), which decides whether
a reconnect rejoins the same room or repeats backend-authorized setup with a new
one; neither refreshes a browser-held credential. Superseded rooms are deleted
and their late results rejected. A voice agent absent from the call makes voice
unready for dependent probing and validation, and voice failures never switch
providers.

[Transcript fragments](./interview-and-voice.md#transcript-and-evidence-contract)
are provisional. A replacement call gets a new transport identity and context
revision; accepted corrections invalidate claims derived from the earlier
wording.

### Model and observation unavailability

Unavailability of the OpenAI API, a voice provider, or Jev pauses only work that
requires that role, with no silent substitute, lower-fidelity path, or model
substitution.

- Routine Jev intake with complete pinned context does not depend on GPT-6.1 Sol;
  a Jev outage blocks that route after bounded retries.
  [Conditional assistance](./signal-and-outreach.md#conditional-assistance)
  follows the intake contract.
- During interviews, Jev supplies concurrent semantic attention, never a serial
  speech preapproval gate.
- Product-specific claims and validation pause whenever the extension, browser
  SDK, Server SDK, trace correlation, target freshness, or exact candidate
  readiness is incomplete.

## User intent during interruption

A spoken request for a quiet moment suppresses speech without activating the
Pause control. Reopening a session preserves consumed budgets and never restores
revoked authority. After successful validation, departure does not stop Case
File assembly, draft-PR delivery, or cleanup unless authority is withdrawn; no
new coding is authorized, and later code changes do not inherit the validation.

A terminal product outcome also triggers
[Outreach Offers eligibility evaluation](./outreach-offers.md#completion-and-entitlement).
Credit issuance failures recover
separately and never reopen the case, change validation, or erase an earned
reward.

## Bounded return and terminal expiry

Each case records at most one return-and-closure deadline, from its pinned
policy:

![The first stop records one fixed deadline; later stops and re-entries never extend it, and expiry closes the case.](../diagrams/svg/failure-recovery.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

Pause, retry, repair, repeated departures, recovery, and resumed work never
extend or reset the deadline. While stopped, the case retains only the durable
artifacts needed to verify and reconstruct the authorized scope, dataset,
attempt, and candidate. Completion before the deadline ends its scheduling
obligation, and expiry cannot rewrite a completed outcome.

### Return

Until the deadline, the originating user can return from their dashboard
through a private return link, and uTrace may send bounded private Discord
reminders within the pinned contact allowance. Re-entry proves the same identity
and reruns current policy, permission, budget, extension, SDK, dataset, preview,
and observation-readiness checks before work resumes. Restored resources must
reproduce the exact authorized scope and data lineage; reconstruction creates no
evidence and resets no consumption.

### Expiry

Expiry is terminal: the controller fences remaining leases, revokes invitations
and credentials, reconciles dispatched effects, destroys unneeded runtime
resources after durable extraction, resolves the Outreach Offer under its
existing participation rules, and closes the case as the diagram shows.

## Blockers and recovery

### Resuming a blocked attempt

An attempt blocked by infrastructure failure can resume after engineering repair
only while the deadline is open. Before resumption, the control plane verifies
tenant, Product Signal, attempt, lease, policy, base revision, scope, workspace
integrity, artifact hashes, candidate identity, and remaining budgets. No
engineer edits product state or fabricates evidence.

Automatic remediation is limited to idempotent, evidence-preserving legal
transitions: reclaiming Jobs, renewing fenced leases, rebuilding a workspace from
pinned inputs and durable artifacts, reconstructing an exact candidate from its
artifact and dataset manifest, re-dispatching an undispatched outbox entry, or
reconciling a provider resource by stable identity.

### Terminal boundaries

Recovery respects these boundaries:

- [Implementation limits](./implementation.md#outcomes-and-budget-exhaustion)
  and denied scope close the work with a blocker Case File.
- Repair cannot resume a closed signal.
- [Initial outreach](./signal-and-outreach.md#originating-user-and-invitation-lifecycle)
  and explicit cancellation keep their no-Case-File outcomes.
- A participant environment failing the supported browser, extension,
  permission, or extension compatibility contract closes before interviewing in
  `canceled` with reason `unsupported_participant_environment`, a user-safe
  notice, no Case File, and no earned reward. Setup timeout has the same outcome,
  distinct from voluntary cancellation; a readiness loss after participation
  follows the deadline rules.
- Data that exceeds the contract stops the case with the user notified and access
  never widened: before participation with no interview, Case File, or reward,
  and after participation in `closed_blocked`.
- Unknown GitHub outcomes stay in reconciliation; a definitive delivery failure
  records the exact draft and branch state.

After participation, all of these close in `closed_blocked` under the
[lifecycle contract's](./control-plane.md#product-signal) guard.

### Undecidable Implementation Gate

An Implementation Gate that cannot decide is a technical blocker, bounded by the
gate Job's run budget and its fresh-context retries; once no run is left, the
change cannot start. The case commits its blocker Case File and closes
`closed_blocked` on publication, including a change asked for while the
participant tried a version. The live agent is told at once, so it never promises
a version that is not coming.

## Restart and reconciliation

Before becoming ready after restart, a service reconciles inbox and outbox state,
Jobs and Queues, leases and heartbeats, session reconnect windows, provider
breakers, stale preview routes, and cleanup work. The API and worker on Supabase
Compute and the voice agent's sessions on Pipecat Cloud recover from
Supabase-backed durable state; no in-memory worker is authoritative. Engineering
responds through code or configuration deployment outside product authority.

Structured logs support operational diagnosis; durable uTrace records remain
workflow truth. Overload shedding, incident freezes, and failure drills are
outside the demo.
