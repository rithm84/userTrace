/**
 * Health report assembly.
 *
 * Reports carry operational metadata only: SDK and capability versions, service
 * and stream identity, manifest and policy versions, lifecycle state, batch
 * watermarks, buffer utilization, adapter status, credential expiry, probe
 * result, and typed failure codes. They never carry product content or a
 * rejected raw value.
 */

import type { ActiveObservation } from '../activation.js';
import { CONTRACT_MAJOR, SDK_CAPABILITIES, SDK_VERSION } from '../constants.js';
import type {
  AdapterStatus,
  BufferStatus,
  ProbeResult,
  ServerSdkHealthReport,
} from '../contracts/health.js';
import type { LifecycleState, ServerSdkFailure } from '../errors.js';

/** Everything the runtime knows when it is asked for its health. */
export interface HealthInputs {
  readonly serviceKey: string;
  readonly serviceInstanceId: string;
  readonly serverStreamId: string;
  readonly lifecycle: LifecycleState;
  readonly observation: ActiveObservation | undefined;
  readonly buffer: BufferStatus;
  readonly credentialExpiresAt: number;
  readonly probe: ProbeResult;
  readonly adapters: readonly AdapterStatus[];
  readonly failures: readonly ServerSdkFailure[];
}

/** Buffer status reported before an activation has supplied real bounds. */
export const UNACTIVATED_BUFFER: BufferStatus = {
  queued_batches: 0,
  queued_spans: 0,
  max_queue_batches: 1,
  max_queue_spans: 1,
  utilization: 0,
  high_water_reached: false,
  last_produced_sequence: 0,
  last_acknowledged_sequence: 0,
};

/** Probe result reported before the activation has supplied a probe. */
export const UNSTARTED_PROBE: ProbeResult = {
  state: 'not_started',
  observed_hops: [],
  required_hops: [],
};

/** Builds one health report. */
export function buildHealthReport(inputs: HealthInputs): ServerSdkHealthReport {
  const observation = inputs.observation;
  return {
    reported_at: new Date().toISOString(),
    sdk_version: SDK_VERSION,
    contract_major: CONTRACT_MAJOR,
    node_version: process.versions.node,
    service_key: inputs.serviceKey,
    service_instance_id: inputs.serviceInstanceId,
    server_stream_id: inputs.serverStreamId,
    ...(observation === undefined
      ? {}
      : {
          activation_id: observation.identities.activation_id,
          manifest_version: observation.manifest.manifest_version,
          policy_version: observation.manifest.policy_version,
          utrace_schema_version: observation.manifest.utrace_schema_version,
          semantic_convention_version: observation.manifest.semantic_convention_version,
        }),
    lifecycle_state: inputs.lifecycle,
    capabilities: [...SDK_CAPABILITIES],
    adapters: [...inputs.adapters],
    buffer: inputs.buffer,
    ...(inputs.credentialExpiresAt === 0
      ? {}
      : { credential_expires_at: new Date(inputs.credentialExpiresAt).toISOString() }),
    probe_result: inputs.probe,
    failures: [...inputs.failures],
  };
}
