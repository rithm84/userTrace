# Context assembly

[Back to uTrace architecture](../ARCHITECTURE.md)

Context assembly combines prepared knowledge with current, source-linked facts.

![The Product Overview and exact code findings join current evidence in scoped context bundles.](../diagrams/svg/context-assembly.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Source ownership

The source and consumer map is drawn above. Supabase holds authoritative workflow
records and artifact references. GitHub and the exact checked-out revision own
code truth. Client-designated documents and SDK annotations supply product
knowledge, distilled into the Product Overview; the overview cannot create
evidence or replace the underlying sources.

Context covers one Product Signal and preserves its one originating user,
policy, Implementation Attempt, Preview Version, and validation evidence.

## Product knowledge

Product knowledge reaches every role through the client's active Product Overview
rather than a search index. The overview is a few thousand characters, so it
travels whole: intake routing reads its purpose, capabilities, workflows, and
terminology for every judgement and the remaining sections when a judgement
reports a fact it could not resolve, and the voice agent's instructions and every
voice tool call's bundle carry all of it as their product-knowledge section, each section citing the overview
version and its content digest. Nothing is looked up while a participant waits.

## Code investigation

Code investigation uses scoped filesystem search and exact-file reads in an
isolated Vercel Sandbox checkout, through read-only commands the agent runs in Codex. Pre-gate
investigation is read-only and has no write lease; the
[harness contract](./implementation.md#agent-harness) owns its configuration. It
starts when the participant claims the invitation, for the concern intake
recorded, so its findings exist before the interview agrees a scope. The gate
waits for an investigation still running, and investigates the agreed concern
itself only when none left findings. Implementation starts only after the gate authorizes a fenced workspace.
Every finding identifies repository, base revision, path, and relevant lines.
Before editing, the agent reads the actual files at the attempt's pinned revision.
A document describing code is a discovery hint, not authority to patch an unseen file.

Repository changes require a fresh base decision and renewed checks. A cached
analysis does not authorize editing a different revision. Source records in the
Case File distinguish product documentation from exact code inspected and
product behavior observed.

## Product Overview lifecycle

Each client has an active immutable Product Overview Version synthesized from
prepared configuration, SDK annotations, designated documents, and source-linked
read-only code findings. It covers product purpose, terminology, primary users,
workflows, known capabilities, entities, integrations, constraints, and sources.
The client configuration script activates the client's curated overview, and
deterministic validation checks its schema and source references before
activation. Inference remains marked as inference.

Activating changed content creates a new version; activating the same content
again keeps the version that already holds it. Routing batches pin the overview
they used; later batches use the newly active version. If there
is no valid overview, intake is preserved and routing waits. A code capability
does not prove that it worked for the originating user.

## Working context during an interview

There is no search index; each bundle carries what its consumer needs. uTrace
keeps the conversation in active context and persists it normally.

Always carry the current objective, agreed scope and acceptance criteria, open
questions, contradictions, relevant current observations, and implementation
status explicitly. Product documentation is derived knowledge, and exact files
and canonical evidence stay authoritative. The orchestration supplies updates
across the voice agent, Jev, GPT-6.1 Sol, and the coding agent; none
automatically knows what another worker discovered. Bundles also carry
lifecycle and policy revisions, prior Commands and Results, and delivered-speech
evidence where available.

The backend retains transcript fragments, source evidence, and previous results.
It selects bounded recent conversation and source-linked summaries when useful.
The agent's speech is recorded a word per fragment as it plays, so a bundle
carries the recent conversation as turns: consecutive fragments of one speaker joined, each turn
citing its first fragment and spanning their time range. A bundle that cannot
hold the whole window keeps the latest turns.
Summaries remain derived and cannot replace a user's correction or canonical
record. The voice agent's in-call context does not control authoritative state.
See [Interview and Voice](./interview-and-voice.md).

## Stage boundaries

Implementation Gate proposals, intake assistance, and Case File synthesis send
GPT-6.1 Sol application-assembled context through the OpenAI Responses API, each
request pinning its reasoning effort and service tier. A request that needs a
typed result uses strict structured outputs. Nothing is stored at OpenAI. Code
investigation and Implementation Attempts run GPT-6.1 Sol inside Codex in the
workspace sandbox, under the [harness contract](./implementation.md#agent-harness).
The voice agent's model receives its instructions, the conversation,
`<application>` messages, and tool results through the Responses API over a
WebSocket, with nothing stored at OpenAI; each tool call runs against a bundle
assembled for it. None of these carries evidence or shared cross-client memory.

Intake receives its immutable message batch and direct conversation context,
authenticated identity, Product Overview, and routing policy. Application code
assembles Jev's input: the surface, the channel, the report as the batch's
ordered text, and the Product Overview excerpt. Routine context assembly does not
require a generative model call. Conditional assistance, under the
[intake contract](./signal-and-outreach.md#conditional-assistance), adds nearby
conversation, the rest of the overview, or GPT-6.1 Sol's interpretation;
production telemetry and unrestricted code do not enter the routing context.

Jev receives question-specific subsets of these bundles: the relevant source
statement with necessary conversational context, named claims or criteria, selected
observations, and supplied alternatives. Jev's acknowledgement and shown-checks
judgements during a call receive a bundle of the recent conversation alone,
because the full working context would only slow them. Pre-filter by scope and exact metadata in
code before semantic selection. Preserve negations, corrections, and uncertainty;
do not send the full transcript, DOM, or telemetry history to every judgment.

The interview receives relevant product knowledge, same-user/same-client context,
and selected correlated evidence. During the call, Jev judges which activity is
worth acknowledging and which agreed checks a new version already showed; before
the gate, it proposes the interview-established flow classification. The voice
agent receives concise customer-appropriate facts through its screen state, cues,
and tool results, not raw repositories or internal diagnostics.

The gate receives the signal, agreed outcome, sufficient evidence, exact code
findings, and policy. Implementation receives
the authorized scope, pinned code revision, checks, and budgets. Validation
receives the exact candidate identity, agreed dataset, criteria, before/after
evidence, and a user-language change summary.

Each backend context has a budget. Preserve critical state first and record
omitted noncritical material. If necessary evidence cannot fit or be retrieved,
request a scoped follow-up rather than silently dropping it or inventing an answer.

## Provenance and concurrent results

Every stage stamps a bundle containing source IDs, versions/hashes, cutoff,
client/user scope, context revision, and purpose. Gate decisions and Case File
claims cite bundle items. Workers receive immutable inputs and return the bundle
identity, source-linked findings, uncertainty, and dependencies.

A correction or changed observation can invalidate a dependent result before it
is applied. Stale results may remain diagnostic evidence but cannot update the
current scope or cause an effect. Unchanged independent results may still be
used after dependency checks. Fast Jev judgments use the same freshness rule.

A bundle pins every revision it was assembled against, but its purpose declares
the ones its applied result depends on, and only those can make it stale:

- `voice_delegation`, a voice agent tool call's context, and
  `flow_classification`: the Product Signal's revision, classification, acceptance
  criteria, concern, and pinned configuration release.
- `jev_attention`: the acceptance criteria the shown checks were judged against.

No purpose depends on telemetry readiness or on the Interview Session's own
revision. A correction that supersedes cited evidence invalidates the bundle
whatever its purpose. A controller rechecks freshness with the Product Signal
locked, and the Implementation Gate decides again on fresh context, at most three
times per step, when its classification or proposal went stale.

## Failure and data boundaries

Redact secrets and excluded material before sending context to any model.
Keep user-scoped transcript/history separate from client-scoped product knowledge.
Untrusted source text never becomes a tool instruction or permission grant.

Retry provider failures within the configured policy, then report the precise
missing dependency. A routing lookup that cannot complete produces a blocked
analysis, never an invented noise verdict. Conversation can continue on facts
already known when a lookup fails. A gate cannot approve work whose required
code or product evidence is unavailable. No retrieval failure resets a budget or
changes the kind of work.
