/**
 * `POST /v1/extension/installations`, mirroring `installation-registration.v1`.
 *
 * The extension registers the public half of a non-exportable device key and
 * proves possession of the private half. It never receives or stores a reusable
 * bearer credential, and it never receives the global identity graph.
 */
import { z } from "zod";

import {
  base64UrlSchema,
  extensionVersionSchema,
  parseWith,
  protocolVersionSchema,
  schemaVersionSchema,
  timestampSchema,
  unique,
  uuidSchema,
} from "./common";

export const proofOfPossessionSchema = z.strictObject({
  algorithm: z.literal("ES256"),
  challenge_b64u: base64UrlSchema,
  signature_b64u: base64UrlSchema,
});

export const installationRegistrationRequestSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("installation_registration_request"),
  link_code: base64UrlSchema,
  device_public_key_spki_b64u: base64UrlSchema,
  extension_version: extensionVersionSchema,
  supported_protocol_versions: unique(z.array(protocolVersionSchema).min(1).max(4)),
  proof_of_possession: proofOfPossessionSchema,
});

export const installationRegistrationResponseSchema = z.strictObject({
  schema_version: schemaVersionSchema,
  kind: z.literal("installation_registration_response"),
  installation_id: uuidSchema,
  account_link_state: z.enum(["linked", "revoked"]),
  negotiated_protocol_version: protocolVersionSchema,
  control_socket_path: z.literal("/v1/extension/control"),
  registered_at: timestampSchema,
});

export const installationMessageSchema = z.union([
  installationRegistrationRequestSchema,
  installationRegistrationResponseSchema,
]);

export type ProofOfPossession = z.infer<typeof proofOfPossessionSchema>;
export type InstallationRegistrationRequest = z.infer<
  typeof installationRegistrationRequestSchema
>;
export type InstallationRegistrationResponse = z.infer<
  typeof installationRegistrationResponseSchema
>;

export function parseInstallationRegistrationResponse(
  candidate: unknown,
): InstallationRegistrationResponse {
  return parseWith(
    installationRegistrationResponseSchema,
    candidate,
    "invalid_installation_message",
    "The control plane returned an unusable installation registration",
  );
}
