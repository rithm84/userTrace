/**
 * The activation gate.
 *
 * Activation is a bounded pre-application gate. The inert bootstrap posts to
 * the reserved same-origin path served by the preview front door, using the
 * preview's existing authenticated session context plus a fresh per-document
 * nonce. It verifies the returned signed document attestation with a packaged
 * Ed25519 public key and exchanges it once for a short-lived write-only
 * telemetry credential. Nothing reaches a URL, browser storage, page markup, or
 * an application-visible global.
 *
 * Reload, full navigation, and a telemetry credential that has expired or that
 * ingestion rejects each run this gate again with a new nonce. A prior document
 * cannot activate or export for its replacement.
 */

import type { BrowserView } from "../core/dom.js";
import type { BrowserSdkConfig } from "../core/config.js";
import { ActivationError } from "../core/failure.js";
import { asObject, parseJson, requireObject, requireString } from "../core/json.js";
import type { RandomSource } from "../core/ids.js";
import { nonce } from "../core/ids.js";
import { ACTIVATE_PATH, TELEMETRY_CREDENTIAL_PATH } from "../core/versions.js";
import type { AnnotationManifest } from "../annotations/manifest.js";
import { decodeAnnotationManifest } from "../annotations/manifest.js";
import type { DocumentAttestation } from "./attestation.js";
import { assertAttestationBinding, assertManifestPinned, decodeAttestation } from "./attestation.js";
import type { TelemetryCredential } from "./credential.js";
import { decodeCredential } from "./credential.js";
import type { SignedEnvelope } from "./signed-envelope.js";
import { decodeSignedEnvelope, payloadDigest, verifySignedEnvelope } from "./signed-envelope.js";

/**
 * The gate is bounded: a control-path request that does not answer within this
 * window aborts, so a slow or hung front door cannot hold the application
 * before hydration indefinitely.
 */
export const ACTIVATION_REQUEST_TIMEOUT_MS = 10_000;

/** Same-origin control-path fetch captured before any hook is installed. */
export type ControlFetch = (path: string, body: string) => Promise<Response>;

export const controlFetch = (view: BrowserView): ControlFetch => {
  const nativeFetch = view.fetch.bind(view);
  return async (path: string, body: string): Promise<Response> =>
    nativeFetch(new URL(path, view.location.origin).toString(), {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "content-type": "application/json", "x-utrace-control": "1" },
      signal: AbortSignal.timeout(ACTIVATION_REQUEST_TIMEOUT_MS),
      body,
    });
};

export interface ActivationRequest {
  readonly config: BrowserSdkConfig;
  readonly documentOrigin: string;
  readonly fetchControl: ControlFetch;
  readonly subtle: SubtleCrypto;
  readonly random: RandomSource;
  readonly nowMs: () => number;
}

/**
 * What one verified document attestation yields. A child frame stops here: it
 * authenticates its own document but never exports, so it never exchanges the
 * attestation for a telemetry credential.
 */
export interface AttestationResult {
  readonly attestation: DocumentAttestation;
  /** The signed envelope itself, which a child frame presents to the coordinator. */
  readonly attestationEnvelope: SignedEnvelope;
  readonly manifest: AnnotationManifest;
  /** Forwarded verbatim to the extension bridge; the control plane re-verifies it. */
  readonly pageProof: SignedEnvelope;
  readonly documentNonce: string;
}

export interface ActivationResult extends AttestationResult {
  readonly credential: TelemetryCredential;
}

const readJsonResponse = async (response: Response, path: string): Promise<string> => {
  const text = await response.text();
  if (!response.ok) {
    throw new ActivationError(
      path === ACTIVATE_PATH ? "activation_endpoint_unavailable" : "credential_rejected",
      `${path} returned a non-success status`,
      { path, status: response.status, body_bytes: text.length, body: text.slice(0, 512) },
    );
  }
  return text;
};

const postControl = async (request: ActivationRequest, path: string, body: string): Promise<string> => {
  let response: Response;
  try {
    response = await request.fetchControl(path, body);
  } catch (cause) {
    throw new ActivationError("activation_endpoint_unavailable", `${path} is unreachable`, {
      path,
      cause: String(cause),
    });
  }
  return readJsonResponse(response, path);
};

/**
 * Runs the attestation half of the gate: nonce, activation request, signature
 * verification against the packaged key, and every binding check.
 */
export const attestDocument = async (request: ActivationRequest): Promise<AttestationResult> => {
  const documentNonce = nonce(request.random);
  const activateBody = JSON.stringify({
    sdk_installation_id: request.config.sdkInstallationId,
    environment_name: request.config.environmentName,
    client_release_id: request.config.clientReleaseId,
    schema_version: request.config.schemaVersion,
    protocol_version: request.config.protocolVersion,
    document_nonce: documentNonce,
    document_origin: request.documentOrigin,
  });
  const activationText = await postControl(request, ACTIVATE_PATH, activateBody);
  const activationBody = asObject(parseJson(activationText, "activation_response"), "activation_response");

  const attestationEnvelope = decodeSignedEnvelope(
    requireObject(activationBody, "attestation", "activation_response"),
    "activation_response.attestation",
  );
  const manifestEnvelope = decodeSignedEnvelope(
    requireObject(activationBody, "annotation_manifest", "activation_response"),
    "activation_response.annotation_manifest",
  );
  const pageProofEnvelope = decodeSignedEnvelope(
    requireObject(activationBody, "page_proof", "activation_response"),
    "activation_response.page_proof",
  );

  const attestationPayload = await verifySignedEnvelope(
    attestationEnvelope,
    request.subtle,
    "activation_response.attestation",
  );
  const attestation = decodeAttestation(attestationPayload);
  assertAttestationBinding(attestation, {
    config: request.config,
    documentOrigin: request.documentOrigin,
    documentNonce,
    nowMs: request.nowMs(),
  });

  const manifestPayload = await verifySignedEnvelope(
    manifestEnvelope,
    request.subtle,
    "activation_response.annotation_manifest",
  );
  const manifest = decodeAnnotationManifest(manifestPayload);
  const manifestDigest = await payloadDigest(manifestEnvelope, request.subtle, "activation_response.annotation_manifest");
  assertManifestPinned(attestation, manifest.manifest_version, manifestDigest);
  if (manifest.redaction.redaction_version !== attestation.redaction_version) {
    throw new ActivationError("incompatible_protocol_or_schema", "manifest redaction version is not the pinned one", {
      expected: attestation.redaction_version,
      received: manifest.redaction.redaction_version,
    });
  }

  const pageProofPayload = await verifySignedEnvelope(
    pageProofEnvelope,
    request.subtle,
    "activation_response.page_proof",
  );
  const proofNonce = requireString(pageProofPayload, "document_nonce", "activation_response.page_proof.payload");
  if (proofNonce !== documentNonce) {
    throw new ActivationError("attestation_invalid_or_expired", "page proof does not bind this document nonce", {
      field: "document_nonce",
    });
  }

  return { attestation, attestationEnvelope, manifest, pageProof: pageProofEnvelope, documentNonce };
};

/**
 * Runs the complete gate: attestation, then one exchange for the short-lived,
 * write-only telemetry credential. Only an exporting top-document runtime does
 * this.
 */
export const activate = async (request: ActivationRequest): Promise<ActivationResult> => {
  const attested = await attestDocument(request);
  const envelope = attested.attestationEnvelope;
  const credentialText = await postControl(
    request,
    TELEMETRY_CREDENTIAL_PATH,
    JSON.stringify({
      attestation: {
        algorithm: envelope.algorithm,
        key_id: envelope.key_id,
        payload_b64url: envelope.payload_b64url,
        signature_b64url: envelope.signature_b64url,
      },
      document_nonce: attested.documentNonce,
    }),
  );
  const credential = decodeCredential(
    asObject(parseJson(credentialText, "telemetry_credential_response"), "telemetry_credential_response"),
  );
  if (credential.expired(request.nowMs())) {
    throw new ActivationError("credential_rejected", "telemetry credential is already expired", {
      expires_at: credential.expiresAt,
    });
  }
  return { ...attested, credential };
};
