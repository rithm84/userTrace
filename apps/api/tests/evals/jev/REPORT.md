# Does Jev earn its place in the live interview?

Evaluated on 2026-11-02 against `main` at `ce2c6be`, with no product code changed.
The measurements, with their method, are in
[Benchmarks: Jev in the live interview](../../../../../docs/arch-components/benchmarks.md#jev-in-the-live-interview);
[README.md](./README.md) says how to rerun them. This report draws the conclusions
and recommends what to keep, remove, or wire in. The recommendation was approved on
2026-11-02 without its optional changes and is applied on this branch: see
[Applied](#applied).

## Recommendation

| Jev job | Recommendation | Why, in one line |
| --- | --- | --- |
| Acknowledgements (`judge_acknowledgement`) | Keep | As accurate as Sol on labeled moments and more consistent in whole interviews, at a fraction of the cost, and it fails silent |
| Checks shown (`judge_checks_shown`) | Remove; let Sol judge from the activity it already hears | No measured benefit: without it the agent asked the same questions; with it, replaying the real session, the agent re-asked shown checks more often |
| Flow classification (`propose_flow_classification`) | Keep; optionally run it beside code investigation | The only guard against implementing a product failure; as accurate as Sol, faster and cheaper |
| Evidence coverage, claim relationships, attention routing (unwired) | Delete from code and docs | Nothing calls them, no observed failure needs them, and their judgments belong to the agent in context |

These rest on the replay set, the counterfactual replay of the recorded run's last
validation turn, the recorded run itself, and nine whole scripted interviews over
a real call, three each with Jev, with Sol deciding instead, and with neither
([Whole interviews](#whole-interviews)).

## Acknowledgements: keep

On the 31 moments whose label is not ambiguous, Jev acknowledged every moment
that called for it and was wrong in 4 of 34 acknowledgements, all refreshes. Sol,
asked the same question, was right every time; Sol deciding in its own reply, as
the agent would without Jev, missed the moments where the participant showed what
they had said they would. The differences are a few trials on 31 moments: none of
the three judges is clearly better.

What separates them is speed and cost. Jev answers in 0.22 seconds at the median
for about USD 0.00004; Sol takes 0.96 seconds for USD 0.008 as a judge, and as the
agent turns every quiet moment into a model run at about USD 0.004. The feed
drops a decision later than 1.2 seconds, which Sol as a judge misses at p90. An
unavailable Jev costs an acknowledgement, nothing else. And Jev stays a provider
anyway, as intake's decision model, so removing it here saves no dependency.

Whole interviews separate them further. With Jev the agent acknowledged the
dashboard it was told about and the save it asked for in all three runs and the
unannounced refresh in none. Deciding itself, Sol spoke sooner (0.89 seconds after
a moment against 1.48 with Jev, whose decision comes first) but erratically: it
acknowledged the save once and the unannounced refresh twice, and took four more
model runs per interview, USD 0.72 against 0.44. Without acknowledgements at all,
every interview still validated; what they buy is the participant hearing that
the agent noticed.

One finding is about policy, not Jev: in the recorded run, and in live runs with
Jev, the 15-second spacing between acknowledgements kept the reopen, the moment
the validation was waiting for, from being judged at all, because the save had
been acknowledged seconds before.

## Checks shown: remove

The judgment exists so the agent does not ask the participant to repeat a check
the new version already showed. The evidence says it does not deliver that:

- In nine whole interviews no arm asked the participant to repeat a check. With
  Jev's lines the closing question asked only about the rolling range in 3 of 3
  runs; without them, in 5 of 6, and one also asked whether the save worked.
  Neither the 11 Jev calls per interview nor their lines changed the outcome.
- In the recorded run's last validation turn, replayed 30 times each way, the
  agent asked again about the save or the restore, both already shown, in 18 of 30
  replies with Jev's lines and in 3 of 30 without them. The debrief cue tells the
  agent to have the participant confirm the checks the application reports shown,
  and it does.
- Jev's 0.75 floor keeps it precise (one wrong "shown" in 93) at the cost of recall:
  it missed 15% of the clearly shown checks and 25 of 30 contradictions. Live, it
  never reported the relative date range, and reported the refresh and reopen
  after the participant had begun saying it worked.
- The feed waits for the judgment before sending the activity it judged, about
  0.3 seconds each time during validation.

Sol, asked the same questions, found 99% of shown checks and 83% of
contradictions, but called frozen dates a full restore in 5 of 9 answers. That is
an argument against making Sol a separate checks judge, not for keeping Jev: the
agent already hears the same activity, and the validation controller checks every
answer it records against the observations it cites.

## Flow classification: keep

The brief assumed this call cannot change an outcome because the demo supports
only Feature Request Flow. It can, in one direction: when the interview
established no flow, or a product failure, the gate's screen blocks with
`stale_classification` or `unsupported_flow`. That makes it the one check that
stops a bug report discovered in the interview from being implemented as a
feature. Keeping intake's flow instead got 2 of 5 interviews right; Jev and Sol
each got 15 of 15, Jev in 0.27 seconds for USD 0.0003 and Sol in 0.81 seconds for
USD 0.02. It runs before code investigation and costs a waiting participant about
0.4 seconds; running it beside the investigation, which does not depend on it,
would take that off the path. The gate proposal's own `unsupported_flow` blocker
could absorb it, but that was not measured.

## Unwired jobs: delete

`assess_evidence_coverage`, `relate_claim`, and `route_attention` are called only
by `tests/interview/test_agreements_and_attention.py`, yet
[Jev's interview responsibilities](../../../../../docs/arch-components/interview-and-voice.md#jevs-interview-responsibilities)
describes evidence coverage, claim relationships, and attention as live, and
[Contradictions and corrections](../../../../../docs/arch-components/interview-and-voice.md#contradictions-and-corrections)
has a Jev contradiction flag hold dependent work. No measured run failed in a way
they would catch: the agent reached agreement in every run, and the one agreement
failure on record was a missing tool call, fixed otherwise. Each would judge the
conversation from outside it, which is where the checks lines went wrong.

The PRD still requires that "a corrected statement invalidates the affected
earlier claim until resolved". Deleting the Jev flag leaves that requirement
without a mechanism in the docs; the agent, which hears a correction in context,
could record it through a tool, as it records agreements and requested changes.
That is a design change of its own.

## Whole interviews

`test_jev_ablation_live` holds one scripted interview over a real call in one of
three arms: `jev` (production), `sol` (no Jev call; every moment the floor allows
goes to the agent to acknowledge or not, and the agent judges shown checks from the
activity), and `quiet` (no Jev call and no acknowledgements). Three runs each,
interleaved, all validated; the table is in
[Benchmarks: Whole interviews with and without Jev](../../../../../docs/arch-components/benchmarks.md#whole-interviews-with-and-without-jev).

- **Acknowledgements:** Jev's arm acknowledged exactly the awaited moments it was
  allowed to; Sol's was faster and erratic; `quiet` said nothing and lost nothing
  measurable.
- **Checks:** no arm asked for a repeat; the closing question was the same with and
  without Jev's lines, save one `sol` run that also asked about the save.
- **Talk-overs:** none followed an activity cue in any arm.
- **Validation time:** from "it all came back" to the validation-complete cue,
  19.7–23.2 seconds with Jev, 18.6–19.9 in `sol`, 17.3–20.2 in `quiet`, within the
  spread of three runs.
- **Cost:** Sol's runs were the cost that moved: medians of 18 runs and USD 0.44
  per interview with Jev, 22 and USD 0.72 in `sol`, 16 and USD 0.37 in `quiet`.
  Jev's 11 calls per interview cost about USD 0.0005.

An earlier batch was stopped after one run per arm and carried a script flaw (the
reopen restored a conversion state the save had not carried); it is excluded.

## Applied

Approved with one scope note: keep acknowledgements and flow classification
unchanged, and skip both optional changes.

- **Checks shown, removed.** `app/interview/voice/checks_shown.py` is gone, with
  `judge_checks_shown` and `ChecksShown` in `app/interview/attention.py`,
  `VoiceJudge.checks_shown`, the feed's checks plumbing, and the live test of it.
  The instructions' validation step and the debrief cue now ask the agent to judge
  shown checks from the activity, in exactly the `sol` arm's wording. The
  `jev_attention` bundle, now an acknowledgement's alone, depends on no revision;
  an accepted correction still invalidates it.
- **Unwired jobs, removed.** `assess_evidence_coverage`, `relate_claim`, and
  `route_attention` are gone, with their test.
- **Docs.** `interview-and-voice.md` (Jev's responsibilities are acknowledgements;
  the debrief and preview-activity cues; no contradiction flag), `validation.md`,
  `context-assembly.md`, `ARCHITECTURE.md`, and a dated note in `benchmarks.md`. No
  diagram drew the removed jobs.
- **Kept as it was.** The `attention_purpose` enum's `evidence_coverage` and
  `claim_relationship` values, now unused by new judgments, stay: removing enum
  values needs a migration, and past rows carry them. The evaluation keeps the
  checks-shown question as measured in `measured.py`, and replaying it reproduces
  the earlier questions and counterfactual context exactly.

### The correction requirement

The PRD requires that "a corrected statement invalidates the affected earlier
claim until resolved". `interview-and-voice.md` still says an accepted correction
supersedes the claim, invalidates dependent analysis, and holds incompatible
implementation steps, and `app.interview.evidence.accept_correction` does all
three. Nothing on the live path calls it: only
`tests/interview/test_context_assembly.py` does. The voice agent's
`record_evidence_claim` tool can record a contradicted claim but never accepts a
correction, and the removed Jev flag would only have flagged one. So no one detects
and accepts a correction during the call. A changed ask during validation is
already covered, through `record_requested_change` and the gate.

Proposed owner, for you to decide: the voice agent, which hears the correction in
context, through a tool such as `record_correction` (or a `supersedes` argument on
`record_evidence_claim`) that calls `accept_correction`. Code then applies the
supersession, the invalidation, and the hold, as it does today in the test.

### Shared-file hunks

For merging with the backchannels and fast-preview branches (line numbers are
`main`'s):

- `apps/api/app/interview/voice/instructions.py`, step 4 (lines 138–140): "The
  application tells you which checks the new version already showed:" becomes
  "From what the application tells you they did in the new version, judge which
  checks it already showed:", re-wrapped over the same three lines.
- `apps/api/app/interview/voice/cues.py`, `_DEBRIEF_CUE` (lines 139–140): "the
  application tells you which checks the new version showed;" becomes "judge from
  what the application tells you they did which checks the new version showed;",
  two string lines becoming three.
- `apps/api/app/interview/voice/agent.py`: two deleted lines, the `ShownChecks`
  import (line 76) and its argument to `feed_activity` (line 586).
- `apps/api/app/interview/voice/activity_feed.py`: the module docstring's checks
  sentence (lines 15–17); the import (47); the `checks` parameter (73);
  `held_checks` and the checks call in the loop (85–117); `_append`'s `checks`
  parameter, tail, and log field (190–203); and `_content`'s error message (220).
  `_ACKNOWLEDGE` is untouched.
- `pipeline.py` and the agent's tools are untouched.

## What the recommendation proposed

The optional changes below were not approved and are not applied.

- **Checks shown:** remove `ShownChecks` from the activity feed and
  `judge_checks_shown` with its tests; reword the sentence in the instructions'
  validation step and the debrief cue that say the application reports shown
  checks (the ablation's `sol` arm uses wording that works); update
  `interview-and-voice.md` ("Checks shown during validation"), `validation.md`,
  the `jev_attention` dependency in `context-assembly.md`, and `ARCHITECTURE.md`.
- **Unwired jobs:** delete the three functions and their test; remove their
  bullets and the contradiction flag from `interview-and-voice.md`; decide who owns
  corrections.
- **Flow classification, optional:** run it concurrently with code investigation
  in `app/gate/jobs.py`.
- **Acknowledgements, optional:** revisit the 15-second spacing so an awaited
  moment right after another acknowledgement is still judged.

The voice-agent code these touch (`instructions.py`, `cues.py`, the activity feed)
is also being changed on other branches, so each change should land after them.

## Limits

- One recorded production interview exists; the other moments are scripted, and
  one evaluator labeled them all. The labels and their reasons are in the code.
- The live ablation's participant is scripted and the same in every arm, which
  makes arms comparable but tests one validation pass, three times per arm.
- The counterfactual rebuilds the agent's context from records: its tool calls are
  left out and its cues use today's wording.
- Jev is served under an unversioned ID, so these numbers hold for the Jev served
  on 2026-11-02.
