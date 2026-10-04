/**
 * Preview-gated W3C Trace Context propagation.
 *
 * The provider propagates `traceparent` and compatible `tracestate` only. W3C
 * baggage is neither extracted nor injected, and a syntactically valid
 * `traceparent` is accepted as a remote parent only on a request that already
 * entered the authenticated, active prepared preview.
 */

import type { Context, TextMapGetter, TextMapPropagator, TextMapSetter } from '@opentelemetry/api';
import { W3CTraceContextPropagator } from '@opentelemetry/core';

import { PREVIEW_ENTRY_HEADER } from '../constants.js';
import type { NetworkTarget } from '../contracts/manifest.js';

/** Reads the activation-scoped preview entry token, which rotates on rotation. */
export interface PreviewEntryTokenSource {
  currentToken(): string | undefined;
}

function readCarrierValue(
  carrier: unknown,
  getter: TextMapGetter,
  key: string,
): string | undefined {
  const value = getter.get(carrier, key);
  if (typeof value === 'string') {
    return value;
  }
  if (Array.isArray(value)) {
    const first = value[0];
    return typeof first === 'string' ? first : undefined;
  }
  return undefined;
}

/**
 * Wraps the standard W3C propagator with the preview entry gate.
 *
 * Injection additionally carries the preview entry token so the next
 * manifest-approved server-SDK service can apply the same gate. Only requests
 * toward approved propagation targets reach `inject` at all, because the
 * instrumentation skips every other outbound request.
 */
export class PreviewGatedTraceContextPropagator implements TextMapPropagator {
  private readonly delegate = new W3CTraceContextPropagator();
  private readonly tokens: PreviewEntryTokenSource;

  public constructor(tokens: PreviewEntryTokenSource) {
    this.tokens = tokens;
  }

  public inject(context: Context, carrier: unknown, setter: TextMapSetter): void {
    this.delegate.inject(context, carrier, setter);
    const token = this.tokens.currentToken();
    if (token !== undefined) {
      setter.set(carrier, PREVIEW_ENTRY_HEADER, token);
    }
  }

  public extract(context: Context, carrier: unknown, getter: TextMapGetter): Context {
    const token = this.tokens.currentToken();
    if (token === undefined) {
      return context;
    }
    const presented = readCarrierValue(carrier, getter, PREVIEW_ENTRY_HEADER);
    if (presented !== token) {
      return context;
    }
    return this.delegate.extract(context, carrier, getter);
  }

  public fields(): string[] {
    return [...this.delegate.fields(), PREVIEW_ENTRY_HEADER];
  }
}

/** True when the destination is a manifest-approved propagation target. */
export function isApprovedTarget(
  targets: readonly NetworkTarget[],
  scheme: string,
  host: string,
  port: number,
): boolean {
  const normalizedScheme = scheme.replace(/:$/u, '').toLowerCase();
  const normalizedHost = host.toLowerCase();
  return targets.some(
    (target) =>
      target.scheme === normalizedScheme
      && target.host.toLowerCase() === normalizedHost
      && target.port === port,
  );
}

/** Default port for a scheme, used when a request omits one. */
export function defaultPortForScheme(scheme: string): number {
  return scheme.replace(/:$/u, '').toLowerCase() === 'https' ? 443 : 80;
}
