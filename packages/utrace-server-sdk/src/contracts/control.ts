/**
 * Runtime validators for the control WebSocket protocol at
 * `WS /v1/server-sdk/control`.
 *
 * Mirrors `packages/utrace-contracts/schemas/server-activation/server-control-client-message.v1.schema.json`
 * and `.../server-control-server-message.v1.schema.json`.
 */

import * as z from 'zod';

import { serverActivationSchema, serviceCredentialSchema } from './activation.js';
import { serverSdkFailureSchema, serverSdkHealthReportSchema } from './health.js';
import { LIFECYCLE_STATES } from '../errors.js';

export const activateMessageSchema = z.strictObject({
  type: z.literal('activate'),
  request_id: z.uuid(),
  bootstrap_credential: z.string().min(1).max(4096),
  service_name: z.string().max(120).regex(/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$/u),
  server_stream_id: z.uuid(),
  service_instance_id: z.uuid(),
  sdk: z.strictObject({
    version: z.string().min(1).max(40),
    contract_major: z.int().min(1),
    capabilities: z.array(z.string().min(1).max(60)).max(32),
  }),
  runtime: z.strictObject({
    node_version: z.string().min(1).max(40),
    platform: z.string().min(1).max(40),
    arch: z.string().min(1).max(40),
    pid: z.int().min(1),
  }),
});

export const rotateMessageSchema = z.strictObject({
  type: z.literal('rotate'),
  request_id: z.uuid(),
  server_stream_id: z.uuid(),
});

export const healthMessageSchema = z.strictObject({
  type: z.literal('health'),
  request_id: z.uuid(),
  report: serverSdkHealthReportSchema,
});

export const commandAckMessageSchema = z.strictObject({
  type: z.literal('command_ack'),
  command_id: z.uuid(),
  lifecycle_state: z.enum(LIFECYCLE_STATES),
});

export const streamReportMessageSchema = z.strictObject({
  type: z.literal('stream_report'),
  request_id: z.uuid(),
  server_stream_id: z.uuid(),
  clean: z.boolean(),
  last_produced_sequence: z.int().min(0),
  last_acknowledged_sequence: z.int().min(0),
  failures: z.array(serverSdkFailureSchema).max(32),
});

export const controlClientMessageSchema = z.discriminatedUnion('type', [
  activateMessageSchema,
  rotateMessageSchema,
  healthMessageSchema,
  commandAckMessageSchema,
  streamReportMessageSchema,
]);

export const activatedMessageSchema = z.strictObject({
  type: z.literal('activated'),
  request_id: z.uuid(),
  activation: serverActivationSchema,
});

export const rotatedMessageSchema = z.strictObject({
  type: z.literal('rotated'),
  request_id: z.uuid(),
  credential: serviceCredentialSchema,
});

export const healthAckMessageSchema = z.strictObject({
  type: z.literal('health_ack'),
  request_id: z.uuid(),
});

export const pauseMessageSchema = z.strictObject({
  type: z.literal('pause'),
  command_id: z.uuid(),
  reason: z.enum(['session_paused', 'policy_hold', 'ingestion_backpressure', 'operator_stop']),
});

export const resumeMessageSchema = z.strictObject({
  type: z.literal('resume'),
  command_id: z.uuid(),
});

export const terminateMessageSchema = z.strictObject({
  type: z.literal('terminate'),
  command_id: z.uuid(),
  reason: z.enum([
    'session_ended',
    'activation_superseded',
    'credential_revoked',
    'client_case_stop',
  ]),
});

export const controlErrorMessageSchema = z.strictObject({
  type: z.literal('error'),
  request_id: z.uuid().optional(),
  failure: serverSdkFailureSchema,
});

export const controlServerMessageSchema = z.discriminatedUnion('type', [
  activatedMessageSchema,
  rotatedMessageSchema,
  healthAckMessageSchema,
  pauseMessageSchema,
  resumeMessageSchema,
  terminateMessageSchema,
  controlErrorMessageSchema,
]);

export type ControlClientMessage = z.infer<typeof controlClientMessageSchema>;
export type ControlServerMessage = z.infer<typeof controlServerMessageSchema>;
export type ActivateMessage = z.infer<typeof activateMessageSchema>;
export type PauseMessage = z.infer<typeof pauseMessageSchema>;
export type TerminateMessage = z.infer<typeof terminateMessageSchema>;
