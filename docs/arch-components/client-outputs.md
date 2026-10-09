# Case File, client dashboard, and GitHub

[Back to uTrace architecture](../ARCHITECTURE.md)

The following diagram shows how validated evidence becomes a sealed Case File and exact GitHub delivery:

![Frozen evidence becomes a validated Case File, exact GitHub delivery, and an authorized dashboard view.](../diagrams/svg/client-outputs.svg)

## Case File identity and assembly

An eligible closed Product Signal owns one structured Case File; other outcomes may produce none. Its metadata identifies the client, opaque originating-user and customer-account references, Product Signal, schema and context revisions, evidence window, gate decisions, attempts, exact Preview Version, Validation Attempt, delivery identities, and immutable-core digest.

GPT-6.1 Sol drafts typed fields from a frozen, source-linked context bundle. Code
checks citation presence and reference validity. A confidence score alone cannot
establish that a claim is supported.

Assembly follows three stages:

1. **Verify content.** Application code checks structure, scope, redaction,
   citations, mandatory evidence, and outcome consistency, then freezes the
   source-backed findings and evidence draft.
2. **Resolve delivery.** For validated implementation, append the verified GitHub
   identities using the [delivery protocol](#github-delivery).
3. **Seal and publish.** Once identities and outcome are final, set `ready` and
   compute the immutable core digest. Publication retries reuse those exact bytes;
   delivery never edits an already sealed core.

The implementation section states facts from the records that own them, never
from model prose: the changed files from the validated Preview Version's frozen
source manifest, the checks from that attempt's last candidate run of each, the
cleanup state from its environment lease, and the approved scope, exclusions,
and required checks from the gate decision.

The [PRD's Case File definition](../PRD.md#case-file) owns its content sections.
A future correction must be a separately identified amendment rather than a
silent rewrite.

Direct personal identifiers are excluded from the published core, and verification
enforces that literally: sealing refuses a core whose rendered bytes contain the
originating user's recorded name, an email address, or a Customer Surface handle.
What the core carries instead is an opaque client-scoped reference for the client
user and the customer account.

The authorized dashboard resolves those references on request, for authorized
staff only, scoped to the client that owns them. The only label uTrace records for
a client user is that person's global display name, which is the same string the
core is forbidden to contain — the difference is that resolution is an authorized
read rather than published content. When no name is recorded, resolution returns
no label and the dashboard shows the reference itself. It never invents one, and a
reference the client does not own resolves to nothing rather than reaching another
tenant's rows. Client staff have no recorded display name at all; their sign-in
email is the only label available for them.

## Variants and evidence threshold

- `validated_implementation` records the exact source and build provenance, candidate and prepared dataset, checks, validation evidence, and delivered PR.
- `no_implementation` records evidence and the gate's reason, including insufficient evidence, disagreement, policy or safety denial, an unsupported flow, or non-fit. It contains no candidate or PR.
- `blocked` records the reached stage, retained artifacts, exact technical or product blocker, and any unvalidated candidate or incomplete delivery. Terminal scope/budget limits, work that no longer fits the live session, and unsuccessful validation use this variant.

Participation followed by a gate denial, client Stop case, unsuccessful validation, or terminal blocker produces a Case File. Return expiry follows the [bounded return contract](./failure-recovery.md#bounded-return-and-terminal-expiry). A resumable infrastructure failure preserves the attempt until recovery or an authorized terminal decision. Explicit participant cancellation and initial invitation decline, unreachable outreach, incompatible pre-participation setup, or opt-out produce no Case File.

Every material claim cites an authorized evidence slice: intake, contextualized transcript fragments or a linked correction, browser observation, correlated server span, gate record, implementation artifact, checks, exact candidate, or validation evidence. A transcript fragment alone does not establish a final user judgment or prove that speech was heard. Missing proof is explicit uncertainty or a blocker. Internal test and evaluation results never appear in a client output.

## Client dashboard

Supabase Auth authenticates client staff. Explicit [membership rows](./identity-and-access.md#client-staff-authentication-with-supabase-auth) authorize the client's owner and admins; authentication alone never grants tenant access. Every list, detail, Case File, citation, artifact, and offer operation rechecks membership and object linkage.

The [PRD](../PRD.md#client-dashboard) owns the dashboard's feature scope. Its
views expose authorized projections, never model reasoning, unrestricted
transcripts, raw telemetry streams, workspace shells, manual lifecycle editing, or
per-action approval controls.

Supabase Realtime may accelerate notifications and projection refreshes, but it never proves media delivery, command completion, authorization, or workflow state. Durable Postgres projections remain authoritative.

## Outreach Offers views

The dashboard reads [offer and credit records](./outreach-offers.md#issuance-and-reconciliation)
for authorized, reserved, and remaining allowance, earned credits, and issuance
or recovery status. These are separate from the sealed Case File; credit retries
do not rewrite product findings. Participants see the offer's terms in their
private invitation and have no view of billing records. A case with no Case File
can still need an offer disposition.

## GitHub delivery

GitHub is source-control truth. A narrowly scoped GitHub App writes to the authorized repository using a deterministic uTrace branch and app-authored commits. Nothing is pushed before same-user validation of the exact candidate and dataset. Production merge remains human-owned, and later edits to the PR do not inherit the validation.

Delivery is one real draft PR in the one authorized repository. It binds to the
exact validated source commit, base revision, build manifest, and Product Signal.
The Case File and dashboard display that applicability explicitly.

Delivery performs this order:

1. reserve the Case File identity and stable authenticated URL;
2. preflight installation, repository, target branch, validated head, deterministic branch, permissions, and existing-marker conditions;
3. create the draft PR with a stable hidden idempotency marker;
4. reconcile and verify the exact branch, head, membership, and dependencies;
5. persist verified delivery identities, seal the final Case File and digest, then publish it.

Retries reuse the same command identity and adopt only an exact marker/branch/head
match. Unknown provider outcomes reconcile before repetition. Movement of the
repository's normal branch after validation does not invalidate the historical
proof and does not create newly validated code. uTrace does not continuously
review, rebase, or revalidate against the moving branch. If delivery cannot apply
the exact validated source safely, it records a delivery blocker rather than
claiming a different head is validated; uTrace does not force-push, delete, or
close a human-visible draft automatically.

A delivery blocker after successful validation changes the Case File rather than
suppressing it. Delivery resolves before the core is sealed, so the still-unsealed
Case File becomes the `blocked` variant carrying the blocker reason, is verified
again against that content, and is then sealed and published; the case closes as
blocked. The validation result itself stays frozen and successful — what failed
is delivery, and the Case File says so, with no PR claimed.

PR bodies contain a sanitized problem and implementation summary, repository-specific files and checks, risks, and authenticated Case File link. They exclude customer identity, transcript excerpts, telemetry, preview credentials, and internal evaluation.

## After validation and publication

Case File assembly, draft-PR delivery, and cleanup may continue after the user leaves once validation evidence is frozen. Human review, merge, conflict resolution, and post-validation changes do not reopen the Product Signal. uTrace never approves or merges its own PR, responds autonomously to review comments, or rewrites the published Case File.
