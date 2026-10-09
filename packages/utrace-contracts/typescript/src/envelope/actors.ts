/**
 * Actor kinds: who or what a producer acted for.
 *
 * The kinds stay separate because the authority model does. Customer
 * participants never inherit client-staff access, client staff cannot edit
 * lifecycle state, model roles are advisory and never impersonate the person
 * whose evidence informed their proposal, and provider identities never become
 * product identity.
 */

import { z } from "zod";

import { uuidV7Schema } from "../ids/ids.js";
import { providerNameSchema } from "./failures.js";
import { snakeCaseNameSchema } from "./values.js";

export const CLIENT_MEMBER_ROLES = ["owner", "admin"] as const;

/** Sol for reasoning, investigation, and coding; Jev for intake decisions and semantic attention. */
export const AGENT_ROLES = ["sol", "jev"] as const;

export const clientMemberRoleSchema = z.enum(CLIENT_MEMBER_ROLES);
export const agentRoleSchema = z.enum(AGENT_ROLES);

export type ClientMemberRole = z.infer<typeof clientMemberRoleSchema>;
export type AgentRole = z.infer<typeof agentRoleSchema>;

/**
 * The client-scoped participant.
 *
 * Validation always requires the original `client_user_id`; no colleague,
 * client staff member, or second account may substitute.
 */
export const customerParticipantActorSchema = z.strictObject({
  kind: z.literal("customer_participant"),
  client_user_id: uuidV7Schema,
  customer_account_id: uuidV7Schema.optional(),
  customer_membership_id: uuidV7Schema.optional(),
});

/** Client staff: exactly one owner and zero or more admins per client. */
export const clientMemberActorSchema = z.strictObject({
  kind: z.literal("client_member"),
  client_member_id: uuidV7Schema,
  role: clientMemberRoleSchema,
});

/** A cognitive role. Its results are advisory; only the control plane authorizes effects. */
export const utraceAgentRoleActorSchema = z.strictObject({
  kind: z.literal("utrace_agent_role"),
  role: agentRoleSchema,
  model_version: z.string().min(1).max(200),
});

/** An external provider identity, kept as a scoped reference. */
export const providerIdentityActorSchema = z.strictObject({
  kind: z.literal("provider_identity"),
  provider: providerNameSchema,
  external_id: z.string().min(1).max(256),
  installation_ref: z.string().min(1).max(256).optional(),
  resource_type: snakeCaseNameSchema.optional(),
});

/** An internal uTrace service acting under its own scoped identity. */
export const serviceActorSchema = z.strictObject({
  kind: z.literal("service"),
  service: snakeCaseNameSchema,
});

/** The platform itself: scheduling, lease scans, reconciliation, and expiry. */
export const systemActorSchema = z.strictObject({
  kind: z.literal("system"),
});

export const actorSchema = z.discriminatedUnion("kind", [
  customerParticipantActorSchema,
  clientMemberActorSchema,
  utraceAgentRoleActorSchema,
  providerIdentityActorSchema,
  serviceActorSchema,
  systemActorSchema,
]);

export type CustomerParticipantActor = z.infer<typeof customerParticipantActorSchema>;
export type ClientMemberActor = z.infer<typeof clientMemberActorSchema>;
export type UTraceAgentRoleActor = z.infer<typeof utraceAgentRoleActorSchema>;
export type ProviderIdentityActor = z.infer<typeof providerIdentityActorSchema>;
export type ServiceActor = z.infer<typeof serviceActorSchema>;
export type SystemActor = z.infer<typeof systemActorSchema>;
export type Actor = z.infer<typeof actorSchema>;
