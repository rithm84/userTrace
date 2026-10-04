"use client";

import { useMotionValueEvent, type MotionValue } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { catchUp, heldMoment, segmentAt, shownScroll } from "./scrollTimeline";

/** The scroll a page's progress shows (`shownScroll`): the reader's `scroll`, held to the moment the page draws. */
export function useShownScroll(scroll: MotionValue<number>, drawn: number, segments: readonly number[]): number {
  const [px, setPx] = useState(() => scroll.get());
  useMotionValueEvent(scroll, "change", setPx);
  return shownScroll(px, drawn, segments);
}

/**
 * The moment a page draws. It follows `target` (milliseconds into the timeline, set from the
 * scroll) through `catchUp`, running frames only while it has ground to make up, and it never
 * plays backwards what the reader has seen: it remembers, for each segment, the furthest point
 * drawn while the page was on screen (`watching`) and holds there on the way back
 * (`heldMoment`). Off screen, and on coming on screen, the page lands on its target without
 * playing, and what it passed there was never drawn for the reader, so it still plays when they
 * scroll back down to it. The
 * memory lasts the visit. Reduced motion reports the finished page.
 */
export function useScrollTimeline(target: MotionValue<number>, segments: readonly number[], watching: boolean, reducedMotion: boolean): number {
  const [drawn, setDrawn] = useState(() => target.get());
  const shown = useRef(drawn);
  const watched = useRef<number[]>(segments.slice(0, -1));

  useEffect(() => {
    if (reducedMotion) return;
    const see = (): void => {
      const segment = segmentAt(shown.current, segments);
      watched.current[segment] = Math.max(watched.current[segment], shown.current);
    };
    // coming on screen (a reload, a jump, the zoom opening the story), the page starts where the
    // scroll is, rather than playing up to it from wherever it last stood off screen: nothing it
    // would play was ever drawn for the reader. What is on screen then has been seen.
    if (watching) {
      shown.current = target.get();
      see();
    }
    let frame: number | null = null;
    let previous: number | null = null;
    const tick = (now: number): void => {
      const goal = target.get();
      shown.current = catchUp(shown.current, goal, previous === null ? 16 : now - previous, segments, watching);
      if (watching) see();
      previous = now;
      setDrawn(heldMoment(shown.current, watched.current, segments));
      if (shown.current === goal) {
        frame = null;
        previous = null;
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    const wake = (): void => {
      if (frame === null) frame = requestAnimationFrame(tick);
    };
    wake();
    const unsubscribe = target.on("change", wake);
    return () => {
      unsubscribe();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [target, segments, watching, reducedMotion]);

  return reducedMotion ? segments[segments.length - 1] : drawn;
}
