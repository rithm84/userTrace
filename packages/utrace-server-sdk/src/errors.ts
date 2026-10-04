/**
 * Typed failures for the uTrace server SDK.
 *
 * The codes mirror
 * `packages/utrace-contracts/schemas/server-activation/server-sdk-failure.v1.schema.json`.
 * A failure record carries operational metadata only: it never contains product
 * content, a rejected raw value, or an unredacted exception message.
 */

/** Lifecycle states from the server SDK contract. */
export const LIFECYCLE_STATES = [
  'dormant',
  'activating',
  'ready',
  'paused',
  'recovering',
  'failed',
  'terminated',
] as const;

export type LifecycleState = (typeof LIFECYCLE_STATES)[number];

/** Closed set of server SDK failure codes. */
export const FAILURE_CODES = [
  'invalid_activation',
  'credential_rejected',
  'incompatible_runtime_or_contract',
  'duplicate_provider_or_hook',
  'required_adapter_missing',
  'resource_mismatch',
  'redaction_or_schema_failure',
  'propagation_failure',
  'unauthorized_destination',
  'ingestion_unavailable',
  'partial_acceptance',
  'acknowledgement_integrity_failure',
  'buffer_exhaustion',
  'unclean_stream_end',
  'route_not_registered',
  'manifest_signature_invalid',
  'control_connection_lost',
] as const;

export type FailureCode = (typeof FAILURE_CODES)[number];

/** Retry advice from the shared failure contract. */
export type RetryAdvice = 'never' | 'after_delay' | 'after_condition_change';

/** What the failure does to this service's contribution to readiness. */
export type ReadinessConsequence =
  | 'none'
  | 'withdraw_readiness'
  | 'fail_stream'
  | 'terminate_stream';

/** Safe scalar values permitted in a failure detail map. */
export type FailureDetailValue = string | number | boolean;

/** A safe, structured failure record. */
export interface ServerSdkFailure {
  readonly code: FailureCode;
  readonly detected_at: string;
  readonly retry: RetryAdvice;
  readonly readiness_consequence: ReadinessConsequence;
  readonly detail: Readonly<Record<string, FailureDetailValue>>;
  readonly batch_id?: string | undefined;
  readonly batch_sequence?: number | undefined;
  readonly trace_id?: string | undefined;
}

/** Fields a caller supplies when raising a failure. */
export interface ServerSdkFailureInput {
  readonly code: FailureCode;
  readonly retry: RetryAdvice;
  readonly readiness_consequence: ReadinessConsequence;
  readonly detail: Readonly<Record<string, FailureDetailValue>>;
  readonly batch_id?: string | undefined;
  readonly batch_sequence?: number | undefined;
  readonly trace_id?: string | undefined;
}

/**
 * The only error type the SDK throws. It always carries a typed failure so the
 * control plane receives a stable code rather than a prose message.
 */
export class ServerSdkError extends Error {
  public readonly failure: ServerSdkFailure;

  public constructor(message: string, failure: ServerSdkFailure) {
    super(message);
    this.name = 'ServerSdkError';
    this.failure = failure;
  }
}

/** Builds a failure record stamped with the supplied wall-clock time. */
export function createFailure(
  input: ServerSdkFailureInput,
  detectedAt: Date,
): ServerSdkFailure {
  return {
    code: input.code,
    detected_at: detectedAt.toISOString(),
    retry: input.retry,
    readiness_consequence: input.readiness_consequence,
    detail: { ...input.detail },
    ...(input.batch_id === undefined ? {} : { batch_id: input.batch_id }),
    ...(input.batch_sequence === undefined ? {} : { batch_sequence: input.batch_sequence }),
    ...(input.trace_id === undefined ? {} : { trace_id: input.trace_id }),
  };
}

/** Builds a `ServerSdkError` carrying a freshly stamped failure. */
export function failureError(
  message: string,
  input: ServerSdkFailureInput,
  detectedAt: Date,
): ServerSdkError {
  return new ServerSdkError(message, createFailure(input, detectedAt));
}
