# Validation

[Back to uTrace architecture](../ARCHITECTURE.md)

Same-user validation ends in one of these outcomes:

![A Validation Attempt on one exact candidate leads to a draft PR when validated; any other outcome or a passed return deadline closes the case with an unvalidated Case File, and an agreed change goes back to the gate.](../diagrams/svg/validation.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Validation session

A candidate is validated in the live Interview Session that produced it, or in a
later session when the originating user returns within the case-level deadline.
The Product Signal's participant, candidate, criteria, evidence, outcome, and
delivery authority stay independent of any other case.

## Validation binding

Each Validation Attempt binds exactly one of each:

- Product Signal
- originating user
- Implementation Attempt
- immutable Preview Version
- candidate runtime allocation
- prepared dataset manifest and hashes
- authenticated product identity
- acceptance-criteria revision
- workflow
- Interview Session

The Case File cites this exact tuple.

Only the immutable originating user, proven through the Discord-bound invitation
and session authentication, may validate; no colleague, client member, or operator
can substitute.

## Attempt lifecycle

`pending`, `in_progress`, and `interrupted` are open states, and a Product Signal
holds at most one attempt in an open state at a time. `completed` records the
outcome.

Each step has its own requirements:

- **Begin:** the originating user's identity matches, the candidate is ready, and
  observation readiness is current for the participant's open page, which must be
  on the candidate's own preview. Readiness of their previous version, or of a
  candidate page no evaluation has read yet, does not count, so the walkthrough of
  what changed waits until the candidate's own page and controls are observed. The
  live session's voice agent begins the attempt as soon as these hold, not when it
  next asks, so the evidence window opens before the participant tries the
  candidate.
- **Resume:** everything required to begin, on the same exact candidate, so a
  rebuilt or replaced candidate cannot inherit an interrupted attempt.
- **Complete:** a recorded outcome. Completing as `validated` additionally
  requires the user's confirmation of every criterion, affirmative proof for every
  criterion, no unresolved contradiction, and passed
  [full checks](./implementation.md#fast-and-full-checks) on the candidate's
  files. The state machine refuses the transition otherwise, rather than relying
  on the caller to check. An outcome recorded while those checks still run waits
  up to a minute for them; a version whose full checks failed records no outcome,
  because a corrected version replaces it.

An interruption is recorded as such, never as a verdict, and no outcome may be
recorded while the originating user is absent.

## Readiness and session shape

Validation is a voice encounter in the same two-tab experience. The user may
discuss the problem while a candidate prepares, but criterion-level validation
waits for [observation readiness](./interview-and-voice.md#readiness-has-two-boundaries)
on the exact candidate, including its authenticated handoff and dataset.

Confirm during criteria agreement that the approved preview can observe the
affirmative evidence each criterion needs; an unobservable criterion is clarified
or blocked before implementation authority depends on it. The preview
[presents its dataset on today's date](./preview-runtime-and-seeding.md#seed-manifest),
and the agent is told that date, so a criterion about a relative range such as the
last 30 days holds on any day. A check the participant already performed in the
candidate is [confirmed rather than repeated](./interview-and-voice.md#application-cues):
the voice agent judges that from the activity it is told about.

There is no fixed demonstration duration. The conversation stays focused on the
accepted workflow and adjacent surfaces materially touched by the diff. The voice
agent carries the fluid conversation; orchestration supplies final accepted
claims, corrections, criteria, telemetry, and candidate identity. The
[transcript contract](./interview-and-voice.md#transcript-and-evidence-contract)
limits what fragments prove.

Cognitive assistance follows the [runtime roles](../ARCHITECTURE.md#system) and
[context contract](./context-assembly.md#stage-boundaries) and is advisory: the
validation controller owns criteria, evidence binding, outcomes, and transitions.
The agent's judgment of which checks a run already showed never substitutes for
explicit originating-user confirmation, observed behavior, or exact-candidate
binding; unresolved interpretations need clarification or deeper analysis.

## Outcome criteria

`validated` requires explicit originating-user confirmation and proportionate,
affirmative proof for every acceptance criterion on the exact candidate and
dataset. Silence, elapsed time, or the absence of errors is not affirmative proof.

### Evidence for each criterion

Each criterion settles as follows:

![An observable criterion is decided by the newest observed run of its check and the participant's confirmation, with a rerun when the check was not set up or the participant confirmed a contradicted check; a subjective criterion is decided by the participant's judgment of an observed workflow.](../diagrams/svg/validation-criterion-evidence.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

- **Observable behavior**, what the product does when the user acts as the
  observation path reports it, such as a view being saved or what a chart
  displays, needs structured observed evidence. One coherent workflow may support several
  criteria, and one admissible source suffices without separate browser and
  server events.
- **Subjective criteria** use the user's explicit judgment about an observed
  workflow: whether a workflow feels easier, and how something looks, where it
  sits on the page, or whether a control is shown, enabled, or disabled, which the
  observation path does not report. The voice agent marks a check this way when it
  proposes it, so no agreed check needs an observation the path cannot produce.
- **Observability is fixed at agreement**, so an unobserved behavior cannot be
  relabeled as subjective to pass validation.
- **The newest observed run decides.** A contradicting run that a later
  supporting run settles stays on record as superseded; a contradiction with no
  later supporting run stands. A run where the participant did not set up what
  the check needs is evidence neither way.
- **A confirmed contradiction blocks the outcome.** During the live session, no
  outcome is recorded while the originating user has confirmed a check the
  preview contradicted; only a rerun that settles the conflict, or their unsure
  answer, unblocks it.

An enthusiastic statement over an observed error cannot validate. The controller
records criterion-level proof, what the user said, what the product did,
corrections that superseded earlier claims, and any unresolved contradiction.

### Unsuccessful outcomes

- `partially_validated` means some criteria succeeded and others remain
  unconfirmed or failed.
- `not_validated` means the user rejects the outcome or the observed workflow
  still fails.
- `unclear` means ambiguity, correction, interruption, or contradictory evidence
  prevents a supported conclusion.

For any of these, persist the outcome and available feedback first. The same
lifecycle transition closes the Product Signal with an explanatory unvalidated
Case File and no PR, and the session tab tells the participant the new version did
not pass every agreed check, distinct from a technical blocker.

### Closing the conversation

Whatever the outcome, once it is recorded the agent closes the conversation
unprompted: it thanks the participant, says what the product team receives, and
says the session is closing. The session ends once that goodbye has played; the
Case File, any pull request, and cleanup finish after the participant leaves.

### Changes asked during validation

A version the originating user finds partially validated, not validated, or
unclear is why they would want it changed, so before recording that verdict the
agent asks whether they would like a change, while the case's iteration budget
(`implementation_limit`) allows another attempt. A verdict is recorded only once they
want no change or no attempt is left. Once they agree a change out loud:

- The whole change as it now stands and the checks that now apply freeze at a
  new criteria revision.
- The open Validation Attempt is abandoned, and the session returns to
  implementation.
- The Product Signal goes back to `gate_pending` (`revision_requested`), within
  the case's pinned `implementation_limit`.

The next version is validated afresh. If the gate refuses the change, the case
closes without a PR: its Case File records the refusal as the blocker, and the agent
says the change will not be built. Cancellation and other terminal
outcomes take precedence. Feedback never licenses a code revision directly.
Preview Versions and validation outcomes remain immutable.

The [debrief cue](./interview-and-voice.md#application-cues) walks the participant
through what changed before they try a new version.

### A fix after a failed full check

A full check that fails on the version being validated is a technical result, not
the participant's judgment. The open Validation Attempt is abandoned, the
Implementation Attempt corrects the change, and the corrected version swaps in as
a new Preview Version. The participant validates it afresh, and the agent asks for
a quick second look rather than the whole walkthrough again. The criteria stay the
same, and no revision of the change is used.

## Interruption and departure

Temporary transport or voice loss retains the Validation Attempt through the
bounded reconnection window. Departure, Pause, interrupted validation, the return
link, runtime cleanup or rebuild, and terminal expiry follow the
[bounded return and terminal expiry contract](./failure-recovery.md#bounded-return-and-terminal-expiry).

An explicit cancellation ends the Product Signal and produces no Case File or PR.
An infrastructure failure preserves a resumable blocked attempt only within the
applicable recovery deadline; it does not become a user judgment.

## Delivery guarantee

A validated result sent to output assembly always carries its full binding tuple,
criterion-level confirmation, proportionate observed proof, contradiction check,
candidate source digest, base revision, the full-check results on the same file
digests, and artifact provenance. GitHub delivery
may begin only from that frozen record.
