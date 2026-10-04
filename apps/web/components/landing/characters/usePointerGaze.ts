"use client";

import { useEffect, useState, type RefObject } from "react";

/**
 * Where the pointer is relative to an element's centre, as a unit gaze
 * (-1..1 on each axis) that saturates a few hundred pixels out. Drives the
 * gaze binding of any rig so the cast watches the visitor.
 */
export function usePointerGaze(ref: RefObject<HTMLElement | null>, reachPx: number): readonly [number, number] {
  const [gaze, setGaze] = useState<readonly [number, number]>([0, 0]);

  useEffect(() => {
    let frame = 0;
    let last: PointerEvent | null = null;
    let visible = false;
    const apply = (): void => {
      frame = 0;
      const el = ref.current;
      if (!visible || el === null || last === null) {
        return;
      }
      const rect = el.getBoundingClientRect();
      const dx = last.clientX - (rect.left + rect.width / 2);
      const dy = last.clientY - (rect.top + rect.height / 2);
      const len = Math.hypot(dx, dy) || 1;
      const k = Math.min(1, len / reachPx);
      const x = (dx / len) * k, y = (dy / len) * k;
      setGaze((previous) => previous[0] === x && previous[1] === y ? previous : [x, y]);
    };
    const onMove = (event: PointerEvent): void => {
      last = event;
      if (visible && frame === 0) {
        frame = window.requestAnimationFrame(apply);
      }
    };
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (visible) apply();
      else if (frame !== 0) { window.cancelAnimationFrame(frame); frame = 0; }
    });
    if (ref.current !== null) observer.observe(ref.current);
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      observer.disconnect();
      window.removeEventListener("pointermove", onMove);
      if (frame !== 0) {
        window.cancelAnimationFrame(frame);
      }
    };
  }, [ref, reachPx]);

  return gaze;
}
