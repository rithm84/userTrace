"use client";

import { useEffect, useState, type RefObject } from "react";

/**
 * True once the element has crossed `threshold` of the viewport; stays true
 * (once-fired reveals). With `replay`, flips back to
 * false when the element leaves so a DOM movie can restart on re-entry.
 */
export function useInView(
  ref: RefObject<Element | null>,
  threshold: number,
  replay: boolean,
): boolean {
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (el === null) {
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
        } else if (replay) {
          setInView(false);
        }
      },
      { threshold },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, threshold, replay]);

  return inView;
}
