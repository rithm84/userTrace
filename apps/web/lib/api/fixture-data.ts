/**
 * The Shopalytics fixture the product surfaces render without a control plane.
 *
 * Every figure comes from `docs/SCENARIO.md`: Shopalytics is the client,
 * Dudulemon the customer organization, Maya the originating user, and the
 * reward is 250.00 USD. It backs the route smoke tests and
 * `UTRACE_CONTROL_PLANE_FIXTURES=1` local previews; it is illustrative data,
 * never a claim about real work.
 *
 * Every shape here is the control plane's own, so the fixture cannot drift from
 * the contract without failing the same schemas the HTTP client parses. Records
 * are identified by UUID because the control plane mints UUIDv7 identifiers.
 */

import type {
  CaseDetail,
  CaseFileList,
  CaseFileRecord,
  CaseList,
  CaseOverview,
  Client,
  ClientConfiguration,
  ClientMember,
  Delivery,
  InterviewSession,
  OfferDisposition,
  OffersView,
  ParticipantAccount,
  ProductSignal,
  ResolvedReference,
  SessionProjection,
} from "./schemas";
import { REFERENCE_KIND_CLIENT_USER, REFERENCE_KIND_CUSTOMER_ACCOUNT } from "./schemas";

export const FIXTURE_CLIENT_ID = "0199a0c0-0000-7aaa-8000-00000000c101";
export const FIXTURE_SESSION_ID = "0199a0c0-0000-7aaa-8000-0000000055e1";
export const FIXTURE_EXTENSION_INSTALLATION_ID = "0199a0c0-0000-7aaa-8000-000000001457";
export const FIXTURE_PREVIEW_ALLOCATION_ID = "0199a0c0-0000-7aaa-8000-00000000a110";
export const FIXTURE_CASE_ID = "0199a0c0-0000-7aaa-8000-0000000005a7";
export const FIXTURE_CASE_FILE_ID = "0199a0c0-0000-7aaa-8000-0000000000cf";
export const FIXTURE_INVITATION_TOKEN = "demo-invitation";
export const FIXTURE_SAVED_VIEW_NAME = "TikTok sizing drop-off";
export const FIXTURE_EXAMPLE_NOTE =
  "Illustrative Shopalytics example; every name, figure and quote is fictional.";

const OWNER_MEMBER_ID = "0199a0c0-0000-7aaa-8000-000000000e01";
const ADMIN_MEMBER_ID = "0199a0c0-0000-7aaa-8000-000000000e02";
const FORMER_MEMBER_ID = "0199a0c0-0000-7aaa-8000-000000000e03";

const MAYA_CLIENT_USER_ID = "0199a0c0-0000-7aaa-8000-000000000a01";
const DUDULEMON_ACCOUNT_ID = "0199a0c0-0000-7aaa-8000-000000000b01";
const NORTHWIND_ACCOUNT_ID = "0199a0c0-0000-7aaa-8000-000000000b02";
const BRIGHTSAIL_ACCOUNT_ID = "0199a0c0-0000-7aaa-8000-000000000b03";
const ADE_CLIENT_USER_ID = "0199a0c0-0000-7aaa-8000-000000000a02";
const RIN_CLIENT_USER_ID = "0199a0c0-0000-7aaa-8000-000000000a03";

const EXPORT_CASE_ID = "0199a0c0-0000-7aaa-8000-0000000005a8";
const WEEKEND_CASE_ID = "0199a0c0-0000-7aaa-8000-0000000005a9";

const RELEASE_ID = "0199a0c0-0000-7aaa-8000-000000000f01";
const GATE_DECISION_ID = "0199a0c0-0000-7aaa-8000-000000000901";
const ATTEMPT_ID = "0199a0c0-0000-7aaa-8000-000000000701";
const PREVIEW_VERSION_ID = "0199a0c0-0000-7aaa-8000-000000000801";
const VALIDATION_ATTEMPT_ID = "0199a0c0-0000-7aaa-8000-000000000601";
const DELIVERY_ID = "0199a0c0-0000-7aaa-8000-000000000d01";
const OFFER_ID = "0199a0c0-0000-7aaa-8000-000000000201";

export const FIXTURE_MAYA_REF = `${REFERENCE_KIND_CLIENT_USER}:${MAYA_CLIENT_USER_ID}`;
export const FIXTURE_DUDULEMON_REF = `${REFERENCE_KIND_CUSTOMER_ACCOUNT}:${DUDULEMON_ACCOUNT_ID}`;

export const REQUEST_TEXT =
  "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let me save a view for TikTok traffic from women 25-34 on products with negative sizing reviews, then reopen it later?";

export const PULL_REQUEST_URL = "https://github.com/ShopalyticsStore/shopalytics/pull/482";

export const fixtureClient: Client = {
  client: {
    id: FIXTURE_CLIENT_ID,
    name: "Shopalytics",
    product_name: "Shopalytics",
    revision: 4,
    created_at: "2026-10-03T08:00:00Z",
  },
  membership: {
    id: OWNER_MEMBER_ID,
    client_id: FIXTURE_CLIENT_ID,
    role: "owner",
    status: "active",
    created_at: "2026-10-03T08:00:00Z",
    email: "priya@shopalytics.example",
  },
};

export const fixtureConfiguration: ClientConfiguration = {
  configuration_release_id: RELEASE_ID,
  release_number: 3,
  digest: `sha256:${"f3".repeat(32)}`,
  customer_surface: {
    provider: "discord",
    installation_ref: "1310244027450560512",
    watched_channels: [
      { channel_ref: "1310244027450560600", name: "feature-requests" },
      { channel_ref: "1310244027450560601", name: "bugs" },
    ],
  },
  repository: {
    ref: "ShopalyticsStore/shopalytics",
    base_revision: "4845ad6ffd7ba81bd84debe806f9bf660b9381a5",
    permitted_paths: ["src/", "db/", "tests/"],
    blocked_paths: ["deploy/", "utrace/", "fixtures/", "scripts/"],
  },
  outreach_offers: {
    enabled: true,
    currency: "USD",
    fixed_amount_minor_units: 25_000,
    authorized_ceiling_minor_units: 100_000,
    billing_account_ref: "stripe_sandbox_shopalytics",
    billing_use: "invoice credit toward future Shopalytics bills",
    completion_terms: [
      "a completed interview, and participation through validation of any change",
    ],
  },
  data_seeding_manifest_hash: `sha256:${"a7".repeat(32)}`,
  environment_template_id: "shopalytics-dudulemon-preview",
};

export const fixtureMembers: readonly ClientMember[] = [
  fixtureClient.membership,
  {
    id: ADMIN_MEMBER_ID,
    client_id: FIXTURE_CLIENT_ID,
    role: "admin",
    status: "active",
    created_at: "2026-10-03T08:32:54Z",
    email: "tom@shopalytics.example",
  },
  {
    id: FORMER_MEMBER_ID,
    client_id: FIXTURE_CLIENT_ID,
    role: "admin",
    status: "revoked",
    created_at: "2026-10-03T08:33:00Z",
    email: "lena@shopalytics.example",
  },
];

export const fixtureReferences: readonly ResolvedReference[] = [
  {
    ref: FIXTURE_MAYA_REF,
    kind: REFERENCE_KIND_CLIENT_USER,
    id: MAYA_CLIENT_USER_ID,
    label: "Maya Okonkwo",
  },
  {
    ref: FIXTURE_DUDULEMON_REF,
    kind: REFERENCE_KIND_CUSTOMER_ACCOUNT,
    id: DUDULEMON_ACCOUNT_ID,
    label: "Dudulemon",
  },
  {
    ref: `${REFERENCE_KIND_CLIENT_USER}:${ADE_CLIENT_USER_ID}`,
    kind: REFERENCE_KIND_CLIENT_USER,
    id: ADE_CLIENT_USER_ID,
    label: "Ade Bakare",
  },
  {
    ref: `${REFERENCE_KIND_CUSTOMER_ACCOUNT}:${NORTHWIND_ACCOUNT_ID}`,
    kind: REFERENCE_KIND_CUSTOMER_ACCOUNT,
    id: NORTHWIND_ACCOUNT_ID,
    label: "Northwind Goods",
  },
  {
    ref: `${REFERENCE_KIND_CLIENT_USER}:${RIN_CLIENT_USER_ID}`,
    kind: REFERENCE_KIND_CLIENT_USER,
    id: RIN_CLIENT_USER_ID,
    label: "Rin Takahashi",
  },
  {
    ref: `${REFERENCE_KIND_CUSTOMER_ACCOUNT}:${BRIGHTSAIL_ACCOUNT_ID}`,
    kind: REFERENCE_KIND_CUSTOMER_ACCOUNT,
    id: BRIGHTSAIL_ACCOUNT_ID,
    label: "Brightsail",
  },
];

function signal(
  overrides: Readonly<{
    id: string;
    state: ProductSignal["state"];
    initial_flow_type: ProductSignal["initial_flow_type"];
    client_user_id: string;
    customer_account_id: string;
    created_at: string;
    updated_at: string;
    closure_reason?: string;
    established_flow_type?: ProductSignal["established_flow_type"];
  }>,
): ProductSignal {
  return {
    id: overrides.id,
    client_id: FIXTURE_CLIENT_ID,
    state: overrides.state,
    revision: 12,
    machine_version: 1,
    initial_flow_type: overrides.initial_flow_type,
    classification_revision: 1,
    concern_revision: 1,
    acceptance_criteria_revision: 1,
    originating_client_user_id: overrides.client_user_id,
    originating_customer_account_id: overrides.customer_account_id,
    pinned_configuration_release_id: RELEASE_ID,
    created_at: overrides.created_at,
    updated_at: overrides.updated_at,
    ...(overrides.established_flow_type === undefined
      ? {}
      : { established_flow_type: overrides.established_flow_type }),
    ...(overrides.closure_reason === undefined
      ? {}
      : { closure_reason: overrides.closure_reason }),
  };
}

const savedViewsSignal = signal({
  id: FIXTURE_CASE_ID,
  state: "closed_validated",
  initial_flow_type: "feature_request_flow",
  established_flow_type: "feature_request_flow",
  client_user_id: MAYA_CLIENT_USER_ID,
  customer_account_id: DUDULEMON_ACCOUNT_ID,
  created_at: "2026-10-03T14:50:47Z",
  updated_at: "2026-10-03T17:06:56Z",
  closure_reason: "validated_implementation",
});

const exportCrashSignal = signal({
  id: EXPORT_CASE_ID,
  state: "gate_pending",
  initial_flow_type: "feature_request_flow",
  client_user_id: ADE_CLIENT_USER_ID,
  customer_account_id: NORTHWIND_ACCOUNT_ID,
  created_at: "2026-10-03T17:31:06Z",
  updated_at: "2026-10-03T17:59:22Z",
});

const weekendDropSignal = signal({
  id: WEEKEND_CASE_ID,
  state: "implementing",
  initial_flow_type: "feature_request_flow",
  established_flow_type: "feature_request_flow",
  client_user_id: RIN_CLIENT_USER_ID,
  customer_account_id: BRIGHTSAIL_ACCOUNT_ID,
  created_at: "2026-10-03T16:32:17Z",
  updated_at: "2026-10-03T17:57:29Z",
});

function overview(
  current: ProductSignal,
  concern: Readonly<{ pain_point: string; validation_criterion: string }> | null,
): CaseOverview {
  return {
    product_signal_id: current.id,
    originating_user_ref: `${REFERENCE_KIND_CLIENT_USER}:${current.originating_client_user_id}`,
    customer_account_ref: `${REFERENCE_KIND_CUSTOMER_ACCOUNT}:${current.originating_customer_account_id}`,
    ...(concern === null
      ? {}
      : {
          concern: {
            concern_revision: 1,
            origin: "interview_established",
            pain_point: concern.pain_point,
            validation_criterion: concern.validation_criterion,
            established_at: current.created_at,
          },
        }),
  };
}

const savedViewsOverview = overview(
  savedViewsSignal,
  {
    pain_point:
      "Rebuilding the same date range, channel, demographic, review-topic and conversion filters every Monday.",
    validation_criterion: `A named saved view restores every filter and the associated conversion display after leaving or refreshing the dashboard.`,
  },
);

const exportCrashOverview = overview(
  exportCrashSignal,
  {
    pain_point: "Every Monday the growth team downloads the same conversion report by hand.",
    validation_criterion: "The weekly conversion report is delivered without a manual download.",
  },
);

const weekendDropOverview = overview(
  weekendDropSignal,
  {
    pain_point: "A weekend conversion drop cannot be broken down by channel without a manual query.",
    validation_criterion: "The weekend comparison breaks the drop down by acquisition channel.",
  },
);

export const fixtureCases: CaseList = {
  cases: [exportCrashSignal, weekendDropSignal, savedViewsSignal],
  overviews: [
    exportCrashOverview,
    weekendDropOverview,
    savedViewsOverview,
  ],
};

const savedViewsOffer: OfferDisposition = {
  outreach_offer_id: OFFER_ID,
  product_signal_id: FIXTURE_CASE_ID,
  customer_account_id: DUDULEMON_ACCOUNT_ID,
  currency: "USD",
  amount_minor_units: 25_000,
  state: "earned",
  issuance_state: "issued",
  provider_transaction_ref: "cbtxn_1QfixtureDudulemon",
  invoice_application_minor_units: 25_000,
};

const exportCrashOffer: OfferDisposition = {
  outreach_offer_id: "0199a0c0-0000-7aaa-8000-000000000202",
  product_signal_id: EXPORT_CASE_ID,
  customer_account_id: NORTHWIND_ACCOUNT_ID,
  currency: "USD",
  amount_minor_units: 25_000,
  state: "reserved",
};

const weekendDropOffer: OfferDisposition = {
  outreach_offer_id: "0199a0c0-0000-7aaa-8000-000000000203",
  product_signal_id: WEEKEND_CASE_ID,
  customer_account_id: BRIGHTSAIL_ACCOUNT_ID,
  currency: "USD",
  amount_minor_units: 25_000,
  state: "earned",
  issuance_state: "blocked",
  issuance_blocked_reason:
    "The mapped Stripe Customer changed; issuance is blocked for reconciliation and the reward stays owed.",
};

export const fixtureOffers: OffersView = {
  currency: "USD",
  authorized_minor_units: 100_000,
  reserved_minor_units: 25_000,
  earned_minor_units: 50_000,
  remaining_minor_units: 25_000,
  offers: [savedViewsOffer, weekendDropOffer, exportCrashOffer],
};

const savedViewsDelivery: Delivery = {
  delivery_id: DELIVERY_ID,
  case_file_id: FIXTURE_CASE_FILE_ID,
  product_signal_id: FIXTURE_CASE_ID,
  state: "delivered",
  repository_ref: "ShopalyticsStore/shopalytics",
  pull_request_ref: PULL_REQUEST_URL,
};

export const savedViewsCaseFileRecord: CaseFileRecord = {
  id: FIXTURE_CASE_FILE_ID,
  client_id: FIXTURE_CLIENT_ID,
  product_signal_id: FIXTURE_CASE_ID,
  variant: "validated_implementation",
  stage: "published",
  revision: 5,
  core_digest: `sha256:${"9c".repeat(32)}`,
  delivery_id: DELIVERY_ID,
  sealed_at: "2026-10-03T17:06:53Z",
  published_at: "2026-10-03T17:06:56Z",
  created_at: "2026-10-03T17:06:29Z",
};

export const fixtureCaseFiles: CaseFileList = {
  case_files: [savedViewsCaseFileRecord],
  deliveries: [savedViewsDelivery],
};

export const fixtureCaseDetails: Readonly<Record<string, CaseDetail>> = {
  [FIXTURE_CASE_ID]: {
    case: savedViewsSignal,
    overview: savedViewsOverview,
    gate_decisions: [
      {
        id: GATE_DECISION_ID,
        client_id: FIXTURE_CLIENT_ID,
        product_signal_id: FIXTURE_CASE_ID,
        decision: "approved_live",
        classification_revision: 1,
        acceptance_criteria_revision: 1,
        pinned_configuration_release_id: RELEASE_ID,
        decided_at: "2026-10-03T15:24:19Z",
      },
    ],
    implementation_attempts: [
      {
        id: ATTEMPT_ID,
        client_id: FIXTURE_CLIENT_ID,
        product_signal_id: FIXTURE_CASE_ID,
        gate_decision_id: GATE_DECISION_ID,
        state: "candidate_ready",
        revision: 7,
        repository_ref: "ShopalyticsStore/shopalytics",
        base_revision: "8f21c6f4a0b9",
        created_at: "2026-10-03T15:24:23Z",
        updated_at: "2026-10-03T15:25:18Z",
      },
    ],
    validation_attempts: [
      {
        id: VALIDATION_ATTEMPT_ID,
        client_id: FIXTURE_CLIENT_ID,
        product_signal_id: FIXTURE_CASE_ID,
        implementation_attempt_id: ATTEMPT_ID,
        preview_version_id: PREVIEW_VERSION_ID,
        interview_session_id: FIXTURE_SESSION_ID,
        client_user_id: MAYA_CLIENT_USER_ID,
        state: "completed",
        revision: 3,
        dataset_manifest_digest: `sha256:${"a7".repeat(32)}`,
        acceptance_criteria_revision: 1,
        outcome: "validated",
        completed_at: "2026-10-03T15:26:01Z",
        created_at: "2026-10-03T15:25:25Z",
      },
    ],
    interview_sessions: [
      {
        id: FIXTURE_SESSION_ID,
        purpose: "interview",
        state: "closed",
        phase: "closing",
        created_at: "2026-10-03T15:23:28Z",
        updated_at: "2026-10-03T15:26:07Z",
        closed_at: "2026-10-03T15:26:07Z",
        closure_reason: "completed",
      },
    ],
    preview_versions: [
      {
        id: PREVIEW_VERSION_ID,
        implementation_attempt_id: ATTEMPT_ID,
        state: "ready",
        source_commit: "c41d9b70e2a7",
        source_digest: `sha256:${"2d".repeat(32)}`,
        build_digest: `sha256:${"4e".repeat(32)}`,
        base_revision: "8f21c6f4a0b9",
        ready_at: "2026-10-03T15:25:18Z",
      },
    ],
    offer: savedViewsOffer,
    stoppable: false,
    case_file: savedViewsCaseFileRecord,
    delivery: savedViewsDelivery,
  },
  [EXPORT_CASE_ID]: {
    case: exportCrashSignal,
    overview: exportCrashOverview,
    gate_decisions: [],
    implementation_attempts: [],
    validation_attempts: [],
    interview_sessions: [
      {
        id: "0199a0c0-0000-7aaa-8000-0000000055e2",
        purpose: "interview",
        state: "closed",
        phase: "closing",
        created_at: "2026-10-03T17:37:12Z",
        updated_at: "2026-10-03T17:38:07Z",
        closed_at: "2026-10-03T17:38:07Z",
        closure_reason: "completed",
      },
    ],
    preview_versions: [],
    offer: exportCrashOffer,
    stoppable: true,
  },
  [WEEKEND_CASE_ID]: {
    case: weekendDropSignal,
    overview: weekendDropOverview,
    gate_decisions: [
      {
        id: "0199a0c0-0000-7aaa-8000-000000000902",
        client_id: FIXTURE_CLIENT_ID,
        product_signal_id: WEEKEND_CASE_ID,
        decision: "approved_live",
        classification_revision: 1,
        acceptance_criteria_revision: 1,
        pinned_configuration_release_id: RELEASE_ID,
        decided_at: "2026-10-03T16:57:10Z",
      },
    ],
    implementation_attempts: [
      {
        id: "0199a0c0-0000-7aaa-8000-000000000702",
        client_id: FIXTURE_CLIENT_ID,
        product_signal_id: WEEKEND_CASE_ID,
        gate_decision_id: "0199a0c0-0000-7aaa-8000-000000000902",
        state: "executing",
        revision: 2,
        repository_ref: "ShopalyticsStore/shopalytics",
        base_revision: "8f21c6f4a0b9",
        created_at: "2026-10-03T17:29:18Z",
        updated_at: "2026-10-03T17:57:29Z",
      },
    ],
    validation_attempts: [],
    interview_sessions: [],
    preview_versions: [],
    offer: weekendDropOffer,
    stoppable: true,
  },
};

/** One material claim and the single evidence slice this fixture cites for it. */
/**
 * The durable Interview Session row, as `GET /v1/sessions/{id}` answers it.
 * The session tab renders the committed projection below; this is the
 * authoritative record it falls back to.
 */
export const fixtureInterviewSession: InterviewSession = {
  id: FIXTURE_SESSION_ID,
  client_id: FIXTURE_CLIENT_ID,
  product_signal_id: FIXTURE_CASE_ID,
  state: "live",
  revision: 9,
  phase: "discovery",
  readiness: { conversational: true, observation: true },
  character_state: "listening",
  character_revision: 5,
  implementation_progress: "not_started",
  controls: { muted: false, paused: false },
  created_at: "2026-10-03T15:23:28Z",
  updated_at: "2026-10-03T15:24:06Z",
};

export const fixtureSessionProjection: SessionProjection = {
  session_id: FIXTURE_SESSION_ID,
  product_signal_id: FIXTURE_CASE_ID,
  revision: 9,
  client_name: "Shopalytics",
  agent_name: "Ivo",
  agent_character: "coral",
  purpose: "interview",
  state: "interviewing",
  phase: "observed_workflow",
  readiness: { conversational: true, observation: true },
  status_text: "Listening",
  character: {
    revision: 5,
    state: "listening",
    gazeX: 0,
    gazeY: 0,
    speechEnergy: 0,
    motionScale: 0.35,
  },
  controls: { microphone: "unmuted", available: ["mute", "pause", "finish_later", "end_confirm"] },
  implementation_progress: null,
  preview_available: true,
  end_reason: null,
};

/** Maya's own account: the one interview in this fixture, still live. */
export const fixtureParticipantAccount: ParticipantAccount = {
  participation_count: 1,
  interviews: [
    {
      session_id: FIXTURE_SESSION_ID,
      client_name: fixtureSessionProjection.client_name,
      topic: "Saved conversion views",
      purpose: fixtureSessionProjection.purpose,
      state: fixtureSessionProjection.state,
      end_reason: fixtureSessionProjection.end_reason,
      updated_at: fixtureInterviewSession.updated_at,
    },
  ],
  preferences: { revision: 0, visual_guidance_enabled: true, agent_cursor_enabled: true },
};
