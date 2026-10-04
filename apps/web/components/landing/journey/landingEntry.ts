import { JOURNEY, STORY_SCROLL_END, STORY_SCROLL_START, STRIP_SCROLL_END } from './contract';

/**
 * Where a restored scroll position should land. The strip's and the story's scroll are their
 * pages' state, so a position inside either stays; the zoom between them snaps to its nearer
 * end, and anywhere else snaps to the start of the nearest section.
 */
export function landingTop(journey: HTMLElement, reducedMotion: boolean): number {
  const origin = journey.offsetTop - JOURNEY.navH;
  const y = window.scrollY;
  const inside = (from: number, to: number): boolean => y >= origin + from && y <= origin + to;
  if (!reducedMotion && (inside(JOURNEY.heroPx, STRIP_SCROLL_END) || inside(STORY_SCROLL_START, STORY_SCROLL_END))) return y;
  const topOf = (element: HTMLElement): number => element.getBoundingClientRect().top + y - JOURNEY.navH;
  const strip = journey.querySelector<HTMLElement>('.v3-static-strip');
  const story = journey.querySelector<HTMLElement>('.st-stills');
  if (reducedMotion && (strip === null || story === null)) {
    throw new Error('Cannot restore the landing section: the reduced-motion strip or story is missing.');
  }
  const later = document.querySelectorAll<HTMLElement>('[data-landing] main > section:not(#landing-journey), [data-landing] footer');
  const stops = [
    0,
    strip !== null ? topOf(strip) : origin + JOURNEY.heroPx,
    ...(strip !== null ? [] : [origin + STRIP_SCROLL_END]),
    story !== null ? topOf(story) : origin + STORY_SCROLL_START,
    ...[...later].map(topOf),
  ];
  const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
  return stops
    .map((top) => Math.max(0, Math.min(maxScroll, top)))
    .reduce((nearest, top) => Math.abs(top - y) < Math.abs(nearest - y) ? top : nearest);
}
