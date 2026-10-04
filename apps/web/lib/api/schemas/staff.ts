/**
 * What the control plane returns on every staff route the dashboard calls.
 *
 * These mirror `apps/api/openapi.json` exactly. Every response is parsed here
 * before it reaches a component, so a drifted field fails loudly at the boundary
 * instead of rendering as an empty card. Nothing is invented: a field the
 * control plane does not send is absent here too, and the surfaces say so rather
 * than filling a gap.
 */

import { z } from "zod";

import { absentWhenNull } from "./absent";

import {
  caseFileStageSchema,
  caseFileVariantSchema,
  clientMemberRoleSchema,
  criterionObservabilitySchema,
  criterionResultSchema,
  deliveryStateSchema,
  evidenceClassSchema,
  flowTypeSchema,
  gateDecisionKindSchema,
  implementationAttemptStateSchema,
  interviewPhaseSchema,
  interviewSessionStateSchema,
  membershipStatusSchema,
  offerIssuanceStateSchema,
  offerStateSchema,
  previewVersionStateSchema,
  productSignalStateSchema,
  validationAttemptStateSchema,
  validationOutcomeSchema,
} from "./lifecycle";

const uuid = z.uuid();
const timestamp = z.iso.datetime({ offset: true });
const minorUnits = z.int();
const currency = z.string().length(3);
const text = z.string().min(1);

/* ------------------------------------------------- opaque client references */

export const REFERENCE_KIND_CLIENT_USER = "client_user";
export const REFERENCE_KIND_CUSTOMER_ACCOUNT = "customer_account";

/**
 * A published Case File, a case, and an Outreach Offer all name a person or an
 * organization by client-scoped reference rather than by name. This is the one
 * place the web spells that format; `GET /v1/clients/{id}/references` resolves
 * it.
 */
export function opaqueReference(
  kind: typeof REFERENCE_KIND_CLIENT_USER | typeof REFERENCE_KIND_CUSTOMER_ACCOUNT,
  identifier: string,
): string {
  return `${kind}:${identifier}`;
}

export const resolvedReferenceSchema = z.object({
  ref: text,
  kind: text,
  id: uuid,
  /** Absent when uTrace holds no recorded name; the surface then shows `ref`. */
  label: absentWhenNull(text),
});
export type ResolvedReference = z.infer<typeof resolvedReferenceSchema>;

export const referenceResolutionSchema = z.object({
  references: z.array(resolvedReferenceSchema),
});

/* ------------------------------------------------------- client and staff */

export const clientRecordSchema = z.object({
  id: uuid,
  name: text,
  product_name: text,
  revision: z.int(),
  created_at: timestamp,
});
export type ClientRecord = z.infer<typeof clientRecordSchema>;

export const clientMemberSchema = z.object({
  id: uuid,
  client_id: uuid,
  role: clientMemberRoleSchema,
  status: membershipStatusSchema,
  created_at: timestamp,
  email: absentWhenNull(z.email()),
});
export type ClientMember = z.infer<typeof clientMemberSchema>;

export const clientSchema = z.object({
  client: clientRecordSchema,
  membership: clientMemberSchema,
});
export type Client = z.infer<typeof clientSchema>;

export const memberListSchema = z.object({ members: z.array(clientMemberSchema) });

export const watchedChannelSchema = z.object({ channel_ref: text, name: text });

const releasePolicySchema = z.object({
  customer_surface: z.object({
    provider: z.literal("discord"),
    installation_ref: text,
    watched_channels: z.array(watchedChannelSchema).min(1),
  }),
  implementation: z.object({
    repository_ref: text,
    base_revision: text,
    permitted_paths: z.array(text).min(1),
    blocked_paths: z.array(z.string()),
  }),
  outreach_offers: z.object({
    enabled: z.boolean(),
    currency,
    fixed_amount_minor_units: minorUnits,
    authorized_ceiling_minor_units: minorUnits,
    billing_account_ref: text,
    billing_use: text,
    completion_terms: z.array(text).min(1),
  }),
  data_seeding: z.object({ manifest_hash: text, environment_template_id: text }),
});

/**
 * `GET /v1/clients/{id}/configuration` answers with the whole active release.
 * The dashboard keeps only its integration-status projection; the rest of the
 * pinned policy stays inside the control plane.
 */
export const clientConfigurationSchema = z
  .object({
    release: z.object({
      id: uuid,
      release_number: z.int(),
      digest: text,
      policy: releasePolicySchema,
    }),
  })
  .transform(({ release }) => ({
    configuration_release_id: release.id,
    release_number: release.release_number,
    digest: release.digest,
    customer_surface: release.policy.customer_surface,
    repository: {
      ref: release.policy.implementation.repository_ref,
      base_revision: release.policy.implementation.base_revision,
      permitted_paths: release.policy.implementation.permitted_paths,
      blocked_paths: release.policy.implementation.blocked_paths,
    },
    outreach_offers: release.policy.outreach_offers,
    data_seeding_manifest_hash: release.policy.data_seeding.manifest_hash,
    environment_template_id: release.policy.data_seeding.environment_template_id,
  }));
export type ClientConfiguration = z.infer<typeof clientConfigurationSchema>;

/* ------------------------------------------------------------------ cases */

export const productSignalSchema = z.object({
  id: uuid,
  client_id: uuid,
  state: productSignalStateSchema,
  revision: z.int(),
  machine_version: z.int(),
  initial_flow_type: flowTypeSchema,
  established_flow_type: absentWhenNull(flowTypeSchema),
  classification_revision: z.int(),
  concern_revision: z.int(),
  acceptance_criteria_revision: z.int(),
  originating_client_user_id: uuid,
  originating_customer_account_id: uuid,
  pinned_configuration_release_id: uuid,
  return_deadline_at: absentWhenNull(timestamp),
  closure_reason: absentWhenNull(text),
  created_at: timestamp,
  updated_at: timestamp,
});
export type ProductSignal = z.infer<typeof productSignalSchema>;

export const caseConcernSchema = z.object({
  concern_revision: z.int(),
  origin: z.enum(["routing_inference", "interview_established"]),
  pain_point: text,
  validation_criterion: text,
  established_at: timestamp,
});
export type CaseConcern = z.infer<typeof caseConcernSchema>;

export const caseOverviewSchema = z.object({
  product_signal_id: uuid,
  concern: absentWhenNull(caseConcernSchema),
  originating_user_ref: text,
  customer_account_ref: text,
});
export type CaseOverview = z.infer<typeof caseOverviewSchema>;

export const caseListSchema = z.object({
  cases: z.array(productSignalSchema),
  overviews: z.array(caseOverviewSchema),
});
export type CaseList = z.infer<typeof caseListSchema>;

export const gateDecisionSchema = z.object({
  id: uuid,
  client_id: uuid,
  product_signal_id: uuid,
  decision: gateDecisionKindSchema,
  blocker_reason: absentWhenNull(text),
  classification_revision: z.int(),
  acceptance_criteria_revision: z.int(),
  pinned_configuration_release_id: uuid,
  decided_at: timestamp,
});
export type GateDecision = z.infer<typeof gateDecisionSchema>;

export const implementationAttemptSchema = z.object({
  id: uuid,
  client_id: uuid,
  product_signal_id: uuid,
  gate_decision_id: uuid,
  state: implementationAttemptStateSchema,
  revision: z.int(),
  repository_ref: text,
  base_revision: text,
  outcome_reason: absentWhenNull(text),
  created_at: timestamp,
  updated_at: timestamp,
});
export type ImplementationAttempt = z.infer<typeof implementationAttemptSchema>;

export const validationAttemptSchema = z.object({
  id: uuid,
  client_id: uuid,
  product_signal_id: uuid,
  implementation_attempt_id: uuid,
  preview_version_id: uuid,
  interview_session_id: uuid,
  client_user_id: uuid,
  state: validationAttemptStateSchema,
  revision: z.int(),
  dataset_manifest_digest: text,
  acceptance_criteria_revision: z.int(),
  outcome: absentWhenNull(validationOutcomeSchema),
  completed_at: absentWhenNull(timestamp),
  created_at: timestamp,
});
export type ValidationAttempt = z.infer<typeof validationAttemptSchema>;

export const caseSessionSchema = z.object({
  id: uuid,
  purpose: z.enum(["interview", "validation"]),
  state: interviewSessionStateSchema,
  phase: interviewPhaseSchema,
  created_at: timestamp,
  updated_at: timestamp,
  closed_at: absentWhenNull(timestamp),
  closure_reason: absentWhenNull(text),
});
export type CaseSession = z.infer<typeof caseSessionSchema>;

export const previewVersionSchema = z.object({
  id: uuid,
  implementation_attempt_id: uuid,
  state: previewVersionStateSchema,
  source_commit: text,
  source_digest: text,
  build_digest: text,
  base_revision: text,
  ready_at: absentWhenNull(timestamp),
});
export type PreviewVersion = z.infer<typeof previewVersionSchema>;

export const caseStopSchema = z.object({
  recorded_at: timestamp,
  outcome: text,
  reason: text,
  client_member_id: uuid,
  staff_role: clientMemberRoleSchema,
});
export type CaseStop = z.infer<typeof caseStopSchema>;

export const deliverySchema = z.object({
  delivery_id: uuid,
  case_file_id: uuid,
  product_signal_id: uuid,
  state: deliveryStateSchema,
  repository_ref: text,
  pull_request_ref: absentWhenNull(z.url()),
  blocker_reason: absentWhenNull(text),
});
export type Delivery = z.infer<typeof deliverySchema>;

export const caseFileRecordSchema = z.object({
  id: uuid,
  client_id: uuid,
  product_signal_id: uuid,
  variant: caseFileVariantSchema,
  stage: caseFileStageSchema,
  revision: z.int(),
  core_digest: absentWhenNull(text),
  delivery_id: absentWhenNull(uuid),
  sealed_at: absentWhenNull(timestamp),
  published_at: absentWhenNull(timestamp),
  created_at: timestamp,
});
export type CaseFileRecord = z.infer<typeof caseFileRecordSchema>;

export const offerDispositionSchema = z.object({
  outreach_offer_id: uuid,
  product_signal_id: uuid,
  customer_account_id: uuid,
  currency,
  amount_minor_units: minorUnits,
  state: offerStateSchema,
  issuance_state: absentWhenNull(offerIssuanceStateSchema),
  provider_transaction_ref: absentWhenNull(text),
  issuance_blocked_reason: absentWhenNull(text),
  /** Absent until an invoice application has been reconciled from Stripe. */
  invoice_application_minor_units: absentWhenNull(minorUnits),
});
export type OfferDisposition = z.infer<typeof offerDispositionSchema>;

export const caseDetailSchema = z.object({
  case: productSignalSchema,
  overview: caseOverviewSchema,
  gate_decisions: z.array(gateDecisionSchema),
  implementation_attempts: z.array(implementationAttemptSchema),
  validation_attempts: z.array(validationAttemptSchema),
  interview_sessions: z.array(caseSessionSchema),
  preview_versions: z.array(previewVersionSchema),
  offer: absentWhenNull(offerDispositionSchema),
  stop: absentWhenNull(caseStopSchema),
  stoppable: z.boolean(),
  case_file: absentWhenNull(caseFileRecordSchema),
  delivery: absentWhenNull(deliverySchema),
});
export type CaseDetail = z.infer<typeof caseDetailSchema>;

export const caseStopResultSchema = z.object({
  product_signal_id: uuid,
  participation_occurred: z.boolean(),
  terminal_state: absentWhenNull(text),
  revoked_outreach_attempts: z.int().nonnegative(),
  fenced_leases: z.int().nonnegative(),
  canceled_jobs: z.int().nonnegative(),
  commands_in_reconciliation: z.int().nonnegative(),
});
export type CaseStopResult = z.infer<typeof caseStopResultSchema>;

/* -------------------------------------------------------------- Case File */

export const citationSchema = z.object({
  evidence_record_id: uuid,
  evidence_class: evidenceClassSchema,
  source_ref: z.string(),
});
export type Citation = z.infer<typeof citationSchema>;

export const citedStatementSchema = z.object({
  statement: text,
  citations: z.array(citationSchema).min(1),
});
export type CitedStatement = z.infer<typeof citedStatementSchema>;

const checkResultSchema = z.object({
  name: z.string(),
  outcome: z.enum(["passed", "failed", "skipped"]),
});

/**
 * The sealed Case File core. It names the originating user and their
 * organization by opaque reference; the dashboard resolves those.
 */
export const caseFileCoreSchema = z.object({
  metadata: z.object({
    schema_revision: z.int(),
    context_revision: z.int(),
    client_id: uuid,
    product_signal_id: uuid,
    originating_user_ref: text,
    customer_account_ref: text,
    evidence_window_id: uuid,
    gate_decision_id: absentWhenNull(uuid),
    implementation_attempt_ids: z.array(uuid),
    preview_version_id: absentWhenNull(uuid),
    validation_attempt_id: absentWhenNull(uuid),
    narrative_source: z.enum(["model_drafted", "evidence_derived"]),
  }),
  variant: caseFileVariantSchema,
  summary: z.object({
    title: text,
    flow_type: flowTypeSchema,
    concern: citedStatementSchema,
    originating_user_ref: text,
    customer_account_ref: text,
    validation_outcome: absentWhenNull(validationOutcomeSchema),
  }),
  customer_evidence: z.object({
    source_message: citedStatementSchema,
    interview_excerpts: z.array(citedStatementSchema),
    expected_behavior: citedStatementSchema,
    actual_behavior: citedStatementSchema,
    impact: citedStatementSchema,
    workaround: absentWhenNull(citedStatementSchema),
  }),
  product_trace: absentWhenNull(
    z.object({
      workflow_ref: z.string(),
      criteria: z.array(
        z.object({
          criterion_id: uuid,
          ordinal: z.int(),
          statement: z.string(),
          observability: criterionObservabilitySchema,
          result: criterionResultSchema,
          user_confirmed: z.boolean(),
          observed_evidence: z.array(citationSchema),
          contradicting_evidence: z.array(citationSchema),
          coverage_limits: z.array(z.string()),
          reason: z.string(),
        }),
      ),
      before: z.object({
        preview_version_id: absentWhenNull(uuid),
        milestones: z.array(citationSchema),
      }),
      after: z.object({
        preview_version_id: absentWhenNull(uuid),
        milestones: z.array(citationSchema),
      }),
      coverage_limits: z.array(z.string()),
    }),
  ),
  gate_decision: absentWhenNull(
    z.object({
      decision: gateDecisionKindSchema,
      decided_at: timestamp,
      reason: z.string(),
      approved_scope: z.array(z.string()),
      excluded_scope: z.array(z.string()),
      required_checks: z.array(z.string()),
    }),
  ),
  implementation: absentWhenNull(
    z.object({
      repository_ref: z.string(),
      branch_ref: absentWhenNull(z.string()),
      pull_request_ref: absentWhenNull(z.url()),
      pull_request_number: absentWhenNull(z.int()),
      implementation_attempt_id: uuid,
      preview_version_id: uuid,
      source_commit: z.string(),
      source_digest: z.string(),
      build_digest: z.string(),
      base_revision: z.string(),
      dataset_manifest_digest: z.string(),
      files_touched: z.array(z.string()),
      checks: z.array(checkResultSchema),
      cleanup_state: z.string(),
    }),
  ),
  risk: z.object({
    blast_radius: citedStatementSchema,
    assumptions: z.array(z.string()),
    follow_up_checks: z.array(z.string()),
  }),
  next_action: z.object({
    action: z.enum(["review_pull_request", "assess_blocker", "no_action"]),
    detail: text,
  }),
  blocker_reason: absentWhenNull(z.string()),
});
export type CaseFileCore = z.infer<typeof caseFileCoreSchema>;

export const caseFileDetailSchema = z.object({
  case_file: caseFileRecordSchema,
  core: absentWhenNull(caseFileCoreSchema),
});
export type CaseFileDetail = z.infer<typeof caseFileDetailSchema>;

export const caseFileListSchema = z.object({
  case_files: z.array(caseFileRecordSchema),
  deliveries: z.array(deliverySchema),
});
export type CaseFileList = z.infer<typeof caseFileListSchema>;

export const caseFileAmendmentsSchema = z.object({
  case_file_id: uuid,
  amendments: z.array(
    z.object({
      id: uuid,
      case_file_id: uuid,
      ordinal: z.int(),
      reason: z.string(),
      recorded_at: timestamp,
    }),
  ),
});
export type CaseFileAmendments = z.infer<typeof caseFileAmendmentsSchema>;

/* ------------------------------------------------------------- offers */

export const allowanceSchema = z.object({
  currency: absentWhenNull(currency),
  authorized_minor_units: minorUnits,
  reserved_minor_units: minorUnits,
  earned_minor_units: minorUnits,
  remaining_minor_units: minorUnits,
});
export type Allowance = z.infer<typeof allowanceSchema>;

export const offersViewSchema = allowanceSchema.extend({
  offers: z.array(offerDispositionSchema),
});
export type OffersView = z.infer<typeof offersViewSchema>;
