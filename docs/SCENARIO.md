# Demo Scenario: Shopalytics Saved Conversion Views

Status: active demo acceptance journey

This document tells the story the demo runs. The [PRD](./PRD.md) defines how each
step behaves, and [ARCHITECTURE.md](./ARCHITECTURE.md) defines the system that runs
it.

## Outcome

Maya, a Dudulemon growth lead, asks Shopalytics for Saved Conversion Views, demonstrates
the recurring workflow in a seeded Shopalytics preview, and agrees on a bounded
solution. uTrace implements that solution live while the conversation continues, returns the exact
candidate to the same person, and opens one PR only after they validate it.

![The Shopalytics Saved Conversion Views journey, from Discord request to live implementation and same-user validation.](./diagrams/svg/scenario-happy-path.svg)

## Actors and request

Shopalytics is the client product; Dudulemon is the customer account. The request
enters Feature Request Flow and targets one Shopalytics repository.

Maya is the interview and validation participant for the entire
journey. No colleague, Shopalytics staff member, or operator substitutes for
them.

Canonical Discord input, typed by Maya from their own Discord account:

```text
I keep rebuilding the same conversion filters every Monday. Can Shopalytics let me save a view for TikTok traffic from women 25-34 on products with negative sizing reviews, then reopen it later?
```

The underlying job is to monitor a recurring conversion problem without
rebuilding the same date-range, channel, demographic, review-topic, sentiment,
and conversion-state filter stack every week.

## Starting state and fixture

Before the request is posted:

- Shopalytics has one active client configuration, published by script: its
  Discord server, repository, preview template, seed manifest, product context,
  and provider grants.
- Dudulemon is an eligible customer account, and this request is neither a replay
  nor a duplicate invitation.
- The authorized repository scope and required checks are pinned.
- A healthy base preview can be claimed for Maya.
- Each preview is served at `<opaque>.preview.utrace.dev` and shares the
  `utrace.dev` site, which is acceptable only because Shopalytics is a
  uTrace-owned fixture.
- A uTrace-controlled Stripe sandbox stands in for Shopalytics' billing account,
  with an existing Stripe Customer mapped to Dudulemon, a zero starting credit
  balance, USD billing, and no tax, discounts, or other balance adjustments.
  Shopalytics has authorized an issuance allowance.

The base preview is seeded with a manually prepared, representative synthetic
Dudulemon dataset containing:

- TikTok and other traffic sources
- women aged 25–34 and other demographic segments
- negative sizing reviews and other review topics and sentiments
- products with a visible conversion drop-off
- conversion and review events inside and outside `Last 30 days`, relative to
  the fixture's pinned clock
- adjacent data that makes incorrect filter restoration observable

Third-party personal data is replaced with shape-preserving synthetic values.

The scripted configuration, synthetic dataset, shared-site preview origin, and
sandbox billing are demo shortcuts that real clients would not use.

## Intake and invitation

Maya's message becomes one Saved Conversion Views Product Signal in Feature
Request Flow. The invitation Maya receives in a private Discord message offers
Dudulemon a **250.00 USD** invoice credit for their completed participation. They
sign in with the invited Discord identity, complete browser, extension, and voice
setup, and start the interview in the seeded base preview.

## Discovery interview

Maya opens the conversion dashboard and rebuilds the filter stack:
`Last 30 days`, the TikTok channel, women 25–34, negative review sentiment on the
sizing topic, one product or category, and the conversion metric. The fixture
clock makes `Last 30 days` deterministic. They show that leaving or refreshing the
dashboard loses this state and explain that rebuilding it every Monday slows
recurring conversion analysis.

The interview establishes these acceptance points:

- the complete filter stack can be saved under a user-supplied name
- the saved view remains available to Maya after leaving or refreshing the
  dashboard
- reopening the view restores every saved filter and the associated conversion
  display
- restored state is specific enough that adjacent seeded segments do not
  appear accidentally
- the requested result is a saved view, not a broad dashboard redesign or a
  collaboration workflow

The interview confirms the Feature Request Flow classification. Shopalytics
annotates its chart state (applied filter values, displayed series identity, and
rendering completion), so the conversion-display criterion is observed rather
than inferred from an API response or Maya's word alone.

The saved-view name used during validation is `TikTok sizing drop-off`.

## Approved live implementation

The gate result is `approved_live`. Its frozen scope is:

- persist saved conversion views for the Dudulemon account
- add a control that saves the current conversion-view state with a name
- add a list or selector for reopening a saved view
- restore the agreed filters and associated visualization state
- remain within the authorized Shopalytics repository, paths, checks, and
  implementation budget

The candidate does not add sharing, notifications, rename, duplication,
deletion, billing changes, or a general dashboard redesign.

The coding agent implements the scope while Maya and the agent keep talking, and
the first candidate reaches readiness; this journey does not exercise a repair.

## Same-user validation

The agent explains the change, the preview switches to the exact candidate, and
Maya performs this validation task:

1. Rebuild the agreed filter stack in the candidate.
2. Save it as `TikTok sizing drop-off`.
3. Leave or refresh the dashboard.
4. Reopen the saved view.
5. Confirm that every filter and the associated conversion display returned,
   without accidentally including adjacent fixture segments.

The outcome is `validated`: Maya confirms each acceptance point, and the
correlated Shopalytics evidence shows the save, reload, restore, and resulting
conversion display without contradiction.

## Delivery and completion

uTrace opens one draft PR in the Shopalytics repository. Production merge remains
with the Shopalytics team.

The Case File records:

- the canonical Discord request and originating user
- the recurring Monday workflow and agreed acceptance points
- the approved live scope and explicit exclusions
- exact source, build, candidate, seed, and check identities
- criterion-level validation evidence for `TikTok sizing drop-off`
- implementation risks, cleanup results, next action, and the one PR identity

Dudulemon earns the **250.00 USD** credit, and uTrace issues it in the Stripe
sandbox. The demo harness then finalizes a **1,000.00 USD** test invoice for
Shopalytics service, which shows the credit applied and **750.00 USD** remaining
due. No real money moves. Credit issuance and Case File delivery are independent
outcomes.

The journey completes after the Case File is published and the seeded dataset,
the implementation workspace, expired invitations, and unneeded preview runtimes
have been cleaned up.

## Acceptance boundary

The happy path passes only when one exact candidate completes the journey with
Maya. The PR must represent the validated source and build, and the Case File must
cite that candidate's checks, dataset, and criterion-level evidence. All
scenario-owned resources must reach their required retained or cleaned state.
Substituting a different user, dataset, or candidate fails acceptance.
