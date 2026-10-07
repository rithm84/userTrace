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
implementation progresses without repetitive status narration. GPT-Live transcript
fragments are provisional: they cannot prove a final utterance or that speech was
heard.

## Provider integration checks

Voice integration checks exercise the GPT-Live session handshake, direct WebRTC
media, sideband attachment, client delegation, reconnection, and revocation.
Reject cross-session or stale setup results and verify that browser responses
contain no OpenAI credential.

Model checks run a GPT-6.1 Sol delegation tool run with the real strict function
tools, check GPT-6.1 Sol gate proposals, intake interpretations, and Case File
drafts against their schemas, and run the
Codex harness in a Vercel Sandbox with the credential added at egress. Record provider configuration revisions, and measure
browser-to-OpenAI voice latency and control-plane-to-provider model latency separately.

## Outreach Offers verification

Stripe sandbox tests cover reservation before invitation, entitlement at the final
participation milestone, completed no-implementation cases, system-ended cases,
voluntary cancellation, and one credit adjustment per offer, including retries
that reuse the same operation. Verify each adjustment's customer, account,
environment, amount, and currency, and reject cross-client, cross-customer, or
sandbox/live mismatches.

## Agent cursor measurements

These measurements, taken on 2026-10-06, are the evidence behind the
[agent cursor's design](./interview-and-voice.md#jevs-interview-responsibilities):
the target registry as the only grounding source, Jev as the judge, and judging
every transcript fragment. Jev is served under the unversioned ID
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
its 11 recordings. The script-first cascade times gestures exactly but speaks
with a second voice, so GPT-Live stays the voice.

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

## Operational observability

OpenTelemetry traces cover Vercel-to-control-plane requests, Supabase operations, GPT-Live session delegation, GPT-6.1 Sol and Jev calls, Discord, GitHub, and Stripe adapters, Vercel Sandbox workspaces and harness runs, and preview routing. Trace context joins operational spans to typed business IDs without placing sensitive content in span attributes. Durable events and evidence remain the authoritative audit path.

The web surfaces report to Vercel Observability. Always-on tracing samples every production request to `utrace-web`, `@vercel/otel` adds Next.js framework and outbound fetch spans, and Web Analytics and Speed Insights record page views and Core Web Vitals. A reported page URL carries route parameters in place of identifiers and no query string, because an invitation path holds a bearer token and query strings hold OAuth codes. The preview front door runs none of this instrumentation: it forwards the browser's `traceparent` untouched, and a second injector fails its propagation probe.

Structured logs use fixed messages and fields for client, signal, session, attempt, candidate, command, job, and provider reference. Metrics cover request rate, errors, duration, queue age, lease health, active sessions, voice reconnects, observation correlation, implementation/check duration, route health, validation return, and delivery. Exemplars connect metrics to traces.

## Traceability

Every provider call records its business IDs and provider reference transactionally. From a Discord Intake Item, operators can traverse Product Signal, invitation, Interview Session, model/tool activity, attempt, workspace sandbox, Preview Version, validation evidence, Case File, and GitHub draft PR in either direction.
