import { Check } from "lucide-react";
import type { JSX } from "react";

import type { TabProgress } from "@/lib/api/schemas";

/** The four user-facing implementation progress states, in order. */
const STEPS: readonly TabProgress[] = [
  "reproduction_captured",
  "making_changes",
  "preparing_the_preview",
  "ready_for_validation",
];

const LABELS: Readonly<Record<TabProgress, string>> = {
  reproduction_captured: "Reproduction captured",
  making_changes: "Making changes",
  preparing_the_preview: "Preparing the preview",
  ready_for_validation: "Ready for validation",
};

/**
 * Where live implementation stands: finished steps are ink circles with a
 * check, the current one is coral, and the rest are outlined.
 */
export function ProgressTrail(
  props: Readonly<{ progress: TabProgress }>,
): JSX.Element {
  const reached = STEPS.indexOf(props.progress);

  return (
    <ol className="ss-progress" data-testid="implementation-progress" data-progress={props.progress}>
      {STEPS.map((step, index) => (
        <li
          key={step}
          data-reached={String(index <= reached)}
          data-current={String(index === reached)}
          data-testid={`progress-${step}`}
        >
          <span className="ss-progress-mark" aria-hidden="true">
            {index < reached ? <Check size={14} strokeWidth={3} /> : index + 1}
          </span>
          <span className="ss-progress-label">{LABELS[step]}</span>
        </li>
      ))}
    </ol>
  );
}
