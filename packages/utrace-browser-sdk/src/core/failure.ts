/**
 * Runtime failure vocabulary.
 *
 * `RuntimeFailureCode` covers every code named in the browser SDK contract plus
 * three codes the contract does not enumerate but that the implementation can
 * actually distinguish: an unreachable activation endpoint, a signature
 * algorithm the host browser cannot verify, and a rejected extension bridge
 * handshake. The contract lists its codes as a minimum ("at least"), so these
 * are additions rather than replacements.
 */
export type RuntimeFailureCode =
  | "attestation_invalid_or_expired"
  | "attestation_algorithm_unsupported"
  | "activation_endpoint_unavailable"
  | "credential_rejected"
  | "incompatible_protocol_or_schema"
  | "duplicate_runtime_or_hook"
  | "required_frame_or_adapter_missing"
  | "baseline_rejected"
  | "target_registry_drift"
  | "ingestion_unavailable"
  | "acknowledgement_integrity_failure"
  | "buffer_exhaustion"
  | "trace_correlation_failure"
  | "bridge_handshake_rejected";

/** Safe operational detail attached to a failure. Never carries product content. */
export type FailureDetail = Readonly<Record<string, string | number | boolean>>;

/** Base class for every failure the runtime reports on its control path. */
export class UTraceRuntimeError extends Error {
  readonly code: RuntimeFailureCode;
  readonly detail: FailureDetail;

  constructor(code: RuntimeFailureCode, message: string, detail: FailureDetail) {
    super(`${code}: ${message}`);
    this.name = "UTraceRuntimeError";
    this.code = code;
    this.detail = detail;
  }
}

/** Activation gate failures: endpoint, attestation, or credential exchange. */
export class ActivationError extends UTraceRuntimeError {
  constructor(code: RuntimeFailureCode, message: string, detail: FailureDetail) {
    super(code, message, detail);
    this.name = "ActivationError";
  }
}

/** Export-path failures: transport, acknowledgement integrity, buffer bounds. */
export class ExportError extends UTraceRuntimeError {
  constructor(code: RuntimeFailureCode, message: string, detail: FailureDetail) {
    super(code, message, detail);
    this.name = "ExportError";
  }
}

/** Static configuration the embedding application supplied is unusable. */
export class ConfigurationError extends Error {
  readonly field: string;

  constructor(field: string, message: string) {
    super(`uTrace browser SDK configuration error on ${field}: ${message}`);
    this.name = "ConfigurationError";
    this.field = field;
  }
}

/** A capability the runtime requires is absent from the host document. */
export class UnsupportedHostError extends Error {
  readonly capability: string;

  constructor(capability: string, message: string) {
    super(`uTrace browser SDK requires ${capability}: ${message}`);
    this.name = "UnsupportedHostError";
    this.capability = capability;
  }
}
