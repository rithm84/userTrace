/**
 * Runtime validators for the observation activation record.
 *
 * Mirrors `packages/utrace-contracts/schemas/server-activation/server-activation.v1.schema.json`.
 */

import * as z from 'zod';

import { PLATFORM_CEILINGS } from '../constants.js';
import { HTTP_METHODS, signedServerInstrumentationManifestSchema } from './manifest.js';

export const PROBE_HOPS = [
  'server',
  'downstream_http',
  'database',
  'cache',
  'queue',
  'custom_protocol',
] as const;

export type ProbeHop = (typeof PROBE_HOPS)[number];

/**
 * The maxima are the packaged platform ceilings, so a handshake value above a
 * ceiling fails validation as an invalid activation rather than being clamped.
 */
const exportLimitsSchema = z.strictObject({
  max_batch_spans: z.int().min(1).max(PLATFORM_CEILINGS.maxBatchSpans),
  max_batch_bytes: z.int().min(1024).max(PLATFORM_CEILINGS.maxBatchBytes),
  max_queue_batches: z.int().min(1).max(PLATFORM_CEILINGS.maxQueueBatches),
  max_queue_spans: z.int().min(1).max(PLATFORM_CEILINGS.maxQueueSpans),
  readiness_high_water_ratio: z.number().gt(0).lt(1),
  max_in_flight_batches: z.int().min(1).max(PLATFORM_CEILINGS.maxInFlightBatches),
  export_interval_millis: z.int().min(10).max(PLATFORM_CEILINGS.maxExportIntervalMillis),
  acknowledgement_lag_millis: z.int().min(100).max(PLATFORM_CEILINGS.maxAcknowledgementLagMillis),
  flush_timeout_millis: z.int().min(100).max(PLATFORM_CEILINGS.maxFlushTimeoutMillis),
  retry: z.strictObject({
    initial_backoff_millis: z.int().min(1).max(PLATFORM_CEILINGS.maxBackoffMillis),
    max_backoff_millis: z.int().min(1).max(PLATFORM_CEILINGS.maxBackoffMillis),
    multiplier: z.number().min(1).max(10),
    jitter_ratio: z.number().min(0).max(1),
    max_attempts: z.int().min(1).max(PLATFORM_CEILINGS.maxRetryAttempts),
  }),
});

const propagationProbeSchema = z.strictObject({
  probe_id: z.uuid(),
  route_template: z.string().min(1).max(200),
  method: z.enum(HTTP_METHODS),
  required_hops: z.array(z.enum(PROBE_HOPS)).min(1).max(32),
});

export const serviceCredentialSchema = z.strictObject({
  token: z.string().min(1).max(4096),
  expires_at: z.iso.datetime(),
});

export const serverActivationSchema = z.strictObject({
  activation_id: z.uuid(),
  client_id: z.uuid(),
  client_user_id: z.uuid(),
  customer_account_id: z.uuid(),
  session_id: z.uuid(),
  preview_environment_id: z.uuid(),
  preview_runtime_allocation_id: z.uuid(),
  preview_version_id: z.uuid().optional(),
  server_stream_id: z.uuid(),
  service_instance_id: z.uuid(),
  service_key: z.string().max(120).regex(/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$/u),
  resource: z.strictObject({
    service_name: z.string().min(1).max(120),
    service_namespace: z.string().min(1).max(120),
    service_version: z.string().min(1).max(120),
    deployment_environment_name: z.string().min(1).max(120),
  }),
  credential: serviceCredentialSchema,
  ingest: z.strictObject({
    traces_path: z.literal('/v1/ingest/otlp/v1/traces'),
    preview_entry_token: z.string().min(16).max(512),
  }),
  limits: exportLimitsSchema,
  probe: propagationProbeSchema,
  signed_manifest: signedServerInstrumentationManifestSchema,
});

export type ExportLimits = z.infer<typeof exportLimitsSchema>;
export type PropagationProbeSpec = z.infer<typeof propagationProbeSchema>;
export type ServiceCredential = z.infer<typeof serviceCredentialSchema>;
export type ServerActivation = z.infer<typeof serverActivationSchema>;
