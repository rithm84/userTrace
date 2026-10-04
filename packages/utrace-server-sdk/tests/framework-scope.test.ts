/**
 * A suppressed framework scope, as serverful Next.js creates one.
 *
 * Next.js starts its own spans under the `next.js` scope around a route
 * handler, so an adapter span inside the handler (a database query) would name
 * a framework span as its parent. The framework span is never exported, so that
 * parentage could never be resolved; the suppressed scope's tracer must leave
 * the adapter span parented to the recorded HTTP `SERVER` span instead.
 */

import { SpanKind, context } from '@opentelemetry/api';
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ScopeSuppressingTracerProvider } from '../src/provider/provider.js';

let exporter: InMemorySpanExporter;
let recorded: NodeTracerProvider;
let contextManager: AsyncLocalStorageContextManager;

beforeEach(() => {
  exporter = new InMemorySpanExporter();
  recorded = new NodeTracerProvider({ spanProcessors: [new SimpleSpanProcessor(exporter)] });
  contextManager = new AsyncLocalStorageContextManager().enable();
  context.setGlobalContextManager(contextManager);
});

afterEach(async () => {
  await recorded.shutdown();
  context.disable();
});

describe('a suppressed framework scope', () => {
  it('parents the spans started inside it to the recorded server span', async () => {
    const provider = new ScopeSuppressingTracerProvider(recorded, new Set(['next.js']));
    const http = provider.getTracer('@opentelemetry/instrumentation-http');
    const framework = provider.getTracer('next.js', '0.0.1');
    const pg = provider.getTracer('@opentelemetry/instrumentation-pg');

    await http.startActiveSpan('POST /api/shopalytics', { kind: SpanKind.SERVER }, async (server) => {
      await framework.startActiveSpan('executing api route (app) /api/shopalytics', async (route) => {
        // What the framework does to its own span cannot reach the recorded one.
        route.updateName('POST /api/shopalytics renamed by the framework');
        route.setAttribute('next.route', '/api/shopalytics');
        await Promise.resolve();
        pg.startSpan('SELECT', { kind: SpanKind.CLIENT }).end();
        route.end();
      });
      server.end();
    });

    const spans = exporter.getFinishedSpans();
    expect(spans.map((span) => span.name).sort()).toEqual(['POST /api/shopalytics', 'SELECT']);
    const server = spans.find((span) => span.name === 'POST /api/shopalytics');
    const query = spans.find((span) => span.name === 'SELECT');
    expect(query?.parentSpanContext?.spanId).toBe(server?.spanContext().spanId);
    expect(query?.spanContext().traceId).toBe(server?.spanContext().traceId);
    expect(server?.attributes).toEqual({});
  });

  it('leaves every other scope recording', () => {
    const provider = new ScopeSuppressingTracerProvider(recorded, new Set(['next.js']));
    const span = provider.getTracer('@opentelemetry/instrumentation-http').startSpan('GET /');
    expect(span.isRecording()).toBe(true);
    span.end();
  });
});
