# Case File, client dashboard, and GitHub

[Back to uTrace architecture](../ARCHITECTURE.md)

Owning records and verified findings feed the Case File; only validated work
gets a draft PR:

![Owning records and verified model findings feed the sealed Case File core, which exchanges identities with the draft PR for validated work; the dashboard shows the Case File and, separately, offer and credit records.](../diagrams/svg/client-outputs.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Case File identity and assembly

An eligible closed Product Signal owns one structured Case File; other outcomes
may produce none. Metadata identifies the client, opaque originating-user and
customer-account references, Product Signal, schema and context revisions,
evidence window, gate decisions, attempts, exact Preview Version, Validation
Attempt, delivery identities, and immutable-core digest. The
[PRD's Case File definition](../PRD.md#case-file) owns its content sections.

Assembly runs in this order:

![Client outputs sends a frozen bundle to GPT-6.1 Sol, verifies and freezes the draft, delivers to GitHub for validated implementation or becomes the blocked variant on a delivery blocker, then seals and publishes the Case File.](../diagrams/svg/client-outputs-delivery.svg)

### Drafting and verification

Verification checks structure, scope, redaction, citation presence and validity,
mandatory evidence, and outcome consistency; a confidence score alone never
establishes a claim. Implementation facts come from owning records, never model
prose; changed files, from the validated Preview Version's frozen source
manifest.

### Sealing

Assembly sets `ready` only once delivery identities and the outcome are final.
Publication retries reuse the sealed bytes, delivery never edits a sealed core,
and a later correction is a separately identified amendment, never a silent
rewrite.

### Personal identifiers

The core carries opaque client-scoped references for the client user and
customer account, never direct personal identifiers; sealing refuses bytes
containing the originating user's recorded name, an email address, or a
Customer Surface handle.

Only the owning client's authorized staff resolve references, on request:

- A client user's only label is their recorded global display name, which the
  core may not contain. With none recorded, the dashboard shows the reference
  and never invents a label.
- A reference the client does not own resolves to nothing, never another
  tenant's rows.
- Client staff have no display name; their sign-in email is their only label.

## Variants and evidence threshold

A Case File has three variants:

- `validated_implementation` records the exact source and build provenance,
  candidate and prepared dataset, checks, validation evidence, and delivered PR.
- `no_implementation` records evidence and the gate's reason, including
  insufficient evidence, disagreement, policy or safety denial, an unsupported
  flow, or non-fit, with no candidate or PR.
- `blocked` records the reached stage, retained artifacts, exact technical or
  product blocker, and any unvalidated candidate or incomplete delivery. It covers
  terminal scope or budget limits, work no longer fitting the live session, and
  unsuccessful validation.

Participation followed by a gate denial, client Stop case, unsuccessful
validation, or terminal blocker produces a Case File; return expiry follows the
[bounded return contract](./failure-recovery.md#bounded-return-and-terminal-expiry).
A resumable infrastructure failure preserves the attempt until recovery or an
authorized terminal decision. Explicit participant cancellation, an initial
invitation decline, unreachable outreach, incompatible pre-participation setup,
and opt-out produce no Case File.

Every material claim cites an authorized evidence slice: intake, contextualized
transcript fragments or a linked correction, browser observation, correlated
server span, gate record, implementation artifact, checks, exact candidate, or
validation evidence. A transcript fragment alone neither establishes a final user
judgment nor proves speech was heard. Missing proof is explicit uncertainty or a
blocker, and client outputs never include internal test or evaluation results.

## Client dashboard

Supabase Auth authenticates client staff, but only explicit
[membership rows](./identity-and-access.md#client-staff-authentication-with-supabase-auth)
grant the owner and admins tenant access. Every list, detail, Case File,
citation, artifact, and offer operation rechecks membership and object linkage.

Within the [PRD's feature scope](../PRD.md#client-dashboard), views show
authorized projections, never model reasoning, unrestricted transcripts, raw
telemetry streams, workspace shells, manual lifecycle editing, or per-action
approval controls.

Postgres projections are authoritative; Supabase Realtime may speed
notifications and refreshes but never proves media delivery, command completion,
authorization, or workflow state.

### Outreach Offers views

[Offer and credit records](./outreach-offers.md#issuance-and-reconciliation)
supply authorized, reserved, and remaining allowance, earned credits, and
issuance or recovery status. They stay separate from the sealed Case File: credit
retries never rewrite findings, and a case without a Case File may still need an
offer disposition. Participants see only the offer terms in their private
invitation, no billing records.

## GitHub delivery

GitHub is source-control truth. A narrowly scoped GitHub App writes app-authored
commits to a deterministic uTrace branch in the one authorized repository, and
nothing is pushed before the same user validates the exact candidate and dataset.
Delivery is one real draft PR bound to the exact validated source commit, base
revision, build manifest, and Product Signal, as the Case File and dashboard
state. Its files are the validated Preview Version's files, and delivery refuses
any whose digests differ from the ones that version's
[full checks](./preview-build.md#readiness-and-preview-version-lifecycle) passed
on.

Preflight checks the installation, repository, target branch, validated head,
deterministic branch, permissions, and existing markers. The draft PR carries a
stable hidden idempotency marker; reconciliation verifies the exact branch, head,
membership, and dependencies.

### Retries and the moving branch

Retries reuse the same command identity, reconcile unknown provider outcomes
before repeating, and adopt only an exact marker, branch, and head match.

Later movement of the normal branch neither invalidates the
historical proof nor creates newly validated code, and uTrace never continuously
reviews, rebases, or revalidates against it. Production merge stays
human-owned; later PR edits do not inherit validation.

### Delivery blockers

If delivery cannot safely apply the exact validated source, it records a
blocker rather than claiming a different head is validated, and never
automatically force-pushes, deletes, or closes a human-visible draft.

Because delivery resolves before sealing, the Case File becomes the `blocked`
variant carrying the blocker reason, and the case closes as blocked. Validation
stays frozen and successful; the Case File records the delivery failure and
claims no PR.

### PR body

PR bodies contain a sanitized problem and implementation summary,
repository-specific files and checks, risks, and the authenticated Case File
link. They exclude customer identity, transcript excerpts, telemetry, preview
credentials, and internal evaluation.

## After validation and publication

Once validation evidence is frozen, assembly, draft-PR delivery, and cleanup may
continue after the user leaves. Human review, merge, conflict resolution, and
post-validation changes never reopen the Product Signal. uTrace never approves or
merges its own PR, answers review comments autonomously, or rewrites the
published Case File.
