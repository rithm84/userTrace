/**
 * Runtime validators for health reports and typed failures.
 *
 * Mirrors `packages/utrace-contracts/schemas/server-activation/server-sdk-health-report.v1.schema.json`
 * and `.../server-sdk-failure.v1.schema.json`.
 */

import * as z from 'zod';

import { ADAPTERS } from './manifest.js';
import { PROBE_HOPS } from './activation.js';
import { FAILURE_CODES, LIFECYCLE_STATES } from '../errors.js';

const versionTriple = z.string().regex(/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/u);

export const serverSdkFailureSchema = z.strictObject({
  code: z.enum(FAILURE_CODES),
  detected_at: z.iso.datetime(),
  retry: z.enum(['never', 'after_delay', 'after_condition_change']),
  readiness_consequence: z.enum([
    'none',
    'withdraw_readiness',
    'fail_stream',
    'terminate_stream',
  ]),
  detail: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
  batch_id: z.uuid().optional(),
  batch_sequence: z.int().min(1).optional(),
  trace_id: z.string().regex(/^[0-9a-f]{32}$/u).optional(),
});

export const PROBE_STATES = [
  'not_started',
  'awaiting_spans',
  'awaiting_acknowledgement',
  'passed',
  'failed',
] as const;

export const PROBE_FAILURE_REASONS = [
  'duplicate_server_span',
  'missing_hop',
  'rejected_field',
  'unexpected_destination',
  'acknowledgement_timeout',
] as const;

export type ProbeState = (typeof PROBE_STATES)[number];
export type ProbeFailureReason = (typeof PROBE_FAILURE_REASONS)[number];

export const probeResultSchema = z.strictObject({
  state: z.enum(PROBE_STATES),
  probe_id: z.uuid().optional(),
  observed_hops: z.array(z.enum(PROBE_HOPS)).max(32),
  required_hops: z.array(z.enum(PROBE_HOPS)).max(32),
  failure_reason: z.enum(PROBE_FAILURE_REASONS).optional(),
});

export const adapterStatusSchema = z.strictObject({
  adapter: z.enum(ADAPTERS),
  required: z.boolean(),
  installed: z.boolean(),
  module_version: z.string().max(40).optional(),
});

export const bufferStatusSchema = z.strictObject({
  queued_batches: z.int().min(0),
  queued_spans: z.int().min(0),
  max_queue_batches: z.int().min(1),
  max_queue_spans: z.int().min(1),
  utilization: z.number().min(0).max(1),
  high_water_reached: z.boolean(),
  last_produced_sequence: z.int().min(0),
  last_acknowledged_sequence: z.int().min(0),
});

export const serverSdkHealthReportSchema = z.strictObject({
  reported_at: z.iso.datetime(),
  sdk_version: z.string().min(1).max(40),
  contract_major: z.int().min(1),
  node_version: z.string().min(1).max(40),
  service_key: z.string().max(120).regex(/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$/u),
  service_instance_id: z.uuid(),
  server_stream_id: z.uuid(),
  activation_id: z.uuid().optional(),
  manifest_version: z.int().min(1).optional(),
  policy_version: versionTriple.optional(),
  utrace_schema_version: versionTriple.optional(),
  semantic_convention_version: versionTriple.optional(),
  lifecycle_state: z.enum(LIFECYCLE_STATES),
  capabilities: z.array(z.string().min(1).max(60)).max(32),
  adapters: z.array(adapterStatusSchema).max(16),
  buffer: bufferStatusSchema,
  credential_expires_at: z.iso.datetime().optional(),
  probe_result: probeResultSchema,
  failures: z.array(serverSdkFailureSchema).max(32),
});

export type AdapterStatus = z.infer<typeof adapterStatusSchema>;
export type BufferStatus = z.infer<typeof bufferStatusSchema>;
export type ProbeResult = z.infer<typeof probeResultSchema>;
export type ServerSdkHealthReport = z.infer<typeof serverSdkHealthReportSchema>;
