"use client";

import { useEffect, useState, type RefObject } from "react";

/**
 * True once the element has come within `marginPx` of the viewport, and stays true. Rigs
 * far down the page wait for this before fetching their `.riv`, so the hero's assets are not
 * competing with a dozen characters the reader has not scrolled to.
 */
export function useNearViewport(ref: RefObject<HTMLElement | null>, marginPx: number): boolean {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (element === null || near) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setNear(true);
        observer.disconnect();
      },
      { rootMargin: `${marginPx}px 0px` },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, marginPx, near]);
  return near;
}
