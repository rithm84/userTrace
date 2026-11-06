"use client";

import { useEffect, useState } from "react";

/**
 * The open case plays on its own clock, the way the demo above it does: the problem as the
 * customer meets it, uTrace on it, the build, then the fixed version, which holds before the
 * case starts over. Every frame of a case is a pure function of `t`, its ms into the loop.
 */
export const CASE_TIMES = Object.freeze({ watch: 3000, build: 5600, fixed: 7800, done: 10800, loop: 14000 });

/** The case's three steps on its rail start here; past `done` all three are complete. */
export const STEP_STARTS: readonly number[] = [0, CASE_TIMES.build, CASE_TIMES.fixed];
/** Where each step's still sits under reduced motion: the last moment of the step, or the held fixed version. */
export const STEP_STILLS: readonly number[] = [CASE_TIMES.build - 1, CASE_TIMES.fixed - 1, CASE_TIMES.loop - 1];

export type CasePhase = "problem" | "watch" | "build" | "fixed" | "done";

export function phaseAt(t: number): CasePhase {
  if (t >= CASE_TIMES.done) return "done";
  if (t >= CASE_TIMES.fixed) return "fixed";
  if (t >= CASE_TIMES.build) return "build";
  if (t >= CASE_TIMES.watch) return "watch";
  return "problem";
}

/** The rail's current step, 0 to 2, or 3 once the case is complete. */
export function stepAt(t: number): number {
  if (t >= CASE_TIMES.done) return STEP_STARTS.length;
  return STEP_STARTS.findLastIndex((start) => t >= start);
}

/** 0 before `start`, 1 after `end`, linear between. */
export function ramp(t: number, start: number, end: number): number {
  return Math.min(1, Math.max(0, (t - start) / (end - start)));
}

/** The longest step the clock takes in one frame, so a backgrounded tab resumes where it left off. */
const MAX_STEP_MS = 100;

/** The case's clock: it advances while `running` and starts over once the fixed version has held. */
export function useCaseClock(running: boolean, initial: number): Readonly<{ t: number; seek: (ms: number) => void }> {
  const [t, setT] = useState(initial);
  useEffect(() => {
    if (!running) return;
    let last = performance.now();
    let frame = requestAnimationFrame(function tick(now: number): void {
      const step = Math.min(MAX_STEP_MS, now - last);
      last = now;
      setT((value) => (value + step) % CASE_TIMES.loop);
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [running]);
  return { t, seek: setT };
}
