/**
 * Identifier format and the enumerated first-class identifier names.
 *
 * Every first-class uTrace entity has its own opaque UUIDv7. Identifiers travel
 * as lowercase canonical strings, which is also their wire form, so there is one
 * representation everywhere and no parse/format step that could disagree between
 * the two bindings.
 *
 * Knowing an identifier grants no authority. Provider identifiers stay scoped
 * strings inside provider references, and W3C trace identity describes technical
 * execution only, so neither appears here.
 */

import { v7 as uuidv7 } from "uuid";
import { z } from "zod";

/** Canonical lowercase UUIDv7: version nibble 7, RFC 9562 variant bits. */
export const UUID_V7_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** A UUIDv7 in lowercase canonical string form. */
export const uuidV7Schema = z.string().regex(UUID_V7_PATTERN);

export type UuidV7 = z.infer<typeof uuidV7Schema>;

/** Mint a fresh UUIDv7 in the canonical wire form. */
export function newId(): UuidV7 {
  return uuidv7();
}

/**
 * The closed set of first-class identifier names.
 *
 * Mirrors `schemas/ids/identifier-name.v1.schema.json`; the conformance suite
 * fails if the two ever diverge. A generic correlation identifier never
 * substitutes for one of these, and relationships stay explicit.
 */
export const IDENTIFIER_NAMES = [
  "client_id",
  "utrace_user_id",
  "client_user_id",
  "customer_account_id",
  "customer_membership_id",
  "client_member_id",
  "intake_item_id",
  "product_signal_id",
  "outreach_attempt_id",
  "session_id",
  "preview_runtime_allocation_id",
  "environment_lease_id",
  "implementation_attempt_id",
  "preview_version_id",
  "validation_attempt_id",
  "case_file_id",
  "delivery_id",
  "job_id",
  "job_run_id",
  "command_id",
  "evidence_window_id",
  "artifact_id",
  "ingestion_receipt_id",
  "normalization_run_id",
  "observation_id",
  "browser_stream_id",
  "server_stream_id",
  "extension_binding_id",
  "document_attachment_id",
  "preview_environment_id",
  "runtime_id",
  "voice_run_id",
] as const;

export const identifierNameSchema = z.enum(IDENTIFIER_NAMES);

export type IdentifierName = z.infer<typeof identifierNameSchema>;

const domainIdShape = Object.fromEntries(
  IDENTIFIER_NAMES.map((name) => [name, uuidV7Schema.optional()]),
) as { readonly [Name in IdentifierName]: z.ZodOptional<typeof uuidV7Schema> };

/**
 * The typed domain identifiers a message carries.
 *
 * Every name is optional: a message states only the relationships it actually
 * establishes. The envelope's client scope, not this object, is what authorizes
 * tenant access; `client_id` appears here only for records whose subject is a
 * client other than the scoped one.
 */
export const domainIdsSchema = z.strictObject(domainIdShape);

export type DomainIds = z.infer<typeof domainIdsSchema>;
