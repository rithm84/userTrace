import type { JSX } from "react";

import { Chip, EmptyState, StatTile, formatMinorUnits, humanize } from "@/components/product/ui";
import type { OffersView } from "@/lib/api/schemas";
import { REFERENCE_KIND_CUSTOMER_ACCOUNT, opaqueReference } from "@/lib/api/schemas";
import { labelFor, type ReferenceLabels } from "@/lib/dashboard/references";

/**
 * The Outreach Offers view: the client-authorized issuance allowance, what is
 * reserved against it, what remains, what participants have earned, and where
 * each credit stands. Invoice application is shown only where Stripe has
 * reconciled it, because an issued credit is not a consumed one.
 */
export function OffersPanel(
  props: Readonly<{ offers: OffersView; labels: ReferenceLabels }>,
): JSX.Element {
  const { offers } = props;
  if (offers.currency === undefined) {
    return (
      <EmptyState testId="offers-unauthorized">
        No issuance allowance has been authorized, so uTrace offers no invoice credit with an
        invitation.
      </EmptyState>
    );
  }
  const currency = offers.currency;
  const amount = (minorUnits: number): string => formatMinorUnits(minorUnits, currency);

  return (
    <div className="pr-stack" data-testid="offers-panel" data-currency={currency}>
      <div className="pr-grid">
        <StatTile
          term="Authorized allowance"
          value={amount(offers.authorized_minor_units)}
          detail={null}
          testId="offers-authorized"
        />
        <StatTile
          term="Reserved"
          value={amount(offers.reserved_minor_units)}
          detail="Held against open invitations"
          testId="offers-reserved"
        />
        <StatTile
          term="Remaining"
          value={amount(offers.remaining_minor_units)}
          detail="Available for new offers"
          testId="offers-remaining"
        />
        <StatTile
          term="Earned credits"
          value={amount(offers.earned_minor_units)}
          detail="Owed once participation completed"
          testId="offers-earned"
        />
      </div>
      {offers.offers.length === 0 ? (
        <EmptyState testId="offers-empty">No offer has been made yet.</EmptyState>
      ) : (
        <ul className="pr-list" data-testid="offer-list">
          {offers.offers.map((offer) => (
            <li key={offer.outreach_offer_id}>
              <div className="pr-card" data-testid={`offer-card-${offer.outreach_offer_id}`}>
                <p className="pr-card-title">
                  {labelFor(
                    props.labels,
                    opaqueReference(REFERENCE_KIND_CUSTOMER_ACCOUNT, offer.customer_account_id),
                  )}{" "}
                  · {formatMinorUnits(offer.amount_minor_units, offer.currency)}
                </p>
                <div className="pr-card-meta">
                  <Chip
                    tone={offer.state === "earned" ? "active" : "neutral"}
                    testId={`offer-state-${offer.outreach_offer_id}`}
                  >
                    {humanize(offer.state)}
                  </Chip>
                  <span data-testid={`offer-issuance-${offer.outreach_offer_id}`}>
                    {offer.issuance_state === undefined
                      ? "Issuance not due"
                      : humanize(offer.issuance_state)}
                  </span>
                  {offer.issuance_blocked_reason === undefined ? null : (
                    <span data-testid={`offer-blocked-${offer.outreach_offer_id}`}>
                      {offer.issuance_blocked_reason}
                    </span>
                  )}
                  {offer.provider_transaction_ref === undefined ? null : (
                    <span className="pr-mono">{offer.provider_transaction_ref}</span>
                  )}
                  {offer.invoice_application_minor_units === undefined ? (
                    <span data-testid={`offer-unapplied-${offer.outreach_offer_id}`}>
                      Not yet reconciled against an invoice
                    </span>
                  ) : (
                    <span data-testid={`offer-applied-${offer.outreach_offer_id}`}>
                      {formatMinorUnits(offer.invoice_application_minor_units, offer.currency)}{" "}
                      applied to an invoice
                    </span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
