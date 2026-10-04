/**
 * The uTrace-owned OTLP/HTTP exporter.
 *
 * It sends binary Protobuf with gzip to the authenticated telemetry ingestion
 * edge and nowhere else: no client collector, no production observability
 * backend, no alternate vendor, no fallback endpoint.
 */

import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import type { IncomingMessage } from 'node:http';

import { context } from '@opentelemetry/api';
import { suppressTracing } from '@opentelemetry/core';
import { ProtobufTraceSerializer } from '@opentelemetry/otlp-transformer';

import { CONTRACT_MAJOR, SDK_VERSION } from '../constants.js';
import type { ExportLimits } from '../contracts/activation.js';
import type { ServerSdkFailureInput } from '../errors.js';
import type { StructuredLogger } from '../logging.js';
import { backoffDelayMillis, parseRetryAfter, type JitterSource } from './backoff.js';
import { envelopeHeaders, type ExportBatch } from './batch.js';

/** Supplies the current short-lived service-instance credential. */
export interface CredentialSource {
  currentToken(): string;
}

/** Terminal outcome of delivering one immutable batch. */
export type DeliveryOutcome =
  | { readonly kind: 'acknowledged'; readonly attempts: number }
  | { readonly kind: 'failed'; readonly failure: ServerSdkFailureInput };

interface RawResponse {
  readonly statusCode: number;
  readonly retryAfter: string | undefined;
  readonly body: Buffer;
}

type AttemptOutcome =
  | { readonly kind: 'acknowledged' }
  | { readonly kind: 'permanent'; readonly failure: ServerSdkFailureInput }
  | { readonly kind: 'retryable'; readonly retryAfterMillis: number | undefined; readonly reason: string };

const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);

function collect(response: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    response.on('data', (chunk: Buffer) => {
      chunks.push(chunk);
    });
    response.on('end', () => {
      resolve(Buffer.concat(chunks));
    });
    response.on('error', reject);
  });
}

function delay(millis: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, millis).unref();
  });
}

/** Relative, so it resolves under the ingestion base URL's path prefix. */
const TRACES_PATH = 'v1/ingest/otlp/v1/traces';

/** The OTLP traces URL under an ingestion base URL that ends in `/`. */
export function otlpTracesUrl(ingestBaseUrl: URL): URL {
  return new URL(TRACES_PATH, ingestBaseUrl);
}

/** Posts immutable batches to `POST /v1/ingest/otlp/v1/traces` under the ingestion base URL. */
export class OtlpTraceExporter {
  private readonly endpoint: URL;
  private readonly credentials: CredentialSource;
  private readonly logger: StructuredLogger;
  private readonly jitter: JitterSource;

  public constructor(
    endpoint: URL,
    credentials: CredentialSource,
    logger: StructuredLogger,
    jitter: JitterSource,
  ) {
    this.endpoint = endpoint;
    this.credentials = credentials;
    this.logger = logger;
    this.jitter = jitter;
  }

  /**
   * Delivers one batch, retrying the same immutable payload.
   *
   * Transport failures and throttling back off with jitter and honour
   * `Retry-After`. Permanent validation or authentication errors are not
   * retried, and a partial success fails the stream.
   */
  public async deliver(batch: ExportBatch, limits: ExportLimits): Promise<DeliveryOutcome> {
    for (let attempt = 1; attempt <= limits.retry.max_attempts; attempt += 1) {
      const outcome = await this.attempt(batch);
      if (outcome.kind === 'acknowledged') {
        return { kind: 'acknowledged', attempts: attempt };
      }
      if (outcome.kind === 'permanent') {
        return { kind: 'failed', failure: outcome.failure };
      }
      if (attempt === limits.retry.max_attempts) {
        break;
      }
      const wait = backoffDelayMillis(attempt, limits.retry, outcome.retryAfterMillis, this.jitter);
      this.logger.warn('export_retry_scheduled', {
        batch_sequence: batch.envelope.batch_sequence,
        attempt,
        delay_millis: wait,
        reason: outcome.reason,
      });
      await delay(wait);
    }
    return {
      kind: 'failed',
      failure: {
        code: 'ingestion_unavailable',
        retry: 'after_delay',
        readiness_consequence: 'withdraw_readiness',
        detail: { attempts: limits.retry.max_attempts },
        batch_id: batch.envelope.batch_id,
        batch_sequence: batch.envelope.batch_sequence,
      },
    };
  }

  private async attempt(batch: ExportBatch): Promise<AttemptOutcome> {
    let response: RawResponse;
    try {
      response = await this.post(batch);
    } catch (error) {
      return {
        kind: 'retryable',
        retryAfterMillis: undefined,
        reason: error instanceof Error ? error.name : 'transport_error',
      };
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return this.interpretSuccess(batch, response.body);
    }
    if (RETRYABLE_STATUS.has(response.statusCode)) {
      return {
        kind: 'retryable',
        retryAfterMillis: parseRetryAfter(response.retryAfter, new Date()),
        reason: `status_${String(response.statusCode)}`,
      };
    }
    const authentication = response.statusCode === 401 || response.statusCode === 403;
    return {
      kind: 'permanent',
      failure: {
        code: authentication ? 'credential_rejected' : 'redaction_or_schema_failure',
        retry: 'never',
        readiness_consequence: 'fail_stream',
        detail: { status_code: response.statusCode },
        batch_id: batch.envelope.batch_id,
        batch_sequence: batch.envelope.batch_sequence,
      },
    };
  }

  private interpretSuccess(batch: ExportBatch, body: Buffer): AttemptOutcome {
    if (body.byteLength === 0) {
      return { kind: 'acknowledged' };
    }
    let rejectedSpans: number;
    try {
      const parsed = ProtobufTraceSerializer.deserializeResponse(new Uint8Array(body));
      rejectedSpans = parsed.partialSuccess?.rejectedSpans ?? 0;
    } catch {
      return {
        kind: 'permanent',
        failure: {
          code: 'acknowledgement_integrity_failure',
          retry: 'never',
          readiness_consequence: 'fail_stream',
          detail: { reason: 'undecodable_acknowledgement', byte_length: body.byteLength },
          batch_id: batch.envelope.batch_id,
          batch_sequence: batch.envelope.batch_sequence,
        },
      };
    }
    if (rejectedSpans > 0) {
      return {
        kind: 'permanent',
        failure: {
          code: 'partial_acceptance',
          retry: 'never',
          readiness_consequence: 'fail_stream',
          detail: { rejected_spans: rejectedSpans, span_count: batch.spanCount },
          batch_id: batch.envelope.batch_id,
          batch_sequence: batch.envelope.batch_sequence,
        },
      };
    }
    return { kind: 'acknowledged' };
  }

  private post(batch: ExportBatch): Promise<RawResponse> {
    const url = otlpTracesUrl(this.endpoint);
    const send = url.protocol === 'https:' ? httpsRequest : httpRequest;
    const headers: Record<string, string> = {
      ...envelopeHeaders(batch.envelope),
      authorization: `Bearer ${this.credentials.currentToken()}`,
      'content-type': 'application/x-protobuf',
      'content-encoding': 'gzip',
      'content-length': String(batch.body.byteLength),
      'user-agent': `utrace-server-sdk/${SDK_VERSION} contract/${String(CONTRACT_MAJOR)}`,
    };

    return context.with(suppressTracing(context.active()), () =>
      new Promise<RawResponse>((resolve, reject) => {
        const clientRequest = send(url, { method: 'POST', headers }, (response) => {
          collect(response)
            .then((body) => {
              resolve({
                statusCode: response.statusCode ?? 0,
                retryAfter:
                  typeof response.headers['retry-after'] === 'string'
                    ? response.headers['retry-after']
                    : undefined,
                body,
              });
            })
            .catch(reject);
        });
        clientRequest.on('error', reject);
        clientRequest.end(batch.body);
      }),
    );
  }
}
