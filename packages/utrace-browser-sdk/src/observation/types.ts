/**
 * Browser observation envelope and typed payloads.
 *
 * Field names are the wire contract and stay snake_case so they match the JSON
 * Schemas in `packages/utrace-contracts/schemas/browser-observation/`. There is
 * no generic `correlation_id`: joins use the exact typed identities that
 * establish the relationship.
 *
 * An optional field is omitted rather than sent as `null`, which is what the
 * schemas say and what `JSON.stringify` produces for an absent or `undefined`
 * key. `undefined` is therefore the runtime's one way to say "no value"; `null`
 * never appears in an observation.
 */

import type { LengthBucket, OutputClass } from "../redaction/classes.js";

export type ObservationType =
  | "baseline.snapshot"
  | "baseline.chunk"
  | "interaction.activation"
  | "interaction.focus_change"
  | "input.metadata"
  | "form.submission"
  | "route.change"
  | "request.started"
  | "request.completed"
  | "error.frontend"
  | "performance.measurement"
  | "product.milestone"
  | "target.registry_change"
  | "dom.change"
  | "observation.gap";

export type Actor = "user" | "agent";
export type VisibilityState = "visible" | "hidden";

/** Repeated envelope fields, encoded once in a transport batch header. */
export type ObservationScope = {
  readonly client_id: string;
  readonly client_user_id: string;
  readonly customer_account_id: string;
  readonly session_id: string;
  readonly preview_environment_id: string;
  readonly preview_runtime_allocation_id: string;
  readonly preview_version_id?: string | undefined;
  readonly browser_stream_id: string;
  readonly schema_version: string;
  readonly policy_version: string;
  readonly redaction_version: string;
}

/** Envelope fields that vary per observation. */
export type ObservationEnvelope = {
  readonly observation_id: string;
  readonly observation_type: ObservationType;
  readonly runtime_id: string;
  readonly document_attachment_id: string;
  readonly frame_id: string;
  readonly source_sequence: number;
  readonly export_sequence: number;
  readonly occurred_at: string;
  readonly monotonic_offset: number;
  readonly route_revision: number;
  readonly visibility_state: VisibilityState;
  readonly target_id?: string | undefined;
  readonly target_revision?: number | undefined;
  readonly trace_id?: string | undefined;
  readonly span_id?: string | undefined;
}

export type Observation = {
  readonly envelope: ObservationEnvelope;
  readonly payload: ObservationPayload;
}

/** Delivery priority. Performance measurements are the first eligible drops. */
export type ObservationPriority = "critical" | "normal" | "low";

export type SafeEntityRef = {
  readonly entity_definition_id: string;
  readonly instance_ref: string;
}

export type TargetSummary = {
  readonly target_id: string;
  readonly target_revision: number;
  readonly layout_revision: number;
  readonly definition_id?: string | undefined;
  readonly visual_only: boolean;
  readonly role: string;
  readonly label?: SafeValue | undefined;
  readonly entity_ref?: SafeEntityRef | undefined;
  readonly semantic_state: TargetSemanticState;
  readonly geometry: TargetGeometry;
}

export type TargetSemanticState = {
  readonly disabled: boolean;
  readonly checked?: boolean | undefined;
  readonly expanded?: boolean | undefined;
  readonly selected?: boolean | undefined;
  readonly busy: boolean;
  readonly invalid: boolean;
}

/** Clipped top-level viewport geometry with visibility and occlusion state. */
export type TargetGeometry = {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly clipped: boolean;
  readonly visibility: "visible" | "offscreen" | "hidden";
  readonly occluded: boolean;
  /**
   * Fraction of the element's own box that is inside the viewport, 0 to 1. The
   * extension needs it to decide whether a target is visible enough for a
   * visual command, and only the shared runtime can measure it.
   */
  readonly visible_fraction: number;
  readonly viewport_width: number;
  readonly viewport_height: number;
}

/** A value that survived redaction, carrying the class that let it through. */
export type SafeValue = {
  readonly output_class: OutputClass;
  readonly value?: string | undefined;
  readonly length_bucket: LengthBucket;
  readonly rule_id: string;
}

export type RouteState = {
  readonly route_template?: string | undefined;
  readonly unknown_route: boolean;
  readonly segment_count: number;
  readonly has_query: boolean;
  readonly has_fragment: boolean;
  readonly params: readonly RouteParam[];
}

export type RouteParam = {
  readonly name: string;
  readonly value: SafeValue;
}

export type BaselineSnapshotPayload = {
  readonly baseline_id: string;
  readonly reason: BaselineReason;
  readonly route: RouteState;
  readonly landmarks: readonly BaselineLandmark[];
  readonly targets: readonly TargetSummary[];
  readonly surfaces: readonly string[];
  readonly entities: readonly SafeEntityRef[];
  readonly node_budget_exhausted: boolean;
}

export type BaselineReason =
  | "activation"
  | "resume"
  | "full_navigation"
  | "settled_route_transition"
  | "delivery_gap"
  | "registry_drift"
  | "authorized_diagnostic";

export type BaselineLandmark = {
  readonly role: string;
  readonly label?: SafeValue | undefined;
  readonly depth: number;
  readonly child_count: number;
}

export type BaselineChunkPayload = {
  readonly baseline_id: string;
  readonly chunk_index: number;
  readonly chunk_count: number;
  readonly chunk_hash: string;
  readonly aggregate_hash?: string | undefined;
  readonly chunk_body: string;
}

export type InteractionActivationPayload = {
  readonly actor: Actor;
  readonly activation_kind: "pointer" | "keyboard";
  readonly role: string;
  readonly label?: SafeValue | undefined;
  readonly entity_ref?: SafeEntityRef | undefined;
  readonly surface?: string | undefined;
}

export type FocusChangePayload = {
  readonly actor: Actor;
  readonly role: string;
  readonly label?: SafeValue | undefined;
  readonly surface?: string | undefined;
}

export type InputEditClass = "insert" | "delete" | "replace" | "clear" | "select_option" | "toggle";

export type InputMetadataPayload = {
  readonly actor: Actor;
  readonly role: string;
  readonly field_name?: SafeValue | undefined;
  readonly edit_class: InputEditClass;
  readonly length_bucket: LengthBucket;
  readonly validity: "valid" | "invalid" | "unchecked";
  readonly changed: boolean;
  readonly sensitive_field: boolean;
  readonly approved_value?: SafeValue | undefined;
}

export type FormSubmissionPayload = {
  readonly actor: Actor;
  readonly form_name?: SafeValue | undefined;
  readonly method: "get" | "post" | "dialog";
  readonly field_count: number;
  readonly invalid_field_count: number;
  readonly surface?: string | undefined;
}

export type RouteChangePayload = {
  readonly actor: Actor;
  readonly transition: "push" | "replace" | "pop" | "load";
  readonly route: RouteState;
  readonly previous_route_template?: string | undefined;
  readonly committed: boolean;
}

export type RequestStartedPayload = {
  readonly browser_request_id: string;
  readonly transport: "fetch" | "xhr";
  readonly method: string;
  readonly route: RouteState;
  readonly same_origin: boolean;
  readonly trace_context_injected: boolean;
  readonly operation_id?: string | undefined;
  readonly actor: Actor;
}

export type RequestCompletedPayload = {
  readonly browser_request_id: string;
  readonly transport: "fetch" | "xhr";
  readonly method: string;
  readonly route: RouteState;
  readonly status_code?: number | undefined;
  readonly duration_ms: number;
  readonly response_bytes?: number | undefined;
  readonly outcome: "completed" | "network_error" | "aborted";
  readonly error_class?: string | undefined;
}

export type FrontendErrorPayload = {
  readonly error_class: string;
  readonly message_fingerprint: string;
  readonly product_area?: string | undefined;
  readonly code_location?: CodeLocation | undefined;
  readonly source: "error_event" | "unhandled_rejection";
  readonly handled: boolean;
}

export type CodeLocation = {
  readonly script_ref: string;
  readonly line: number;
  readonly column: number;
}

export type PerformanceMeasurementPayload = {
  readonly measurements: readonly PerformanceMeasurement[];
  readonly window_start_offset: number;
  readonly window_end_offset: number;
}

export type PerformanceMeasurement = {
  readonly metric: string;
  readonly count: number;
  readonly sum_ms: number;
  readonly max_ms: number;
}

export type ProductMilestonePayload = {
  readonly milestone_id: string;
  readonly severity?: string | undefined;
  readonly priority?: string | undefined;
  readonly entity_ref?: SafeEntityRef | undefined;
  readonly attributes: readonly MilestoneAttribute[];
  readonly actor: Actor;
}

export type MilestoneAttribute = {
  readonly name: string;
  readonly value: SafeValue;
}

export type TargetRegistryChangePayload = {
  readonly change: "mounted" | "updated" | "unmounted" | "layout" | "rejected";
  readonly registry_revision: number;
  readonly target?: TargetSummary | undefined;
  readonly rejected_annotation?: string | undefined;
}

export type DomChangePayload = {
  readonly changes: readonly DomChange[];
  readonly coalesced_count: number;
  readonly window_start_offset: number;
  readonly window_end_offset: number;
  readonly truncated: boolean;
}

export type DomChange = {
  readonly kind: "added" | "removed" | "text" | "attribute" | "state";
  readonly role: string;
  readonly surface?: string | undefined;
  readonly target_id?: string | undefined;
  readonly label?: SafeValue | undefined;
}

/**
 * An explicit gap for a dropped range.
 *
 * The range is expressed in `source_sequence` because a record is dropped from
 * the pending queue before the exporter assigns it an `export_sequence`. The
 * export stream therefore stays contiguous and ingestion's contiguous
 * acknowledgement watermark still advances across the gap.
 */
export type ObservationGapPayload = {
  readonly gap_runtime_id: string;
  readonly gap_first_source_sequence: number;
  readonly gap_last_source_sequence: number;
  readonly count: number;
  readonly observation_class: ObservationPriority;
  readonly reason: "buffer_pressure" | "flow_control" | "transport_loss" | "pagehide_truncated";
  readonly baseline_follows: boolean;
}

export type ObservationPayload =
  | { readonly type: "baseline.snapshot"; readonly body: BaselineSnapshotPayload }
  | { readonly type: "baseline.chunk"; readonly body: BaselineChunkPayload }
  | { readonly type: "interaction.activation"; readonly body: InteractionActivationPayload }
  | { readonly type: "interaction.focus_change"; readonly body: FocusChangePayload }
  | { readonly type: "input.metadata"; readonly body: InputMetadataPayload }
  | { readonly type: "form.submission"; readonly body: FormSubmissionPayload }
  | { readonly type: "route.change"; readonly body: RouteChangePayload }
  | { readonly type: "request.started"; readonly body: RequestStartedPayload }
  | { readonly type: "request.completed"; readonly body: RequestCompletedPayload }
  | { readonly type: "error.frontend"; readonly body: FrontendErrorPayload }
  | { readonly type: "performance.measurement"; readonly body: PerformanceMeasurementPayload }
  | { readonly type: "product.milestone"; readonly body: ProductMilestonePayload }
  | { readonly type: "target.registry_change"; readonly body: TargetRegistryChangePayload }
  | { readonly type: "dom.change"; readonly body: DomChangePayload }
  | { readonly type: "observation.gap"; readonly body: ObservationGapPayload };

const PRIORITY_BY_TYPE: Readonly<Record<ObservationType, ObservationPriority>> = {
  "baseline.snapshot": "critical",
  "baseline.chunk": "critical",
  "interaction.activation": "critical",
  "interaction.focus_change": "critical",
  "input.metadata": "critical",
  "form.submission": "critical",
  "route.change": "critical",
  "request.started": "critical",
  "request.completed": "critical",
  "error.frontend": "critical",
  "product.milestone": "critical",
  "target.registry_change": "critical",
  "observation.gap": "critical",
  "dom.change": "normal",
  "performance.measurement": "low",
};

export const priorityOf = (type: ObservationType): ObservationPriority => PRIORITY_BY_TYPE[type];
