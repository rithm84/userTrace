import { STORY_SEGMENTS } from '../story/beats';
import { STRIP_SEGMENTS } from '../strip/cues';
import { scrollLength } from './scrollTimeline';

export type StageSize = Readonly<{ width: number; height: number }>;

/**
 * Scroll drives the whole journey: it lifts the hero off the strip, plays the strip (the
 * scroll presses Join interview), zooms from the last panel into the story, then plays the
 * story. Each page's scroll comes from its timeline at the shared pace (`scrollTimeline.ts`).
 */
export const JOURNEY = Object.freeze({ navH: 72, heroPx: 900, transitionPx: 900, storyHoldPx: 300 });
/** Where the strip's scroll ends and the zoom into the story begins, relative to the section's top. */
export const STRIP_SCROLL_END = JOURNEY.heroPx + scrollLength(STRIP_SEGMENTS);
/** Where the zoom into the story completes and the story's scroll begins, relative to the section's top. */
export const STORY_SCROLL_START = STRIP_SCROLL_END + JOURNEY.transitionPx;
/** Where the story's scroll ends, relative to the section's top. */
export const STORY_SCROLL_END = STORY_SCROLL_START + scrollLength(STORY_SEGMENTS);
/** The strip's page is what the reader sees: the hero all but lifted and the zoom barely begun. */
export const STRIP_STAGE = Object.freeze({ lifted: 0.92, transition: 0.12 });
export const clamp01 = (value: number): number => Math.min(1, Math.max(0, value));
export function segment(value: number, from: number, to: number): number {
  if (to <= from) throw new RangeError('segment requires from < to.');
  return clamp01((value - from) / (to - from));
}
export function smooth(value: number): number {
  const t = clamp01(value);
  return t * t * (3 - 2 * t);
}
