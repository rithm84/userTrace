/**
 * The shared domain lifecycle vocabulary, as the control plane spells it.
 *
 * `packages/utrace-contracts/schemas/domain` holds the JSON Schemas these
 * mirror. The contract package publishes no TypeScript binding for the `domain`
 * area yet — it ships only `envelope` and `ids` — so the web declares the
 * enumerations here and `tests/product/domain-enums.test.ts` asserts every one
 * of them equals the shipped schema, member for member. A value the control
 * plane adds or removes therefore fails a test rather than a page.
 *
 * Nothing here is a display string: the surfaces render these snake_case values
 * through `humanize`, which keeps the DOM showing exactly what was recorded.
 */

import { z } from "zod";

export const productSignalStateSchema = z.enum([
  "awaiting_interview",
  "interviewing",
  "gate_pending",
  "implementing",
  "awaiting_validation",
  "validating",
  "closed_validated",
  "closed_no_implementation",
  "closed_blocked",
  "canceled",
]);
export type ProductSignalState = z.infer<typeof productSignalStateSchema>;

export const flowTypeSchema = z.enum(["bugfix_flow", "feature_request_flow"]);
export type FlowType = z.infer<typeof flowTypeSchema>;

export const gateDecisionKindSchema = z.enum(["approved_live", "no_implementation"]);

export const implementationAttemptStateSchema = z.enum([
  "authorized",
  "queued",
  "acquiring_environment",
  "executing",
  "checking",
  "publishing_candidate",
  "candidate_ready",
  "scope_change_required",
  "checks_failed",
  "unsafe",
  "infrastructure_blocked",
  "paused",
  "canceled",
  "budget_exhausted",
]);

export const previewVersionStateSchema = z.enum(["building", "ready", "retired", "failed"]);

export const validationAttemptStateSchema = z.enum([
  "pending",
  "in_progress",
  "interrupted",
  "completed",
  "abandoned",
]);

export const validationOutcomeSchema = z.enum([
  "validated",
  "partially_validated",
  "not_validated",
  "unclear",
]);

export const criterionResultSchema = z.enum(["met", "unmet", "unconfirmed", "contradicted"]);

export const criterionObservabilitySchema = z.enum(["observed", "subjective"]);

export const evidenceClassSchema = z.enum([
  "intake_source",
  "transcript_fragment",
  "browser_observation",
  "server_span",
  "implementation_artifact",
  "check_result",
  "validation_observation",
]);

export const caseFileVariantSchema = z.enum([
  "validated_implementation",
  "no_implementation",
  "blocked",
]);

export const caseFileStageSchema = z.enum([
  "draft",
  "verified",
  "delivery_resolved",
  "ready",
  "published",
]);

export const clientMemberRoleSchema = z.enum(["owner", "admin"]);

export const membershipStatusSchema = z.enum(["active", "revoked"]);

export const deliveryStateSchema = z.enum([
  "reserved",
  "preflighting",
  "creating",
  "reconciling",
  "delivered",
  "blocked",
]);

export const offerStateSchema = z.enum(["reserved", "earned", "released"]);

export const offerIssuanceStateSchema = z.enum(["pending", "dispatched", "issued", "blocked"]);

export const interviewPhaseSchema = z.enum([
  "participant_setup",
  "greeting",
  "discovery",
  "implementation",
  "validation",
  "closing",
]);

export const interviewSessionStateSchema = z.enum([
  "invited",
  "claimed",
  "preparing",
  "live",
  "closed",
]);

export const agentCharacterStateSchema = z.enum([
  "idle",
  "listening",
  "thinking",
  "speaking",
  "acknowledgement",
  "concern",
  "paused",
  "reconnecting",
  "completion",
]);

export const implementationProgressSchema = z.enum([
  "not_started",
  "reproduction_captured",
  "making_changes",
  "preparing_preview",
  "ready_for_validation",
]);

/** Every domain enumeration, by the schema file that owns it. */
export const DOMAIN_ENUMS: Readonly<Record<string, readonly string[]>> = {
  "product-signal-state": productSignalStateSchema.options,
  "flow-type": flowTypeSchema.options,
  "gate-decision-kind": gateDecisionKindSchema.options,
  "implementation-attempt-state": implementationAttemptStateSchema.options,
  "preview-version-state": previewVersionStateSchema.options,
  "validation-attempt-state": validationAttemptStateSchema.options,
  "validation-outcome": validationOutcomeSchema.options,
  "criterion-result": criterionResultSchema.options,
  "criterion-observability": criterionObservabilitySchema.options,
  "evidence-class": evidenceClassSchema.options,
  "case-file-variant": caseFileVariantSchema.options,
  "case-file-stage": caseFileStageSchema.options,
  "client-member-role": clientMemberRoleSchema.options,
  "membership-status": membershipStatusSchema.options,
  "delivery-state": deliveryStateSchema.options,
  "offer-state": offerStateSchema.options,
  "offer-issuance-state": offerIssuanceStateSchema.options,
  "interview-phase": interviewPhaseSchema.options,
  "interview-session-state": interviewSessionStateSchema.options,
  "agent-character-state": agentCharacterStateSchema.options,
  "implementation-progress": implementationProgressSchema.options,
};

/**
 * The Product Signal states in which uTrace holds no further authority. The
 * control plane's own nonterminal set is the complement of these, and
 * `stoppable` on a case detail is the authoritative answer; this is what a list
 * uses to separate in-progress work from finished work.
 */
const TERMINAL_PRODUCT_SIGNAL_STATES: ReadonlySet<ProductSignalState> =
  new Set<ProductSignalState>([
    "closed_validated",
    "closed_no_implementation",
    "closed_blocked",
    "canceled",
  ]);

export function isTerminalProductSignalState(state: ProductSignalState): boolean {
  return TERMINAL_PRODUCT_SIGNAL_STATES.has(state);
}
