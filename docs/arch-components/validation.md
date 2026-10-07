# Validation

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows the same-user validation outcomes:

![The originating user validates one exact candidate; any other outcome closes the case with an unvalidated Case File.](../diagrams/svg/validation.svg)

## Validation session

A candidate is validated in the live Interview Session that produced it, or in a
later session when the originating user returns within the case-level deadline.
The Product Signal's participant, candidate, criteria, evidence, outcome, and
delivery authority stay independent of any other case.

## Validation binding

Each Validation Attempt binds one Product Signal, originating user, Implementation Attempt, immutable Preview Version, candidate runtime allocation, prepared dataset manifest and hashes, authenticated product identity, acceptance-criteria revision, workflow, and Interview Session. The Case File cites this exact tuple.

Only the immutable originating user may validate. uTrace proves that identity through the Discord-bound invitation and session authentication. A colleague, client member, or operator cannot substitute.

## Attempt lifecycle

A Validation Attempt is `pending` until it begins, `in_progress` while the
originating user works through the criteria, and `interrupted` when they leave;
an interrupted attempt returns to `in_progress` on resume. Those three states are
open, and a Product Signal holds at most one attempt in an open state at a time.
The terminal states are `completed`, which records the outcome, and `abandoned`,
which any open state can reach.

Beginning an attempt requires the originating user's identity to match, the
candidate to be ready, and observation readiness to be current. The live
session's voice sideband begins it as soon as those hold, rather than when the
agent next delegates, so the evidence window opens before the participant starts
trying the candidate. Resuming an
interrupted attempt requires all of that and the same exact candidate, so a
rebuilt or replaced candidate cannot inherit an interrupted attempt. Completing
an attempt requires a recorded outcome, and completing it as `validated`
additionally requires the user's confirmation of every criterion, affirmative
proof for every criterion, and no unresolved contradiction — the state machine
refuses the transition otherwise, rather than relying on the caller to check.

An interruption records itself as an interruption. It is not a verdict, and no
outcome may be recorded while the originating user is absent.

## Readiness and session shape

Validation is a voice encounter using the same two-tab experience and the complete structured observation path. The user may discuss the problem while a candidate prepares, but criterion-level validation cannot start until the exact candidate, embedded browser SDK, Server SDK, extension binding, trace correlation, authenticated handoff, and dataset are ready. Confirm during criteria agreement that the approved preview can observe the affirmative evidence each criterion needs; an unobservable criterion is clarified or blocked before implementation authority depends on it. The preview runs on its dataset's fixture clock, so a criterion about dates or ranges is stated as what the preview shows on that date, and the agent is told the date so it never measures the preview against today's. A check the participant already performed in the candidate is [confirmed rather than repeated](./interview-and-voice.md#jevs-interview-responsibilities).

There is no fixed demonstration duration. The conversation remains focused on the accepted workflow and adjacent surfaces materially touched by the diff. GPT-Live carries the fluid conversation; orchestration supplies final accepted claims, corrections, criteria, telemetry, and candidate identity. Transcript fragments do not prove a final user turn or that the user heard an utterance.

Cognitive assistance follows the [runtime roles](../ARCHITECTURE.md#system) and
[context contract](./context-assembly.md#stage-boundaries). Its outputs are
advisory; the validation controller owns criteria, evidence binding, outcomes,
and transitions. Jev assesses coverage and claim relationships per supplied
criterion using the [interview responsibilities](./interview-and-voice.md#jevs-interview-responsibilities).
Its scores never substitute for explicit originating-user confirmation, observed
behavior, or exact-candidate binding; unresolved interpretations require
clarification or deeper analysis.

## Outcome criteria

`validated` requires explicit originating-user confirmation and proportionate,
affirmative proof for every acceptance criterion on the exact candidate and
dataset. One coherent workflow may support several criteria, and a criterion does
not require separate browser and server events when one admissible source is
sufficient. Observable behavior must have structured observed evidence; subjective
criteria, such as whether a workflow feels easier, use the user's explicit judgment
about an observed workflow. An unobserved objective behavior cannot be relabeled
as subjective to pass validation. Silence, elapsed time, or the absence of errors is not affirmative proof.

An enthusiastic statement over an observed error cannot validate. The controller records criterion-level proof, what the user said, what the product did, corrections that superseded earlier claims, and any unresolved contradiction.

The newest observed run of a check decides it. A run that contradicted a criterion
is settled when the participant later repeats the check and the product supports
it; the earlier run stays on record as superseded. A contradiction with no later
supporting run stands. A run in which the participant did not set up what the
check needs is not evidence either way, so the agent asks them to run it again.

During the live session, no outcome is recorded while the originating user has
confirmed a check that what the preview showed contradicts. The conflict is theirs
to settle: the agent tells them what the preview showed and asks them to run that
check again, and only an answer that settles it, or their unsure answer, lets the
outcome be recorded.

- `partially_validated` means some criteria succeeded and others remain unconfirmed or failed.
- `not_validated` means the user rejects the outcome or the observed workflow still fails.
- `unclear` means ambiguity, correction, interruption, or contradictory evidence prevents a supported conclusion.

For `partially_validated`, `not_validated`, or `unclear`, persist the outcome and
available feedback first. The same lifecycle transition closes the Product Signal
with an explanatory unvalidated Case File and no PR, and the session tab tells the
participant the new version did not pass every agreed check, distinct from a
technical blocker; the demo does not iterate after such an outcome.

Whatever the outcome, once it is recorded the agent closes the conversation
itself: it thanks the participant, says what the product team receives, and says
the session is closing, rather than waiting to be asked. The session ends once
that goodbye has played; the Case File, any pull request, and cleanup finish
after the participant leaves.

A change is asked for before the verdict instead: while trying a new version, the
originating user may ask for something to be different in it. Once they agree it
out loud, the whole change as it now stands and the checks that now apply freeze
at a new criteria revision, the open Validation Attempt is abandoned, the session
returns to implementation, and the Product Signal goes back to `gate_pending`
(`revision_requested`), within the case's pinned `implementation_limit`. The next
version is validated afresh. If the gate refuses the change, the case returns to
`awaiting_validation` (`revision_declined`) with the criteria that version was
approved for, and the agent says so.

Before the person tries a new version, the agent walks them through what changed:
once its page has loaded, the agent is told the controls it shows that no earlier
page of the session showed, then those the previous page showed in another region
of the screen, each control named once whatever value it shows. It describes the
ones that deliver the agreed change in the order the person will use them, a
sentence each, and the cursor circles each one as it is named. Cancellation and other terminal outcomes take precedence. Feedback
never licenses a code revision directly. Preview Versions and validation outcomes
remain immutable.

## Interruption and departure

Temporary transport or voice loss retains the Validation Attempt through the
bounded reconnection window. No outcome may be recorded while the originating user
is absent. Departure, Pause, interrupted validation, the return link, runtime
cleanup or rebuild, and terminal expiry follow the centralized
[bounded return and terminal expiry contract](./failure-recovery.md#bounded-return-and-terminal-expiry).
Validation resumes only against the same exact candidate after fresh readiness
checks.

An explicit cancellation ends the Product Signal and produces no Case File or PR. An infrastructure failure preserves a resumable blocked attempt only within the applicable recovery deadline; it does not become a user judgment. A successful validation may be followed by user departure while delivery and cleanup complete.

## Delivery guarantee

A validated result sent to output assembly always carries its full binding tuple,
criterion-level confirmation, proportionate observed proof, contradiction check,
candidate source commit, base revision, and artifact provenance. GitHub delivery
may begin only from that frozen record.
