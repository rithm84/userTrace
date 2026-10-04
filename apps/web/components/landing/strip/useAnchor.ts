"use client";

import { useLayoutEffect, useState, type RefObject } from "react";

/** Centre and size of the target, in the panel's design pixels. */
export type Anchor = Readonly<{ x: number; y: number; width: number; height: number }>;

/**
 * Where an element inside a panel sits (centre and size), in the panel's own design pixels,
 * whatever zoom or scale the page is under. Re-measured when `key` changes (a chip landed, the
 * Add filter button moved) and on resize; null until the target exists.
 */
export function useAnchor(container: RefObject<HTMLElement | null>, selector: string, designWidth: number, key: number): Anchor | null {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  useLayoutEffect(() => {
    const measure = (): void => {
      const root = container.current;
      const target = root?.querySelector(selector) ?? null;
      if (root === null || target === null) {
        setAnchor(null);
        return;
      }
      const box = root.getBoundingClientRect();
      const rect = target.getBoundingClientRect();
      const k = box.width / designWidth;
      if (k === 0) return;
      setAnchor({ x: (rect.left + rect.width / 2 - box.left) / k, y: (rect.top + rect.height / 2 - box.top) / k, width: rect.width / k, height: rect.height / k });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [container, selector, designWidth, key]);
  return anchor;
}
