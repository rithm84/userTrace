import type { ChipTone } from "@/components/product/ui";
import type { FlowType, ProductSignalState } from "@/lib/api/schemas";
import { isTerminalProductSignalState } from "@/lib/api/schemas";

/** States where the case waits on a person or a deadline rather than on work. */
const WAITING_STATES: ReadonlySet<ProductSignalState> = new Set<ProductSignalState>([
  "awaiting_interview",
  "gate_pending",
  "awaiting_validation",
]);

export function lifecycleTone(state: ProductSignalState): ChipTone {
  if (isTerminalProductSignalState(state)) return "closed";
  if (WAITING_STATES.has(state)) return "waiting";
  return "active";
}

/** Flow types keep their PRD capitalisation. */
export function flowTypeLabel(flowType: FlowType): string {
  return flowType === "bugfix_flow" ? "Bugfix Flow" : "Feature Request Flow";
}
