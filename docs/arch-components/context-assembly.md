# Context assembly

[Back to uTrace architecture](../ARCHITECTURE.md)

Context assembly combines prepared knowledge with current, source-linked facts:

![The Product Overview and exact code findings join current evidence in scoped context bundles.](../diagrams/svg/context-assembly.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Source ownership

Supabase holds authoritative workflow records and artifact references; GitHub and
the exact checked-out revision own code truth. The Product Overview distills
client-designated documents and SDK annotations but cannot create evidence or
replace its sources.

Context covers one Product Signal and preserves its one originating user, policy,
Implementation Attempt, Preview Version, and validation evidence.

## Product knowledge

Product knowledge reaches every role through the client's active Product Overview,
not a search index. At a few thousand characters it travels whole, and each
section a bundle carries cites the overview version and its content digest:

- Intake routing reads purpose, capabilities, workflows, and terminology for every
  judgement, and the remaining sections when a judgement reports a fact it could
  not resolve.
- The voice agent's instructions and every voice tool call's bundle carry all of
  it as their product-knowledge section.

Nothing is looked up while a participant waits.

## Code investigation

Code investigation runs read-only commands through Codex in an isolated Vercel
Sandbox checkout: scoped filesystem search and exact-file reads, with no write
lease before the gate, configured by the
[harness contract](./implementation.md#agent-harness).

Investigation starts when the participant claims the invitation, for the concern
intake recorded, so findings exist before the interview agrees a scope. The gate
waits for a running investigation and investigates the agreed concern itself only
when none left findings.

Every finding identifies repository, base revision, path, and relevant lines.
Before editing, the agent reads the actual files at the attempt's pinned revision;
a document describing code is a discovery hint, not authority to patch an unseen
file. Repository changes require a fresh base decision and renewed checks, and a
cached analysis never authorizes editing a different revision. Case File source
records distinguish product documentation, exact code inspected, and product
behavior observed.

## Product Overview lifecycle

Each client has an active immutable Product Overview Version synthesized from
prepared configuration, SDK annotations, designated documents, and source-linked
read-only code findings. It covers product purpose, terminology, primary users,
workflows, known capabilities, entities, integrations, constraints, and sources,
with inference marked as inference. The client configuration script activates the
curated overview after deterministic validation of its schema and source
references.

Activating changed content creates a new version; reactivating the same content
keeps the version that holds it. Routing batches pin the overview they used, and
later batches use the newly active one. Without a valid overview, intake is
preserved and routing waits. A code capability does not prove it worked for the
originating user.

## Working context during an interview

uTrace keeps the conversation in active context and persists it normally. Bundles
always explicitly carry the current objective, agreed scope and acceptance
criteria, open questions, contradictions, relevant current observations, and
implementation status, plus lifecycle and policy revisions, prior Commands and
Results, and delivered-speech evidence where available. Orchestration relays
updates across the voice agent, Jev, GPT-6.1 Sol, and the coding agent, since none
automatically knows what another discovered.

### Recent conversation

The backend retains transcript fragments, source evidence, and previous results,
and selects bounded recent conversation and source-linked summaries when useful.
A bundle carries recent conversation as turns, since agent speech is recorded a
word per fragment: one speaker's consecutive fragments joined, each turn citing
its first fragment and spanning their time range. A bundle that cannot hold the
whole window keeps the latest turns.

Summaries stay derived and cannot replace a user's correction or canonical record.
The voice agent's in-call context does not control authoritative state; see
[Interview and Voice](./interview-and-voice.md).

## Stage boundaries

### Model transport

Model calls follow the [model access contract](./deployment.md#model-access) and
carry only application-assembled context. Implementation Gate proposals, intake
assistance, and Case File synthesis reach GPT-6.1 Sol through the OpenAI
Responses API, with strict structured outputs for typed results; code
investigation and Implementation Attempts run in the [Codex
harness](./implementation.md#agent-harness). The voice agent's model receives
its instructions, the conversation, `<application>` messages, and tool results
over a Responses API WebSocket, and each tool call runs against its own bundle.
Nothing is stored at OpenAI, and none of these carries evidence or shared
cross-client memory.

### Intake

Intake receives the immutable batch's
[routing base context](./signal-and-outreach.md#routing-context-and-cognition),
widened only by [conditional assistance](./signal-and-outreach.md#conditional-assistance);
production telemetry and unrestricted code never enter it. On the routine path,
application code assembles Jev's input with no generative model call: the
surface, the channel, the report as the batch's ordered text, and the Product
Overview excerpt.

### Jev judgements

Jev receives question-specific subsets of these bundles: the relevant source
statement with necessary conversational context, named criteria, selected
observations, and supplied alternatives. Its in-call acknowledgement judgement
gets only the recent conversation, for speed. Code
pre-filters by scope and exact metadata before semantic selection. Preserve
negations, corrections, and uncertainty; never send the full transcript, DOM, or
telemetry history to every judgement.

### Interview, gate, implementation, and validation

| Stage | Receives |
| --- | --- |
| Interview | Relevant product knowledge, same-user and same-client context, and selected correlated evidence; the voice agent gets concise customer-appropriate facts through its screen state, cues, and tool results, never raw repositories or internal diagnostics. |
| Implementation Gate | The signal, agreed outcome, sufficient evidence, exact code findings, and policy. |
| Implementation | The authorized scope, pinned code revision, checks, and budgets. |
| Validation | The exact candidate identity, agreed dataset, criteria, before/after evidence, and a user-language change summary. |

### Context budgets

Each backend context has a budget: preserve critical state first, record omitted
noncritical material, and request a scoped follow-up when necessary evidence
cannot fit or be retrieved, never silently dropping it or inventing an answer.

## Provenance and concurrent results

Every stage stamps a bundle with source IDs, versions or hashes, cutoff, client
and user scope, context revision, and purpose; gate decisions and Case File claims
cite its items. Workers receive immutable inputs and return the bundle identity,
source-linked findings, uncertainty, and dependencies.

A correction or changed observation can invalidate a dependent result before it
applies:

![A controller has a bundle assembled for a purpose and sends it to a model role; when the result returns, the controller locks the Product Signal and compares the revisions the purpose depends on, applying a current result and keeping a stale one only as diagnostic evidence, with the Implementation Gate deciding again on fresh context at most three times per step.](../diagrams/svg/context-assembly-freshness.svg)

Unchanged independent results may still be used after dependency checks, and fast
Jev judgements follow the same rule. Only the revisions a bundle's purpose
declares as dependencies can make it stale:

- `voice_delegation`, a voice agent tool call's context, and
  `flow_classification`: the Product Signal's revision, classification, acceptance
  criteria, concern, and pinned configuration release.
- `jev_attention`, an in-call acknowledgement's context: none, since it rests only
  on the conversation it read.

No purpose depends on telemetry readiness or the Interview Session's own revision.
A correction that supersedes cited evidence invalidates the bundle whatever its
purpose.

## Failure and data boundaries

Redact secrets and excluded material before sending context to any model. Keep
user-scoped transcript and history separate from client-scoped product knowledge.
Untrusted source text never becomes a tool instruction or permission grant.

Retry provider failures within the configured policy, then report the precise
missing dependency. A routing lookup that cannot complete yields a blocked
analysis, never an invented noise verdict. Conversation may continue on known
facts when a lookup fails. A gate cannot approve work whose required code or
product evidence is unavailable. No retrieval failure resets a budget or changes
the kind of work.
