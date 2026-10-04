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

export const PRODUCT_SIGNAL_STATES = [
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
] as const;

export const FLOW_TYPES = ["bugfix_flow", "feature_request_flow"] as const;

export const GATE_DECISION_KINDS = ["approved_live", "no_implementation"] as const;

export const IMPLEMENTATION_ATTEMPT_STATES = [
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
] as const;

export const PREVIEW_VERSION_STATES = ["building", "ready", "retired", "failed"] as const;

export const VALIDATION_ATTEMPT_STATES = [
  "pending",
  "in_progress",
  "interrupted",
  "completed",
  "abandoned",
] as const;

export const VALIDATION_OUTCOMES = [
  "validated",
  "partially_validated",
  "not_validated",
  "unclear",
] as const;

export const CRITERION_RESULTS = ["met", "unmet", "unconfirmed", "contradicted"] as const;

export const CRITERION_OBSERVABILITIES = ["observed", "subjective"] as const;

export const EVIDENCE_CLASSES = [
  "intake_source",
  "transcript_fragment",
  "browser_observation",
  "server_span",
  "implementation_artifact",
  "check_result",
  "validation_observation",
] as const;

export const CASE_FILE_VARIANTS = [
  "validated_implementation",
  "no_implementation",
  "blocked",
] as const;

export const CASE_FILE_STAGES = [
  "draft",
  "verified",
  "delivery_resolved",
  "ready",
  "published",
] as const;

export const CLIENT_MEMBER_ROLES = ["owner", "admin"] as const;

export const MEMBERSHIP_STATUSES = ["active", "revoked"] as const;

export const DELIVERY_STATES = [
  "reserved",
  "preflighting",
  "creating",
  "reconciling",
  "delivered",
  "blocked",
] as const;

export const OFFER_STATES = ["reserved", "earned", "released"] as const;

export const OFFER_ISSUANCE_STATES = ["pending", "dispatched", "issued", "blocked"] as const;

export const INTERVIEW_PHASES = [
  "participant_setup",
  "greeting",
  "discovery",
  "implementation",
  "validation",
  "closing",
] as const;

export const INTERVIEW_SESSION_STATES = [
  "invited",
  "claimed",
  "preparing",
  "live",
  "closed",
] as const;

export const AGENT_CHARACTER_STATES = [
  "idle",
  "listening",
  "thinking",
  "speaking",
  "acknowledgement",
  "concern",
  "paused",
  "reconnecting",
  "completion",
] as const;

export const IMPLEMENTATION_PROGRESS_STATES = [
  "not_started",
  "reproduction_captured",
  "making_changes",
  "preparing_preview",
  "ready_for_validation",
] as const;

/** Every domain enumeration, by the schema file that owns it. */
export const DOMAIN_ENUMS: Readonly<Record<string, readonly string[]>> = {
  "product-signal-state": PRODUCT_SIGNAL_STATES,
  "flow-type": FLOW_TYPES,
  "gate-decision-kind": GATE_DECISION_KINDS,
  "implementation-attempt-state": IMPLEMENTATION_ATTEMPT_STATES,
  "preview-version-state": PREVIEW_VERSION_STATES,
  "validation-attempt-state": VALIDATION_ATTEMPT_STATES,
  "validation-outcome": VALIDATION_OUTCOMES,
  "criterion-result": CRITERION_RESULTS,
  "criterion-observability": CRITERION_OBSERVABILITIES,
  "evidence-class": EVIDENCE_CLASSES,
  "case-file-variant": CASE_FILE_VARIANTS,
  "case-file-stage": CASE_FILE_STAGES,
  "client-member-role": CLIENT_MEMBER_ROLES,
  "membership-status": MEMBERSHIP_STATUSES,
  "delivery-state": DELIVERY_STATES,
  "offer-state": OFFER_STATES,
  "offer-issuance-state": OFFER_ISSUANCE_STATES,
  "interview-phase": INTERVIEW_PHASES,
  "interview-session-state": INTERVIEW_SESSION_STATES,
  "agent-character-state": AGENT_CHARACTER_STATES,
  "implementation-progress": IMPLEMENTATION_PROGRESS_STATES,
};

export const productSignalStateSchema = z.enum(PRODUCT_SIGNAL_STATES);
export type ProductSignalState = z.infer<typeof productSignalStateSchema>;

export const flowTypeSchema = z.enum(FLOW_TYPES);
export type FlowType = z.infer<typeof flowTypeSchema>;

export const gateDecisionKindSchema = z.enum(GATE_DECISION_KINDS);
export type GateDecisionKind = z.infer<typeof gateDecisionKindSchema>;

export const implementationAttemptStateSchema = z.enum(IMPLEMENTATION_ATTEMPT_STATES);
export type ImplementationAttemptState = z.infer<typeof implementationAttemptStateSchema>;

export const previewVersionStateSchema = z.enum(PREVIEW_VERSION_STATES);
export type PreviewVersionState = z.infer<typeof previewVersionStateSchema>;

export const validationAttemptStateSchema = z.enum(VALIDATION_ATTEMPT_STATES);
export type ValidationAttemptState = z.infer<typeof validationAttemptStateSchema>;

export const validationOutcomeSchema = z.enum(VALIDATION_OUTCOMES);
export type ValidationOutcome = z.infer<typeof validationOutcomeSchema>;

export const criterionResultSchema = z.enum(CRITERION_RESULTS);
export type CriterionResult = z.infer<typeof criterionResultSchema>;

export const criterionObservabilitySchema = z.enum(CRITERION_OBSERVABILITIES);
export type CriterionObservability = z.infer<typeof criterionObservabilitySchema>;

export const evidenceClassSchema = z.enum(EVIDENCE_CLASSES);
export type EvidenceClass = z.infer<typeof evidenceClassSchema>;

export const caseFileVariantSchema = z.enum(CASE_FILE_VARIANTS);
export type CaseFileVariant = z.infer<typeof caseFileVariantSchema>;

export const caseFileStageSchema = z.enum(CASE_FILE_STAGES);
export type CaseFileStage = z.infer<typeof caseFileStageSchema>;

export const clientMemberRoleSchema = z.enum(CLIENT_MEMBER_ROLES);
export type ClientMemberRole = z.infer<typeof clientMemberRoleSchema>;

export const membershipStatusSchema = z.enum(MEMBERSHIP_STATUSES);
export type MembershipStatus = z.infer<typeof membershipStatusSchema>;

export const deliveryStateSchema = z.enum(DELIVERY_STATES);
export type DeliveryState = z.infer<typeof deliveryStateSchema>;

export const offerStateSchema = z.enum(OFFER_STATES);
export type OfferState = z.infer<typeof offerStateSchema>;

export const offerIssuanceStateSchema = z.enum(OFFER_ISSUANCE_STATES);
export type OfferIssuanceState = z.infer<typeof offerIssuanceStateSchema>;

export const interviewPhaseSchema = z.enum(INTERVIEW_PHASES);
export type InterviewPhase = z.infer<typeof interviewPhaseSchema>;

export const interviewSessionStateSchema = z.enum(INTERVIEW_SESSION_STATES);
export type InterviewSessionState = z.infer<typeof interviewSessionStateSchema>;

export const agentCharacterStateSchema = z.enum(AGENT_CHARACTER_STATES);
export type AgentCharacterState = z.infer<typeof agentCharacterStateSchema>;

export const implementationProgressSchema = z.enum(IMPLEMENTATION_PROGRESS_STATES);
export type ImplementationProgress = z.infer<typeof implementationProgressSchema>;

/**
 * The Product Signal states in which uTrace holds no further authority. The
 * control plane's own nonterminal set is the complement of these, and
 * `stoppable` on a case detail is the authoritative answer; this is what a list
 * uses to separate in-progress work from finished work.
 */
export const TERMINAL_PRODUCT_SIGNAL_STATES: ReadonlySet<ProductSignalState> =
  new Set<ProductSignalState>([
    "closed_validated",
    "closed_no_implementation",
    "closed_blocked",
    "canceled",
  ]);

export function isTerminalProductSignalState(state: ProductSignalState): boolean {
  return TERMINAL_PRODUCT_SIGNAL_STATES.has(state);
}
