'use client';

import { useCallback, useEffect, useRef, useState, type JSX } from 'react';
import { preload } from 'react-dom';
import { useReducedMotionPreference } from '@/components/agent/useReducedMotion';
import { CITY_RIG_URLS } from '../city/cast';
import { Hero } from '../hero/Hero';
import { Demo } from '../story/Demo';
import './journey.css';

const PRELOADED = ['/agent/rive.wasm', ...CITY_RIG_URLS] as const;

/**
 * The top of the page: the hero, then the product demo. The hero is gated on its own parts (the
 * city's rigs) so it never appears in pieces, its city runs only while it is on screen, and the
 * demo mounts only after it, so the hero's assets load first.
 */
export function LandingJourney(): JSX.Element {
  const heroRef = useRef<HTMLDivElement>(null);
  const preference = useReducedMotionPreference();
  const reducedMotion = preference.resolved && preference.reducedMotion;
  const [heroReady, setHeroReady] = useState(false);
  const [heroOnScreen, setHeroOnScreen] = useState(false);
  const onHeroReady = useCallback((): void => setHeroReady(true), []);
  for (const href of PRELOADED) preload(href, { as: 'fetch', crossOrigin: 'anonymous' });
  useEffect(() => {
    const hero = heroRef.current;
    if (hero === null) throw new Error('Cannot watch the hero: its stage is missing.');
    const onScreen = new IntersectionObserver(([entry]) => setHeroOnScreen(entry.isIntersecting));
    onScreen.observe(hero);
    return () => onScreen.disconnect();
  }, []);
  return <div id="landing-journey" data-testid="landing-journey">
    <div ref={heroRef} data-testid="journey-stage" className={'v3-stage v3-reveal' + (heroReady || reducedMotion ? ' is-ready' : '')}>
      <Hero live={heroOnScreen && heroReady && !reducedMotion} still={reducedMotion} onReady={onHeroReady} />
    </div>
    {preference.resolved && (heroReady || reducedMotion) ? <Demo reducedMotion={reducedMotion} /> : null}
  </div>;
}
