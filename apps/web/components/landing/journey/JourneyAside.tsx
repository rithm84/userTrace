'use client';

import { ArrowDown } from 'lucide-react';
import type { MotionValue } from 'motion/react';
import type { JSX, ReactNode } from 'react';

import { PANEL_CUE, STRIP_SEGMENTS } from '../strip/cues';
import { PANEL_ROWS, PANELS } from '../strip/stripGeometry';
import { marksProgress } from './scrollTimeline';
import { useShownScroll } from './useScrollTimeline';

/** Where the page goes past the journey. */
export const AFTER_JOURNEY_HREF = '#where-it-helps';

/**
 * The column beside a journey page: in the stage's right margin when the page leaves 108px for
 * it, and over the page's bottom-right corner otherwise.
 */
export function JourneyAside(props: Readonly<{ left: number | null; children: ReactNode }>): JSX.Element {
  return <div className={'v3-aside' + (props.left === null ? ' v3-aside--corner' : '')} style={props.left === null ? undefined : { left: props.left }}>
    {props.children}
  </div>;
}

/** The way past the strip and the story for a reader who does not want to scroll through them. */
export function SkipAhead(): JSX.Element {
  return <a className="lp-btn lp-btn--secondary lp-btn--sm" href={AFTER_JOURNEY_HREF} data-testid="journey-skip-ahead"><ArrowDown size={16} strokeWidth={2.2} aria-hidden="true" /> Skip ahead</a>;
}

const PANEL_MARKS = PANELS.map((shape) => PANEL_CUE[shape.id]);

/**
 * Where the reader is in the strip: a miniature of its rows of panels, cells leaning like its
 * gutters, over the count. It follows the scroll (`scroll`, px into the strip's stretch), rests
 * included, so every bit of scroll moves something even while a finished panel holds, but only
 * as far as the strip has drawn (`drawn`, ms into its timeline): it counts the panels on screen,
 * not the ones the page is still catching up to.
 */
export function StripProgress(props: Readonly<{ scroll: MotionValue<number>; drawn: number }>): JSX.Element {
  const px = useShownScroll(props.scroll, props.drawn, STRIP_SEGMENTS);
  const done = marksProgress(px, PANEL_MARKS, STRIP_SEGMENTS);
  // the count names the panels on screen, which a fill resting at a panel's end must not run past
  const label = props.drawn < PANEL_MARKS[0] ? 'Introduction' : `${PANEL_MARKS.filter((mark) => props.drawn >= mark).length} of ${PANELS.length}`;
  // the index of each row's first panel
  const starts = PANEL_ROWS.map((_, row) => PANEL_ROWS.slice(0, row).reduce((sum, count) => sum + count, 0));
  return <div className="v3-progress" data-testid="strip-progress" data-done={done.toFixed(2)} data-total={PANELS.length}>
    <span className="v3-progress-map" aria-hidden="true">
      {PANEL_ROWS.map((count, row) => <span key={row}>{Array.from({ length: count }, (_, i) => <i key={i}><b style={{ width: `${Math.max(0, Math.min(1, done - starts[row] - i)) * 100}%` }} /></i>)}</span>)}
    </span>
    <span className="v3-progress-count">{label}</span>
  </div>;
}
