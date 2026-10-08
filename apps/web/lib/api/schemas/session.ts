/**
 * What the control plane returns on every participant route the session tab
 * calls, and the shape of the durable session projection Supabase Realtime
 * distributes.
 *
 * Two different things live here and they are not the same record. The API's
 * `GET /v1/sessions/{id}` answers the durable `interview_sessions` row, in the
 * lifecycle's own vocabulary. The projection in `public.session_projections` is
 * the tab's vocabulary — an encounter, not a lifecycle row — and the control
 * plane recomputes and commits it inside the transaction that caused it. The
 * tab renders the projection and falls back to the durable record.
 */

import { z } from "zod";

import { absentWhenNull } from "./absent";

import {
  agentCharacterStateSchema,
  implementationProgressSchema,
  interviewPhaseSchema,
  interviewSessionStateSchema,
} from "./lifecycle";

const uuid = z.uuid();
const timestamp = z.iso.datetime({ offset: true });
const text = z.string().min(1);

/* --------------------------------------------------------- durable record */

export const sessionControlsStateSchema = z.object({
  muted: z.boolean(),
  paused: z.boolean(),
});

export const sessionReadinessSchema = z.object({
  conversational: z.boolean(),
  observation: z.boolean(),
});

/** The durable Interview Session row, as `GET /v1/sessions/{id}` answers it. */
export const interviewSessionSchema = z.object({
  id: uuid,
  client_id: uuid,
  product_signal_id: uuid,
  state: interviewSessionStateSchema,
  revision: z.int(),
  phase: interviewPhaseSchema,
  readiness: sessionReadinessSchema,
  character_state: agentCharacterStateSchema,
  character_revision: z.int(),
  implementation_progress: implementationProgressSchema,
  controls: sessionControlsStateSchema,
  closure_reason: absentWhenNull(text),
  created_at: timestamp,
  updated_at: timestamp,
});
export type InterviewSession = z.infer<typeof interviewSessionSchema>;

export const sessionRecordSchema = z.object({ session: interviewSessionSchema });

/* ------------------------------------------------------- tab projection */

export const tabStateSchema = z.enum([
  "participant_setup",
  "interviewing",
  "paused",
  "reconnecting",
  "ended",
]);
export type TabState = z.infer<typeof tabStateSchema>;

/**
 * The participant-facing phase. The control plane splits the durable
 * `discovery` phase in two: problem discussion may begin while the preview
 * prepares, and observed workflow probing waits for the complete evidence path.
 */
export const tabPhaseSchema = z.enum([
  "preflight",
  "greeting",
  "problem_discussion",
  "observed_workflow",
  "implementation",
  "validation",
  "closed",
]);

/** The user-facing implementation progress the session tab shows. */
export const tabProgressSchema = z.enum([
  "reproduction_captured",
  "making_changes",
  "preparing_the_preview",
  "ready_for_validation",
]);
export type TabProgress = z.infer<typeof tabProgressSchema>;

export const sessionEndReasonSchema = z.enum([
  "finish_later",
  "connection_lost",
  "ended_by_participant",
  "client_stopped",
  "completed",
  "no_implementation",
  "blocked",
  "not_validated",
]);
export type SessionEndReason = z.infer<typeof sessionEndReasonSchema>;

/**
 * The character states the interview runtime publishes in the projection.
 *
 * Two more than the shared `agent-character-state` contract: the runtime's
 * migration adds `paused` and `reconnecting` to `public.agent_character_state`
 * without adding them to the schema the contract package ships. The Rive rig
 * carries both, so the tab renders them; the drift is recorded for integration.
 */
export const CHARACTER_PROJECTION_STATES = [
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

export const characterProjectionStateSchema = z.enum(CHARACTER_PROJECTION_STATES);

export const characterProjectionSchema = z.object({
  revision: z.int().nonnegative(),
  state: characterProjectionStateSchema,
  gazeX: z.number().min(-1).max(1),
  gazeY: z.number().min(-1).max(1),
  speechEnergy: z.number().min(0).max(1),
  motionScale: z.number().min(0).max(1),
});

export const sessionControlActionSchema = z.enum([
  "mute",
  "unmute",
  "pause",
  "resume",
  "finish_later",
  "end_confirm",
]);
export type SessionControlAction = z.infer<typeof sessionControlActionSchema>;

export const controlsStateSchema = z.object({
  microphone: z.enum(["muted", "unmuted"]),
  available: z.array(sessionControlActionSchema),
});
export type ControlsState = z.infer<typeof controlsStateSchema>;

export const readinessSchema = z.object({
  conversational: z.boolean(),
  observation: z.boolean(),
  unavailable_detail: absentWhenNull(text),
});

export const agentCharacterColourSchema = z.enum([
  "coral",
  "black",
  "mint",
  "sky",
  "lilac",
  "peach",
  "aqua",
]);
export type AgentCharacterColour = z.infer<typeof agentCharacterColourSchema>;

/**
 * The Postgres table the projection lives in. Supabase Realtime distributes its
 * rows under row-level security scoped to the participant, and the session tab
 * reads the same table for its first render.
 */
export const SESSION_PROJECTION_TABLE = "session_projections";

/**
 * The committed `session_projections.projection` document. Realtime carries the
 * row; the durable Postgres projection stays authoritative, and `revision` is
 * monotonic so an out-of-order delivery can be dropped.
 */
export const sessionProjectionSchema = z.object({
  session_id: uuid,
  product_signal_id: uuid,
  revision: z.int().nonnegative(),
  client_name: text,
  agent_name: text,
  agent_character: agentCharacterColourSchema,
  purpose: z.enum(["interview", "validation"]),
  state: tabStateSchema,
  phase: tabPhaseSchema,
  readiness: readinessSchema,
  status_text: text,
  character: characterProjectionSchema,
  controls: controlsStateSchema,
  implementation_progress: tabProgressSchema.nullable(),
  preview_available: z.boolean(),
  end_reason: sessionEndReasonSchema.nullable(),
});
export type SessionProjection = z.infer<typeof sessionProjectionSchema>;

/**
 * The committed projection row, as `GET /v1/sessions/{id}/projection` answers
 * it: the same revision and document Supabase Realtime distributes, so the
 * first render and every later revision are one record.
 */
export const sessionProjectionDocumentSchema = z.object({
  revision: z.int().nonnegative(),
  projection: sessionProjectionSchema,
});

/** The revision a control acknowledgement commits; the subscription sees it too. */
export const sessionControlAckSchema = z.object({
  revision: z.int().nonnegative(),
  state: tabStateSchema,
  controls: controlsStateSchema,
});
export type SessionControlAck = z.infer<typeof sessionControlAckSchema>;

/* --------------------------------------------------- participant routes */

export const invitationClaimSchema = z.object({
  session_id: uuid,
  product_signal_id: uuid,
  already_claimed: z.boolean(),
});
export type InvitationClaim = z.infer<typeof invitationClaimSchema>;

export const preflightReportSchema = z.object({
  browser: z.object({
    name: text,
    major_version: z.int().nonnegative(),
    supported: z.boolean(),
  }),
  extension: z.object({
    installed: z.boolean(),
    account_linked: z.boolean(),
    installation_proof: text.optional(),
    protocol_version: text.optional(),
  }),
  permissions: z.object({ host: z.boolean(), microphone: z.boolean() }),
});
export type PreflightReport = z.infer<typeof preflightReportSchema>;

/** The checks the control plane runs, in the order it reports them. */
export const PREFLIGHT_CHECK_IDS = [
  "supported_browser",
  "extension_installed",
  "extension_account_linked",
  "extension_host_permission",
  "microphone",
] as const;

export const preflightCheckIdSchema = z.enum(PREFLIGHT_CHECK_IDS);
export type PreflightCheckId = z.infer<typeof preflightCheckIdSchema>;

export const preflightResultSchema = z.object({
  passed: z.boolean(),
  revision: z.int().nonnegative(),
  checks: z.array(
    z.object({ id: preflightCheckIdSchema, status: z.enum(["passed", "failed"]), detail: z.string() }),
  ),
});
export type PreflightResult = z.infer<typeof preflightResultSchema>;

export const voiceConnectionSchema = z.object({
  sdp_answer: text,
  connection_id: uuid,
  voice_run_id: uuid,
});
export type VoiceConnectionRecord = z.infer<typeof voiceConnectionSchema>;

export const previewHandoffSchema = z.object({
  front_door_url: z.url(),
  expires_at: text,
});
export type PreviewHandoff = z.infer<typeof previewHandoffSchema>;

/**
 * The single-use offer the session tab hands the official extension. `offer` is
 * opaque to the page: the control plane validates it together with the
 * installation's proof of possession and the embedded SDK's page proof.
 */
export const bindingOfferSchema = z.object({
  schema_version: z.literal(1),
  offer_token: text,
  expires_at: text,
  protocol_version: text,
  session_id: uuid,
  client_id: uuid,
  extension_installation_id: uuid,
  preview_runtime_allocation_id: uuid,
});
export type BindingOffer = z.infer<typeof bindingOfferSchema>;

/**
 * The one-time code the account-link page hands back to the extension. The
 * extension exchanges it at `POST /v1/extension/installations` together with a
 * device public key and a proof of possession; the page never sees an
 * installation identity or a credential.
 */
export const extensionLinkCodeSchema = z.object({
  link_code: z.string().min(16).max(4096).regex(/^[A-Za-z0-9_-]+$/u),
  expires_at: text,
  redirect_uri: z.url(),
});
export type ExtensionLinkCode = z.infer<typeof extensionLinkCodeSchema>;

/* ---------------------------------------------------- participant account */

/**
 * The participant's global action preferences (`public.extension_preferences`).
 * An update names the revision it read, so a stale page is refused rather than
 * overwriting a newer choice.
 */
export const participantPreferencesSchema = z.object({
  revision: z.int().nonnegative(),
  visual_guidance_enabled: z.boolean(),
  agent_cursor_enabled: z.boolean(),
});
export type ParticipantPreferences = z.infer<typeof participantPreferencesSchema>;

/** One Interview Session the participant took part in, newest first. */
export const participantInterviewSchema = z.object({
  session_id: uuid,
  client_name: text,
  topic: text,
  purpose: z.enum(["interview", "validation"]),
  state: tabStateSchema,
  end_reason: sessionEndReasonSchema.nullable(),
  updated_at: timestamp,
});
export type ParticipantInterview = z.infer<typeof participantInterviewSchema>;

/** `GET /v1/participants/me`: the signed-in participant's own account. */
export const participantAccountSchema = z.object({
  participation_count: z.int().nonnegative(),
  interviews: z.array(participantInterviewSchema),
  preferences: participantPreferencesSchema,
});
export type ParticipantAccount = z.infer<typeof participantAccountSchema>;

export type ParticipantPreferencesUpdate = Readonly<{
  expected_revision: number;
  visual_guidance_enabled: boolean;
  agent_cursor_enabled: boolean;
}>;
