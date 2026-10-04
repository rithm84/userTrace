'use client';

import type { JSX } from 'react';
import { City } from '../city/City';
import { Waitlist } from '../Waitlist';
import { ROTATING_WORDS, RotatingWord } from './RotatingWord';

export const HERO_SUBHEAD = 'Forward-deployed agents that investigate product feedback through user interviews, uncover underlying pain points, and build solutions those same users validate.';

/**
 * The settled hero copy over the isometric city. `live` runs the city's motion, `still` holds its
 * validated moment for reduced motion; `onReady` fires once every rig in the city has drawn, so
 * the journey can reveal the hero as a whole.
 */
export function Hero(props: Readonly<{ live: boolean; still: boolean; onReady: () => void }>): JSX.Element {
  return <section id="waitlist" aria-labelledby="hero-title" className="lp-hero v3-hero" data-testid="landing-hero">
    <div className="v3-hero-copy">
      <h1 id="hero-title" className="lp-display">
        <span className="sr-only">Ship {ROTATING_WORDS.map((entry) => entry.word).join(', ')} your customers actually want.</span>
        <span aria-hidden="true">Ship <span className="v3-word"><RotatingWord fontSize={60} /></span> your<br />customers actually want.</span>
      </h1>
      <p className="lp-lede">{HERO_SUBHEAD}</p>
      <div className="v3-hero-actions" data-testid="hero-cta-row">
        <Waitlist id="hero" />
      </div>
    </div>
    <City live={props.live} still={props.still} onReady={props.onReady} />
  </section>;
}
