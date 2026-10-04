/** Three finite ink strokes punctuate the coral rig's forward-entry realisation. */
"use client";

import { motion } from "motion/react";
import type { CSSProperties, JSX } from "react";

import { useReducedMotionPreference } from "@/components/agent/useReducedMotion";

type RealiseMarksProps = Readonly<{ on: boolean; style: CSSProperties }>;

const WAVES: readonly string[] = [
  "M 12 32 C 2 28 18 23 8 18 C 1 14 13 10 6 6",
  "M 30 28 C 20 24 36 19 26 14 C 19 10 31 6 24 2",
  "M 48 32 C 38 28 54 23 44 18 C 37 14 49 10 42 6",
];

export function RealiseMarks({ on, style }: RealiseMarksProps): JSX.Element {
  const { reducedMotion } = useReducedMotionPreference();
  return (
    <motion.div
      aria-hidden="true"
      data-testid="realise-marks"
      data-on={on}
      initial={false}
      animate={{ rotate: on && !reducedMotion ? [0, -2, 2, 0] : 0 }}
      transition={{ duration: reducedMotion ? 0 : 0.24 }}
      style={{ width: 58, height: 36, pointerEvents: "none", transformOrigin: "50% 100%", ...style }}
    >
      <svg width="58" height="36" viewBox="0 0 58 36" fill="none">
        {WAVES.map((d, index) => (
          <motion.path
            key={d}
            d={d}
            pathLength={1}
            stroke="var(--ink)"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray="1 1"
            initial={{ pathOffset: reducedMotion && on ? 0 : -1, opacity: reducedMotion && on ? 1 : 0 }}
            animate={{
              pathOffset: reducedMotion ? (on ? 0 : -1) : on ? [-1, 0, 0, -1] : -1,
              opacity: reducedMotion ? (on ? 1 : 0) : on ? [1, 1, 1, 0] : 0,
            }}
            transition={reducedMotion ? { duration: 0 } : on
              ? { duration: 0.9, delay: index * 0.04, times: [0, 0.2, 0.78, 1], ease: "easeInOut" }
              : { duration: 0.12 }}
          />
        ))}
      </svg>
    </motion.div>
  );
}
