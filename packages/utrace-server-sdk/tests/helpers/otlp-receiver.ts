/**
 * A test telemetry ingestion edge.
 *
 * The real edge is a uTrace-owned OpenTelemetry Collector gateway built by the
 * telemetry workstream. This server implements the documented contract the
 * server SDK depends on: authenticated OTLP/HTTP binary Protobuf with gzip at
 * `POST /v1/ingest/otlp/v1/traces`, full-success-only acknowledgement, and
 * retryable statuses with `Retry-After`.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { gunzipSync } from 'node:zlib';

import { decodeTraceRequest, encodePartialSuccessResponse, type DecodedSpan } from './protobuf.js';

/** One received batch, as the edge saw it. */
export interface ReceivedBatch {
  readonly receivedAt: number;
  readonly headers: Readonly<Record<string, string>>;
  readonly authorization: string | undefined;
  readonly contentEncoding: string | undefined;
  readonly contentType: string | undefined;
  readonly uncompressedBytes: number;
  readonly spans: readonly DecodedSpan[];
  readonly resourceAttributes: Readonly<Record<string, string | boolean | number | readonly string[]>>;
}

/** How the edge answers the next request. */
export type ReceiverBehaviour =
  | { readonly kind: 'accept' }
  | { readonly kind: 'retryable'; readonly status: number; readonly retryAfterSeconds?: number }
  | { readonly kind: 'permanent'; readonly status: number }
  | { readonly kind: 'partial'; readonly rejectedSpans: number };

/** In-process ingestion edge for tests. */
export class TestOtlpReceiver {
  private readonly server: Server;
  private readonly received: ReceivedBatch[] = [];
  private behaviours: ReceiverBehaviour[] = [];
  private fallback: ReceiverBehaviour = { kind: 'accept' };

  public constructor() {
    this.server = createServer((request, response) => {
      this.handle(request, response).catch(() => {
        response.statusCode = 500;
        response.end();
      });
    });
  }

  /** Every batch the edge accepted or rejected, in arrival order. */
  public get batches(): readonly ReceivedBatch[] {
    return this.received;
  }

  /** Queues behaviours consumed one per request, then falls back. */
  public program(behaviours: readonly ReceiverBehaviour[], fallback: ReceiverBehaviour): void {
    this.behaviours = [...behaviours];
    this.fallback = fallback;
  }

  public listen(port: number): Promise<URL> {
    return new Promise((resolve) => {
      this.server.listen(port, '127.0.0.1', () => {
        resolve(new URL(`http://127.0.0.1:${String(port)}`));
      });
    });
  }

  public close(): Promise<void> {
    return new Promise((resolve) => {
      this.server.closeAllConnections();
      this.server.close(() => {
        resolve();
      });
    });
  }

  private async handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    if (request.method !== 'POST' || request.url !== '/v1/ingest/otlp/v1/traces') {
      response.statusCode = 404;
      response.end();
      return;
    }
    const body = await this.readBody(request);
    const headers: Record<string, string> = {};
    for (const [name, value] of Object.entries(request.headers)) {
      if (typeof value === 'string') {
        headers[name] = value;
      }
    }
    const uncompressed =
      request.headers['content-encoding'] === 'gzip' ? gunzipSync(body) : body;
    const groups = decodeTraceRequest(uncompressed);
    this.received.push({
      receivedAt: Date.now(),
      headers,
      authorization: headers['authorization'],
      contentEncoding: headers['content-encoding'],
      contentType: headers['content-type'],
      uncompressedBytes: uncompressed.byteLength,
      spans: groups.flatMap((group) => group.spans),
      resourceAttributes: groups[0]?.resourceAttributes ?? {},
    });

    const behaviour = this.behaviours.shift() ?? this.fallback;
    switch (behaviour.kind) {
      case 'retryable':
        if (behaviour.retryAfterSeconds !== undefined) {
          response.setHeader('retry-after', String(behaviour.retryAfterSeconds));
        }
        response.statusCode = behaviour.status;
        response.end();
        return;
      case 'permanent':
        response.statusCode = behaviour.status;
        response.end();
        return;
      case 'partial': {
        const payload = encodePartialSuccessResponse(
          behaviour.rejectedSpans,
          'rejected by validation',
        );
        response.statusCode = 200;
        response.setHeader('content-type', 'application/x-protobuf');
        response.end(payload);
        return;
      }
      default:
        response.statusCode = 200;
        response.setHeader('content-type', 'application/x-protobuf');
        response.end(Buffer.alloc(0));
    }
  }

  private readBody(request: IncomingMessage): Promise<Buffer> {
    return new Promise((resolve, reject) => {
      const chunks: Buffer[] = [];
      request.on('data', (chunk: Buffer) => {
        chunks.push(chunk);
      });
      request.on('end', () => {
        resolve(Buffer.concat(chunks));
      });
      request.on('error', reject);
    });
  }
}
