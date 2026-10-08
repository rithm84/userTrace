# Evaluation and operational observability

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how durable evidence supports verification while operational telemetry monitors the platform:

![Automated tests and the demo harness verify the journey from durable evidence while OpenTelemetry monitors platform operation.](../diagrams/svg/evaluation-and-observability.svg)

## Verification ownership

Automated tests and the demo harness verify the demo journey. Their results inform
releases; they never participate in live authority, change product state, or
supply evidence missing from the product loop. There is no training or
fine-tuning pipeline. Browser effects beyond observation, pointing, and
highlighting are out of scope.

## Verification lanes

- Deterministic tests exercise auth, Feature Request Flow intake, structured
  observation, interruption, live implementation, validation, cancellation,
  blocking, delivery, and Outreach Offers against pinned fixtures, using real
  provider sandboxes where practical.
- Opt-in live voice checks (`UTRACE_REALTIME_E2E=1`) run real calls against the
  local stack. The browser call test runs the API, both worker lanes, and a
  production build of the web app, and drives the real session tab in Chrome
  under its real autoplay policy: a synthetic microphone speaks recorded lines,
  and the test measures what the tab actually plays, including through a reload
  and an agent that joins late. The interview quality test holds the scenario's
  whole interview, greeting to recorded validation, with Maya played by a model
  that hears the agent; it fails on a repeated or re-listed sentence, a second
  greeting, a stall, or a walk back through confirmed checks, and on a judge
  model finding repetition, a restart, a stall, or anything false.
- The demo harness (`tools/demo-harness`) brings a target control plane to the
  [scenario's](../SCENARIO.md) starting state with `demo prepare`, returns a demo
  database to its pre-prepare state with `demo reset-target`, and follows the live
  journey with `demo watch`, which prints each milestone as it commits by reading
  staff read models.
- Every failed, blocked, cancelled, and abandoned case remains observable in
  durable records.

### Running the demo

- `demo prepare` seeds the deployed database, so run `demo reset-target` first. A
  Product Signal, Job, or offer left over from a rehearsal is indistinguishable
  from live work, and the worker acts on it.
- The watched-channel consumer lives inside the deployed worker, so confirm the
  worker is up before the request is posted. If a message was posted while the
  consumer was down, re-ingest that exact message through the
  [operator route](./signal-and-outreach.md#discord-ingestion-and-identity) rather
  than reposting it, which would create a second message.
- A rehearsal posts the canonical request through the bot instead of a person.
  Intake refuses a bot-authored message unless an author-attribution row maps
  that identity to a customer identity, so a rehearsal database carries exactly
  one such row. Reset the rehearsal's Product Signal before the recorded run, or
  the canonical request opens a second Product Signal beside it and the worker
  acts on both.

Verification uses no fixed demonstration duration. It assesses whether the agent
gathers sufficient evidence while maintaining a fluid conversation and whether
implementation progresses without repetitive status narration. Voice transcript
fragments are provisional: they cannot prove a final utterance or that speech was
heard.

## Provider integration checks

Voice integration checks exercise Daily room and meeting-token creation, the voice
agent joining the call, Flux turns, the model's tool calls and gesture marks,
playback, reconnection, and revocation. Reject cross-session or stale setup
results and verify that browser responses contain no provider credential.

Model checks run the voice agent's tools against real records, check GPT-6.1 Sol
gate proposals, intake interpretations, and Case File drafts against their
schemas, and run the
Codex harness in a Vercel Sandbox with the credential added at egress. Record provider configuration revisions, and measure
the voice agent's reply latency and control-plane-to-provider model latency separately.

## Outreach Offers verification

Stripe sandbox tests cover reservation before invitation, entitlement at the final
participation milestone, completed no-implementation cases, system-ended cases,
voluntary cancellation, and one credit adjustment per offer, including retries
that reuse the same operation. Verify each adjustment's customer, account,
environment, amount, and currency, and reject cross-client, cross-customer, or
sandbox/live mismatches.

## Agent cursor measurements

These measurements, taken on 2026-10-06, are the evidence behind grounding the
[agent cursor](./interview-and-voice.md#agent-cursor) in the target registry. They
also record the GPT-Live design it replaced, in which Jev judged every transcript
fragment of the agent's speech; the active design places each gesture from the
voice agent's own inline mark, measured under
[voice and reasoning model measurements](#voice-and-reasoning-model-measurements). Jev is served under the unversioned ID
`typesafe-ai/jev` and the Decisions API was in public beta, so these numbers are a
dated snapshot; rerun them before changing a floor, a question, or a model.
Latency was measured from a workstation, not from the deployed control plane.

### Finding the named control

Each approach answered 68 queries twice across eight Shopalytics states,
including a restyled conversion page and pages without uTrace annotations: 116
answers that name a control and 20 that name nothing on screen. A hit is a point
inside the named control; for a query that names nothing, the correct answer is
no point.

| Approach | Model | Named control | Nothing on screen | p50 latency |
| --- | --- | --- | --- | --- |
| Registry, one choice | Jev | 100% | 100% | 272 ms |
| Registry, one choice | Decisions API (`gpt-6-luna`) | 100% | 100% | 124 ms |
| Accessibility names only, one choice | Jev | 68% | 100% | 273 ms |
| Registry boxes drawn on the screenshot | `gemini-3.5-flash-lite` | 97% | 75% | 924 ms |
| Screenshot | `gpt-6-astra` | 100% | 100% | 2,553 ms |
| Screenshot | Claude computer use (`claude-sonnet-5-5`) | 98% | 100% | 1,623 ms |
| Screenshot | `gemini-robotics-er-2-preview` | 98% | 90% | 1,006 ms |
| Screenshot | `gemini-3.8-flash` | 97% | 100% | 3,091 ms |
| Screenshot | Holo2-8B on an H100 | 98% | 0% | 774 ms |
| Screenshot | Moondream 2 on an H100 | 81% | 0% | 269 ms |

### Pointing inside a component

Eight queries, answered twice, named a part of a component on the applied
conversion page: a chart point, an axis label, a product row, or a review. A
circle around the whole chart or table does not count.

| Approach | Hits | p50 latency |
| --- | --- | --- |
| Registry before parts were declared (whole component only) | 2/16 | 113 ms |
| Registry with declared parts, Jev choice | 16/16 | 270 ms |
| Registry with declared parts, Decisions API choice | 16/16 | 147 ms |
| Registry component, then Moondream 2 on its crop | 10/16 | 375 ms |
| Claude computer use on the screenshot | 16/16 | 1,657 ms |
| `gemini-3.8-flash` on the screenshot | 15/16 | 4,168 ms |
| Holo2-8B on the screenshot | 14/16 | 1,197 ms |
| `gemini-robotics-er-2-preview` on the screenshot | 14/16 | 1,219 ms |
| `gpt-6-astra` on the screenshot | 14/16 | 2,227 ms |

With the parts declared, the conversion page offers 44 targets instead of 13. On
its 52 whole-control queries, answered twice, a Jev choice still found 102 of 104
and the Decisions API 100 of 104. One yes-or-no question per target scaled worse:
88 of 104, and 13 of 16 parts.

### Circling while the agent speaks

GPT-Live (`gpt-live-1`) recorded 16 recaps of the seven scenario filters through
uTrace's own session and sideband connectors, 112 mentions in all. Each judge was
replayed against the recorded transcript in real time, with the production show
rules: a recap's circles are held until its turn is judged a recap, and a
judgement that finishes after a later one was shown is dropped. A mention is on
time when the cursor arrives between 0.8 seconds before the first word that
names the control and 0.25 seconds after its value is heard. Heard times come
from the recorded audio. GPT-Live's transcript of a value word arrived a median
0.68 seconds (10th to 90th percentile: 0.46 to 1.01 seconds) before the word was
heard.

| What Jev judges | Question | Targets | Pointed | On time | Circled something not named | Second circle on the date filter | Jev p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Each finished clause (previous design) | Which control | 13 | 107 | 38 (34%) | 0 | 1 | 333 ms |
| Last six words, every fragment | Which control | 13 | 93 | 69 (62%) | 0 | 10 | 339 ms |
| Last six words, every fragment | Named last | 13 | 111 | 104 (93%) | 0 | 8 | 303 ms |
| Last six words, every fragment | Named last | 44 | 111 | 101 (90%) | 2 | 6 | 318 ms |
| Last six words, every fragment | Yes or no per target | 13 | 112 | 96 (86%) | 45 | 16 | 301 ms |

The two unnamed circles in the 44-target run were the whole filter bar; no chart
point, row, or review was circled. Lowering the target floor to 0.4 circled
reviews three times. The second date circles fall on the custom-range control,
which had no released label in these runs and has one now. Yes-or-no questions
are answered independently, so every date preset answered yes to "Last 30 days"
and the product table answered yes to the product's name.

On labeled clauses held out from calibration, asking for the control named last
matched the earlier question on recaps (53 of 68 each, no harmful answers) but
scored 31 of 36 against 35 of 36 on debriefs, so debriefs keep the earlier
question until recorded debriefs can be measured.

### The judge model

Both models answered the same labeled clauses, twice each, with floors fitted on
a calibration set and scored on held-out cases. A harmful answer circles a
control nobody named.

| Form | Model | Recap | Debrief | Harmful | p50 latency |
| --- | --- | --- | --- | --- | --- |
| One yes or no per target | Jev | 65/68 (95.6%) | 34/36 (94.4%) | 0 | 264 ms |
| One yes or no per target | Decisions API | 60/68 (88.2%) | 30/36 (83.3%) | 6 | 159 ms |

The Decisions API's harmful answers named controls from earlier in the agent's
turn rather than the words being said. An earlier comparison on 122 held-out
cases with production's choice questions found the same order: Jev 99% on recaps
against 94%, and 96% on delegation routing against 88%, at about twice the
latency.

### Voice pipelines

Before choosing to keep GPT-Live, 41 recorded recaps compared how each pipeline
could place gestures. Here a mention is on time when the cursor arrives between
1.5 seconds before and 0.25 seconds after its value is heard.

| Pipeline | Gestures from | Takes | First audio, p50 | Pointed | On time |
| --- | --- | --- | --- | --- | --- |
| `claude-haiku-4-5` script with marks, Deepgram Aura-2 speech flushed at each mark | The script's marks | 6 | 0.77 s | 42/42 | 100% |
| `gpt-live-1` | Jev clause judge | 6 | 0.87 s | 41/42 | 60% |
| `gpt-realtime-2` over WebSocket | Jev clause judge | 5 | 0.75 s | 35/35 | 0% |
| `gpt-realtime-2` with pointing tools | Its own tool calls | 5 | 7.69 s | 35/35 | 0% |
| Gemini 3.8 Live with pointing tools | Its own tool calls | 2 | 10.64 s | 7/14 | 7% |

`gpt-realtime-2` delivers its audio seconds ahead of playback, so a gesture keyed
to arrival is early without alignment to the audio. With pointing tools it made
every call before speaking. Gemini 3.8 Live spoke the tool syntax aloud in 3 of
its 11 recordings. The script-first cascade timed gestures exactly but spoke
with a second voice, so GPT-Live stayed the voice until a full script-first
pipeline with its own turn-taking was measured; see
[voice and reasoning model measurements](#voice-and-reasoning-model-measurements).

### Measurement harness

The harness was not committed; it ran from a workstation. It consisted of:

- A Playwright capture of the Shopalytics fixture served locally at 1440×900,
  saving a screenshot and the target registry, as the SDK reports it, for each
  state: base, filters applied, a candidate with saved-view controls, a restyle,
  and four other pages.
- 68 labeled grounding queries and eight queries naming parts of a component,
  each with the boxes that count as a hit, run against every approach twice.
- Recorders for each voice pipeline. The GPT-Live recorder is a Python WebRTC
  peer that uses uTrace's own GPT-Live connector and sideband, cues the recap,
  and timestamps every audio frame and transcript fragment.
- A scorer that transcribes each recording with Deepgram `nova-3` word
  timestamps, simulates playback to find when each word is heard, and replays
  every judge against the recorded transcript in real time under the production
  show rules.
- Labeled recap, debrief, delegation, acknowledgement, and intake clauses, split
  into calibration and held-out sets, scored with the production acceptance
  rules.

## Voice and reasoning model measurements

These measurements, taken on 2026-10-07 from a workstation, are the evidence
behind keeping GPT-6.1 Sol in its
[reasoning roles](./interview-and-voice.md#conversation-and-backend-work) and
behind the active voice design. They compare Claude Haiku 5.5 (`claude-haiku-5-5`)
in Sol's roles, and a script-first voice pipeline ("option A") in which a text
model writes the reply with inline gesture marks, a speech engine speaks it, and
the application owns turn-taking. Option A, built on Pipecat with Sonnet 5.5 and
Eleven v4 Turbo, is the active [voice agent](./interview-and-voice.md#conversation-and-backend-work);
GPT-Live with delegation to Sol is the baseline it replaced. Provider models change under the same names, so
these numbers are a dated snapshot; rerun them before changing a model or a
pipeline.

### Claude Haiku 5.5 in Sol's roles

Sol ran each role as production pins it: `gpt-6.1-sol` on the Fast tier, at low
effort for gate proposals, delegations, and code work, and medium for intake and
Case File drafting. Haiku ran every role at low, medium, and high effort, each
with adaptive thinking and with thinking disabled; its code work ran through the
Claude Agent SDK. Each structured role answered frozen production inputs four
times. The five Implementation Gate
cases are an approval, a terminated preview environment, missing evidence, an
exhausted Implementation Attempt budget, and another account's data.

| Configuration | Gate proposals correct | Gate after controller checks | Gate p50 / p90 | Case File drafts verified | Case File p50 | Intake correct | Intake p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sol, production efforts | 18/20 | 20/20 | 4.6 s / 10.7 s | 4/4 | 18.1 s | 16/16 | 2.1 s |
| Haiku, low, adaptive | 12/20 | 20/20 | 6.2 s / 8.9 s | 3/4 | 9.5 s | 16/16 | 2.0 s |
| Haiku, medium, adaptive | 12/20 | 19/20 | 7.3 s / 11.3 s | 4/4 | 11.5 s | 16/16 | 2.7 s |
| Haiku, high, adaptive | 12/20 | 18/20 | 11.2 s / 14.2 s | 4/4 | 15.6 s | 16/16 | 2.7 s |
| Haiku, low, thinking disabled | 6/20 | 18/20 | 3.4 s / 4.0 s | 4/4 | 7.9 s | 16/16 | 1.7 s |
| Haiku, medium, thinking disabled | 6/20 | 17/20 | 3.3 s / 4.0 s | 3/4 | 7.8 s | 16/16 | 1.6 s |
| Haiku, high, thinking disabled | 6/20 | 18/20 | 3.3 s / 4.3 s | 4/4 | 7.7 s | 16/16 | 1.6 s |

Every Haiku configuration proposed live implementation in every run for the
terminated environment and the missing evidence; Sol refused the first in 4 of 4
runs and the second in 2 of 4. With thinking disabled, Haiku also approved the exhausted budget in every run and the
other account's data in half. The gate controller's own checks caught most of
these, but a proposal is not meant to depend on them. A structured Haiku call
cost about USD 0.001.

Delegations answered GPT-Live during the live realtime tests
(`test_realtime_voice_live`, `test_realtime_journey_live`, and
`test_realtime_vague_ask_live`), with each model in turn answering:

| Delegation model | Answers | p50 | p90 | Slowest | Tests passed |
| --- | --- | --- | --- | --- | --- |
| Sol, low | 35 | 3.3 s | 7.0 s | 7.9 s | 10/12 |
| Haiku, low, adaptive | 18 | 4.6 s | 6.3 s | 16.9 s | 5/6 |
| Haiku, low, thinking disabled | 15 | 5.6 s | 22.2 s | 29.9 s | 4/6 |
| Haiku, medium, adaptive | 9 | 5.2 s | 7.9 s | 7.9 s | 3/3 |
| Haiku, high, adaptive | 5 | 9.6 s | 11.4 s | 11.4 s | 2/3 |

Each model failed both of the same two tests at least once. Two of Haiku's four
failures ended with GPT-Live saying "One moment" and never giving the answer,
and a third never answered at all.

Code work ran three times per configuration on the Saved Conversion Views change.
Investigation counts how many of the eight files the production change touches
were cited, every cited range having been verified; implementation counts runs
whose candidate passed the acceptance suite.

| Harness and model | Investigation p50 | Needed files cited | Implementation passed | Time to candidate, p50 | Cost per implementation |
| --- | --- | --- | --- | --- | --- |
| Codex, Sol, low | 18 s | 7, 7, 8 | 3/3 | 72 s | USD 0.34 |
| Agent SDK, Haiku, low, adaptive | 21 s | 6, 7, 6 | 3/3 | 110 s | USD 0.024 |
| Agent SDK, Haiku, medium, adaptive | 21 s | 6, 7, 6 | 3/3 | 106 s | USD 0.027 |
| Agent SDK, Haiku, high, adaptive | 45 s | 6, 6, 6 | 3/3 | 148 s | USD 0.030 |
| Agent SDK, Haiku, low, thinking disabled | 14 s | 5, 6, 6 | 2/3 | 72 s | USD 0.016 |

The failed run stopped to report that the change needed a wider scope. With
thinking on, Haiku implemented as reliably at under a tenth of the cost, but took
half again as long or more, and cited fewer of the needed files.

### Script-first voice with gesture marks

The text model receives production's interview instructions with delegation
replaced by an `<application>` block of on-screen controls under short handles
(`T1`, `T2`, ...), and writes a mark before the words that name a control
(`<c id="T5"/>the channel filter`) or wrapped around them. Nine scenarios, run four
times each, cover the recap, a vague ask, a request to be shown a control, a
question about which control is the segment, the debrief, and four moments that
must not circle anything. A run is exact when it marks every required control,
nothing outside the allowed set, and no malformed mark.

| Text model | Exact, mark before | Exact, mark around | First token p50 |
| --- | --- | --- | --- |
| `claude-sonnet-5-5`, `between_tools` thinking, low effort | 34/36 | 36/36 | 732 ms |
| `gpt-6.1-sol`, low, Fast tier | 36/36 | 32/36 | 872 ms |
| `gemini-3.8-flash`, low thinking | 36/36 | 35/36 | 1,454 ms |
| `gpt-6-luna`, no reasoning, Fast tier | 31/36 | 31/36 | 557 ms |
| `gemini-3.5-flash-lite`, minimal thinking | 30/36 | 31/36 | 791 ms |
| `claude-haiku-5-5`, low, thinking disabled | 30/36 | 27/36 | 585 ms |
| `claude-haiku-5-5`, low, adaptive | 28/36 | 27/36 | 463 ms |

End to end, a scripted participant's synthesized speech streamed in real time to
Deepgram Flux (`flux-general-en`), whose eager end of turn started the text
model; the text, marks removed, streamed to ElevenLabs. Each configuration ran
two 12-turn interviews. Reply latency runs from the participant's last speech
(RMS at least 0.01) to the first audio from ElevenLabs, before any network to a
listener.

| Text model | Speech engine | Marks | Reply p50 | Reply p90 | Required circles | Circles not asked for | Words per walkthrough step |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sonnet 5.5 | Eleven v4 Turbo | Before | 1.63 s | 2.23 s | 18/18 | 0 | 1.0 |
| Sonnet 5.5 | Eleven Flash v2.5 | Before | 1.96 s | 2.59 s | 18/18 | 0 | 1.0 |
| Sol | Eleven v4 Turbo | Before | 1.85 s | 2.43 s | 18/18 | 0 | 1.0 |
| Haiku 5.5, thinking disabled | Eleven v4 Turbo | Before | 1.26 s | 1.75 s | 16/18 | 0 | 23.8 |
| `gpt-6-luna` | Eleven v4 Turbo | Around | 1.50 s | 2.02 s | 7/18 | 0 | 11.9 |
| `gemini-3.5-flash-lite` | Eleven v4 Turbo | Around | 1.78 s | 2.41 s | 10/18 | 8 | 15.1 |

Words per walkthrough step should be one ("Mm-hmm"); Haiku narrated the
participant's steps. A circle lands on its word only as well as the speech
engine's character timing matches the audio. Against Deepgram `nova-3` word
timestamps on the same audio, Eleven v4 Turbo's timing was off by a median 53 ms
(p90 193 ms), Flash v2.5 by 23 ms (p90 69 ms), and Eleven v4 ran a median 350 ms
ahead of its audio, which rules it out. Eleven v4 and v4 Turbo stream only over
the Text to Dialogue WebSocket.

Flux's thresholds trade speed for cut-off turns. Over 22 turns each, the
participant's last speech to Flux's end of turn took a median 665 ms at
`eot_threshold` 0.5 with `eager_eot_threshold` 0.3, with two transcripts cut short;
747 ms at 0.6 and 0.4, all complete; and 995 ms at 0.7 and 0.5. Option A uses 0.6
and 0.4.

The GPT-Live baseline, recorded on the server during the live realtime
tests, replied a median 1.45 s after the participant's last speech (p90
4.0 s, 103 replies). Each stretch of agent speech counts as a reply, so a
delegated answer counts from the participant's last speech even when a holding
phrase came first. The 21 replies slower than 2.5 s were all delegated recaps,
proposals, and answers waiting on Sol; 18 of them followed a quick "One moment".
Its recap circles arrived a median 517 ms
after their word (p90 742 ms, 62 circles), and the recaps circled 49 of the 56
filters across 14 sessions.

### Full-duplex turn-taking

Option A was then built with full-duplex turn-taking on Pipecat 1.12.0, from its
own components: Flux turn events with eager end of turn, Sonnet 5.5, ElevenLabs'
Text to Dialogue service with Eleven v4 Turbo, and Pipecat's WebRTC transport.
Production runs the same pipeline over Daily's transport. A user
turn interrupts the agent, the context keeps only the words that were played,
and a pattern aggregator splits the marks out of the text. Four pieces are
uTrace's own:

- While the agent talks, an utterance opens a turn only once its new words are
  neither a backchannel ("mm-hmm", "yeah", "right", "okay") nor echo. Echo means
  no speech-level audio at the participant's microphone during the utterance, or
  words that replay the agent's last four seconds in order. A backchannel that
  outlasts the agent's reply opens the turn, because it may answer a question.
- Text reaches the speech engine a whole word at a time instead of a sentence at
  a time, and the first clause of each reply is flushed, because Eleven v4 Turbo
  otherwise holds about 40 characters before it speaks. Together these saved
  roughly 0.4 seconds before the first audio.
- Pipecat releases a mark once the audio before it has been handed to the
  transport, which ran a median 200 ms (up to 720 ms) before its word played.
  Each gesture is instead sent with its word's text frame, which the transport
  releases on its playout clock, and an interruption drops gestures still
  waiting.
- The page holds each circle back by the audio's measured lag, from the server's
  "bot started speaking" message to that reply's audio being heard, so the
  cursor lands as the word begins. Production dispatches each circle through the
  extension's visual-command path instead, without this delay.

A scripted participant joined over WebRTC as a browser would and spoke
synthesized lines in real time: a long ask with two backchannels during the
reply, a short answer, a sentence with a 1.4-second pause in the middle, a cut-in
during the new version's walkthrough, and a cut-in 0.25 seconds into a reply.
GPT-Live, created as production creates it but without the sideband, heard the
same lines. With production's instructions and no delegation answers its
replies stay short, so its cut-ins and backchannels were measured on 30-second
monologues instead. All times are as the participant's client received the
audio, after both network legs; option A ran on the same workstation, GPT-Live in
OpenAI's cloud.

| Clean audio | Option A | GPT-Live |
| --- | --- | --- |
| First audio after the participant's last speech, p50 / p90 | 2.03 s / 2.44 s (32 replies) | 1.78 s / 2.43 s (32 replies), often only "One moment" |
| Silent after a cut-in, p50 / p90 | 0.74 s / 0.84 s (8 mid-walkthrough), 0.73 s / 0.82 s (8 at a reply's start) | 1.17 s / 1.48 s (19 mid-monologue) |
| Backchannels that stopped the agent | 0/11 | 7/20 |
| Agent heard during a 1.4-second pause | 0/8 | 0/8 |

The reply rows are not comparable. Option A's first audio is its answer. Without
the sideband, nothing answers GPT-Live's delegations, so its first audio was often
the holding phrase, and no answer followed to be timed. With delegations answered,
the GPT-Live baseline's p90 is 4.0 seconds (see above).

Option A's reply time divides into Flux's end of turn (a median 1.02 seconds after
the participant's last speech reached the bot), the reply's first audio leaving
the bot (0.86 seconds later, with Sonnet's first token at a median 0.81 seconds
and the speech engine's first audio at 0.15 seconds), and 0.16 seconds of network
and jitter buffer. A cut-in took 0.10 seconds to reach the bot, 0.51 seconds for
Flux to report the turn, no measurable time to decide, and 0.13 seconds until the
participant heard silence. When Flux ended a turn during the pause, the reply was
cancelled before any of it played.

Across the 8 clean option A runs, 28 gestures fired, each as its word started on
the server. Their messages reached the client a median 164 ms before the word's
audio, and with the page's delay the circle landed a median 2 ms before the word
was heard (largest error 47 ms, 90th percentile 19 ms). No gesture fired after an
interruption, and in all 73 assistant turns the context held exactly the words
played, with the marks of the gestures shown. Asked where the save button is, the
agent circled it in 8 of 8 runs. Asked to show the segment filter, it circled the
filter in 5 of the 7 runs whose reply reached the control's name before the
participant cut in; in the other 2 it named the filter without a mark.

Echo is the open problem. The participant's microphone carried the agent's own
audio, mixed in 60 ms late at 20 or 30 dB below the agent's level, standing in for
what a browser's echo cancellation fails to remove.

| Echo at the microphone | Option A: silent after a cut-in, p50 | Option A: backchannels that interrupted | Option A: echo utterances ignored | GPT-Live: silent after a cut-in, p50 | GPT-Live: backchannels that stopped it |
| --- | --- | --- | --- | --- | --- |
| None | 0.74 s | 0/11 | none occurred | 1.17 s | 7/20 |
| 30 dB below | 0.80 s | 4/7 | 22 | 1.63 s | 3/8 |
| 20 dB below | 1.86 s, and 1 of 4 never stopped it | 3/5 | 19 | 1.28 s | 1/8 |

Flux transcribes leaked echo, often misheard ("Ruth" for "Wren", "Shore" for
"short"), and folds it into the same turn as a backchannel spoken over it, so the
echo's words are credited to the participant. GPT-Live hears its own output: echo
slowed its cut-ins at 30 dB below but did not make it stop on backchannels more
often. Option A's backchannel counts here come from its
own turn decisions, which the client cannot see; GPT-Live's come from its audio.
Option A therefore depends on echo cancellation removing the agent's voice
before Flux hears it.

### Whole interviews

On 2026-10-08 the interview quality test held the scenario's whole interview on a
workstation's local stack, over a live Daily call with the production pipeline and
tools. Maya was played by `claude-sonnet-5-5`, hearing the agent's played words
and answering through synthesized speech; a terse persona answered in as few words
as it could; `claude-opus-5-5` judged each transcript. Each row is one interview,
and each design keeps the changes of the rows above it. Reply time runs from the
end of the participant's speech to the agent's first played word.

| Agent | Persona | Checks' findings | Judge: naturalness, efficiency | Judge's findings | Reply p50 | Reply p90 |
| --- | --- | --- | --- | --- | --- | --- |
| A tool call could end an answer in silence | Maya | a 20-second stall after the agreement; the build line twice | 7, 8 | stall, repetition, awkward | 1.52 s | 7.98 s |
| It speaks after a silent recording | Maya | none | 8, 8 | closing on her word, which the rubric now accepts | 1.97 s | 5.50 s |
| It speaks after a silent recording | Terse | none | 8, 8 | awkward, over-confirmation, repetition | 1.79 s | 7.23 s |
| It lets them finish validating before asking | Maya | none | 9, 9 | none | 1.43 s | 6.85 s |
| It lets them finish validating before asking | Terse | none | 9, 9 | none | 1.93 s | 7.20 s |
| It acknowledges before recording (active) | Maya | none | 9, 9 | none | 1.62 s | 2.39 s |
| It acknowledges before recording (active) | Terse | none | 9, 9 | none | 1.69 s | 2.54 s |

Over the three rounds in which the acknowledgement came first, 5 of 6 interviews
passed; the failure, before the last instruction changes, was two mild
repetitions: a restated check list and an echoed thanks. Recorded silently, an
agreement was acknowledged 5.6 seconds after the participant agreed; said first,
1.0 second after, with the recording running while it played. Thinking only
between tool calls answered about as fast, but in two of four rounds the model
spoke its reasoning aloud ("They've confirmed save, reopen..."); adaptive thinking
at low effort never did. The GPT-Live baseline replied at a p50 of 1.45 seconds
and a p90 of 4.0 seconds (see above).

### Voice and model measurement harness

These harnesses were not committed; they ran from a workstation. They consisted
of:

- For Haiku: the five gate cases, a Case File drafting bundle, and intake
  contexts frozen from production inputs and run through production's own
  prompts and schemas; a pytest plugin that swapped the delegation model inside
  the live realtime tests; and a code benchmark that ran Codex and the Claude
  Agent SDK against the same Shopalytics checkout and acceptance suite.
- For option A's text stage: the nine scenarios with their required and allowed
  controls, scored per run.
- For option A end to end: participant lines synthesized with `gpt-4o-mini-tts`,
  streamed to Flux in 80 ms chunks in real time, with every turn's speech end,
  Flux events, first token, first audio, and mark timing recorded; a check of each
  speech engine's character timing against Deepgram `nova-3` word timestamps; and
  a pytest plugin that recorded the GPT-Live baseline's audio, transcript
  fragments, and gestures during the live realtime tests.
- For full duplex: a Python WebRTC participant (`aiortc`) that speaks the same
  synthesized lines in real time, optionally mixes the agent's audio back into its
  microphone, records the agent's audio level every 20 ms and every gesture
  message, and mirrors the page's circle delay; the bot's own event log of
  microphone activity, Flux events, turn decisions, words as they played,
  gestures, and the final context; and a GPT-Live peer created with production's
  session configuration.

## Operational observability

OpenTelemetry traces cover Vercel-to-control-plane requests, Supabase operations, the voice agent's Daily, Deepgram, Anthropic, and ElevenLabs calls, GPT-6.1 Sol and Jev calls, Discord, GitHub, and Stripe adapters, Vercel Sandbox workspaces and harness runs, and preview routing. Trace context joins operational spans to typed business IDs without placing sensitive content in span attributes. Durable events and evidence remain the authoritative audit path.

The web surfaces report to Vercel Observability. Always-on tracing samples every production request to `utrace-web`, `@vercel/otel` adds Next.js framework and outbound fetch spans, and Web Analytics and Speed Insights record page views and Core Web Vitals. A reported page URL carries route parameters in place of identifiers and no query string, because an invitation path holds a bearer token and query strings hold OAuth codes. The preview front door runs none of this instrumentation: it forwards the browser's `traceparent` untouched, and a second injector fails its propagation probe.

Structured logs use fixed messages and fields for client, signal, session, attempt, candidate, command, job, and provider reference. Metrics cover request rate, errors, duration, queue age, lease health, active sessions, voice reconnects, observation correlation, implementation/check duration, route health, validation return, and delivery. Exemplars connect metrics to traces.

## Traceability

Every provider call records its business IDs and provider reference transactionally. From a Discord Intake Item, operators can traverse Product Signal, invitation, Interview Session, model/tool activity, attempt, workspace sandbox, Preview Version, validation evidence, Case File, and GitHub draft PR in either direction.
