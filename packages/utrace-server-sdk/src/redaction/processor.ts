/**
 * The redaction span processor.
 *
 * It is the only processor on the uTrace provider, so redaction is enforced
 * before a finished span can reach the export buffer. Telemetry failure never
 * delays or fails the client's business request: a rejected span withdraws
 * readiness and reports the exact failure instead of throwing into the caller.
 */

import type { Context } from '@opentelemetry/api';
import type { ReadableSpan, Span, SpanProcessor } from '@opentelemetry/sdk-trace-base';

import type { ProbeHop } from '../contracts/activation.js';
import type { ServerSdkFailureInput } from '../errors.js';
import type { RedactionPolicy } from './policy.js';
import { redactSpan } from './span.js';

/** Where redacted spans and redaction failures go. */
export interface RedactedSpanSink {
  accept(span: ReadableSpan, hop: ProbeHop | undefined): void;
  reject(failure: ServerSdkFailureInput): void;
  excluded(reason: string): void;
  flush(): Promise<void>;
  stop(): Promise<void>;
}

/** Applies the uTrace redaction pipeline to every finished span. */
export class RedactionSpanProcessor implements SpanProcessor {
  private readonly policy: RedactionPolicy;
  private readonly sink: RedactedSpanSink;

  public constructor(policy: RedactionPolicy, sink: RedactedSpanSink) {
    this.policy = policy;
    this.sink = sink;
  }

  public onStart(_span: Span, _parentContext: Context): void {
    // Redaction runs on finished spans only; nothing is recorded at start.
  }

  public onEnd(span: ReadableSpan): void {
    const outcome = redactSpan(span, this.policy);
    switch (outcome.kind) {
      case 'export':
        this.sink.accept(outcome.span, outcome.hop);
        return;
      case 'exclude':
        this.sink.excluded(outcome.reason);
        return;
      default:
        this.sink.reject(outcome.failure);
    }
  }

  public forceFlush(): Promise<void> {
    return this.sink.flush();
  }

  public shutdown(): Promise<void> {
    return this.sink.stop();
  }
}
