# Outreach Offers and invoice credits

[Back to uTrace architecture](../ARCHITECTURE.md)

Offer authorization, earned credit, and its effect on billing belong to separate
owners:

![An authorized offer earns one Stripe invoice credit after required participation; unearned offers release their allowance.](../diagrams/svg/outreach-offers.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Product and provider boundary

The [PRD](../PRD.md#outreach-offers) owns the product terms: one offer per case,
credit for the originating user's customer organization, the participation
requirements, how a case's end decides the reward, and the uTrace-controlled
Stripe sandbox that stands in for the client's billing account. Stripe's
[customer credit balance](https://docs.stripe.com/invoicing/customer/balance)
applies available credit to the next finalized invoice and carries unused credit
forward. No usage meter is required, and uTrace never expires earned credits.

## Customer mapping

The client configuration maps the client's `customer_account_id` to one existing
Stripe Customer in the sandbox billing account, with a compatible billing
currency. Every offer uses that recorded mapping, never email similarity, a model
guess, or a participant-supplied Stripe ID, and includes the account,
environment, Customer ID, mapping revision, and currency. Participants need no
Stripe login or bank onboarding.

Issuance first rechecks the mapping and customer availability. Changed, deleted,
or ambiguous billing identities block it for reconciliation without redirecting
the reward or erasing its entitlement.

Sandbox credentials remain in server-side credential custody, and the adapter
never creates charges, finalizes invoices, issues refunds, or moves funds.

## Offer and issuance states

Offers and their credit issuance move through these states:

![A reserved offer becomes earned or released; an earned offer's issuance moves from pending through dispatched to issued, or to blocked until reconciled.](../diagrams/svg/outreach-offers-lifecycle.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Budget and offer reservations

The owner authorizes a client and currency issuance ceiling that limits the face
value of credits uTrace may issue, with no prefunding, cash custody, or balance
transfer. The fixed offer amount, currency, billing use, and completion terms
come from the
[client configuration release](./client-configuration.md#immutable-configuration-releases),
not the dashboard, and models cannot negotiate amounts, increase allowance, or
change recipients.

The full promised credit is reserved atomically with the invitation intent,
binding one offer, Product Signal, customer organization, billing mapping,
amount, currency, and completion terms. Existing rewards and reservations count
against the ceiling, so concurrent invitations cannot exceed the remaining
allowance.

- Exhausted allowance blocks a new rewarded invitation with an actionable status.
- Unearned offers release their reservation after outreach and participation are
  reconciled.
- Earned or issued amounts keep consuming allowance, including after invoice
  application.
- Only explicit owner authorization increases the budget, and edits cannot cancel
  outstanding promises or silently reset consumed allowance.

## Completion and entitlement

Evaluate the [PRD's earning rules](../PRD.md#outreach-offers) from recorded
participation, gate decisions, validation results, and the reason work ended. A
session ending, a Case File existing, or an agent saying "done" is not sufficient
evidence, and eligibility is never assigned solely from `closed_no_implementation`,
`closed_blocked`, or `canceled`.

- Failing to return within the return deadline leaves participation incomplete.
- A client Stop case is system-initiated, not participant cancellation. If
  requests made before it took effect remain incomplete, eligibility records the
  outstanding participation rather than assuming forfeiture from the stop label.
- Temporary disconnection and a resumable blocker leave eligibility pending.

At the final required participation milestone, atomically record the decision and
evidence, convert the reservation into an earned credit obligation or release it,
and enqueue eligible issuance, without waiting for PR delivery or merge. Each
offer has one entitlement.

## Issuance and reconciliation

### Allowance ledger

Keep an append-only client and currency record of authorized allowance,
reservations, releases, earned credits, and issuance results. Amounts use integer
minor units, never scaled decimals.

Each entry moves *remaining* allowance, the ceiling minus what is reserved minus
what is earned, which keeps the record free of double counting:

| Entry | Movement of remaining allowance |
| --- | --- |
| `budget_authorized` | The change in the ceiling, not the whole ceiling |
| `reservation_created` | Down by the offer amount |
| `reservation_released` | Up by the offer amount |
| `credit_earned` | Zero: the reservation already debited it; conversion only changes the accounting column |
| `credit_issued`, `issuance_blocked` | None; they only record the operation |

Setting the ceiling below what is reserved plus earned is refused with the
consumed amount, never accepted and reconciled later. Each
operation carries a stable identity, causal evidence, and scoped Stripe
references. Stripe owns the billing balance; uTrace owns its promises and issuance
records, separately from the immutable Case File core.

### Issuing a credit

The backend issues earned credit by
[creating a customer balance transaction](https://docs.stripe.com/api/customer_balance_transactions/create)
with a negative amount in the agreed currency. It records the returned
transaction ID and verifies customer, account, environment, amount, and currency.
The adjustment adds to the existing balance, never overwriting a total that may
include other credits or debits. Metadata carries stable offer and operation
references, never interview content or personal evidence.

### Retries and reconciliation

Retries reuse the same operation and
[idempotency key](https://docs.stripe.com/api/idempotent_requests), but Stripe can
prune keys after at least 24 hours. Ambiguous responses therefore reconcile
against the customer's transaction history and operation metadata before
redispatch, especially outside that window, and a slow confirmation never
justifies a fresh adjustment.

Repairs to credentials and mappings never ask the participant to repeat completed
work, and case cleanup keeps the minimized records needed to reconcile
outstanding credits. uTrace never reverses an earned credit for a negative
verdict, cancellation after completion, or non-use.

### Application to invoices

A verified adjustment means the credit is issued, not necessarily consumed.
Stripe applies the customer's aggregate credit balance as invoices finalize; the
demo harness finalizes the test invoice that shows the credit applied.
Application needs no other customer session and never reopens the case.

## Verification and fixture

The [evaluation contract](./evaluation-and-observability.md#outreach-offers-verification)
owns reservation, entitlement, issuance, and recovery tests.
[SCENARIO.md](../SCENARIO.md#starting-state-and-fixture) owns the prepared
billing account, customer, and invoice fixture. Use a
[Stripe sandbox](https://docs.stripe.com/testing-use-cases) with visibly simulated
credits and invoices. Sandbox credentials and fixture-only invoice controls never
grant live billing authority to the product preview or the adapter.
