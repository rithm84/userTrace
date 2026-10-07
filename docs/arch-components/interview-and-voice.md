# Interview and voice runtime

[Back to uTrace architecture](../ARCHITECTURE.md)

The runtime separates conversational timing from authority over backend effects.

![Voice conversation, backend reasoning, structured observation, and effect authorization run concurrently.](../diagrams/svg/interview-and-voice.svg)

## Interview ownership

The originating user remains the sole interview and validation participant.
Implementation runs live, during the conversation. Validation requires the session
tab, separate preview tab, extension, and browser/server observation path.

Before interviewing begins, invitation claim runs the [participant compatibility preflight](./browser-extension.md#installation-identity-permissions-and-packaging). The user may receive setup guidance, but the Interview Session does not enter interview activity and reward-eligible participation does not begin until the supported browser, extension, required permission, connectivity, and protocol checks pass. Preview preparation may continue after admission; runtime observation readiness still gates product-dependent probing and is checked continuously.

## Conversation and backend work

GPT-Live (`gpt-live-1`) runs through the OpenAI API and owns full-duplex audio,
conversational timing, and native interruption. The session tab carries WebRTC
audio after backend-authenticated setup. The extension does not capture or relay
audio. Supabase Realtime carries application updates, not media.

Use GPT-Live client delegation. Here, client means uTrace's application backend,
not the browser. GPT-Live announces each delegation with
`session.delegation.created`, which carries metadata only, never the user's
utterance or task. The backend keeps the transcript and application state needed
to interpret the request, assembles context, runs GPT-6.1 Sol and tools, checks
results against current authority, and returns concise findings over the
sideband: `session.commentary.append` for results GPT-Live should say aloud,
`session.thinking.append` for facts it can use in later replies, and
`session.instructions.append` to redirect its behavior. Each append carries the
delegation ID and at most 500 tokens. A provider delegation ID is an opaque
correlation ID, never permission to act. See
[OpenAI's delegation guide](https://developers.openai.com/api/docs/guides/live-delegation).

GPT-6.1 Sol on the Fast tier handles interactive evidence interpretation, targeted
retrieval, and question guidance at a low pinned effort, because the participant
waits on every delegation. Its tools are Responses API function tools in strict
mode, and a run stores nothing at OpenAI: each turn resends the earlier output with
its encrypted reasoning. Gate proposals and code work run the same model, the code
work in the [Codex harness](./implementation.md#agent-harness), and Case File
synthesis runs it at a higher pinned effort. Jev supplies concurrent
judgments under the
[Jev decision contract](./cross-component-contracts.md#jev-decision-contract). These judgments can initiate scoped read-only work or direct attention;
they cannot confirm evidence or authorize an effect. Deterministic connection,
identity, lease, and policy facts never require a classifier.

The model routing configuration is versioned. Each Job records its task, selected
model and effort, prompt/schema version, input bundle, deadline, and result.
Failed work retries within its configured bounds and fails explicitly. No model
is ever substituted for another. An unavailable analysis path blocks dependent work, not unrelated
conversation. Jev is an active component, not a shadow-only feature or an
additional serial approval step for every utterance.

### Jev's interview responsibilities

- **Evidence coverage:** for each supplied objective or acceptance criterion,
  identify whether relevant statements or observations address it, leave it
  unanswered, or contradict it. Code checks missing records; Jev assesses meaning.
  GPT-Live or the delegation model uses the gaps to formulate follow-up questions.
  During validation, each time new product milestones arrive from the candidate,
  Jev judges per agreed check whether the participant's activity since the
  Validation Attempt opened shows the product doing it. A check judged shown or
  contradicted reaches GPT-Live in the same quiet append as the activity that
  showed it, so it asks the participant to confirm what they saw instead of
  repeating it, and answers once; the judgement records no outcome. The activity
  itself is described by what changed: a milestone heard before is reported by the
  values that changed, not repeated whole.
- **Claim relationships:** compare a new contextualized statement with supplied
  relevant claims: supports, contradicts, unrelated, or unclear. A flag does not
  rewrite the canonical account; complex interpretation goes to a delegation.
- **Attention and routing:** identify relevant observations and choose among
  supplied lookup or reasoning tasks. The controller checks scope and budgets.
- **Visual targets:** select from current, eligible SDK target IDs and semantic
  descriptions. Code verifies binding and freshness; Jev invents neither selectors
  nor coordinates, and no word is matched against a control. The agent cursor
  follows the agent's words at two moments only, and circles each control named
  there: the recap, when the agent plays the participant's request back to them
  once they have finished explaining, and the debrief, when it walks them through
  what a new version added. Each moment is spent by the first agent turn that
  circles something in it, so a later restatement of the same request is not
  circled again, and outside them the cursor stays still. GPT-Live's transcript
  of its own speech arrives a fraction of a second before the participant hears
  it, and every transcript fragment in a moment is judged as it arrives, over the
  agent's last six words: Jev judges whether they are about a specific control on
  screen and which; in the recap, which control they name last, whether the
  agent's turn is playing back the request or what the participant just showed,
  and whether the participant had finished or is still partway through showing
  something, and in the debrief,
  only the controls the new version added or moved are offered. Judging each
  fragment rather than each finished clause is what lands the circle while the
  words are heard, and asking for the control named last keeps a window that
  still holds the previous control on the newest one. A confident answer is
  circled at once; fragments are judged concurrently and shown in the order
  spoken, each control is circled once per moment, and a control shown in the
  last few seconds is not shown again. The measurements behind this design are in
  [agent cursor measurements](./evaluation-and-observability.md#agent-cursor-measurements). GPT-Live is instructed to listen
  through a walkthrough, at most with a brief backchannel, and to restate the
  request in one pass once the participant has finished. A gesture brings the preview tab and its window to the front, because a
  gesture the participant cannot see helps no one and a hidden window pauses the
  cursor's animation. When the participant asks to be shown something, GPT-Live
  delegates.
  Because the delegation carries no task, Jev judges
  from the latest words whether the agent needs a control shown, which target,
  and whether to circle or point, while GPT-6.1 Sol starts on the same delegation
  in parallel. A confident show-and-target answer dispatches the gesture and
  answers the delegation, and Sol is stopped; otherwise Sol's finding answers it.
  The delegation usually arrives before the agent's own words ("Let me show you
  the segment filter") are transcribed, so an unsure judgement is repeated as
  those words arrive, for up to 1.5 seconds. Targets are described by role, the
  client's declared definition, the label they currently show, and where they sit
  on screen among targets described alike; a target with neither definition nor
  label is not offered. When a page registers more targets than are offered,
  visible ones come first, declared controls before the visual-only parts of
  components, then top to bottom, so a filter is never crowded out by chart
  points or reviews.
- **Acknowledgements:** each time the participant does something in the preview,
  Jev judges from the conversation and that activity whether it is a moment the
  conversation was waiting on, such as the screen they said they would open coming
  up or a step the agent asked for, and so worth a few spoken words. Code only
  decides when one may be spoken: nobody has the floor (the agent is not talking
  or awaiting a delegation's answer, and the participant has paused), the
  application is not announcing a new version, whose debrief acknowledges its
  loading, the last acknowledgement was at least 15 seconds ago, and the decision
  arrives within about a second of the activity, or the moment passes quietly.
  Activity from before the connection, or held while someone was talking, is not
  acknowledged.
- **Progress questions:** the same batch asks whether the agent needs only the
  status of the change being built, such as how it is going or whether the
  participant must reload. When it confidently does and a change is in flight
  (`reproduction_captured` through `ready_for_validation`), the delegation is
  answered from the session's recorded `implementation_progress` and Sol is
  stopped; before agreement, or when unsure, Sol answers. Agreements and
  validation answers being recorded are a separate option, so they always reach
  Sol and its tools.

These jobs run on relevant updates, not as mandatory checks before every utterance.
[Context Assembly](./context-assembly.md#stage-boundaries) owns retrieved-passage
selection; [flow classification](#flow-classification) owns the classification judgment.

## WebRTC session setup

The diagram separates authenticated session setup from the media and control
connections:

![The browser exchanges SDP through FastAPI, then connects media directly to GPT-Live while the backend attaches sideband control.](../diagrams/svg/voice-session-bootstrap.svg)

FastAPI validates the originating user, current Interview Session, and connection
revision before accepting an SDP offer. It chooses the pinned GPT-Live model,
voice instructions, and client delegation server-side. Browser input cannot select
a provider endpoint, model, or privileged session configuration.

The backend creates the session through the OpenAI GPT-Live API with the browser's
SDP offer, binds the returned provider session ID to the current `voice_run_id`,
and returns the SDP answer to that browser connection. OpenAI credentials remain
server-side, and the browser never receives a provider key; uTrace participant
authentication authorizes setup rather than authenticating the browser to OpenAI.
See [Getting started with GPT-Live](https://developers.openai.com/api/docs/guides/live).

The backend attaches the authenticated sideband WebSocket at
`wss://api.openai.com/v1/live/sessions/{session_id}/attach` for transcripts,
delegation, context updates, and session control, authenticating with its
server-side API key. Muting uses `session.input_audio.mute` and
`session.input_audio.unmute`, and closing sends `session.close` and waits for
`session.closed`. The browser data channel carries session events, while media uses
WebRTC tracks. A sideband replays nothing from before it attached, so the session
tab keeps its microphone track disabled until conversational readiness, which the
attachment completes. GPT-Live waits for the caller to speak first, so when the
session is still in its `greeting` phase the attached backend appends a
`session.commentary.append` cue that has the agent open the call; a connection that
replaces one the participant already spoke on gets no cue. Once the preview's
route has been committed and it has stayed unopened for a short grace period, the
backend appends one
`session.instructions.append` asking the agent to have them open it at the next
natural pause, and the session tab's status names the same step; if the preview
opens before the agent speaks that ask, a second append retracts it. While the
observation path is ready, the backend also appends what the participant does in
the preview as `session.thinking.append` context as soon as it is committed: the
product's declared milestones with their released values, clicks on named
controls, and page loads, coalesced so a repeated report is not sent again. The
agent therefore knows what they did without having to delegate. A moment Jev
judges worth [acknowledging](#jevs-interview-responsibilities) goes as
`session.commentary.append` instead, asking the agent for a few words. Context appended
while the agent is talking can make it break off mid-sentence, so every
application append except a delegation's own answer waits until the agent's
output audio has gone quiet. The agent speaks a delegation's answer as soon as it
arrives, so the answer instead waits for the participant to pause, for at most
eight seconds, because a muted microphone never shows a pause. Recording an agreement promises nothing: the agent
says it is getting the change started, and says it is building only once the
Implementation Gate has authorized an attempt, when the backend appends one
`session.commentary.append` saying so while that attempt is still building. When the
gate stops the agreed change, by failing for good or by choosing no implementation,
the backend appends one telling the agent the change will not start, so it never
promises a version that is not coming. A reconnected sideband repeats either while
it is still the case's latest gate outcome. None of these polls: database triggers notify the
`voice_sideband` channel with the Interview Session whose mute Commands, preview
route, readiness, implementation progress, or preview activity changed, and a gate
outcome that stops the change notifies it in the transaction that records it. The
sideband re-reads that session's state when woken, and again whenever its listener
reconnects. A Validation Attempt the sideband begins itself, which no trigger
reports, wakes the debrief directly. Session descriptions and
transport credentials are excluded from logs and model context. See
[OpenAI's server-side controls](https://developers.openai.com/api/docs/guides/voice-server-controls).

A late setup response cannot activate a superseded or revoked connection. Close
the unused provider session and record cleanup. On revocation or replacement,
stop accepting old connection events and close the old GPT-Live session over its
sideband; expiring uTrace authentication alone does not terminate
already-established media.

## Runtime identities and sessions

An Interview Session represents one live encounter for interviewing, validation,
or both, and has a stable `session_id`. A case may span several such sessions when
the originating user leaves and returns. Every provider connection has a new
`voice_run_id`, provider session reference, and participant connection ID.
A reconnect may preserve the Interview Session while replacing provider resources;
old callbacks cannot change the current session. When GPT-Live loses the session
tab's media connection, it ends that provider session and the projection reports
`reconnecting`; the tab then opens one new connection, retrying a few times if
that fails. Only one connection has current
participant authority. Authenticated takeover revokes the previous connection.

An Interview Session moves `invited` → `claimed` → `preparing` → `live` →
`closed`. Its voice connection has an independent lifecycle, `connecting` →
`active` → `reconnecting` → `closed`; setup completes before interviewing, and
return and recovery windows expire.
Interview phase, implementation status, and Rive character state are projections;
none is a provider-owned lifecycle. Each Product Signal keeps its own ledger and
candidate and validation identities.

The character states are `idle`, `listening`, `thinking`, `speaking`,
`acknowledgement`, `concern`, `paused`, `reconnecting`, and `completion`. The
user-facing implementation-progress states are `reproduction_captured` →
`making_changes` → `preparing_the_preview` → `ready_for_validation`. Those lists
are the whole contract: the control plane, the database, and the extension carry
exactly the same sets. The
[PRD's character description](../PRD.md#agent-character) names the expressive ones
as examples rather than enumerating them. The animation rig carries further states
for purely visual purposes; those never cross the contract, and no surface may
invent one.

During the conversation, who is talking sets the character, from the call's audio
levels: `speaking` while the agent's audio plays, `thinking` while the agent has
gone quiet waiting on a delegation's answer, and `listening` otherwise. GPT-Live
generates audio faster than it plays, so an agent turn ends when its last speech
has played on the timeline its audio carries, not when generation stops; talking
over the agent ends it at once. Every
change is committed and published together, so the session tab and the extension
show the same state.

The session tab renders one projection document, published with a monotonic
revision. The tab reads it two ways, and both carry the same document: once over
the authenticated session route, for the first render and after any reconnect, and
then continuously through Supabase Realtime on the projection's own row, which
row-level security exposes to that participant and to the client's staff. The tab
ignores any update whose revision is not newer than what it holds. Realtime is
transport for speed, not a second contract — a missed notification is repaired by
re-reading the route. The narrow acknowledgement a control action returns is not
the projection either; it reports the revision and control state that action
produced.

Beside the call, the session tab shows a live transcript built from the
`session.input_transcript.delta` and `session.output_transcript.delta` events on
the browser's data channel. It is a caption for the participant only: the tab
neither stores nor reports it, and the durable transcript is the backend's copy
from the sideband under the [transcript contract](#transcript-and-evidence-contract).

The session admits only the originating user and agent. Client owners/admins do
not join, listen, or take over the interview. uTrace authentication and the
GPT-Live session binding identify the user, session, and current connection.
Closing or replacing a provider connection cannot by itself cancel the Product
Signal.

## Readiness has two boundaries

After participant compatibility preflight passes, conversational readiness requires
the authenticated participant, functioning
voice transport and controls, an attached backend sideband, and a current backend
session context. It permits
greeting, explaining the interview, and asking the user to describe the problem
while preview setup continues.

Observation readiness additionally requires the exact preview allocation and
dataset, extension binding, browser SDK, shared observation runtime, server SDK,
and browser-to-server correlation. Product-activity claims, observation-dependent
probing, visual commands, and validation require this complete path. Connected
sockets alone do not establish readiness. Replacement resources invalidate their
old readiness evidence.

One record owns readiness. A per-session readiness row holds each component fact
separately — participant authentication, voice transport, sideband attachment, and
backend context currency on the conversational side; preview allocation, browser
SDK, Server SDK, and browser-to-server correlation on the observation side —
together with extension readiness and the typed blockers. The two summary
boundaries above are derived from those components in the same transaction that
writes them, so a summary can never disagree with its parts. Any other copy of
those two flags is a read convenience, never a second source, and a reader that
must explain *why* readiness is missing reads the component row.

Each time telemetry ingestion publishes browser, server, or correlation
readiness, it recombines the open session's preview-allocation and observation
components into this record. It leaves extension readiness as the extension's
connection, binding, and Pause last set it, so a healthy telemetry path never
restores readiness they withdrew. A changed record republishes the session
projection and is pushed to the extension.

The compatibility preflight that precedes all of this returns only its pass
state, revision, and per-check results. It mints no credential, because every
session route authorizes the participant's own authenticated identity.

When observation readiness disappears, tell the user what is unavailable, stop
new observation-dependent claims and commands, and recover within the configured
window. Healthy voice may continue discussing already established facts or the
user's goals. Never pretend to see activity or continue validation without its
evidence path. Loss of voice instead enters voice recovery and suspends validation.
The [failure contract](./failure-recovery.md) determines whether work can continue,
pause, resume, or close with a blocker.

## Flow classification

The interview starts from Jev's initial Feature Request Flow classification and
confirms it from the user's statements and observed evidence. Jev proposes the
interview-established classification; a delegation resolves that bounded judgment
when it remains unresolved. Before a gate proposal, record the accepted classification,
supporting sources, and why the initial judgment was retained. The control plane
accepts a typed, source-linked record; a spoken label alone does not change policy
or state. Insufficient evidence remains unresolved rather than inventing a final
classification.

If the evidence shows that the concern is a product failure rather than a feature
request, the demo does not switch flows. The gate records no implementation, and
the Case File names the unsupported flow as its reason.

If the seeded dataset cannot represent the workflow the user needs to show, stop
observation-dependent work and close through the blocker path. The session cannot
change the seed manifest or treat an incomplete substitute as representative
evidence.

## Concurrent work and corrections

Conversation does not wait for every retrieval or implementation step. Independent
read-only lookups and analyses may run concurrently. An implementation Job runs
under its own attempt authorization and reports typed checkpoints, checks, and
candidate readiness. A progress notification cannot claim a successful check or
ready Preview Version until its authoritative result exists.

Use quiet context updates for observations and routine implementation findings;
request spoken updates only for useful findings, relevant questions, blockers, or
candidate readiness. Candidate readiness is pushed, not polled: when the session's
progress reaches `ready_for_validation`, which it does only once the preview serves
the candidate, the attached sideband appends one `session.commentary.append` update
that carries the agreed checks, so the agent tells the participant without first
having to ask and walks through exactly the recorded criteria. The sideband also
begins the Validation Attempt itself as soon as the
[validation controller](./validation.md#attempt-lifecycle) allows it, so the agent
asks each check in turn and delegates once, after the last answer, to have the
answers recorded. GPT-Live chooses
ordinary conversational timing. Application
validation of backend results does not preapprove each spoken sentence or guarantee
silence. The user experience avoids repeated filler and constant progress narration.

Every result names the evidence/context revision and dependencies it used. A
plausible Jev contradiction flag against a requirement used by active implementation
places new dependent steps on hold while interpretation or participant clarification
resolves it. Conversation and independent work continue; dispatched operations
settle or checkpoint with their outcomes recorded. A flag is not a confirmed
correction. Dismissed flags release the hold after dependency checks; unresolved
flags remain within existing time and execution budgets and use the blocker path
if they cannot be resolved.

This hold is an internal dependency check, not the participant's Pause control.
An accepted user correction supersedes the affected claim immediately. Dependent
analysis is invalidated; if authorized implementation is affected, stop new
incompatible steps, record the current workspace state, and re-evaluate the gate
against corrected scope. Neither a stale worker result nor a fluent spoken promise
can expand scope.

## Transcript and evidence contract

Store timestamped input/output transcript fragments with speaker, provider event,
connection, and provenance. GPT-Live streams them as
`session.input_transcript.delta` and `session.output_transcript.delta`, each with
`start_ms` and `end_ms`. A fragment is not a completed turn, and a transcript can
contain mistakes or later corrections. Do not invent a definitive end-of-turn event
or assume the application receives speech early enough to veto it. See
[Managing GPT-Live sessions](https://developers.openai.com/api/docs/guides/live-conversations).

Evidence extraction references spans of fragments and relevant observation IDs.
Claims distinguish proposed, confirmed, contradicted, and withdrawn meaning.
Keep corrections as linked records rather than overwriting source fragments.
A bare “yes” needs the question and current acceptance criterion; a later “actually”
can invalidate an apparent confirmation. Validation requires a deliberate
criterion-bound check with the originating user and proportionate affirmative evidence,
not a silence timer or a classifier score.

Generated speech, transmitted audio, and heard speech are different facts. Record
playback/interruption evidence where the transport exposes it; otherwise delivery
is unknown. An append acknowledgment or output transcript is not proof the user
heard a statement. After interruption, restate critical scope or validation
questions when needed instead of assuming unheard wording was accepted.

## Active context

The backend keeps the recent conversation, durable agreed scope and criteria,
open questions, contradictions, selected current observations, pending Jobs, and
verified results available to the relevant agent. Those facts are explicitly
supplied; agents do not share memory automatically. Source-linked context bundles
and corrections follow [Context Assembly](./context-assembly.md).

GPT-Live manages its own conversational memory, including provider summarization.
The application cannot rely on disabling implicit truncation or controlling all
internal context reduction. Durable critical facts remain outside that memory and
are reintroduced after reconnection or material change. Backend model context is
budgeted separately, with omitted noncritical sources recorded. The system does not
index the live interview for search; ordinary persisted transcripts and explicit
working context support this loop.

## Pause, interruption, and departure

Speech barge-in affects conversational playback. It does not cancel implementation
or tools. Explicit Pause stops voice and observation and blocks new live
implementation steps; already-dispatched operations finish or checkpoint with recorded outcomes.
Resume rechecks readiness and supplies fresh context. A spoken request for a quiet
moment can suppress speech without pausing implementation.

A temporary connection loss allows current authorized live implementation/checks
during a bounded reconnect window, within the existing attempt budget. The window is
two minutes from the lost connection; the session tab opens a new one by itself
within seconds, so it runs out only when the person has gone. A connection not
replaced in time closes the encounter as **Finish later** does, with closure reason
`reconnect_window_expired`; a paused session keeps its own Pause semantics. No validation
completes while the user is absent. When the window expires, further live work
stops under the lifecycle contract. **Finish later**, departure, and a reconnection timeout all checkpoint the encounter and start or preserve the fixed case-level return deadline in [Failure Recovery](./failure-recovery.md#bounded-return-and-terminal-expiry). The same user returns through the dashboard link or bounded private Discord reminders; return requires fresh readiness and does not reset budgets. Expiry closes the case and cleans resources.

The [failure and recovery contract](./failure-recovery.md) owns cancellation,
technical blockers, budget exhaustion, and post-validation completion. Reconnecting
voice or opening another session never resets implementation budgets.

## Tools and customer language

All tool proposals pass schema, tenant, phase, readiness, policy, permission,
freshness, and budget checks before dispatch. Side effects are serialized within
the affected authority scope; independent read-only work can fan out. Cancellation
and late-result reconciliation follow [Cross-Component Contracts](./cross-component-contracts.md).

Product text, speech, DOM labels, retrieved docs, and tool output are untrusted
content. They cannot redefine instructions or grant authority. Return plain,
customer-appropriate findings to the voice model. Internal source code, stack
traces, credentials, and raw telemetry stay in the backend and engineer-facing
Case File. The agent can describe what the user experienced without narrating
implementation internals.

## Verification

Exercise useful questions during concurrent work, interruptions and corrections,
preview setup delay, observation loss with healthy voice, voice loss with ongoing
checks, Pause versus quiet requests, departure/resume, explicit cancellation,
changed criteria, and exact-candidate validation. Test responsibilities are
defined in [Evaluation and Observability](./evaluation-and-observability.md).
Measure actual time to useful response, grounded question quality, and completion;
provider benchmarks are not uTrace measurements.
