/**
 * Fetch and XHR capture.
 *
 * Native fetch and XHR receive full paired observation and W3C propagation.
 * `request.started` and `request.completed` are joined by a browser request id.
 * `traceparent` and a compatible `tracestate` are injected only on same-origin
 * requests or on origins the signed release manifest approved as server-SDK
 * endpoints; an arbitrary third party never receives uTrace context.
 *
 * Summaries exclude raw headers, query strings, and request or response bodies.
 */

import type { BrowserView } from "../core/dom.js";
import { uuidv7 } from "../core/ids.js";
import type { RequestCompletedPayload, RequestStartedPayload, RouteState } from "../observation/types.js";
import type { RuntimeContext } from "../runtime/context.js";
import { resolveRequestOrigin } from "../trace/context.js";
import type { HookUninstall } from "./install.js";
import { assertUnowned, markOwner } from "./install.js";

/** Header the runtime places on its own control-path requests so they are not observed. */
const CONTROL_HEADER = "x-utrace-control";

interface RequestIdentity {
  readonly browser_request_id: string;
  readonly method: string;
  readonly route: RouteState;
  readonly same_origin: boolean;
  readonly injected: boolean;
  readonly trace_id: string | null;
  readonly span_id: string | null;
  readonly started_at: number;
}

const normalizeMethod = (method: string): string => method.toUpperCase();

const emitStarted = (context: RuntimeContext, identity: RequestIdentity, transport: "fetch" | "xhr"): void => {
  const payload: RequestStartedPayload = {
    browser_request_id: identity.browser_request_id,
    transport,
    method: identity.method,
    route: identity.route,
    same_origin: identity.same_origin,
    trace_context_injected: identity.injected,
    actor: context.actor(),
  };
  context.emit(
    { type: "request.started", body: payload },
    { targetId: null, targetRevision: null, traceId: identity.trace_id, spanId: identity.span_id },
  );
};

const emitCompleted = (
  context: RuntimeContext,
  identity: RequestIdentity,
  transport: "fetch" | "xhr",
  outcome: RequestCompletedPayload["outcome"],
  statusCode: number | null,
  responseBytes: number | null,
  errorClass: string | null,
): void => {
  const payload: RequestCompletedPayload = {
    browser_request_id: identity.browser_request_id,
    transport,
    method: identity.method,
    route: identity.route,
    status_code: statusCode ?? undefined,
    duration_ms: Math.round(context.clock.monotonicNowMs() - identity.started_at),
    response_bytes: responseBytes ?? undefined,
    outcome,
    error_class: errorClass ?? undefined,
  };
  context.emit(
    { type: "request.completed", body: payload },
    { targetId: null, targetRevision: null, traceId: identity.trace_id, spanId: identity.span_id },
  );
};

const contentLength = (value: string | null): number | null => {
  if (value === null) return null;
  const parsed = Number.parseInt(value, 10);
  return Number.isSafeInteger(parsed) ? parsed : null;
};

interface PreparedRequest {
  readonly identity: RequestIdentity;
  readonly headers: Readonly<Record<string, string>> | null;
}

/** Resolves route, injection eligibility, and trace identity for one request. */
const prepare = (context: RuntimeContext, url: string, method: string): PreparedRequest => {
  const documentOrigin = context.view.location.origin;
  const requestOrigin = resolveRequestOrigin(url, documentOrigin);
  const sameOrigin = requestOrigin === documentOrigin;
  const injectAllowed =
    requestOrigin !== null && context.manifest.traceInjectionAllowed(requestOrigin, documentOrigin);
  const absolute = new URL(url, documentOrigin);
  const route = context.routes.resolve(absolute, context.redaction);
  if (!injectAllowed) {
    return {
      identity: {
        browser_request_id: uuidv7(context.clock.wallNowMs(), context.random),
        method: normalizeMethod(method),
        route,
        same_origin: sameOrigin,
        injected: false,
        trace_id: null,
        span_id: null,
        started_at: context.clock.monotonicNowMs(),
      },
      headers: null,
    };
  }
  const span = context.trace.createClientSpan();
  return {
    identity: {
      browser_request_id: uuidv7(context.clock.wallNowMs(), context.random),
      method: normalizeMethod(method),
      route,
      same_origin: sameOrigin,
      injected: true,
      trace_id: span.trace_id,
      span_id: span.span_id,
      started_at: context.clock.monotonicNowMs(),
    },
    headers: { traceparent: span.traceparent, tracestate: span.tracestate },
  };
};

interface RequestDescriptor {
  readonly url: string;
  readonly method: string;
  readonly control: boolean;
}

const readInit = (input: RequestInfo | URL, init: RequestInit | undefined, view: BrowserView): RequestDescriptor => {
  if (input instanceof view.Request) {
    const request: Request = input;
    return {
      url: request.url,
      method: init?.method ?? request.method,
      control: request.headers.get(CONTROL_HEADER) !== null,
    };
  }
  const headers = new Headers(init?.headers ?? {});
  return {
    url: input instanceof URL ? input.href : input,
    method: init?.method ?? "GET",
    control: headers.get(CONTROL_HEADER) !== null,
  };
};

export const installFetchHook = (context: RuntimeContext, runtimeId: string): HookUninstall => {
  const view = context.view;
  const original = view.fetch;
  assertUnowned("fetch", original, runtimeId);

  const patched = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const descriptor = readInit(input, init, view);
    if (descriptor.control) return original.call(view, input, init as RequestInit);
    const prepared = prepare(context, descriptor.url, descriptor.method);
    let nextInput = input;
    let nextInit = init;
    if (prepared.headers !== null) {
      if (input instanceof view.Request) {
        const request: Request = input;
        const merged = new Headers(request.headers);
        for (const [name, value] of Object.entries(prepared.headers)) merged.set(name, value);
        nextInput = new view.Request(request, { headers: merged });
      } else {
        const merged = new Headers(init?.headers ?? {});
        for (const [name, value] of Object.entries(prepared.headers)) merged.set(name, value);
        nextInit = { ...init, headers: merged };
      }
    }
    emitStarted(context, prepared.identity, "fetch");
    try {
      const response = await original.call(view, nextInput, nextInit as RequestInit);
      emitCompleted(
        context,
        prepared.identity,
        "fetch",
        "completed",
        response.status,
        contentLength(response.headers.get("content-length")),
        null,
      );
      return response;
    } catch (cause) {
      const aborted = cause instanceof Error && cause.name === "AbortError";
      emitCompleted(
        context,
        prepared.identity,
        "fetch",
        aborted ? "aborted" : "network_error",
        null,
        null,
        cause instanceof Error ? cause.name : "UnknownError",
      );
      throw cause;
    }
  };

  markOwner(patched, runtimeId);
  view.fetch = patched;
  return (): void => {
    view.fetch = original;
  };
};

export const installXhrHook = (context: RuntimeContext, runtimeId: string): HookUninstall => {
  const view = context.view;
  const prototype = view.XMLHttpRequest.prototype;
  const originalOpen = prototype.open;
  const originalSend = prototype.send;
  assertUnowned("xhr", originalOpen, runtimeId);

  const descriptors = new WeakMap<XMLHttpRequest, { url: string; method: string }>();
  const active = new WeakMap<XMLHttpRequest, RequestIdentity>();

  const patchedOpen = function patchedOpen(
    this: XMLHttpRequest,
    method: string,
    url: string | URL,
    async?: boolean,
    username?: string | null,
    password?: string | null,
  ): void {
    descriptors.set(this, { url: url instanceof URL ? url.toString() : url, method });
    originalOpen.call(this, method, url, async ?? true, username ?? null, password ?? null);
  };

  const patchedSend = function patchedSend(this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null): void {
    const descriptor = descriptors.get(this);
    if (descriptor === undefined) {
      originalSend.call(this, body);
      return;
    }
    const prepared = prepare(context, descriptor.url, descriptor.method);
    if (prepared.headers !== null) {
      for (const [name, value] of Object.entries(prepared.headers)) this.setRequestHeader(name, value);
    }
    active.set(this, prepared.identity);
    emitStarted(context, prepared.identity, "xhr");
    this.addEventListener("loadend", () => {
      const identity = active.get(this);
      if (identity === undefined) return;
      active.delete(this);
      const status = this.status;
      const outcome = status === 0 ? "network_error" : "completed";
      emitCompleted(
        context,
        identity,
        "xhr",
        outcome,
        status === 0 ? null : status,
        contentLength(this.getResponseHeader("content-length")),
        status === 0 ? "NetworkError" : null,
      );
    });
    originalSend.call(this, body);
  };

  markOwner(patchedOpen, runtimeId);
  prototype.open = patchedOpen;
  prototype.send = patchedSend;
  return (): void => {
    prototype.open = originalOpen;
    prototype.send = originalSend;
  };
};
