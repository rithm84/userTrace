# Benchmarks

[Back to uTrace architecture](../ARCHITECTURE.md)

These dated measurements justify uTrace's model, pipeline, and infrastructure
choices. Each ran from a workstation, not the deployed control plane. Providers
change models under the same names, so rerun a measurement before changing the
model, pipeline, threshold, or floor it supports.
[Evaluation and observability](./evaluation-and-observability.md) owns the checks.

## Agent cursor

Taken on 2026-10-06, these ground the
[agent cursor](./interview-and-voice.md#agent-cursor) in the target registry. Jev
is served under the unversioned ID `typesafe-ai/jev`; the Decisions API was in
public beta.

### Finding the named control

Each approach answered 68 queries twice across eight Shopalytics states,
including a restyled conversion page and pages without uTrace annotations: 116
answers name a control and 20 name nothing on screen. A hit is a point inside the
named control, or no point when nothing is named.

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

Eight queries, answered twice, named part of a component on the applied
conversion page: a chart point, axis label, product row, or review. Circling the
whole chart or table misses.

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

Declared parts raise the page's targets from 13 to 44. On its 52 whole-control
queries, answered twice, a Jev choice still found 102 of 104 and the Decisions API
100 of 104; one yes-or-no question per target scaled worse: 88 of 104, and 13 of
16 parts.

### The judge model

Scored on held-out labeled recap and debrief clauses; a harmful answer circles a
control nobody named.

| Judge | Recaps correct | Debriefs correct | Harmful answers | p50 latency |
| --- | --- | --- | --- | --- |
| Jev | 65/68 | 34/36 | 0 | 264 ms |
| Decisions API | 60/68 | 30/36 | 6 | 159 ms |

### Voice pipelines

Each measured pipeline fires circles differently:

![Option A's text model writes gesture marks inline and each mark fires as its word plays; GPT-Live's circles come from a Jev judge reading the transcript of its speech; other speech-to-speech models circle through pointing tool calls.](../diagrams/svg/benchmarks-voice-pipelines.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

In the GPT-Live design the inline marks replaced, Jev circled at best 93% of the
controls GPT-Live's recaps named on time. On 41 recorded recaps, the script-first
cascade put every circle on time; no speech-to-speech model did:

- `gpt-live-1` with a Jev clause judge: 60% of mentions on time.
- `gpt-realtime-2`: audio arrived seconds ahead of playback, so gestures were
  early.
- With pointing tools, `gpt-realtime-2` made every call before speaking (first
  audio p50 7.69 s); Gemini 3.8 Live spoke tool syntax aloud in 3 of 11
  recordings.

## Voice and reasoning models

Taken from 2026-10-07 to 2026-10-09, these back keeping GPT-6.1 Sol in its
[reasoning roles](./interview-and-voice.md#conversation-and-backend-work) and the
active voice design. Option A, the [script-first cascade](#voice-pipelines) with
application-owned turn-taking, was first measured with Sonnet 5.5 and is the
active [voice agent](./interview-and-voice.md#voice-pipeline), with GPT-6.1 Sol on
the Ultrafast tier writing its words. It replaced the GPT-Live pipeline, which
replied a median 1.45 seconds after the participant's last speech (p90 4.0
seconds, 103 replies, recorded on the server during the live realtime tests).

### Claude Haiku 5.5 in Sol's roles

Sol ran each role as `gpt-6.1-sol` on the Fast tier: low effort for gate proposals
and code work, medium for intake and Case File drafting. Haiku ran each role at
low, medium, and high effort, with adaptive thinking and with thinking disabled,
and code work through the Claude Agent SDK.

#### Structured roles

Each structured role answered frozen production inputs four times, through
production's prompts and schemas. The five Implementation Gate cases: an approval,
a terminated preview environment, missing evidence, an exhausted Implementation
Attempt budget, and another account's data.

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
terminated environment and the missing evidence, which Sol refused in 4 of 4 and
2 of 4 runs. With thinking disabled, Haiku also approved the exhausted budget every
time and the other account's data in half the runs. The gate controller's checks
caught most of these, but proposals should not rely on them. A structured Haiku
call cost about USD 0.001.

#### Code work

Each configuration ran three times on the Saved Conversion Views change, against
one Shopalytics checkout and acceptance suite. Investigation counts cited files
among the eight the production change touches, every cited range verified;
implementation counts runs whose candidate passed the acceptance suite.

| Harness and model | Investigation p50 | Needed files cited | Implementation passed | Time to candidate, p50 | Cost per implementation |
| --- | --- | --- | --- | --- | --- |
| Codex, Sol, low | 18 s | 7, 7, 8 | 3/3 | 72 s | USD 0.34 |
| Agent SDK, Haiku, low, adaptive | 21 s | 6, 7, 6 | 3/3 | 110 s | USD 0.024 |
| Agent SDK, Haiku, medium, adaptive | 21 s | 6, 7, 6 | 3/3 | 106 s | USD 0.027 |
| Agent SDK, Haiku, high, adaptive | 45 s | 6, 6, 6 | 3/3 | 148 s | USD 0.030 |
| Agent SDK, Haiku, low, thinking disabled | 14 s | 5, 6, 6 | 2/3 | 72 s | USD 0.016 |

The failed run stopped to report that the change needed a wider scope. With
thinking on, Haiku implemented as reliably at under a tenth of the cost, but took
half again as long or more and cited fewer needed files.

### Script-first voice with gesture marks

The text model gets the interview instructions and an `<application>` block naming
on-screen controls by short handles (`T1`, `T2`, ...), and marks a control before
(`<c id="T5"/>the channel filter`) or around the words naming it. Nine scenarios,
four runs each, cover the recap, a vague ask, a request to be shown a control, a
question about which control is the segment, the debrief, and four moments that
must not circle anything. A run is exact if it marks every required control,
nothing outside the allowed set, and no malformed mark. The agent writes marks
before words.

| Text model | Exact, mark before | Exact, mark around | First token p50 |
| --- | --- | --- | --- |
| `claude-sonnet-5-5`, `between_tools` thinking, low effort | 34/36 | 36/36 | 732 ms |
| `gpt-6.1-sol`, low, Fast tier | 36/36 | 32/36 | 872 ms |
| `gemini-3.8-flash`, low thinking | 36/36 | 35/36 | 1,454 ms |
| `gpt-6-luna`, no reasoning, Fast tier | 31/36 | 31/36 | 557 ms |
| `gemini-3.5-flash-lite`, minimal thinking | 30/36 | 31/36 | 791 ms |
| `claude-haiku-5-5`, low, thinking disabled | 30/36 | 27/36 | 585 ms |
| `claude-haiku-5-5`, low, adaptive | 28/36 | 27/36 | 463 ms |

#### End to end

A scripted participant's speech, synthesized with `gpt-4o-mini-tts`, streamed to
Deepgram Flux (`flux-general-en`) in real time in 80 ms chunks; Flux's eager end of
turn started the text model, whose text, marks removed, streamed to ElevenLabs.
Each configuration ran two 12-turn interviews. Reply latency runs from the
participant's last speech (RMS at least 0.01) to ElevenLabs' first audio, before
any network to a listener.

| Text model | Speech engine | Marks | Reply p50 | Reply p90 | Required circles | Circles not asked for | Words per walkthrough step |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sonnet 5.5 | Eleven v4 Turbo | Before | 1.63 s | 2.23 s | 18/18 | 0 | 1.0 |
| Sonnet 5.5 | Eleven Flash v2.5 | Before | 1.96 s | 2.59 s | 18/18 | 0 | 1.0 |
| Sol | Eleven v4 Turbo | Before | 1.85 s | 2.43 s | 18/18 | 0 | 1.0 |
| Haiku 5.5, thinking disabled | Eleven v4 Turbo | Before | 1.26 s | 1.75 s | 16/18 | 0 | 23.8 |
| `gpt-6-luna` | Eleven v4 Turbo | Around | 1.50 s | 2.02 s | 7/18 | 0 | 11.9 |
| `gemini-3.5-flash-lite` | Eleven v4 Turbo | Around | 1.78 s | 2.41 s | 10/18 | 8 | 15.1 |

A walkthrough step should get at most one word ("Mm-hmm"), and since the agent
[listens without a word](#listening-instead-of-backchanneling) usually none; Haiku
narrated the steps.

#### Speech engine timing

A circle lands on its word only as well as the engine's character timing matches
the audio. Against Deepgram `nova-3` word timestamps on the same audio:

| Speech engine | Character timing error |
| --- | --- |
| Eleven v4 Turbo | Median 53 ms, p90 193 ms |
| Eleven Flash v2.5 | Median 23 ms, p90 69 ms |
| Eleven v4 | A median 350 ms ahead of its audio, which rules it out |

Eleven v4 and v4 Turbo stream only over the Text to Dialogue WebSocket.

#### Flux thresholds

Flux's thresholds trade speed for cut-off turns. Over 22 turns each, from the
participant's last speech to Flux's end of turn:

| `eot_threshold` | `eager_eot_threshold` | Median | Transcripts cut short |
| --- | --- | --- | --- |
| 0.5 | 0.3 | 665 ms | 2 |
| 0.6 | 0.4 | 747 ms | None |
| 0.7 | 0.5 | 995 ms | Not reported |

Option A used 0.6 and 0.4 with Sonnet, and uses 0.7 and 0.4
[with Sol](#gpt-61-sol-ultrafast-as-the-voice-text-model).

### Full-duplex turn-taking

Option A was then built with full-duplex turn-taking on Pipecat 1.12.0 from its own
components: Flux turn events with eager end of turn, Sonnet 5.5, ElevenLabs' Text
to Dialogue service with Eleven v4 Turbo, and Pipecat's WebRTC transport
(production uses Daily's). A user turn interrupts the agent, the context keeps only
played words, and a pattern aggregator splits marks out of the text.

#### uTrace's additions

uTrace added four pieces; production keeps the first three, which the
[voice pipeline](./interview-and-voice.md#voice-pipeline) defines, echo included:

1. **Backchannel and echo guard:** while the agent talks, an utterance opens a
   turn only once its new words are neither a backchannel ("mm-hmm", "yeah",
   "right", "okay") nor echo, or once a backchannel outlasts the agent's reply.
2. **Word-at-a-time text:** the speech engine gets whole words, not sentences, and
   each reply's first clause is flushed, since Eleven v4 Turbo otherwise holds
   about 40 characters before speaking. Together these saved roughly 0.4 seconds
   to first audio.
3. **Gestures on the playout clock:** Pipecat releases a mark once the audio before
   it reaches the transport, a median 200 ms (up to 720 ms) before its word
   played. Each gesture instead rides its word's text frame, which the transport
   releases on its playout clock.
4. **Page delay:** the page held each circle back by the measured lag from the
   server's "bot started speaking" message to the reply being heard, landing the
   cursor as the word begins. Production dispatches circles through the
   extension's visual-command path, without this delay.

#### Participant script

A scripted participant, a Python WebRTC client (`aiortc`), joined like a browser
and spoke synthesized lines in real time: a long ask with two backchannels during
the reply, a short answer, a sentence with a 1.4-second pause in the middle, a
cut-in during the new version's walkthrough, and a cut-in 0.25 seconds into a
reply. GPT-Live, in its then-production configuration but with nothing answering
its delegations, heard the same lines; its replies stayed short, so its cut-ins and
backchannels were measured on 30-second monologues. Times are as the participant's
client received the audio, after both network legs; option A ran on the same
workstation, GPT-Live in OpenAI's cloud.

#### Clean audio

| Measure | Option A | GPT-Live |
| --- | --- | --- |
| First audio after the participant's last speech, p50 / p90 | 2.03 s / 2.44 s (32 replies) | 1.78 s / 2.43 s (32 replies), often only "One moment" |
| Silent after a cut-in, p50 / p90 | 0.74 s / 0.84 s (8 mid-walkthrough), 0.73 s / 0.82 s (8 at a reply's start) | 1.17 s / 1.48 s (19 mid-monologue) |
| Backchannels that stopped the agent | 0/11 | 7/20 |
| Agent heard during a 1.4-second pause | 0/8 | 0/8 |

The reply rows are not comparable: option A's first audio is its answer, but no
answer followed GPT-Live's holding phrase to be timed. With its delegations
answered, GPT-Live's p90 was 4.0 seconds.

Option A's time splits:

- **Reply:** end of turn a median 1.02 seconds after the participant's last speech
  reached the bot; first audio leaving the bot 0.86 seconds later (Sonnet's first
  token at a median 0.81, the speech engine's first audio at 0.15); then 0.16
  seconds of network and jitter buffer. A reply to a turn Flux ended during the
  pause was cancelled before any of it played.
- **Cut-in:** 0.10 seconds to reach the bot, 0.51 for Flux to report the turn, no
  measurable time to decide, and 0.13 until the participant heard silence.

#### Gesture timing

In the 8 clean option A runs, 28 gestures fired, each as its word started on the
server. Their messages reached the client a median 164 ms before the word's audio;
with the page's delay, circles landed a median 2 ms before the word was heard
(largest error 47 ms, 90th percentile 19 ms). No gesture fired after an
interruption, and in all 73 assistant turns the context held exactly the words
played, with the shown gestures' marks. Asked where the save button is, the agent
circled it in 8 of 8 runs; asked to show the segment filter, it circled it in 5 of
the 7 runs whose reply reached its name before the participant cut in, and named it
unmarked in the other 2.

#### Echo

Echo is the open problem. The participant's microphone carried the agent's audio
60 ms late at 20 or 30 dB below the agent's level, standing in for what browser
echo cancellation misses.

| Echo at the microphone | Option A: silent after a cut-in, p50 | Option A: backchannels that interrupted | Option A: echo utterances ignored | GPT-Live: silent after a cut-in, p50 | GPT-Live: backchannels that stopped it |
| --- | --- | --- | --- | --- | --- |
| None | 0.74 s | 0/11 | none occurred | 1.17 s | 7/20 |
| 30 dB below | 0.80 s | 4/7 | 22 | 1.63 s | 3/8 |
| 20 dB below | 1.86 s, and 1 of 4 never stopped it | 3/5 | 19 | 1.28 s | 1/8 |

Flux transcribes leaked echo, often misheard ("Ruth" for "Wren", "Shore" for
"short"), into the turn of a backchannel spoken over it, crediting the echo's words
to the participant. GPT-Live heard its own output: echo slowed its cut-ins at
30 dB below but did not make it stop on backchannels more often. Option A's
backchannel counts come from its turn decisions, which the client cannot see;
GPT-Live's, from its audio. Option A therefore depends on echo cancellation
removing the agent's voice before Flux hears it.

### Whole interviews

On 2026-10-08 the
[interview quality test](./evaluation-and-observability.md#live-voice-checks) ran on
a workstation's local stack over a live Daily call with the production pipeline and
tools. `claude-sonnet-5-5` played the originating user (the `maya` persona),
hearing the agent's played words and answering in synthesized speech; a `terse`
persona answered as briefly as it could; `claude-opus-5-5` judged each transcript.
Each row is one interview; each design keeps the changes above it. Reply time runs
from the end of the participant's speech to the agent's first played word.

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

In the three rounds with the acknowledgement first, 5 of 6 interviews passed; the
failure, before the last instruction changes, was two mild repetitions: a restated
check list and an echoed thanks. Recorded silently, an agreement was acknowledged
5.6 seconds after the participant agreed; said first, 1.0 second after, with the
recording running during playback.

### GPT-6.1 Sol Ultrafast as the voice text model

GPT-6.1 Sol on the Ultrafast tier replaced Claude Sonnet 5.5 in the same pipeline,
through the Responses API over one WebSocket per call. Sol accepts no reasoning
effort below `low`; Pipecat requests `none` unless the effort is set.

#### Reply latency

From Flux's committed end of turn to the agent's first audio:

| Text model | Interviews | End of turn to first text, p50 / p90 | First text to first audio, p50 / p90 | End of turn to first audio, p50 / p90 |
| --- | --- | --- | --- | --- |
| `claude-sonnet-5-5`, adaptive thinking, low effort, `eot_threshold` 0.6 | 8 | 0.71 s / 1.35 s | 0.17 s / 0.48 s | 1.04 s / 1.57 s |
| `gpt-6.1-sol`, low effort, Ultrafast tier, `eot_threshold` 0.6 | 10 | 0.42 s / 0.74 s | 0.19 s / 0.31 s | 0.62 s / 0.94 s |
| `gpt-6.1-sol`, low effort, Ultrafast tier, `eot_threshold` 0.7 | 6 | 0.29 s / 0.77 s | 0.18 s / 0.25 s | 0.52 s / 0.96 s |

Sol starts replying at Flux's eager end of turn, so most of a reply is written
before the turn commits; the rest is Flux waiting for the turn to end.

#### End-of-turn threshold

A faster reply also reaches a participant's pause between sentences sooner:

| Text model, `eot_threshold` | Turns Flux ended that the participant continued within two seconds | Turns where the agent's audio had started before they went on | Interviews | Reply p50 / p90 |
| --- | --- | --- | --- | --- |
| Sonnet, 0.6 | 12 percent | 1 of 129 | 8 | 1.60 s / 2.56 s |
| Sol, 0.6 | 18 percent | 13 of 186, for up to 0.4 s | 10 | 1.27 s / 2.12 s |
| Sol, 0.7 | 5 percent | 3 of 92, for at most 0.05 s | 6 | 1.22 s / 1.89 s |

Since Sol's reply is ready by the later commit, replies at 0.7 were no slower. The
agent uses 0.7.

#### Adjustments for Sol

- Sol writes typographic apostrophes ("I’m"), so the echo guard and the tests'
  checks normalize apostrophes.
- Sol speaks only from the activity it is given, so the activity feed names the
  values that stayed whenever several change at once.
- The instructions keep a check about saving under a name the participant
  chooses, not an example they offered, and have an interrupted reply finish in a
  whole sentence instead of resuming mid-sentence.

#### Browser call and Codex

With Sol, the browser call test passed both scenarios: the greeting was heard 4.6
seconds after the tab opened, a reload rejoined the same call and agent, and an
agent started 40 seconds late joined one connection without a loop. The other live
realtime tests also passed. In Codex, the same investigation task took 9.2 seconds
on the Ultrafast tier and 19.1 seconds on the Fast tier, at USD 0.51 against
USD 0.19.

### The voice agent's host

On 2026-10-09 the first production call with the agent in the Supabase Compute
worker carried no audio either way:

- Daily's call logs showed the browser's media connections up over UDP, sending
  the participant's microphone at about 45 kbps and receiving nothing.
- The agent's send and receive connections stayed in ICE `checking` all call,
  even with Daily's TURN relays on TCP and TLS over port 443 in its ICE
  configuration.
- Signaling, app messages, and the agent's HTTPS calls to Daily, Deepgram, and
  OpenAI worked.
- The same `daily-python` sender reached a listener from a Linux container with
  all outbound UDP dropped (through Daily's TCP relay) and from a Daytona sandbox,
  so Compute's network blocks more than UDP.

The agent moved to Pipecat Cloud. Run from its production image on a workstation
and started through the image's `/bot` route as Pipecat Cloud starts it, it joined
the call 1.5 seconds after the start request; its greeting reached the participant
3.5 seconds after it.

## Live checks

On 2026-10-31 the committed live voice checks ran 23 times on a workstation's
local stack with the production providers: GPT-6.1 Sol on the Ultrafast tier, Flux
at `eot_threshold` 0.7 and `eager_eot_threshold` 0.4, Eleven v4 Turbo, and a Daily
call per run. The interview quality test did not run: its participant and judge
are Claude models, and the workstation had no `ANTHROPIC_API_KEY`.

| Check | Runs | Passed |
| --- | --- | --- |
| Realtime voice | 5 | 5 |
| Acknowledgement | 3 | 3 |
| Walkthrough | 3 | 3 |
| Vague ask | 3 | 3 |
| Browser call, both scenarios | 3 | 3 |
| Journey, before [the agreement fix](#recording-the-agreement) | 6 | 2 |
| Journey, after it | 5 | 5 |

### Reply timing

From Flux's committed end of turn, on the agent's clock, to the first text sent to
the speech engine and the transport's first audio:

| Check | Replies | End of turn to first text, p50 / p90 | End of turn to first audio, p50 / p90 |
| --- | --- | --- | --- |
| Realtime voice | 25 | 0.65 s / 0.86 s | 0.79 s / 1.02 s |
| Acknowledgement | 15 | 0.38 s / 0.74 s | 0.52 s / 0.90 s |
| Walkthrough | 18 | 0.16 s / 0.51 s | 0.32 s / 0.66 s |
| Vague ask | 9 | 0.39 s / 1.02 s | 0.53 s / 1.17 s |
| Journey | 60 | 0.42 s / 0.68 s | 0.59 s / 0.84 s |
| All | 127 | 0.42 s / 0.77 s | 0.59 s / 0.92 s |

First text to first audio took a median 0.16 seconds (p90 0.19); no reply exceeded
1.40 seconds from the end of turn. Walkthrough replies, each "Mm-hmm.", are
fastest. Flux withdrew 40 of its 173 eager ends of turn because the participant
kept talking. The same span took 0.52 / 0.96 seconds (p50 / p90) in the
[2026-10-07 interviews](#gpt-61-sol-ultrafast-as-the-voice-text-model).

From the end of the participant's scripted line to the agent's first played word,
23 replies took a median 1.10 seconds (p90 2.03): 1.30 for "what did I apply" (5
replies), 0.73 for a request to circle the channel filter, 0.81 for "how is the
change going", 1.68 for the opening line, and 2.03 for the recap after the ask.
The greeting began a median 1.28 seconds after the call connected.

### Gestures and cues

All 72 circles in 16 runs fired on the first word of the phrase naming their
control: "Last" for the date range, "TikTok" for the channel, "the" in "the
channel filter", and "Save" for the save button. Each dispatch committed a median
10 ms after that word started playing on the agent (p90 13 ms, largest 14 ms),
excluding the extension's delivery after dispatch.

- Asked to circle the channel filter, the agent did in 5 of 5 runs, a median 1.04
  seconds after the ask ended; asked which control is the segment, it circled the
  segment filter in 5 of 5.
- Asked what was applied, it named every applied filter in 5 of 5.
- In the walkthrough it said only "Mm-hmm." to each step in all 6 walkthrough and
  acknowledgement runs, and circled nothing in the 3 walkthrough runs.

Each [application cue](./interview-and-voice.md#application-cues) reached the
agent within a fraction of a second of its trigger:

| Cue | Runs | Appended after its trigger | Agent's first words after the trigger |
| --- | --- | --- | --- |
| The new version is ready | 8 | 0.00–0.01 s | Not measured |
| It is being built, after the gate's approval | 2 | 0.03–0.04 s | None: it stayed silent while building in 2 of 2 |
| The debrief, after the new version loaded | 3 | 0.02 s | 0.80–0.94 s |
| Acknowledge the loaded dashboard | 3 | 0.28–0.62 s | 1.05–1.54 s ("There it is.") |
| The applied filters, after they committed | 5 | 0.30–0.56 s | Not measured |

In 3 of 3 acknowledgement runs the agent acknowledged nothing during the
walkthrough or the new version's announcement. In the two passing journeys no Job
failed, no cursor target went stale, and no connection sat idle in a transaction.

### Browser call

In each of three runs the session tab heard the greeting 2.92–3.55 seconds after
opening (median 3.54), rejoined the same call and agent 0.98–1.02 seconds after a
reload, and heard the agent's reply a median 1.63 seconds after the participant's
line ended (6 replies, largest 1.96). With the agent started late, the tab heard
the greeting 1.05–1.46 seconds after its call connected, over one connection and
one agent run.

### Recording the agreement

Before the fix, in 4 of 6 journey runs the agent said its agreement line ("That's
agreed; I'll get that started and tell you when it's ready to try") without calling
`record_acceptance_criteria`, so nothing was recorded. The logged Responses API
events show each failing answer ending with a reasoning item, a message, and no
function call; the pipeline delivered every event Sol sent. Each such run reached
agreement on a contradictory answer:

- After the participant's ask, the agent recapped and asked what the view is for
  instead of proposing the change, in all 6 journey, 3 walkthrough, and 3
  vague-ask runs; the instructions' discovery step allows this, since the
  participant has not said what the weekly analysis is for.
- The scripted participant, expecting a proposal there, later answered "Would you
  like us to go ahead?" with "No, that's everything. That sounds right to me.",
  and the agent declared agreement on it.

A replay outside the pipeline, with the production instructions, tools, model,
tier, and effort but without the live context's application notes and earlier
reasoning, called the tool with the agreement line in 10 of 10 replies; it did not
reproduce the missing call.

#### Fix and results

The application now makes the promise itself once the agreement is recorded
([tools and customer language](./interview-and-voice.md#tools-and-customer-language)),
and the journey's participant says what the view is for before agreeing. With
both, the journey passed 5 of 5 runs: in 3 the model said "Agreed." before its call
and the application followed with "I'll get that started and tell you when it's
ready to try."; in 2 it called without a word and the application said the whole
line 55–69 ms after the call arrived. The agreement's first audio came a median
1.14 seconds (0.61–1.37) after Flux's end of turn. The realtime voice (2 runs),
acknowledgement (2), walkthrough (1), and vague-ask (1) checks also passed on the
same code.

### Listening instead of backchanneling

On 2026-11-02 the live checks ran on a workstation's local stack with the
production providers, before and after the agent could
[listen without a word](./interview-and-voice.md#listening-and-backchannels).
Before, its instructions made "Mm-hmm." its answer to every walkthrough step.
A new listening check replays a recorded call: trying the new version, the
participant says "Sure." and "Let me try.", then a few words for each of six more
steps as they set their filters, save the view, refresh, and reopen it, and
finally how it went.

What the agent said while the participant was still taking the steps:

| Check | Before | After |
| --- | --- | --- |
| New version tried, 8 spoken steps | "Mm-hmm." after 8 of 8 and 7 of 8 steps, 2.9–6.7 s apart | Nothing in 3 of 3 runs: every step got the listening mark |
| Walkthrough, 4 steps | "Mm-hmm." after 3 and 4 of 4 steps: 0.75 and 1.00 words per step | Nothing in 3 of 3: 0 words per step |
| Acknowledgement check's walkthrough, 3 steps | "Mm-hmm." after every step: 1.00 words per step | Nothing in 3 of 3 |

Narration did not return: no step got more than a listening mark, and no run
circled a control during a walkthrough. The agent still answered as soon as it was
owed:

- After the participant said how the try went, it answered 1.83–2.42 seconds
  after they stopped, against 2.46 and 4.26 seconds before.
- The recap after the participant's ask began 1.89–2.10 seconds after it, against
  2.37 and 2.53 seconds before.
- The only backchannel it spoke was "Go ahead.", in 10 of the 11 runs where the
  participant offered to show their workflow or said they would open their
  dashboard; in the other it listened.
- In the realtime voice check it answered what was applied, circled the channel
  filter, and gave the build's progress 0.64–1.13 seconds after each question. In
  both journey runs it said nothing while the change was built.
- The listening stage held an answer's start a median 59 ms before passing it on
  (largest 131 ms, 10 holds).

With the example phrases removed from the
[acknowledgement cue](./interview-and-voice.md#jevs-interview-responsibilities),
the loaded dashboard was acknowledged with "I'm following.", "I'm with you.", and
"I'm with you." 1.07–1.48 seconds after it loaded. Before, it was "There it is."
in both runs, 1.23 and 1.28 seconds after.

| Check | Before | After |
| --- | --- | --- |
| Listening | 0 of 2 | 3 of 3 |
| Walkthrough | 2 of 2 | 3 of 3 |
| Acknowledgement | 2 of 2 | 3 of 3 |
| Realtime voice | 1 of 1 | 2 of 2 |
| Journey | Not run | 2 of 2 |
| Vague ask | Not run | 1 of 1 |

#### Without the checks-shown note

Those runs still had Jev's note on which checks a new version showed, ending "Let
them carry on, with no questions". That judgment has since moved to the agent
([Jev in the live interview](#jev-in-the-live-interview)), and the note is gone.
Without it, in 1 of 2 listening runs the agent began asking "Did it keep the view under your…"
right after "And reopening it.", before the participant said how it went. A
replay outside the pipeline, with the production instructions, tools, model,
tier, and effort, did so in 2 of 4. The instructions now say that naming even the
last step means the participant is looking at the result. With that, the replay
listened in 6 of 6, still asked how it went when the participant said only "Okay,
done." (4 of 4), and the live listening check passed 3 of 3, with nothing said
during the try and the outcome answered 1.83–2.55 seconds after it. The realtime
voice check passed 1 of 1; in it the listening stage withheld a "Mm-hmm." the agent
gave a Jev acknowledgement 4 seconds after its own "Go ahead.", the one
backchannel it withheld in these runs.

#### Cancelled answers

A barge-in cancels the answer being generated, but in 35 cancellations over the
earlier runs the API finished every one, and the next answer waited for the rest of it:
0.5–4.6 seconds in 10 of the 35. Past Pipecat's five-second cap, the cancelled
answer was delivered as the next one. Twice the listening mark meant for a
walkthrough step answered the participant's ask, and the acknowledgement check
failed. A cancelled answer now gets half a second before its connection is
replaced ([GPT-6.1 Sol](./interview-and-voice.md#voice-pipeline)). The 54
reconnects took a median 0.23 seconds, and the 14 runs above replaced two
connections.

#### Runs left out

Two runs crossed a laptop sleep. The 26 runs between 20:11 and 21:01 ran while
GPT-6.1 Sol on the Ultrafast tier stalled 2–28 seconds between accepting a request
and its first text, against 0.8–1.6 seconds for the same greeting before then and
after. A probe outside the pipeline at 21:02, with the production instructions,
measured 5.3–9.2 seconds to first text on Ultrafast and 1.0–1.6 seconds on the
default tier. Answers that late arrived after the participant's next line, so those
runs measured the provider, not the change.

### Preview cold start

Eight cold starts of the Shopalytics base preview (template revision 13, 2 vCPU,
Vercel Sandbox), from creating the sandbox to the app answering its health check
with the dataset verified, took a median 4.8 seconds (3.86–8.03). Creating the
sandbox alone took 1.1–3.1 seconds, most of the spread. This excludes the readiness
probes and the session's claim, which warm and cold starts share, so a
[warm runtime](./preview-runtime-and-seeding.md#allocation-lifecycle) saves only
these seconds, which overlap participant setup and the greeting.

## Jev in the live interview

Taken on 2026-11-02, these test whether Jev's interview judgments earn their place
beside the voice agent's own model, which already hears the participant's preview
activity as application notes. During an interview Jev judges whether an action
merits a few spoken words (`judge_acknowledgement`), which agreed checks a new
version already showed (`judge_checks_shown`), and, before the Implementation
Gate, which flow the interview established (`propose_flow_classification`). Jev
ran as `typesafe-ai/jev` with the production questions and thresholds; GPT-6.1 Sol
ran as the voice agent runs it, `gpt-6.1-sol` on the Ultrafast tier at low effort.
Each judge answered every moment three times from a workstation, one request at a
time per provider; latency is the request alone. Jev lists at USD 0.042 per million
input tokens on Vercel AI Gateway, with no output price.

Following these measurements, production stopped asking Jev which checks were
shown: the voice agent judges that from the activity it hears, with the wording
the `sol` arm below used. Jev's unwired evidence coverage, claim relationship,
and attention routing judgments were removed with it.

### Replay set

The labeled set mixes moments from the one recorded production interview
(2026-11-01, Product Signal `01a19627-8d6d-70cc-b1ca-165f98e70e89`) with scripted
Saved Conversion Views moments. A label a careful reader could set either way is
marked ambiguous and reported both ways.

| Question | Moments | From the recorded run | Labels |
| --- | --- | --- | --- |
| Acknowledgement | 35 | 14 | 11 acknowledge, 24 stay quiet; 4 ambiguous |
| Checks shown | 46 batches, 138 checks | 16 batches | 41 shown, 10 contradicted, 87 not shown; 7 ambiguous |
| Flow classification | 5 interviews | 1 | 2 feature requests, 2 product failures, 1 unclear |

Recorded moments are rebuilt from the session's transcript, its activity, and the
context bundle each judgment read. Replayed, Jev reached its live decision in all
36 acknowledgement trials and in 171 of 174 check decisions. Twelve of the recorded
run's 14 acknowledgement moments call for silence, so the scripted moments add
awaited ones: a page the participant said they would open, a step the agent asked
for, a result they were waiting on, and the steps, asides, and idle clicks around
them. The scripted checks replay the 2026-10-03 validation pass at each new
milestone, and copies in which the reopen restores some filters, none, or frozen
dates, or skips the refresh, against three phrasings of the agreed checks.

### Acknowledgements

Precision and recall of acknowledging, over 35 moments answered three times; in
parentheses, without the four ambiguous moments.

| Judge | Precision | Recall | p50 / p90 latency | USD per call |
| --- | --- | --- | --- | --- |
| Jev | 80% (88%) | 97% (100%) | 0.22 s / 0.32 s | 0.00004 |
| Sol, asked the same question | 83% (100%) | 91% (100%) | 0.96 s / 1.24 s | 0.008 |
| Sol as the agent, deciding in its own reply | 81% (100%) | 76% (83%) | 0.93 s / 1.21 s | 0.004 |
| Rule: acknowledge a save, a reopen, or a page load | 69% (80%) | 82% (80%) | — | — |

- Jev's clear false acknowledgements were refreshes: the recorded run's unannounced
  refresh between the save and the reopen (3 of 3 repeats), and a refresh the agent
  had asked for as the first half of "refresh and reopen it" (1 of 3).
- As the agent, Sol stayed silent, or said "Mm-hmm.", when the participant showed
  what they said they would show: a refresh losing their filters, and the trend
  chart going blank.
- The production feed drops a decision that arrives more than 1.2 seconds after the
  activity was read. Sol's standalone p90 is past that.
- In the recorded run the 15-second spacing between acknowledgements, not Jev, kept
  the reopen from being judged: the save had been acknowledged 10 seconds earlier.

### Checks shown

Each of 46 batches answered three times. A check reported shown that the activity
did not show tells the agent never to ask about it; in parentheses, without the
ambiguous labels. The rules match each of the demo's checks to its milestones by
the check's words, so they fit this scenario only.

| Judge | Shown: precision | Shown: recall | Contradicted: recall | Reported shown, wrongly | p50 / p90 latency | USD per call |
| --- | --- | --- | --- | --- | --- | --- |
| Jev | 99% | 75% (85%) | 17% | 1 (0) | 0.22 s / 0.35 s | 0.0001 |
| Sol, asked the same questions | 94% | 99% (99%) | 83% | 8 (5) | 1.02 s / 1.40 s | 0.009 |
| Rules for this scenario | 98% | 95% (94%) | 100% | 1 (0) | — | — |

- Jev's floor of 0.75 makes it miss rather than err: it reported 5 of 30
  contradictions and never reported the recorded run's relative date range. Live,
  it reported the refresh and reopen 2.95 seconds after the reopen, once the
  participant had already begun saying it worked.
- Sol called a reopen that restored frozen dates a restore of every filter in 5 of
  its 9 answers across the three phrasings.
- Production awaits the judgment before sending the activity it judged, so during
  validation each activity note waited for it: 0.27 seconds at the median in the
  recorded run, from the bundle to the recorded answer.

### The recorded run's last validation turn

The recorded participant reopened the saved view and said "It looks like it works
perfect, and the positioning is good too." That turn was replayed 30 times with the
agent's context rebuilt from the session, with Jev's two checks lines (the save
shown, then the refresh and reopen), and 30 times without them, the instructions
and debrief cue asking the agent to judge from the activity instead.

| Agent's context | Asked about the relative range | Also asked again about the save or the restore |
| --- | --- | --- |
| With Jev's checks lines | 30 of 30 | 18 of 30 |
| Without them | 30 of 30 | 3 of 30 |

The debrief cue tells the agent to have the participant confirm the checks the
application reports shown, and with the lines it did, walking back through checks
the activity had already shown. Live, the agent asked only about the range.

### Flow classification

| Judge | Correct | p50 / p90 latency | USD per call |
| --- | --- | --- | --- |
| Jev | 15 of 15 | 0.27 s / 0.52 s | 0.0003 |
| Sol, asked the same question | 15 of 15 | 0.81 s / 1.08 s | 0.02 |
| No classification: keep intake's flow | 2 of 5 | — | — |

The gate runs the classification before code investigation and its proposal. In
the recorded run it took 0.40 seconds from the bundle's assembly to the recorded
answer, which came 1.66 seconds after the agreement.

### Whole interviews with and without Jev

Nine scripted interviews ran over a real call on a workstation's local stack, three
per arm, interleaved. Each arm changed only the running agent:

- `jev`: production. Jev decides which moments are acknowledged and which agreed
  checks were shown.
- `sol`: no Jev call. Every moment the floor allows is appended for the agent to
  acknowledge or pass over, without the 15-second spacing, and the instructions and
  debrief cue ask the agent to judge shown checks from the activity.
- `quiet`: no Jev call and no acknowledgements; checks as in `sol`.

The participant said they would open the dashboard and it loaded; set three filters
while naming each; agreed the change; and, once the new version was debriefed, said
only "Okay, let me try it", then rebuilt the filters, saved, refreshed, and reopened
without a word, and said "Looks like it all came back".

| Arm | Validated | Spoke after the dashboard, the save, the refresh, the reopen | First word after a moment, median | The closing question asked about | Sol runs, USD per interview (medians) | Jev calls per interview |
| --- | --- | --- | --- | --- | --- | --- |
| `jev` | 3 of 3 | 3, 3, 0, 0 of 3 | 1.48 s | The rolling range, 3 of 3 | 18, 0.44 | 11 |
| `sol` | 3 of 3 | 3, 1, 2, 2 of 3 | 0.89 s | The rolling range, 2 of 3; it and the save, 1 of 3 | 22, 0.72 | 0 |
| `quiet` | 3 of 3 | 0, 0, 0, 0 of 3 | — | The rolling range, 3 of 3 | 16, 0.37 | 0 |

- With Jev, the agent said "There it is." or "Nice." at the dashboard (once only
  "Mm-hmm.") and at the save, never at the unannounced refresh, and never at the
  reopen, which came within 15 seconds of the save's acknowledgement.
- Deciding itself, Sol spoke sooner but less consistently: "Nice." after the
  unannounced refresh in 2 of 3 runs, the save acknowledged in 1.
- No arm asked the participant to repeat a check, and no activity cue made the
  agent talk over them.
- From "it all came back" to the application's validation-complete cue took
  19.7–23.2 seconds with Jev, 18.6–19.9 with `sol`, and 17.3–20.2 with `quiet`;
  most of it is the agent's question, the scripted answer, and the recording.
- Jev's 34 calls in the `jev` runs took 0.27 seconds at the median (p90 0.37).

## Measurement harnesses

Only the Jev evaluation is committed, in
[`apps/api/tests/evals/jev`](../../apps/api/tests/evals/jev/): a read-only session
export, the code that rebuilds the labeled replay set from it (the set holds a
production session's transcript, so it is not committed), the replay and its
scorer, the counterfactual turn, and the live ablation. Beyond the methods above,
the others comprised:

- **Agent cursor:** Playwright captures of the Shopalytics fixture served locally
  at 1440×900, a screenshot and the SDK-reported target registry per state (base,
  filters applied, a candidate with saved-view controls, a restyle, and four other
  pages); recorders timestamping every audio frame and transcript fragment of each
  voice pipeline; a scorer that transcribes recordings with Deepgram `nova-3` word
  timestamps, simulates playback to find when each word is heard, and replays every
  judge on the recorded transcript in real time; and labeled recap, debrief,
  acknowledgement, and intake clauses in calibration and held-out sets, scored with
  the production acceptance rules.
- **Option A end to end:** a record of each turn's speech end, Flux events, first
  token, first audio, and mark timing.
- **Full duplex:** the participant optionally mixed the agent's audio into its
  microphone, recorded the agent's audio level every 20 ms and every gesture
  message, and mirrored the page's circle delay; the bot logged microphone
  activity, Flux events, turn decisions, words as played, gestures, and the final
  context.
- **Live checks:** a runner for the committed live checks, one at a time with `-s`
  and one log per run; a read-only poller copying `visual_command_dispatches`,
  `voice_transcript_fragments`, and `voice_connections` from the local database
  every 0.5 seconds, before each test emptied them; a log parser timing each
  committed end of turn to the next text sent to the speech engine and the next
  "Bot started speaking", and counting eager ends of turn and their withdrawals; a
  pytest plugin logging every Responses API WebSocket event, function-call run, and
  tool run in the journey, except text and argument deltas; and a script running
  the warm runtime's sandbox provisioning steps without database writes, deleting
  each sandbox.
