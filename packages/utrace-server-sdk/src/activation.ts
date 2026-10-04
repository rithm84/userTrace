/**
 * Activation resolution.
 *
 * Turns the control plane's activation record into the immutable observation
 * scope this process runs under: a verified manifest, the one service this
 * process is, the limits it must honour, and the identities every exported
 * batch claims.
 */

import { CONTRACT_MAJOR, PLATFORM_CEILINGS } from './constants.js';
import type { ExportLimits, ServerActivation } from './contracts/activation.js';
import type { ManifestService, ServerInstrumentationManifest } from './contracts/manifest.js';
import { verifySignedManifest } from './contracts/signature.js';
import type { ManifestTrustAnchor } from './environment.js';
import { failureError } from './errors.js';

/** Identities every exported batch claims. */
export interface ActivationIdentities {
  readonly activation_id: string;
  readonly client_id: string;
  readonly client_user_id: string;
  readonly customer_account_id: string;
  readonly session_id: string;
  readonly preview_environment_id: string;
  readonly preview_runtime_allocation_id: string;
  readonly preview_version_id?: string;
  readonly server_stream_id: string;
  readonly service_instance_id: string;
}

/** The immutable scope one activated process runs under. */
export interface ActiveObservation {
  readonly activation: ServerActivation;
  readonly identities: ActivationIdentities;
  readonly manifest: ServerInstrumentationManifest;
  readonly service: ManifestService;
  readonly limits: ExportLimits;
}

/** Process-created identities the activation must echo back unchanged. */
export interface StreamIdentity {
  readonly serverStreamId: string;
  readonly serviceInstanceId: string;
  readonly serviceKey: string;
}

function assertLimitsBeneathCeilings(limits: ExportLimits, now: Date): void {
  const checks: readonly (readonly [string, number, number])[] = [
    ['max_batch_spans', limits.max_batch_spans, PLATFORM_CEILINGS.maxBatchSpans],
    ['max_batch_bytes', limits.max_batch_bytes, PLATFORM_CEILINGS.maxBatchBytes],
    ['max_queue_batches', limits.max_queue_batches, PLATFORM_CEILINGS.maxQueueBatches],
    ['max_queue_spans', limits.max_queue_spans, PLATFORM_CEILINGS.maxQueueSpans],
    ['max_in_flight_batches', limits.max_in_flight_batches, PLATFORM_CEILINGS.maxInFlightBatches],
    ['export_interval_millis', limits.export_interval_millis, PLATFORM_CEILINGS.maxExportIntervalMillis],
    [
      'acknowledgement_lag_millis',
      limits.acknowledgement_lag_millis,
      PLATFORM_CEILINGS.maxAcknowledgementLagMillis,
    ],
    ['flush_timeout_millis', limits.flush_timeout_millis, PLATFORM_CEILINGS.maxFlushTimeoutMillis],
    ['retry.max_backoff_millis', limits.retry.max_backoff_millis, PLATFORM_CEILINGS.maxBackoffMillis],
    ['retry.max_attempts', limits.retry.max_attempts, PLATFORM_CEILINGS.maxRetryAttempts],
  ];
  for (const [name, value, ceiling] of checks) {
    if (value > ceiling) {
      throw failureError('An activation limit exceeds its fixed platform ceiling.', {
        code: 'invalid_activation',
        retry: 'never',
        readiness_consequence: 'fail_stream',
        detail: { limit: name, supplied: value, ceiling },
      }, now);
    }
  }
  if (limits.retry.initial_backoff_millis > limits.retry.max_backoff_millis) {
    throw failureError('The activation retry backoff bounds are inverted.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: {
        initial_backoff_millis: limits.retry.initial_backoff_millis,
        max_backoff_millis: limits.retry.max_backoff_millis,
      },
    }, now);
  }
}

function findService(
  manifest: ServerInstrumentationManifest,
  serviceKey: string,
  now: Date,
): ManifestService {
  const service = manifest.services.find((candidate) => candidate.service_key === serviceKey);
  if (service === undefined) {
    throw failureError('The activation names a service the manifest does not declare.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { service_key: serviceKey, manifest_version: manifest.manifest_version },
    }, now);
  }
  return service;
}

function assertRegisteredOperationsResolve(
  manifest: ServerInstrumentationManifest,
  service: ManifestService,
  now: Date,
): void {
  const rootKinds = new Set(['product_operation', 'background_root']);
  const definedRoots = new Set(
    manifest.annotation_definitions
      .filter((definition) => rootKinds.has(definition.kind))
      .map((definition) => definition.name),
  );
  for (const operation of service.registered_operations) {
    if (!definedRoots.has(operation)) {
      throw failureError(
        'A registered operation has no product operation or background root definition.',
        {
          code: 'invalid_activation',
          retry: 'never',
          readiness_consequence: 'fail_stream',
          detail: { operation_name: operation, service_key: service.service_key },
        },
        now,
      );
    }
  }
}

/**
 * Validates the activation against the trust anchor, the process's own stream
 * identity, and the packaged contract major, then returns the observation scope.
 */
export function resolveActivation(
  activation: ServerActivation,
  anchor: ManifestTrustAnchor,
  stream: StreamIdentity,
  now: Date,
): ActiveObservation {
  if (activation.server_stream_id !== stream.serverStreamId) {
    throw failureError('The activation echoes a different server stream.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { expected: stream.serverStreamId, received: activation.server_stream_id },
    }, now);
  }
  if (activation.service_instance_id !== stream.serviceInstanceId) {
    throw failureError('The activation echoes a different service instance.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { expected: stream.serviceInstanceId, received: activation.service_instance_id },
    }, now);
  }
  if (activation.service_key !== stream.serviceKey) {
    throw failureError('The activation binds a different service key than this process claims.', {
      code: 'invalid_activation',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { expected: stream.serviceKey, received: activation.service_key },
    }, now);
  }

  const manifest = verifySignedManifest(activation.signed_manifest, anchor, now);

  if (manifest.server_sdk_contract_major !== CONTRACT_MAJOR) {
    throw failureError('The manifest requires a different server SDK contract major.', {
      code: 'incompatible_runtime_or_contract',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: {
        manifest_contract_major: manifest.server_sdk_contract_major,
        sdk_contract_major: CONTRACT_MAJOR,
      },
    }, now);
  }

  const service = findService(manifest, activation.service_key, now);
  assertRegisteredOperationsResolve(manifest, service, now);

  if (
    activation.resource.service_name !== service.service_name ||
    activation.resource.service_namespace !== service.service_namespace ||
    activation.resource.service_version !== service.service_version
  ) {
    throw failureError('The activation resource identity contradicts the manifest service.', {
      code: 'resource_mismatch',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: {
        service_key: service.service_key,
        manifest_service_name: service.service_name,
        activation_service_name: activation.resource.service_name,
      },
    }, now);
  }

  assertLimitsBeneathCeilings(activation.limits, now);

  return {
    activation,
    identities: {
      activation_id: activation.activation_id,
      client_id: activation.client_id,
      client_user_id: activation.client_user_id,
      customer_account_id: activation.customer_account_id,
      session_id: activation.session_id,
      preview_environment_id: activation.preview_environment_id,
      preview_runtime_allocation_id: activation.preview_runtime_allocation_id,
      ...(activation.preview_version_id === undefined
        ? {}
        : { preview_version_id: activation.preview_version_id }),
      server_stream_id: activation.server_stream_id,
      service_instance_id: activation.service_instance_id,
    },
    manifest,
    service,
    limits: activation.limits,
  };
}
