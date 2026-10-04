/**
 * A scroll-driven page's timeline, shared by the strip and the story.
 *
 * A page's timeline is cut into segments (the strip's introduction and panels, the story's
 * beats), given as their start times followed by the page's end. Scroll plays each segment
 * at `SCROLL_PX_PER_MS` of scroll per millisecond of authored time, then rests for
 * `REST_PX` on its last frame before the next segment begins, so a flick's momentum tail
 * settles on a finished panel rather than carrying the reader into the next one.
 */

/** Scroll per millisecond of authored time: 150px per second of the story. */
export const SCROLL_PX_PER_MS = 0.15;
/** The scroll after each segment during which the page holds its finished frame. */
export const REST_PX = 320;
/** The pace, relative to authored, at which the page closes a small gap to the scroll. */
export const CATCH_UP_RATE = 2;
/** A larger gap closes faster: at about the pace that would close it within this long. */
export const CATCH_UP_WINDOW_MS = 1000;
/**
 * The least real time the page spends in each panel or beat it passes while catching up from
 * far behind: enough for a panel's border to draw on before the next one arrives, so panels
 * always arrive one at a time.
 */
export const MIN_SEGMENT_MS = 450;
/** The longest frame the catch-up credits, so a stalled tab does not leap on its next frame. */
const MAX_FRAME_MS = 100;

function checkSegments(segments: readonly number[]): void {
  if (segments.length < 2 || segments[0] !== 0) throw new RangeError(`A timeline needs segment starts from 0 and an end; got ${segments.join(", ")}.`);
  for (let i = 1; i < segments.length; i++) {
    if (!(segments[i] > segments[i - 1])) throw new RangeError(`Timeline segments must increase; got ${segments.join(", ")}.`);
  }
}

/** The scroll a page's timeline takes: every segment played through, then its rest. */
export function scrollLength(segments: readonly number[]): number {
  checkSegments(segments);
  const duration = segments[segments.length - 1];
  return duration * SCROLL_PX_PER_MS + (segments.length - 1) * REST_PX;
}

/**
 * The moment of the timeline `px` of scroll into the page. During a segment's rest it holds
 * the segment's last frame (just short of the next segment's start); the last rest holds
 * the finished page.
 */
export function timeAtScroll(px: number, segments: readonly number[]): number {
  checkSegments(segments);
  let start = 0;
  for (let index = 0; index < segments.length - 1; index++) {
    const length = (segments[index + 1] - segments[index]) * SCROLL_PX_PER_MS;
    if (px < start + length) return segments[index] + Math.max(0, px - start) / SCROLL_PX_PER_MS;
    start += length;
    const last = index === segments.length - 2;
    if (px < start + REST_PX) return last ? segments[index + 1] : segments[index + 1] - 1;
    start += REST_PX;
  }
  return segments[segments.length - 1];
}

/** The segment a moment falls in; the page's end belongs to the last one. */
export function segmentAt(ms: number, segments: readonly number[]): number {
  const index = segments.findLastIndex((start) => ms >= start);
  return Math.min(Math.max(index, 0), segments.length - 2);
}

/**
 * One frame of the page catching up with the moment the scroll asks for.
 *
 * On screen (`watching`), the page plays toward the target in either direction, so a slow
 * scroll scrubs, a brisk one plays the beats quickly, and a still reader stops the page. A
 * small gap closes at `CATCH_UP_RATE` times the authored pace; a larger one faster, easing in
 * like a spring (`CATCH_UP_WINDOW_MS`), but never so fast that a segment takes less than
 * `MIN_SEGMENT_MS`: the page never skips a panel or beat, so they arrive (or leave) one at a
 * time rather than two at once. Off screen there is nothing to show, so it lands at once.
 */
export function catchUp(shown: number, target: number, frameMs: number, segments: readonly number[], watching: boolean): number {
  if (!watching) return target;
  const from = segmentAt(shown, segments);
  const fastest = Math.max(CATCH_UP_RATE, (segments[from + 1] - segments[from]) / MIN_SEGMENT_MS);
  const rate = Math.min(fastest, Math.max(CATCH_UP_RATE, Math.abs(target - shown) / CATCH_UP_WINDOW_MS));
  const step = Math.min(frameMs, MAX_FRAME_MS) * rate;
  return target > shown ? Math.min(target, shown + step) : Math.max(target, shown - step);
}

/**
 * The moment a page draws: the scroll's moment, except that a segment never goes back below the
 * furthest point of it already drawn on screen (`watched`, one moment per segment). Scrolling
 * back still moves between panels or beats, but the one on screen holds what the reader already
 * saw instead of playing it backwards; one entered from behind shows its finished frame.
 */
export function heldMoment(moment: number, watched: readonly number[], segments: readonly number[]): number {
  if (watched.length !== segments.length - 1) throw new RangeError(`Need one watched moment per segment (${segments.length - 1}); got ${watched.length}.`);
  return Math.max(moment, watched[segmentAt(moment, segments)]);
}

/** The scroll into a page at which its timeline first reaches `ms`. */
export function scrollAtTime(ms: number, segments: readonly number[]): number {
  checkSegments(segments);
  const index = segmentAt(ms, segments);
  const before = segments[index] * SCROLL_PX_PER_MS + index * REST_PX;
  return before + (Math.min(ms, segments[index + 1]) - segments[index]) * SCROLL_PX_PER_MS;
}

/**
 * The scroll a page's progress shows while the page draws `drawn`: the reader's scroll `px`
 * wherever that scroll shows the same moment (a segment's rest shows its last frame, so a rest
 * still moves the progress), and otherwise the scroll at the moment drawn. The progress never
 * runs ahead of a page still catching up, nor behind a finished segment the page holds.
 */
export function shownScroll(px: number, drawn: number, segments: readonly number[]): number {
  const index = segmentAt(drawn, segments);
  const last = index === segments.length - 2;
  const from = scrollAtTime(drawn, segments);
  const resting = drawn >= (last ? segments[index + 1] : segments[index + 1] - 1);
  const to = resting ? segments[index + 1] * SCROLL_PX_PER_MS + (index + 1) * REST_PX : from;
  return Math.min(Math.max(px, from), to);
}

/**
 * How far the scroll `px` has come through `marks` (ascending moments, such as the panels'
 * arrivals), as a continuous count: 0 before the first mark, then k plus the fraction of the
 * scroll from mark k to the next one, or to the end of the page's last segment after the last
 * mark, so the count is full once the page has finished. A rest counts as part of the stretch
 * it follows, so a reader scrolling through it still sees progress move.
 */
export function marksProgress(px: number, marks: readonly number[], segments: readonly number[]): number {
  if (marks.length === 0) throw new RangeError("A progress count needs at least one mark.");
  const stops = [...marks.map((mark) => scrollAtTime(mark, segments)), scrollAtTime(segments[segments.length - 1], segments)];
  if (px < stops[0]) return 0;
  const reached = Math.min(stops.findLastIndex((stop) => px >= stop), marks.length - 1);
  const span = stops[reached + 1] - stops[reached];
  return reached + Math.min(1, (px - stops[reached]) / span);
}
