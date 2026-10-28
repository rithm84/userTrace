"use client";

import { createContext, useContext, useEffect, useRef, useState, type JSX, type ReactNode } from "react";
import { ArrowUpRight, Check } from "lucide-react";
import { NAV_H } from "./journey/contract";
import "./waitlist.css";

export const CONTACT_HREF = "mailto:rithm84@utrace.dev";

type Placement = "hero" | "closing";
type WaitlistState = Readonly<{ joinedFrom: Placement | null; join: (placement: Placement) => void }>;
const WaitlistContext = createContext<WaitlistState | null>(null);

/** Both entry points share one visit's confirmation; no email is stored or sent. */
export function WaitlistProvider(props: Readonly<{ children: ReactNode }>): JSX.Element {
  const [joinedFrom, join] = useState<Placement | null>(null);
  return <WaitlistContext value={{ joinedFrom, join }}>{props.children}</WaitlistContext>;
}

/** Validated email entry and its local confirmation, ready for a future waitlist service. */
export function Waitlist(props: Readonly<{ id: Placement }>): JSX.Element {
  const state = useContext(WaitlistContext);
  const result = useRef<HTMLDivElement>(null);
  const submittedHere = useRef(false);
  if (state === null) throw new Error("Waitlist must be rendered inside WaitlistProvider.");
  const { joinedFrom, join } = state;
  useEffect(() => {
    if (joinedFrom === null || !submittedHere.current) return;
    submittedHere.current = false;
    result.current?.focus({ preventScroll: true });
  }, [joinedFrom]);
  return <div className={`lp-waitlist lp-waitlist--${props.id}`} data-testid={`${props.id}-waitlist`}>
    {joinedFrom !== null ? <div ref={result} className="lp-waitlist-bar lp-waitlist-success" tabIndex={-1} role={joinedFrom === props.id ? "status" : undefined} data-waitlist-target={props.id} data-testid={`${props.id}-waitlist-success`}>
      <span className="lp-waitlist-confirmation">We’ll be in touch.</span>
      <span className="lp-waitlist-submit lp-waitlist-joined"><span>Joined</span><Check aria-hidden="true" size={21} strokeWidth={3} /></span>
    </div> : <form className="lp-waitlist-bar" data-testid={`${props.id}-waitlist-form`} onSubmit={(event) => { event.preventDefault(); submittedHere.current = true; join(props.id); }}>
      <label className="sr-only" htmlFor={`${props.id}-waitlist-email`}>Email address</label>
      <input id={`${props.id}-waitlist-email`} data-testid={`${props.id}-waitlist-email`} data-waitlist-target={props.id} name="email" type="email" autoComplete="email" placeholder="Your email address" required />
      <button className="lp-waitlist-submit" type="submit" data-testid={`${props.id}-waitlist-submit`}><span>Join<span className="lp-waitlist-more"> waitlist</span><span className="lp-waitlist-bang" aria-hidden="true">!</span></span><ArrowUpRight aria-hidden="true" size={21} strokeWidth={2.5} /></button>
    </form>}
  </div>;
}

/** Prefer the unlocked form already on screen; otherwise reveal and focus the hero's email field. */
export function focusLandingWaitlist(): void {
  const closing = document.querySelector('[data-waitlist-target="closing"]');
  const hero = document.querySelector('[data-waitlist-target="hero"]');
  if (!(closing instanceof HTMLElement) || !(hero instanceof HTMLElement)) {
    throw new Error("Cannot open the waitlist: the hero or closing form or confirmation is missing.");
  }
  const bounds = closing.getBoundingClientRect();
  if (closing.closest('[data-phase="settled"]') !== null && bounds.top >= NAV_H && bounds.bottom <= window.innerHeight && bounds.left >= 0 && bounds.right <= window.innerWidth) {
    closing.focus({ preventScroll: true });
    return;
  }
  hero.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "instant" });
}
