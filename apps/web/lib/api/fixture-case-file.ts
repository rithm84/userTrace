/**
 * The Shopalytics fixture's published Case File: the record
 * `GET .../case-files/{id}` answers, with its core frozen at publication.
 */

import {
  FIXTURE_CASE_ID,
  FIXTURE_CLIENT_ID,
  FIXTURE_DUDULEMON_REF,
  FIXTURE_MAYA_REF,
  FIXTURE_SAVED_VIEW_NAME,
  PULL_REQUEST_URL,
  REQUEST_TEXT,
  savedViewsCaseFileRecord,
} from "./fixture-data";
import type { CaseFileCore, CaseFileDetail, Citation, CitedStatement } from "./schemas";

const ATTEMPT_ID = "0199a0c0-0000-7aaa-8000-000000000701";
const GATE_DECISION_ID = "0199a0c0-0000-7aaa-8000-000000000901";
const PREVIEW_VERSION_ID = "0199a0c0-0000-7aaa-8000-000000000801";
const VALIDATION_ATTEMPT_ID = "0199a0c0-0000-7aaa-8000-000000000601";
const EVIDENCE_WINDOW_ID = "0199a0c0-0000-7aaa-8000-000000000301";
const CRITERION_IDS = [
  "0199a0c0-0000-7aaa-8000-000000000401",
  "0199a0c0-0000-7aaa-8000-000000000402",
  "0199a0c0-0000-7aaa-8000-000000000403",
] as const;
const EVIDENCE_IDS = [
  "0199a0c0-0000-7aaa-8000-000000000501",
  "0199a0c0-0000-7aaa-8000-000000000502",
  "0199a0c0-0000-7aaa-8000-000000000503",
  "0199a0c0-0000-7aaa-8000-000000000504",
  "0199a0c0-0000-7aaa-8000-000000000505",
  "0199a0c0-0000-7aaa-8000-000000000506",
] as const;

function cited(
  statement: string,
  evidenceIndex: number,
  evidenceClass: Citation["evidence_class"],
  sourceRef: string,
): CitedStatement {
  return {
    statement,
    citations: [
      {
        evidence_record_id: EVIDENCE_IDS[evidenceIndex],
        evidence_class: evidenceClass,
        source_ref: sourceRef,
      },
    ],
  };
}

const savedViewsCore: CaseFileCore = {
  metadata: {
    schema_revision: 1,
    context_revision: 1,
    client_id: FIXTURE_CLIENT_ID,
    product_signal_id: FIXTURE_CASE_ID,
    originating_user_ref: FIXTURE_MAYA_REF,
    customer_account_ref: FIXTURE_DUDULEMON_REF,
    evidence_window_id: EVIDENCE_WINDOW_ID,
    gate_decision_id: GATE_DECISION_ID,
    implementation_attempt_ids: [ATTEMPT_ID],
    preview_version_id: PREVIEW_VERSION_ID,
    validation_attempt_id: VALIDATION_ATTEMPT_ID,
    narrative_source: "model_drafted",
  },
  variant: "validated_implementation",
  summary: {
    title: "Saved conversion views",
    flow_type: "feature_request_flow",
    concern: cited(
      "The growth lead rebuilds the same conversion filter stack every Monday because the dashboard keeps no saved view.",
      0,
      "intake_source",
      "discord:feature-requests/1310244027450560700",
    ),
    originating_user_ref: FIXTURE_MAYA_REF,
    customer_account_ref: FIXTURE_DUDULEMON_REF,
    validation_outcome: "validated",
  },
  customer_evidence: {
    source_message: cited(
      REQUEST_TEXT,
      0,
      "intake_source",
      "discord:feature-requests/1310244027450560700",
    ),
    interview_excerpts: [
      cited(
        "Every Monday I set the same date range, channel, age band, review topic and conversion state, and then I lose it the moment I leave the page.",
        1,
        "transcript_fragment",
        "transcript:0199a0c0-…-55e1#00:04:12",
      ),
      cited(
        "I do not need to share it with anyone. I just need it to come back the way I left it.",
        2,
        "transcript_fragment",
        "transcript:0199a0c0-…-55e1#00:09:48",
      ),
    ],
    expected_behavior: cited(
      "A named view restores the whole filter stack and the conversion display when reopened.",
      2,
      "transcript_fragment",
      "transcript:0199a0c0-…-55e1#00:11:02",
    ),
    actual_behavior: cited(
      "Leaving or refreshing the conversion dashboard clears every applied filter.",
      3,
      "browser_observation",
      "browser:route_change/0199a0c0-…-0503",
    ),
    impact: cited(
      "The recurring Monday conversion review starts with several minutes of filter rebuilding before any analysis.",
      1,
      "transcript_fragment",
      "transcript:0199a0c0-…-55e1#00:06:30",
    ),
  },
  product_trace: {
    workflow_ref: "conversion-dashboard/saved-views",
    criteria: [
      {
        criterion_id: CRITERION_IDS[0],
        ordinal: 0,
        statement: "The complete filter stack can be saved under a user-supplied name.",
        observability: "observed",
        result: "met",
        user_confirmed: true,
        observed_evidence: [
          {
            evidence_record_id: EVIDENCE_IDS[3],
            evidence_class: "browser_observation",
            source_ref: "browser:form_submit/0199a0c0-…-0504",
          },
          {
            evidence_record_id: EVIDENCE_IDS[4],
            evidence_class: "server_span",
            source_ref: "server:POST /api/saved-views/0199a0c0-…-0505",
          },
        ],
        contradicting_evidence: [],
        coverage_limits: [],
        reason: `Saving under "${FIXTURE_SAVED_VIEW_NAME}" produced a persisted view and a 201 from the saved-view endpoint.`,
      },
      {
        criterion_id: CRITERION_IDS[1],
        ordinal: 1,
        statement:
          "Reopening the view restores every saved filter and the associated conversion display.",
        observability: "observed",
        result: "met",
        user_confirmed: true,
        observed_evidence: [
          {
            evidence_record_id: EVIDENCE_IDS[5],
            evidence_class: "validation_observation",
            source_ref: "browser:chart_state/0199a0c0-…-0506",
          },
        ],
        contradicting_evidence: [],
        coverage_limits: [],
        reason:
          "The annotated chart state after reload matched the saved filter values and the displayed series identity.",
      },
      {
        criterion_id: CRITERION_IDS[2],
        ordinal: 2,
        statement: "Restored state does not include adjacent seeded segments.",
        observability: "observed",
        result: "met",
        user_confirmed: true,
        observed_evidence: [
          {
            evidence_record_id: EVIDENCE_IDS[5],
            evidence_class: "validation_observation",
            source_ref: "browser:chart_state/0199a0c0-…-0506",
          },
        ],
        contradicting_evidence: [],
        coverage_limits: ["Checked against the seeded adjacent segments only."],
        reason: "No adjacent demographic or review-topic segment appeared in the restored series.",
      },
    ],
    before: { milestones: [] },
    after: {
      preview_version_id: PREVIEW_VERSION_ID,
      milestones: [
        {
          evidence_record_id: EVIDENCE_IDS[5],
          evidence_class: "validation_observation",
          source_ref: "browser:chart_state/0199a0c0-…-0506",
        },
      ],
    },
    coverage_limits: [],
  },
  gate_decision: {
    decision: "approved_live",
    decided_at: "2026-10-03T15:24:19Z",
    reason:
      "A bounded, well-evidenced persistence change inside the authorized repository, within the live budget.",
    approved_scope: [
      "Persist saved conversion views for the Dudulemon account",
      "Add a control that saves the current conversion-view state with a name",
      "Add a list or selector for reopening a saved view",
      "Restore the agreed filters and associated visualization state",
    ],
    excluded_scope: [
      "Sharing, notifications, rename, duplication or deletion",
      "Billing changes",
      "A general dashboard redesign",
    ],
    required_checks: ["npm run lint", "npm run typecheck", "npm test", "npm run build"],
  },
  implementation: {
    repository_ref: "ShopalyticsStore/shopalytics",
    branch_ref: "utrace/saved-conversion-views",
    pull_request_ref: PULL_REQUEST_URL,
    pull_request_number: 482,
    implementation_attempt_id: ATTEMPT_ID,
    preview_version_id: PREVIEW_VERSION_ID,
    source_commit: "c41d9b70e2a7",
    source_digest: `sha256:${"2d".repeat(32)}`,
    build_digest: `sha256:${"4e".repeat(32)}`,
    base_revision: "8f21c6f4a0b9",
    dataset_manifest_digest: `sha256:${"a7".repeat(32)}`,
    files_touched: [
      "src/dashboard/conversion/SavedViews.tsx",
      "src/dashboard/conversion/filters.ts",
      "src/server/savedViews.ts",
      "src/server/migrations/0042_saved_views.sql",
    ],
    checks: [
      { name: "npm run lint", outcome: "passed" },
      { name: "npm run typecheck", outcome: "passed" },
      { name: "npm test", outcome: "passed" },
      { name: "npm run build", outcome: "passed" },
    ],
    cleanup_state: "workspace destroyed; candidate runtime retained until the Case File published",
  },
  risk: {
    blast_radius: cited(
      "One new table and one dashboard panel; no existing conversion query changes shape.",
      3,
      "implementation_artifact",
      "artifact:diff/0199a0c0-…-0504",
    ),
    assumptions: [
      "Saved views are per client user, not shared across the customer organization.",
      "The filter stack is serializable as it stands today.",
    ],
    follow_up_checks: [
      "Confirm the new table's retention under the Shopalytics data policy.",
      "Check the saved-view list under an account with hundreds of views.",
    ],
  },
  next_action: {
    action: "review_pull_request",
    detail: `Review and merge ${PULL_REQUEST_URL}. It carries the exact commit the growth lead validated.`,
  },
};

export const fixtureCaseFile: CaseFileDetail = {
  case_file: savedViewsCaseFileRecord,
  core: savedViewsCore,
};
