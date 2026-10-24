# Evaluation and operational observability

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how durable evidence supports verification while operational telemetry monitors the platform:

![Automated tests and the demo harness verify the journey from durable evidence while structured logs and durable IDs support operational diagnosis.](../diagrams/svg/evaluation-and-observability.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

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
  local stack. The browser call test runs the API, which hosts each voice agent
  session in its own process, the worker, and a
  production build of the web app, and drives the real session tab in Chrome
  under its real autoplay policy: a synthetic microphone speaks recorded lines,
  and the test measures what the tab actually plays, including through a reload
  and an agent that joins late. The interview quality test holds the scenario's
  whole interview, greeting to recorded validation, with the scenario's
  originating user played by a model that hears the agent; it fails on a
  repeated or re-listed sentence, a second greeting, a stall, or a walk back
  through confirmed checks, and on a judge model finding repetition, a restart,
  a stall, or anything false.
- Each test empties the tables it owns, so two test processes cannot share one
  database. Live checks run side by side on separate local Supabase stacks: a copy
  of `supabase/` with its own `project_id` and ports, started with
  `supabase start --workdir`, gives each process its own database. Tests that bind
  a fixed local port, such as the browser call test and the telemetry ingestion
  tests, still run one process at a time.
- The demo harness (`tools/demo-harness`) brings a target control plane to the
  [scenario's](../SCENARIO.md) starting state with `demo prepare`, and follows the
  live journey with `demo watch`, which prints each milestone as it commits by
  reading staff read models. `demo rehearse-intake` posts the canonical request
  and checks what routing did, for rehearsals only; `demo settle-invoice`
  finalizes the service invoice and cross-checks the issued credit; and
  `demo verify` asserts the scenario's acceptance boundary. `demo reset-target`
  reports what a demo database holds, and with
  `--i-understand-this-deletes-demo-state` returns it to its pre-prepare state.
- Every failed, blocked, cancelled, and abandoned case remains observable in
  durable records.

### Running the demo

- `demo prepare` seeds the deployed database, so run
  `demo reset-target --i-understand-this-deletes-demo-state` first. A
  Product Signal, Job, or offer left over from a rehearsal is indistinguishable
  from live work, and the worker acts on it.
- The watched-channel consumer lives inside the deployed worker, so confirm the
  worker is up before the request is posted. If a message was posted while the
  consumer was down, re-ingest that exact message through the
  [operator route](./signal-and-outreach.md#discord-ingestion-and-identity) rather
  than reposting it, which would create a second message.
- A rehearsal (`demo rehearse-intake`) posts the canonical request through the
  bot instead of a person. Intake refuses a bot-authored message unless an author-attribution row maps
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
also cover the GPT-Live design it replaced, in which Jev judged GPT-Live's
transcript of the agent's speech and circled at best 93% of the controls its
recaps named on time; the active design places each gesture from the voice
agent's own inline mark, measured under
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

### The judge model

On held-out labeled recap and debrief clauses, Jev judged 65 of 68 recaps and 34
of 36 debriefs correctly, against 60 and 30 for the Decisions API, and gave no
harmful answer (a circle on a control nobody named) against the Decisions API's
six, at a p50 of 264 ms against 159 ms.

### Voice pipelines

On 41 recorded recaps, a script-first cascade whose text model wrote gesture marks
put every circle on time, and no speech-to-speech model did: `gpt-live-1` with a
Jev clause judge was on time for 60% of mentions, `gpt-realtime-2` delivered its
audio seconds ahead of playback so its gestures were early, and with pointing
tools `gpt-realtime-2` made every call before speaking (first audio p50 7.69 s)
while Gemini 3.8 Live spoke the tool syntax aloud in 3 of its 11 recordings. The
full script-first pipeline is measured under
[voice and reasoning model measurements](#voice-and-reasoning-model-measurements).

### Measurement harness

The harness was not committed; it ran from a workstation. It consisted of:

- A Playwright capture of the Shopalytics fixture served locally at 1440×900,
  saving a screenshot and the target registry, as the SDK reports it, for each
  state: base, filters applied, a candidate with saved-view controls, a restyle,
  and four other pages.
- 68 labeled grounding queries and eight queries naming parts of a component,
  each with the boxes that count as a hit, run against every approach twice.
- Recorders for each voice pipeline, timestamping every audio frame and
  transcript fragment.
- A scorer that transcribes each recording with Deepgram `nova-3` word
  timestamps, simulates playback to find when each word is heard, and replays
  every judge against the recorded transcript in real time.
- Labeled recap, debrief, acknowledgement, and intake clauses, split into
  calibration and held-out sets, scored with the production acceptance rules.

## Voice and reasoning model measurements

These measurements, taken on 2026-10-07 from a workstation, are the evidence
behind keeping GPT-6.1 Sol in its
[reasoning roles](./interview-and-voice.md#conversation-and-backend-work) and
behind the active voice design. They compare Claude Haiku 5.5 (`claude-haiku-5-5`)
in Sol's roles, and a script-first voice pipeline ("option A") in which a text
model writes the reply with inline gesture marks, a speech engine speaks it, and
the application owns turn-taking. Option A, first measured with Sonnet 5.5, is the
active [voice agent](./interview-and-voice.md#conversation-and-backend-work), with
GPT-6.1 Sol on the Ultrafast tier writing its words; the retired GPT-Live pipeline
is the baseline it replaced. Provider models change under the same names, so
these numbers are a dated snapshot; rerun them before changing a model or a
pipeline.

### Claude Haiku 5.5 in Sol's roles

Sol ran each role as `gpt-6.1-sol` on the Fast tier, at low
effort for gate proposals and code work, and medium for intake and Case File
drafting. Haiku ran every role at low, medium, and high effort, each with
adaptive thinking and with thinking disabled; its code work ran through the
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

The text model receives the interview instructions and an `<application>` block
of on-screen controls under short handles (`T1`, `T2`, ...), and writes a mark before the words that name a control
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
747 ms at 0.6 and 0.4, all complete; and 995 ms at 0.7 and 0.5. Option A used 0.6
and 0.4 with Sonnet, and uses 0.7 and 0.4 with Sol (see
[GPT-6.1 Sol Ultrafast as the voice text model](#gpt-61-sol-ultrafast-as-the-voice-text-model)).

The retired GPT-Live baseline, recorded on the server during the live realtime
tests, replied a median 1.45 seconds after the participant's last speech (p90 4.0
seconds, 103 replies).

### Full-duplex turn-taking

Option A was then built with full-duplex turn-taking on Pipecat 1.12.0, from its
own components: Flux turn events with eager end of turn, Sonnet 5.5, ElevenLabs'
Text to Dialogue service with Eleven v4 Turbo, and Pipecat's WebRTC transport.
Production runs the same pipeline over Daily's transport. A user
turn interrupts the agent, the context keeps only the words that were played,
and a pattern aggregator splits the marks out of the text. uTrace added four
pieces of its own, and production keeps the first three:

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
GPT-Live, configured as production then configured it but with nothing answering
its delegations, heard the same lines. Its replies stayed short, so its cut-ins
and backchannels were measured on 30-second monologues instead. All times are as
the participant's client received the audio, after both network legs; option A ran on the same workstation, GPT-Live in
OpenAI's cloud.

| Clean audio | Option A | GPT-Live |
| --- | --- | --- |
| First audio after the participant's last speech, p50 / p90 | 2.03 s / 2.44 s (32 replies) | 1.78 s / 2.43 s (32 replies), often only "One moment" |
| Silent after a cut-in, p50 / p90 | 0.74 s / 0.84 s (8 mid-walkthrough), 0.73 s / 0.82 s (8 at a reply's start) | 1.17 s / 1.48 s (19 mid-monologue) |
| Backchannels that stopped the agent | 0/11 | 7/20 |
| Agent heard during a 1.4-second pause | 0/8 | 0/8 |

The reply rows are not comparable. Option A's first audio is its answer.
GPT-Live's first audio was often a holding phrase, and no answer followed to be
timed. With its delegations answered, the GPT-Live baseline's p90 was 4.0 seconds
(see above).

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
echo's words are credited to the participant. GPT-Live heard its own output: echo
slowed its cut-ins at 30 dB below but did not make it stop on backchannels more
often. Option A's backchannel counts here come from its
own turn decisions, which the client cannot see; GPT-Live's come from its audio.
Option A therefore depends on echo cancellation removing the agent's voice
before Flux hears it.

### Whole interviews

On 2026-10-08 the interview quality test held the scenario's whole interview on a
workstation's local stack, over a live Daily call with the production pipeline and
tools. The scenario's originating user, the `maya` persona, was played by
`claude-sonnet-5-5`, hearing the agent's played words and answering through
synthesized speech; a `terse` persona answered in as few words as it could; and
`claude-opus-5-5` judged each transcript. Each row is one interview, and each
design keeps the changes of the rows above it. Reply time runs from the
end of the participant's speech to the agent's first played word.

| Agent | Persona | Checks' findings | Judge: naturalness, efficiency | Judge's findings | Reply p50 | Reply p90 |
| --- | --- | --- | --- | --- | --- | --- |
| A tool call could end an answer in silence | Maya | a 20-second stall after the agreement; the build line twice | 7, 8 | stall, repetition, awkward | 1.52 s | 7.98 s |
| It speaks after a silent recording | Maya | none | 8, 8 | closing on her word, which the rubric now accepts | 1.97 s | 5.50 s |
| It speaks after a silent recording | Terse | none | 8, 8 | awkward, over-confirmation, repetition | 1.79 s | 7.23 s |
| It lets them finish validating before asking | Maya | none | 9, 9 | none | 1.43 s | 6.85 s |
| It lets them finish validating before asking | Terse | none | 9, 9 | none | 1.93 s | 7.20 s |
| It acknowledges before recording | Maya | none | 9, 9 | none | 1.62 s | 2.39 s |
| It acknowledges before recording | Terse | none | 9, 9 | none | 1.69 s | 2.54 s |
| GPT-6.1 Sol Ultrafast writes its words | Maya | none | 9, 9 | none | 1.73 s | 3.18 s |
| GPT-6.1 Sol Ultrafast writes its words | Terse | none | 6, 6 | wrong, over-confirmation, awkward | 1.80 s | 3.03 s |
| It names filters that stayed and treats an example name as an example | Maya | none | 9, 9 | none | 1.01 s | 1.69 s |
| It names filters that stayed and treats an example name as an example | Terse | none | 7, 7 | repetition, over-confirmation, awkward | 1.21 s | 2.06 s |
| End of turn at 0.7, and an interrupted reply finishes in a whole sentence (active) | Maya | none | 8, 9 | none | 1.46 s | 2.17 s |
| End of turn at 0.7, and an interrupted reply finishes in a whole sentence (active) | Terse | none | 8, 9 | none | 1.36 s | 2.04 s |

Over the three rounds in which the acknowledgement came first, 5 of 6 interviews
passed; the failure, before the last instruction changes, was two mild
repetitions: a restated check list and an echoed thanks. Recorded silently, an
agreement was acknowledged 5.6 seconds after the participant agreed; said first,
1.0 second after, with the recording running while it played. The GPT-Live
baseline replied at a p50 of 1.45 seconds and a p90 of 4.0 seconds (see above).

### GPT-6.1 Sol Ultrafast as the voice text model

GPT-6.1 Sol on the Ultrafast tier replaced Claude Sonnet 5.5 in the same pipeline,
through the Responses API over one WebSocket per call. Sol accepts no reasoning
effort below `low`, and Pipecat requests `none` unless the effort is set. In the
nine-scenario text-stage benchmark Sol marked every required control exactly in
36 of 36 runs with the mark before its words, the form the agent uses.

A reply's time splits into Flux's end of turn and the agent's own part. From
Flux's committed end of turn, the agent's first audio took:

| Text model | Interviews | End of turn to first text, p50 / p90 | First text to first audio, p50 / p90 | End of turn to first audio, p50 / p90 |
| --- | --- | --- | --- | --- |
| `claude-sonnet-5-5`, adaptive thinking, low effort, `eot_threshold` 0.6 | 8 | 0.71 s / 1.35 s | 0.17 s / 0.48 s | 1.04 s / 1.57 s |
| `gpt-6.1-sol`, low effort, Ultrafast tier, `eot_threshold` 0.6 | 10 | 0.42 s / 0.74 s | 0.19 s / 0.31 s | 0.62 s / 0.94 s |
| `gpt-6.1-sol`, low effort, Ultrafast tier, `eot_threshold` 0.7 | 6 | 0.29 s / 0.77 s | 0.18 s / 0.25 s | 0.52 s / 0.96 s |

Sol's reply begins at Flux's eager end of turn, so most of it is written before
the turn is committed; the rest of a reply's time is Flux waiting for the turn to
end. A faster reply also reaches a participant's pause between sentences sooner.
At `eot_threshold` 0.6, Flux ended 18 percent of turns that the participant then
continued within two seconds (12 percent with Sonnet), and in 13 of 186 turns Sol's
audio had started, for up to 0.4 seconds, before they went on (1 of 129 with
Sonnet). At 0.7 Flux ended 5 percent of turns early, and the agent's audio started
in 3 of 92, for at most 0.05 seconds. Because Sol's reply is ready by the later
commit, replies took a median 1.22 seconds and a p90 of 1.89 seconds at 0.7 over
six interviews, against 1.27 and 2.12 seconds at 0.6 over ten, 1.60 and 2.56
seconds with Sonnet over eight, and 1.45 and 4.0 seconds for the GPT-Live
baseline. The agent uses 0.7.

Sol writes typographic apostrophes ("I’m"), so the echo guard and the tests'
checks compare words with apostrophes normalized. Sol speaks only from the
activity it is given, so the activity feed names the values that stayed whenever
several change at once. The instructions keep a check about saving under a name
the participant chooses, not one they offered as an example, and have an
interrupted reply finish in a whole sentence instead of picking up mid-sentence.

The browser call test passed both scenarios with Sol: the greeting was heard 4.6
seconds after the tab opened, a reload rejoined the same call and agent, and an
agent started 40 seconds late joined one connection without a loop. The other
live realtime tests run with Sol also passed.

In Codex, the same investigation task took 9.2 seconds on the Ultrafast tier and
19.1 seconds on the Fast tier, at $0.51 against $0.19.

### The voice agent's host

On 2026-10-09 the first production call with the agent in the Supabase Compute
worker carried no audio either way. Daily's call logs showed the browser's media
connections up over UDP, sending the participant's microphone at about 45 kbps
and receiving nothing, while the agent's send and receive connections stayed in
ICE `checking` for the whole call; signaling, app messages, and the agent's HTTPS
calls to Daily, Deepgram, and OpenAI worked. With Daily's TURN relays on TCP and
TLS over port 443 in its ICE configuration, the agent still never connected. The
same `daily-python` sender reached a listener from a Linux container with all
outbound UDP dropped, through Daily's TCP relay, and from a Daytona sandbox, so
Compute's network blocks more than UDP. The agent moved to Pipecat Cloud. Run from
its production image on a workstation and started through the image's `/bot`
route as Pipecat Cloud starts it, the agent joined the call 1.5 seconds after the
start request and its greeting reached the participant 3.5 seconds after it.

### Voice and model measurement harness

These harnesses were not committed; they ran from a workstation. They consisted
of:

- For Haiku: the five gate cases, a Case File drafting bundle, and intake
  contexts frozen from production inputs and run through production's own
  prompts and schemas, and a code benchmark that ran Codex and the Claude Agent
  SDK against the same Shopalytics checkout and acceptance suite.
- For option A's text stage: the nine scenarios with their required and allowed
  controls, scored per run.
- For option A end to end: participant lines synthesized with `gpt-4o-mini-tts`,
  streamed to Flux in 80 ms chunks in real time, with every turn's speech end,
  Flux events, first token, first audio, and mark timing recorded; and a check of
  each speech engine's character timing against Deepgram `nova-3` word
  timestamps.
- For full duplex: a Python WebRTC participant (`aiortc`) that speaks the same
  synthesized lines in real time, optionally mixes the agent's audio back into its
  microphone, records the agent's audio level every 20 ms and every gesture
  message, and mirrors the page's circle delay; the bot's own event log of
  microphone activity, Flux events, turn decisions, words as they played,
  gestures, and the final context.

## Operational observability

The control plane and the voice agent emit no OpenTelemetry spans or metrics. The control plane uses OpenTelemetry only to receive the Server SDK's product spans over OTLP; those spans are evidence under the [telemetry contract](./telemetry-and-evidence.md), not operational tracing. Durable events and evidence remain the authoritative audit path.

The web surfaces report to Vercel Observability. Always-on tracing samples every production request to `utrace-web`, `@vercel/otel` adds Next.js framework and outbound fetch spans, and Web Analytics and Speed Insights record page views and Core Web Vitals. A reported page URL carries route parameters in place of identifiers and no query string, because an invitation path holds a bearer token and query strings hold OAuth codes. The preview front door runs none of this instrumentation: it forwards the browser's `traceparent` untouched, and a second injector fails its propagation probe.

The control plane's operational record is its structured logs, which use fixed messages and fields for client, signal, session, attempt, candidate, command, job, and provider reference.

## Traceability

Every provider call records its business IDs and provider reference transactionally. From a Discord Intake Item, operators can traverse Product Signal, invitation, Interview Session, model/tool activity, attempt, workspace sandbox, Preview Version, validation evidence, Case File, and GitHub draft PR in either direction.
