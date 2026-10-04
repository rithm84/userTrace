/**
 * The capability surface a capture hook is given.
 *
 * Hooks never talk to the transport, the registry mutation API, or the control
 * path directly. They produce typed payloads and hand them to the runtime,
 * which owns envelopes, sequences, redaction versions, and export.
 */

import type { BrowserView } from "../core/dom.js";
import type { RandomSource } from "../core/ids.js";
import type { Clock } from "../core/time.js";
import type { Actor, ObservationPayload, RouteState } from "../observation/types.js";
import type { ManifestIndex } from "../annotations/manifest.js";
import type { RedactionPipeline } from "../redaction/pipeline.js";
import type { TargetRegistry } from "../registry/targets.js";
import type { RouteTemplates } from "../routes/templates.js";
import type { TraceCoordinator } from "../trace/context.js";

export interface EmitOptions {
  readonly targetId: string | null;
  readonly targetRevision: number | null;
  readonly traceId: string | null;
  readonly spanId: string | null;
}

export const NO_TARGET_OR_TRACE: EmitOptions = {
  targetId: null,
  targetRevision: null,
  traceId: null,
  spanId: null,
};

export interface RuntimeContext {
  readonly view: BrowserView;
  readonly clock: Clock;
  readonly random: RandomSource;
  readonly manifest: ManifestIndex;
  readonly redaction: RedactionPipeline;
  readonly registry: TargetRegistry;
  readonly routes: RouteTemplates;
  readonly trace: TraceCoordinator;
  /** Current committed route state and its revision. */
  readonly currentRoute: () => RouteState;
  readonly routeRevision: () => number;
  /** Whether the current activity is attributed to the user or the agent. */
  readonly actor: () => Actor;
  readonly emit: (payload: ObservationPayload, options: EmitOptions) => void;
  /**
   * Commits a route transition. The runtime advances `route_revision`, emits the
   * committed `route.change` record, and schedules the settled-transition
   * baseline checkpoint.
   */
  readonly commitRouteChange: (transition: "push" | "replace" | "pop" | "load", actor: Actor) => void;
  /** Reports coalesced DOM mutations so the registry and taint stay current. */
  readonly onDomMutation: (records: readonly MutationRecord[]) => void;
}
