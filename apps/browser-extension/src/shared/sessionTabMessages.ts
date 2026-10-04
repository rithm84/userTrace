/**
 * The uTrace session tab's half of the extension conversation.
 *
 * `externally_connectable` lists only the uTrace-owned session-tab origins, so
 * this is the one place a page may reach the service worker. It may ask two
 * things and nothing else: whether this installation is linked and permitted
 * (the participant compatibility preflight), and that the extension take the
 * session's single-use binding offer. It cannot start an interview, bind a tab,
 * read a binding, or issue a product command, and it never receives a
 * credential.
 *
 * Both messages carry `protocol_version: "1"`, the extension protocol's major.
 * The page speaks majors, not the `1.x` minors the control socket negotiates.
 */
import { z } from "zod";

import { bindingOfferSchema } from "../protocol/control";
import { parseWith, uuidSchema } from "../protocol/common";

/** The protocol major the session tab and the extension agree on. */
export const SESSION_TAB_PROTOCOL_MAJOR = "1";

export const preflightRequestSchema = z.strictObject({
  type: z.literal("utrace_preflight_request"),
  protocol_version: z.string().min(1).max(8),
});

export const bindingOfferMessageSchema = z.strictObject({
  type: z.literal("utrace_binding_offer"),
  session_id: uuidSchema,
  binding_offer: bindingOfferSchema,
});

export const sessionTabMessageSchema = z.discriminatedUnion("type", [
  preflightRequestSchema,
  bindingOfferMessageSchema,
]);

export type PreflightRequest = z.infer<typeof preflightRequestSchema>;
export type BindingOfferMessage = z.infer<typeof bindingOfferMessageSchema>;
export type SessionTabMessage = z.infer<typeof sessionTabMessageSchema>;

/**
 * What the extension can prove about itself to the page. `installation_proof`
 * is `base64url(JSON(proof_of_possession))`: the page forwards it to the
 * control plane, which decodes it and checks the signature against the
 * registered device key. The page cannot read or forge it, and an unlinked
 * installation has none.
 */
export type PreflightResultMessage = Readonly<{
  type: "utrace_preflight_result";
  account_linked: boolean;
  host_permission: boolean;
  installation_proof: string | null;
  protocol_version: string;
}>;

export type BindingOfferAckMessage = Readonly<{
  type: "utrace_binding_offer_ack";
  accepted: boolean;
  detail?: string;
}>;

export type SessionTabResponse = PreflightResultMessage | BindingOfferAckMessage;

/** Parses one page message. An unknown shape is refused, never coerced. */
export function parseSessionTabMessage(candidate: unknown): SessionTabMessage {
  return parseWith(
    sessionTabMessageSchema,
    candidate,
    "invalid_binding_offer",
    "The uTrace session tab sent a message the extension cannot accept",
  );
}
