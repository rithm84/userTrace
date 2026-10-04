/**
 * Immutable export batches.
 *
 * A batch is serialized once, keeps its identity across every retry, and
 * carries its stream-scoped sequence and batch ID outside the OTLP payload.
 */

import { createHash, randomUUID } from 'node:crypto';
import { gzipSync } from 'node:zlib';

import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { ProtobufTraceSerializer } from '@opentelemetry/otlp-transformer';

import { CONTRACT_MAJOR, EXPORT_ENVELOPE_HEADER_PREFIX, SDK_VERSION } from '../constants.js';
import type { ActiveObservation } from '../activation.js';
import { failureError } from '../errors.js';

/** The batch envelope carried in `x-utrace-*` request headers. */
export interface ExportBatchEnvelope {
  readonly activation_id: string;
  readonly server_stream_id: string;
  readonly service_instance_id: string;
  readonly batch_id: string;
  readonly batch_sequence: number;
  readonly span_count: number;
  readonly sdk_version: string;
  readonly contract_major: number;
  readonly manifest_version: number;
  readonly policy_version: string;
  readonly utrace_schema_version: string;
  readonly semantic_convention_version: string;
  readonly content_sha256: string;
}

/** One immutable, already-redacted, already-serialized batch. */
export interface ExportBatch {
  readonly envelope: ExportBatchEnvelope;
  readonly body: Buffer;
  readonly spanCount: number;
}

/** Converts the envelope into its wire header names. */
export function envelopeHeaders(envelope: ExportBatchEnvelope): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(envelope)) {
    headers[`${EXPORT_ENVELOPE_HEADER_PREFIX}${key.replaceAll('_', '-')}`] = String(value);
  }
  return headers;
}

/**
 * Serializes redacted spans into one immutable batch.
 *
 * The uncompressed protobuf body is hashed before compression so ingestion can
 * recognize an exact duplicate retry, then gzipped for transport.
 */
export function createBatch(
  spans: readonly ReadableSpan[],
  sequence: number,
  observation: ActiveObservation,
  now: Date,
): ExportBatch {
  const serialized = ProtobufTraceSerializer.serializeRequest([...spans]);
  if (serialized === undefined) {
    throw failureError('The OTLP serializer produced no payload for a non-empty batch.', {
      code: 'redaction_or_schema_failure',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: { reason: 'serialization_returned_empty', span_count: spans.length },
    }, now);
  }
  const uncompressed = Buffer.from(serialized);
  if (uncompressed.byteLength > observation.limits.max_batch_bytes) {
    throw failureError('A serialized batch exceeds the activation payload limit.', {
      code: 'redaction_or_schema_failure',
      retry: 'never',
      readiness_consequence: 'fail_stream',
      detail: {
        reason: 'batch_above_byte_limit',
        byte_length: uncompressed.byteLength,
        limit: observation.limits.max_batch_bytes,
      },
    }, now);
  }

  const envelope: ExportBatchEnvelope = {
    activation_id: observation.identities.activation_id,
    server_stream_id: observation.identities.server_stream_id,
    service_instance_id: observation.identities.service_instance_id,
    batch_id: randomUUID(),
    batch_sequence: sequence,
    span_count: spans.length,
    sdk_version: SDK_VERSION,
    contract_major: CONTRACT_MAJOR,
    manifest_version: observation.manifest.manifest_version,
    policy_version: observation.manifest.policy_version,
    utrace_schema_version: observation.manifest.utrace_schema_version,
    semantic_convention_version: observation.manifest.semantic_convention_version,
    content_sha256: createHash('sha256').update(uncompressed).digest('hex'),
  };

  return { envelope, body: gzipSync(uncompressed), spanCount: spans.length };
}
