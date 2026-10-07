# Outreach Offers and invoice credits

[Back to uTrace architecture](../ARCHITECTURE.md)

The diagram separates offer authorization, earned credit, and its effect on billing.

![An authorized offer earns one Stripe invoice credit after required participation; unearned offers release their allowance.](../diagrams/svg/outreach-offers.svg)

## Product and provider boundary

The [PRD](../PRD.md#outreach-offers) owns participation requirements. One offer
covers one case and rewards the originating user's customer organization with
credit toward the client's product bills. The demo issues Stripe customer invoice
credits in a uTrace-controlled Stripe sandbox that stands in for the client's
billing account. Stripe's
[customer credit balance](https://docs.stripe.com/invoicing/customer/balance)
applies available credit to the next finalized invoice and carries unused credit
forward. No usage meter is required, and uTrace does not expire earned credits.

## Customer mapping

The client configuration maps the client's `customer_account_id` to one existing
Stripe Customer in the sandbox billing account, with a compatible billing
currency. Use that recorded mapping, not email similarity, a model guess, or a
participant-supplied Stripe ID. Include the account, environment, Customer ID,
mapping revision, and currency in every offer. Participants need no Stripe login
or bank onboarding. Sandbox credentials remain in server-side credential custody,
and the adapter never creates charges, finalizes invoices, issues refunds, or moves
funds.

Recheck the mapping and customer availability before issuance. Changed, deleted,
or ambiguous billing identities block issuance for reconciliation; they do not
redirect the reward or erase its entitlement.

## Budget and offer reservations

The owner authorizes a client/currency issuance ceiling. Owners and admins can
configure fixed offer amounts and eligibility within it. The budget limits the
face value of credits uTrace may issue; no prefunding, cash custody, or balance
transfer is involved. Offer policy is pinned with the case's configuration.
Models cannot negotiate amounts, increase allowance, or change recipients.

Reserve the full promised credit and commit invitation intent atomically. Each
reservation binds one offer, Product Signal, customer organization, billing
mapping, amount, currency, and completion terms. Concurrent invitations cannot
exceed the remaining allowance. Existing rewards and reservations count against
the ceiling; return reminders reuse the original offer.

Exhausted allowance blocks a new rewarded invitation with an actionable status.
Unearned offers release their reservation after outreach and participation are
reconciled. Earned or issued amounts continue to consume allowance, including
after invoice application. An owner must explicitly authorize any increase; budget
edits cannot cancel outstanding promises or silently reset consumed allowance.

## Completion and entitlement

Evaluate the [PRD's completion conditions](../PRD.md#outreach-offers) from recorded
participation, gate decisions, validation results, and the
reason work ended. A session ending, a Case File existing, or an agent saying
“done” is not sufficient evidence.

A participant who fails to return within the return deadline has not completed
participation. A system-caused stop qualifies
when they fulfilled every request up to that point. Do not assign eligibility
solely from `closed_no_implementation`, `closed_blocked`, or `canceled`.
Pre-participation termination or voluntary withdrawal before completion earns
nothing. Temporary disconnection and a resumable blocker leave eligibility pending.

A client Stop case is a system-initiated stop, not participant cancellation. Apply
the same fulfillment rule as other system-caused stops: if the participant
completed every request made before Stop took effect, the reward is earned;
otherwise eligibility records the outstanding participation rather than assuming
forfeiture from the stop label alone.

At the final required participation milestone, atomically record the decision and
evidence, convert the reservation into an earned credit obligation or release it,
and enqueue eligible issuance. Do not wait for PR delivery or merge. Enforce one
entitlement per offer. Negative validation cannot itself deny the reward.

## Issuance and reconciliation

Keep an append-only client/currency record of authorized allowance, reservations,
releases, earned credits, and issuance results. Amounts use integer minor units
and are never represented as scaled decimals.

A ledger entry's movement is the change in *remaining* allowance, where remaining
is the authorized ceiling minus what is reserved minus what is earned. That
definition is what keeps the record honest about double counting:

- Authorizing a budget moves the ledger by the change in the ceiling, not by the
  whole ceiling.
- A reservation moves remaining down by the offer amount; a release moves it back
  up.
- Converting a reservation into an earned credit moves nothing. The reservation
  already debited remaining, and conversion only changes which column accounts
  for it, so the earned entry is recorded with a zero movement.
- Issuance and issuance failure record their operation without moving allowance
  at all.

A ceiling may not be set below what is already reserved plus earned; that request
is refused with the consumed amount rather than accepted and reconciled later.
Each operation carries a stable identity, causal evidence, and scoped Stripe
references. Stripe owns the billing balance; uTrace owns its promises and issuance
records, separately from the immutable Case File core.

The backend issues earned credit by [creating a customer balance transaction](https://docs.stripe.com/api/customer_balance_transactions/create)
with a negative amount in the agreed currency. Record the returned transaction ID
and verify customer, account, environment, amount, and currency. Add the adjustment
to the existing balance; never overwrite the total, which may include other
credits or debits. Attach stable offer and operation references as metadata,
without interview content or personal evidence.

Persist the Command and outbox intent before calling Stripe. Retries reuse the
same operation and [idempotency key](https://docs.stripe.com/api/idempotent_requests).
Stripe can prune keys after at least 24 hours, so the key alone cannot guarantee
permanent deduplication. Reconcile ambiguous responses against the customer's
transaction history and operation metadata before redispatch, especially outside
that window. If the outcome cannot be established, block for reconciliation;
never create a fresh adjustment merely because an outcome was slow to confirm.

A verified adjustment means the credit is issued, not necessarily consumed.
Stripe applies the customer's aggregate credit balance as invoices finalize; the
demo harness finalizes the test invoice that shows the credit applied. Application
does not require another customer session and does not reopen the case.

Failures preserve the earned obligation until issuance is confirmed. Repair
credentials and mappings as needed without asking the participant to repeat
completed work. Case cleanup preserves the minimized records needed to reconcile
outstanding credits. uTrace does not reverse earned credits because of a negative
verdict, cancellation after completion, or non-use.

## Verification and fixture

The [evaluation contract](./evaluation-and-observability.md#outreach-offers-verification)
owns reservation, entitlement, issuance, and recovery tests.
[SCENARIO.md](../SCENARIO.md#starting-state-and-fixture) owns the prepared
billing account, customer, and invoice fixture. Use a
[Stripe sandbox](https://docs.stripe.com/testing-use-cases) with visibly simulated
credits and invoices. Sandbox credentials and fixture-only invoice controls never
grant live billing authority to the product preview or the adapter.
