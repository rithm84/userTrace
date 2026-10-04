/**
 * Document attestation claims.
 *
 * The control plane binds the attestation to the client, client user, customer
 * account, Interview Session, preview environment, runtime allocation, optional
 * candidate Preview Version, origin, document nonce, SDK installation,
 * protocol, annotation-manifest version, redaction-policy version, and expiry.
 * Every one of those bindings is checked here; a mismatch fails the activation
 * gate instead of downgrading the runtime.
 */

import type { BrowserSdkConfig } from "../core/config.js";
import { ActivationError } from "../core/failure.js";
import type { JsonObject } from "../core/json.js";
import { optionalString, requireString } from "../core/json.js";
import { isExpired } from "../core/time.js";

export interface DocumentAttestation {
  readonly attestation_id: string;
  readonly client_id: string;
  readonly client_user_id: string;
  readonly customer_account_id: string;
  readonly session_id: string;
  readonly preview_environment_id: string;
  readonly preview_runtime_allocation_id: string;
  readonly preview_version_id: string | null;
  readonly document_attachment_id: string;
  readonly origin: string;
  readonly document_nonce: string;
  readonly sdk_installation_id: string;
  readonly protocol_version: string;
  readonly schema_version: string;
  readonly annotation_manifest_version: string;
  readonly annotation_manifest_digest: string;
  readonly redaction_version: string;
  readonly policy_version: string;
  readonly issued_at: string;
  readonly expires_at: string;
}

export const decodeAttestation = (payload: JsonObject): DocumentAttestation => {
  const path = "document_attestation";
  return {
    attestation_id: requireString(payload, "attestation_id", path),
    client_id: requireString(payload, "client_id", path),
    client_user_id: requireString(payload, "client_user_id", path),
    customer_account_id: requireString(payload, "customer_account_id", path),
    session_id: requireString(payload, "session_id", path),
    preview_environment_id: requireString(payload, "preview_environment_id", path),
    preview_runtime_allocation_id: requireString(payload, "preview_runtime_allocation_id", path),
    preview_version_id: optionalString(payload, "preview_version_id", path),
    document_attachment_id: requireString(payload, "document_attachment_id", path),
    origin: requireString(payload, "origin", path),
    document_nonce: requireString(payload, "document_nonce", path),
    sdk_installation_id: requireString(payload, "sdk_installation_id", path),
    protocol_version: requireString(payload, "protocol_version", path),
    schema_version: requireString(payload, "schema_version", path),
    annotation_manifest_version: requireString(payload, "annotation_manifest_version", path),
    annotation_manifest_digest: requireString(payload, "annotation_manifest_digest", path),
    redaction_version: requireString(payload, "redaction_version", path),
    policy_version: requireString(payload, "policy_version", path),
    issued_at: requireString(payload, "issued_at", path),
    expires_at: requireString(payload, "expires_at", path),
  };
};

export interface AttestationBinding {
  readonly config: BrowserSdkConfig;
  readonly documentOrigin: string;
  readonly documentNonce: string;
  readonly nowMs: number;
}

/** Rejects an attestation that does not bind this exact document and build. */
export const assertAttestationBinding = (attestation: DocumentAttestation, binding: AttestationBinding): void => {
  const mismatch = (field: string, expected: string, received: string): ActivationError =>
    new ActivationError("attestation_invalid_or_expired", `attestation ${field} does not bind this document`, {
      field,
      expected,
      received,
    });

  if (attestation.origin !== binding.documentOrigin) {
    throw mismatch("origin", binding.documentOrigin, attestation.origin);
  }
  if (attestation.document_nonce !== binding.documentNonce) {
    throw mismatch("document_nonce", "the nonce minted for this document", "a different nonce");
  }
  if (attestation.sdk_installation_id !== binding.config.sdkInstallationId) {
    throw mismatch("sdk_installation_id", binding.config.sdkInstallationId, attestation.sdk_installation_id);
  }
  if (attestation.protocol_version !== binding.config.protocolVersion) {
    throw new ActivationError("incompatible_protocol_or_schema", "attestation pins another protocol major", {
      expected: binding.config.protocolVersion,
      received: attestation.protocol_version,
    });
  }
  if (attestation.schema_version !== binding.config.schemaVersion) {
    throw new ActivationError("incompatible_protocol_or_schema", "attestation pins another schema major", {
      expected: binding.config.schemaVersion,
      received: attestation.schema_version,
    });
  }
  if (isExpired(attestation.expires_at, binding.nowMs)) {
    throw new ActivationError("attestation_invalid_or_expired", "attestation is expired", {
      expires_at: attestation.expires_at,
      now: new Date(binding.nowMs).toISOString(),
    });
  }
};

/** Rejects a manifest whose version or digest is not the one the attestation pinned. */
export const assertManifestPinned = (
  attestation: DocumentAttestation,
  manifestVersion: string,
  manifestDigest: string,
): void => {
  if (attestation.annotation_manifest_version !== manifestVersion) {
    throw new ActivationError("incompatible_protocol_or_schema", "annotation manifest version is not the pinned one", {
      expected: attestation.annotation_manifest_version,
      received: manifestVersion,
    });
  }
  if (attestation.annotation_manifest_digest !== manifestDigest) {
    throw new ActivationError("attestation_invalid_or_expired", "annotation manifest digest is not the pinned one", {
      expected: attestation.annotation_manifest_digest,
      received: manifestDigest,
    });
  }
};
