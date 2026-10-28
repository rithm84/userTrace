"use client";

import { useLayoutEffect, useState, type RefObject } from "react";

import type { Rect, Targets } from "./agentScene";

/**
 * The registered targets inside a surface (`[data-target]` elements), each as a box in the
 * surface's own design pixels whatever zoom or scale the page is under, the way the browser SDK
 * registers a product's targets for the extension. Measured before paint whenever `layout`
 * changes (a filter took a value, a saved view appeared) and on resize.
 */
export function useTargets(container: RefObject<HTMLElement | null>, designWidth: number, layout: string): Targets {
  const [targets, setTargets] = useState<Targets>({});
  useLayoutEffect(() => {
    const measure = (): void => {
      const root = container.current;
      if (root === null) return;
      const box = root.getBoundingClientRect();
      const k = box.width / designWidth;
      if (k === 0) return;
      const found: Record<string, Rect> = {};
      for (const element of root.querySelectorAll<HTMLElement>("[data-target]")) {
        const id = element.dataset.target;
        if (id === undefined) continue;
        const rect = element.getBoundingClientRect();
        found[id] = { x: (rect.left - box.left) / k, y: (rect.top - box.top) / k, width: rect.width / k, height: rect.height / k };
      }
      setTargets(found);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [container, designWidth, layout]);
  return targets;
}
