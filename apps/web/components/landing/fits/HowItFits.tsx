/** The setup checklist: four bays in the order the page told the case, with Customer Surface selection. */
"use client";

import { ArrowUp, Check } from "lucide-react";
import { useState, type JSX, type ReactNode } from "react";

import { PlatformPile } from "./PlatformPile";
import type { Platform } from "./platforms";
import "./fits.css";

const SURFACES_CAPTION = "Community servers, support systems, app reviews, public forums, social platforms, in-product feedback, customer calls, issue trackers.";

type SetupRow = Readonly<{ title: string; detail: string }>;

const PRODUCT_ROWS: readonly SetupRow[] = [
  { title: "The uTrace SDK", detail: "It runs only in previews, and shows what your user did and what your backend did about it." },
  { title: "Code access", detail: "Install the uTrace GitHub app on the repository you choose." },
  { title: "A safe preview copy", detail: "A copy of your app on seeded data, where interviews and changes happen away from production." },
];

const REWARD_ROWS: readonly SetupRow[] = [
  { title: "Stripe, or your own credits", detail: "Thank-yous land as invoice credits through Stripe, or in your product's own credit system." },
  { title: "A budget you set", detail: "uTrace sets each reward aside before it invites anyone, and never spends past it." },
];

const RULE_ROWS: readonly SetupRow[] = [
  { title: "What agents may change", detail: "Allowed paths and blocked code surfaces, fixed before a case starts." },
  { title: "Accounts only humans interview", detail: "Mark Sensitive Accounts, and uTrace leaves them to your team." },
  { title: "Required checks", detail: "The tests every change has to pass before a user sees it." },
];

/** A bay's checklist: what that connection takes, each item ticked. */
function SetupRows(props: Readonly<{ rows: readonly SetupRow[] }>): JSX.Element {
  return (
    <ul className="fits-rows">
      {props.rows.map((row) => (
        <li className="fits-row" key={row.title}>
          <span className="fits-check" aria-hidden="true">
            <Check size={14} strokeWidth={3} />
          </span>
          <div>
            <h4>{row.title}</h4>
            <p>{row.detail}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** One numbered bay of the setup checklist. */
function Bay(props: Readonly<{ number: string; id: string; title: string; children: ReactNode }>): JSX.Element {
  return (
    <article className="fits-bay" data-testid={`fits-bay-${Number(props.number)}`} aria-labelledby={props.id}>
      <h3 className="fits-bay-title" id={props.id}>
        <span>{props.number}</span>
        {props.title}
      </h3>
      {props.children}
    </article>
  );
}

export function HowItFits(): JSX.Element {
  const [selected, setSelected] = useState<Platform | null>(null);

  return (
    <section className="fits-section" aria-labelledby="fits-title" data-testid="landing-fits" id="integrations">
      <div className="lp-wrap">
        <header className="fits-heading">
          <p className="lp-kicker">Fits into your world</p>
          <h2 className="lp-h2" id="fits-title">Four things to connect.</h2>
          <p className="lp-lede">uTrace plugs into what you already have, in the order you just watched it work.</p>
        </header>
        <div className="fits-workbench">
          <Bay number="01" id="fits-surfaces-title" title="Where your customers talk">
            <div className="fits-surfaces-body">
              <PlatformPile selectedId={selected?.id ?? null} onSelect={setSelected} />
              <p className="fits-caption" id="fits-caption" data-testid="fits-caption" aria-live="polite" aria-atomic="true">
                {selected?.caption ?? SURFACES_CAPTION}
              </p>
            </div>
          </Bay>
          <Bay number="02" id="fits-product-title" title="Your product">
            <SetupRows rows={PRODUCT_ROWS} />
          </Bay>
          <Bay number="03" id="fits-rewards-title" title="Rewards">
            <SetupRows rows={REWARD_ROWS} />
            <a className="fits-back" href="#for-your-users" data-testid="fits-rewards-back">
              <ArrowUp size={16} strokeWidth={2.4} aria-hidden="true" />
              What your users get
            </a>
          </Bay>
          <Bay number="04" id="fits-rules-title" title="Your rules">
            <SetupRows rows={RULE_ROWS} />
          </Bay>
        </div>
      </div>
    </section>
  );
}
