/** Why users take the interview: the Case File's counterpart, told by the one bill it lightens. */
import { Check, Tag } from "lucide-react";
import type { JSX } from "react";

import { SHOPALYTICS } from "../story/shopalyticsFixture";
import { Caption } from "../pieces";
import { ShopalyticsLogo } from "../surfaces/discord";
import "./users.css";

const { offer } = SHOPALYTICS;
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const DUE = offer.invoice.amount - offer.credit;

/** What the reader should take away, one short line each. */
const PROMISES: readonly string[] = [
  "They get the change they asked for, and they sign it off.",
  "An honest no earns the same credit as a yes.",
  // naming the plan ties the invoice's credit to its plan row: one plan's reward, not a fixed price
  "Rewards scale with each customer’s plan and size.",
  "You set the budget, and uTrace never offers past it.",
];

/**
 * Dudulemon's next Shopalytics invoice on Stripe's hosted invoice page, with Maya's interview
 * credit taken off it: Stripe's own layout, type and discount chip (`users.css`).
 */
function StripeInvoice(): JSX.Element {
  return (
    <div className="sp-page" data-testid="users-invoice">
      <div className="sp-merchant">
        <ShopalyticsLogo size={28} />
        Shopalytics
      </div>
      <div className="sp-card">
        <p className="sp-amount">{usd.format(DUE)}</p>
        <p className="sp-soft">Due {offer.invoice.due}</p>
        <dl className="sp-parties">
          <dt>To</dt>
          <dd>Dudulemon</dd>
          <dt>From</dt>
          <dd>Shopalytics</dd>
        </dl>
      </div>
      <div className="sp-card">
        <p className="sp-soft sp-number">Invoice {offer.invoice.number}</p>
        <div className="sp-line">
          <span>
            {offer.invoice.plan}
            <small>{offer.invoice.period}</small>
          </span>
          <span>{usd.format(offer.invoice.amount)}</span>
        </div>
        <div className="sp-line sp-rule">
          <span>Subtotal</span>
          <span>{usd.format(offer.invoice.amount)}</span>
        </div>
        <div className="sp-line">
          <span>
            <span className="sp-chip">
              <Tag size={13} aria-hidden="true" />
              Interview credit
            </span>
            <small>Applied from Dudulemon&rsquo;s balance</small>
          </span>
          <span className="sp-soft">−{usd.format(offer.credit)}</span>
        </div>
        <div className="sp-line sp-rule sp-total">
          <span>Amount due</span>
          <span>{usd.format(DUE)}</span>
        </div>
        <span className="sp-pay">Pay {usd.format(DUE)}</span>
      </div>
      <p className="sp-footer">
        Powered by <b>stripe</b>
        <i aria-hidden="true" />
        Terms
        <span>Privacy</span>
      </p>
    </div>
  );
}

/**
 * For your users: why the originating user says yes. One bill carries it: Maya's interview
 * took its credit off Dudulemon's Shopalytics invoice. Three short lines say the rest.
 */
export function ForYourUsers(): JSX.Element {
  return (
    <section className="fu-section" aria-labelledby="users-title" data-testid="landing-users" id="for-your-users">
      <div className="lp-wrap fu-layout">
        <figure className="fu-crop">
          <Caption on style={{ left: -14, top: -20, maxWidth: 270 }}>
            Maya&rsquo;s interview took ${offer.credit} off Dudulemon&rsquo;s bill.
          </Caption>
          <StripeInvoice />
        </figure>
        <div className="fu-intro">
          <p className="lp-kicker">For your users</p>
          <h2 className="lp-h2" id="users-title">
            Why they
            <br />
            say yes.
          </h2>
          <p className="lp-lede">With Outreach Offers, every interview earns the customer (or their organization) a credit on their bill, through Stripe or your product&rsquo;s own credits.</p>
          <ul className="fu-promises">
            {PROMISES.map((promise) => (
              <li key={promise}>
                <span aria-hidden="true">
                  <Check size={14} strokeWidth={3} />
                </span>
                {promise}
              </li>
            ))}
          </ul>
          <p className="fu-note">Illustrative example.</p>
        </div>
      </div>
    </section>
  );
}
