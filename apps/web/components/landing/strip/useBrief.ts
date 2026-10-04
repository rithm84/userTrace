"use client";

import { useEffect, useState } from "react";

/**
 * True while `on`, but for no longer than `ms` of real time after it turned on. The strip's
 * moment follows the scroll, so a reader who stops inside a short cue would otherwise hold it
 * indefinitely (a flash left lit, a rig timeline left running into frames it was cut before).
 */
export function useBrief(on: boolean, ms: number): boolean {
  const [spent, setSpent] = useState(false);
  // leaving the cue re-arms it, so entering it again plays it again
  if (!on && spent) setSpent(false);
  useEffect(() => {
    if (!on) return;
    const timer = window.setTimeout(() => setSpent(true), ms);
    return () => window.clearTimeout(timer);
  }, [on, ms]);
  return on && !spent;
}
